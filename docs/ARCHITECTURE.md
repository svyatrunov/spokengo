# Архитектура SpokenGo

## Поток данных (happy path)

```
[Ctrl+Space]
   │  HotkeyManager (поток с message-loop)
   ▼
StateMachine: IDLE → RECORDING        ── фиксируем HWND цели (focus snapshot)
   │  AudioRecorder пишет 16k/mono/int16 в буфер
[Ctrl+Space]                           ── (toggle) или отпускание (push-to-talk)
   ▼
StateMachine: RECORDING → TRANSCRIBING
   │  WAV → Storage (страховка) → провайдер (фоновый поток)
   ▼  Transcript
StateMachine: TRANSCRIBING → INJECTING
   │  Storage.save_transcript
   │  SetForegroundWindow(target)
   │  ClipboardCycle: snapshot → set(text) → SendInput(Ctrl+V) → restore
   ▼
StateMachine: INJECTING → IDLE
```

При любой ошибке: `* → ERROR → IDLE`, аудио остаётся в `Storage`/очереди.

## Слои и зависимости

```
        app.py (оркестратор, трей, single-instance)
          │ владеет
   ┌──────┼───────────────┬───────────────┬──────────────┐
hotkeys  pipeline        config         storage        overlay
          │ использует     │              │
   ┌──────┼─────────┐   secrets_store   (sqlite + files)
 audio  transcribe  inject
        (registry→   (clipboard логика +
         providers)   windows backend)
```

Правило зависимостей: ядро (`state`, `pipeline`, `config`, `storage`,
`transcribe`, `inject/clipboard`) **не импортирует** Windows-API. Платформенные
вызовы живут в `inject/windows.py`, `hotkeys.py` (win-ветка), `overlay.py` и
прячутся за интерфейсами `TextInjector`, `HotkeyManager`, `ClipboardBackend`.
Поэтому весь core тестируется на Linux в CI.

## Ключевые интерфейсы

```python
class TranscriptionProvider(Protocol):
    name: str
    def transcribe(self, audio_path: str, *, language: str | None) -> Transcript: ...

class ClipboardBackend(Protocol):           # реальный = win32, тестовый = fake
    def get_text(self) -> str | None: ...
    def set_text(self, text: str) -> None: ...

class TextInjector(Protocol):
    def capture_target(self) -> Target: ...   # HWND + история фокуса
    def inject(self, target: Target, text: str) -> bool: ...  # paste, фолбэк на набор

class HotkeyManager(Protocol):
    def register(self, combo: str, on_press, on_release) -> None: ...
```

## Расширение провайдеров

Добавить модель = новый класс + регистрация:

```python
@register_provider("openai")
class OpenAIProvider(TranscriptionProvider):
    def transcribe(self, audio_path, *, language=None) -> Transcript: ...
```

В конфиге `provider = "openai"`, ключ — в keyring под этим именем. Ядро не меняется.

## Потоки выполнения

- **UI/трей** — главный поток.
- **Hotkey** — отдельный поток с Windows message loop.
- **Audio** — колбэк sounddevice (свой поток PortAudio).
- **Transcribe + network** — пул фоновых задач, чтобы UI не вис.
- **Queue worker** — фоновый ретрай офлайн-очереди.

Состояние между потоками меняется только через `StateMachine` под локом.
