<p align="center">
  <img src="assets/icon.png" width="96" alt="SpokenGo">
</p>

<h1 align="center">SpokenGo</h1>

<p align="center">
  Voice input into <b>any</b> text field, anywhere on Windows.<br>
  Press your hotkey → speak → transcribed text is pasted where your cursor was.
</p>

<p align="center">
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows-0078D4">
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-3776AB">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="tests" src="https://img.shields.io/badge/tests-passing-success">
</p>

---

## Features

- **Works in any field** — chat, browser, editor, terminal, address bar. No per-app setup.
- **Global hotkey** — start with `Ctrl+Space` (configurable), **stop with `Enter`**, **cancel with `Esc`**.
- **Two transcription modes** — cloud via Groq Whisper API, or fully offline via a local faster-whisper model (switch in one click, no restart).
- **Live overlay** — a floating pill shows it's recording and *which app* text will land in.
- **Nothing is lost** — last transcript stays on the clipboard, History has one-click copy and per-item retry, failed items are queued for later.
- **Private by default** — audio and history stay on your machine; API key lives in Windows Credential Manager, never in a file.

<p align="center">
  <img src="assets/screenshot.png" width="620" alt="SpokenGo control panel and recording overlay">
</p>

---

## Install (Windows)

You need [Python 3.10+](https://www.python.org/downloads/) — tick **Add to PATH** during install.

```powershell
git clone https://github.com/svyatrunov/SpokenGo.git
cd SpokenGo
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

The installer:
1. Creates an isolated `.venv`
2. Installs SpokenGo and all runtime dependencies
3. Asks for your Groq API key (optional — you can add it later)
4. Puts a **SpokenGo shortcut** on your Desktop and in the Start Menu

After that just double-click the icon — no terminal needed.

> **Why no `.exe`?** Unsigned executables trigger SmartScreen / antivirus. Installing from source into a venv is safer and fully transparent.

---

## Groq API key (cloud mode)

1. Open [console.groq.com/keys](https://console.groq.com/keys) and create a key — free tier is generous.
2. Paste it in the app: **Settings → API key → Save**.

The key is stored in Windows Credential Manager (service `SpokenGo`), never on disk.

---

## Local mode (offline, no API key)

SpokenGo can transcribe entirely on your machine using [faster-whisper](https://github.com/SYSTRAN/faster-whisper). No internet connection or API key required.

**Step 1 — install the library** (once, into the same environment as SpokenGo):

```powershell
.venv\Scripts\pip install faster-whisper
```

**Step 2 — download a model:**

```powershell
.venv\Scripts\huggingface-cli download Systran/faster-whisper-medium
```

Models are cached in `~/.cache/huggingface/hub/`. Available sizes:

| Model | Size | Speed | Accuracy |
|---|---|---|---|
| `Systran/faster-whisper-tiny` | ~75 MB | fastest | lower |
| `Systran/faster-whisper-base` | ~145 MB | fast | good |
| `Systran/faster-whisper-small` | ~465 MB | good | good |
| `Systran/faster-whisper-medium` | ~1.5 GB | moderate | **great** |
| `Systran/faster-whisper-large-v3` | ~3 GB | slow | best |

**Step 3 — switch in the app:**

Open **Settings → Провайдер → 🖥 Локально**. SpokenGo auto-detects the downloaded model and switches immediately. Switch back to ☁ Groq any time.

> Runs on CPU by default (`int8` quantization). No GPU required.

---

## Usage

| Action | Key |
|---|---|
| Start recording | `Ctrl+Space` (or your combo) |
| Stop & paste | `Enter` |
| Cancel | `Esc` |

Press the hotkey anywhere, speak, press `Enter`. Text is pasted into the focused field. The **Record** button in the window does the same thing.

### Start automatically at login

```powershell
powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1
```

Undo with `scripts\autostart.ps1 -Remove`. A single-instance guard prevents double-launch.

---

## Privacy & security

| What | Where |
|---|---|
| API key | Windows Credential Manager — never in files or the repo |
| Audio recordings | `%APPDATA%\SpokenGo\` — local only, auto-deleted after N days (configurable) |
| Transcript history | `%APPDATA%\SpokenGo\` — local only |
| Network (cloud mode) | One endpoint only: `api.groq.com` |
| Network (local mode) | None — transcription runs entirely on your machine |

No telemetry. No analytics. No background calls. Failed transcriptions are retried only when you press **Retry**.

The last transcript stays on the clipboard so you can paste it again anywhere even if the first paste missed. Set `restore_clipboard = true` in the config to restore your previous clipboard instead.

---

## CLI

```
spokengo                         # open the control panel (default)
spokengo gui                     # same, explicit
spokengo run                     # headless mode (hotkey only, no window)
spokengo install                 # (re)create the Desktop shortcut
spokengo install --icon logo.png # use a custom icon (.ico / .png / .jpg)
spokengo install --start-menu    # also add to Start Menu
spokengo set-key groq <KEY>      # store a Groq API key
spokengo history                 # print recent transcripts
spokengo logs -n 50              # view log file (for troubleshooting)
spokengo --version
```

---

## Development

```bash
pip install -e ".[dev]"
pytest            # 91 tests, run on any OS without hardware
```

The platform layer (win32 injection, microphone, global hotkeys, overlay window) is isolated behind interfaces and mocked in tests — the entire core (state machine, providers, queue, storage, clipboard cycle) is tested without Windows or a network connection.

### Add a transcription provider

```python
from spokengo.transcribe.registry import register_provider
from spokengo.transcribe.base import Transcript

@register_provider("openai")
class OpenAIProvider:
    name = "openai"
    def __init__(self, api_key): ...
    def transcribe(self, audio_path, *, model, language=None) -> Transcript: ...
```

Then set `provider = "openai"` in `config.toml` and `spokengo set-key openai <KEY>`.

---

## Contributing

Issues and PRs are welcome — new providers, macOS/Linux support, UI polish. Please run `pytest` before opening a PR.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design, [docs/BUGS.md](docs/BUGS.md) for edge cases handled.

## License

[MIT](LICENSE).
