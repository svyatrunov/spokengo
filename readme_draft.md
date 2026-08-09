# SpokenGo

A Windows voice-input utility that transcribes speech via a global hotkey and injects the text into whatever field is focused. Press a key, speak, release — the transcription appears where your cursor is.

![Screenshot placeholder](docs/screenshot.png)

---

## Requirements

- Windows 10 or later (64-bit)
- Python 3.10+
- A microphone
- **Groq API key** (free tier, default) _or_ a locally downloaded faster-whisper model

---

## Installation

```powershell
git clone https://github.com/your-org/spokengo.git
cd spokengo
pip install -e ".[runtime]"
```

That's it. No PyPI package is available yet — install from the repository.

---

## Quick Start

### 1. Get a Groq API key

Create a free account at <https://console.groq.com> and generate an API key.

### 2. Launch the app

```powershell
spokengo-gui   # GUI mode (no console window)
# or
spokengo       # CLI mode
```

On first launch, a desktop/Start-menu shortcut is created automatically.

### 3. Enter your API key

Open **Settings**, paste your Groq API key into the API Key field, and save. The key is stored in the Windows Credential Store — it is never written to the config file.

### 4. Start dictating

1. Click into any text field (browser, editor, chat app, etc.).
2. Press **Ctrl+Space** (default hotkey) to begin recording.
3. Speak. Press **Enter** to stop and transcribe, or **Esc** to cancel (audio is briefly kept for recovery).
4. The transcribed text is injected at the cursor. If no field is focused, the text is copied to the clipboard as a fallback.

---

## Local Whisper (Optional)

To run transcription entirely offline with [faster-whisper](https://github.com/SYSTRAN/faster-whisper):

```powershell
pip install -e ".[runtime,local]"
```

Then in **Settings**, switch the provider to **Local** and select a model (e.g. `base`, `small`, `medium`). The model is downloaded automatically on first use and cached locally. Local mode has no recording time limit.

---

## Settings Reference

Settings are stored in `%APPDATA%\SpokenGo\config.toml`.

| Setting | Default | Description |
|---|---|---|
| `hotkey` | `ctrl+space` | Global hotkey to start recording |
| `provider` | `groq` | Transcription backend: `groq` or `local` |
| `model` | `whisper-large-v3-turbo` | Groq Whisper model to use |
| `local_model` | `base` | faster-whisper model size |
| `max_seconds` | `60` | Max recording length in seconds (Groq only; local is unlimited) |
| `store_audio` | `true` | Retain `.wav` files after transcription for retry |

The Groq API key is **not** in `config.toml`; it lives in the Windows Credential Store under the entry `SpokenGo`.

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Space` | Start recording (configurable) |
| `Enter` | Stop recording and transcribe |
| `Esc` | Cancel recording (audio kept ~60 s for recovery, then purged) |

These shortcuts are global — they work even when the SpokenGo window is not focused.

---

## Troubleshooting

**Hotkey doesn't trigger**
Another application may have registered the same combination. Change the hotkey in Settings to something less common (e.g. `ctrl+alt+space`).

**Microphone access blocked**
Windows privacy settings may be blocking microphone access. Go to **Settings → Privacy & security → Microphone** and ensure microphone access is on for desktop apps.

**Antivirus flags the executable**
SpokenGo uses `pywin32` to inject keystrokes, which some heuristics flag as keylogger-like. Add the SpokenGo folder to your antivirus exclusion list, or install from source and verify the code yourself.

**PortAudio / sounddevice error on startup**
`sounddevice` requires PortAudio. On Windows, it is bundled with the `sounddevice` wheel, but if you see a DLL error, install the Microsoft Visual C++ Redistributable (2015–2022) and retry.

**Groq transcription fails intermittently**
Transient Groq API errors are queued and retried automatically. If failures persist, check your API key and rate limits at <https://console.groq.com>.

---

## License

MIT — see [LICENSE](LICENSE).
