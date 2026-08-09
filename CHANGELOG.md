# Changelog

All notable changes are documented here. This project uses
[semantic versioning](https://semver.org/).

## 0.10.0

- **Local mode** — transcribe offline with [faster-whisper](https://github.com/SYSTRAN/faster-whisper): no API key, no internet. Switch between cloud and local in one click (Settings → Провайдер). Models are auto-detected from the HuggingFace hub cache.
- **Desktop shortcut** — `spokengo install` creates a Desktop shortcut with a custom icon; `--icon logo.png` converts any image to `.ico` automatically. The shortcut is also created silently on the first GUI launch.
- **App icon** — new microphone icon (purple gradient, all six sizes: 16 / 32 / 48 / 64 / 128 / 256 px).

## 0.9.0

- Reliability: dictations are never lost. The last transcript stays on the
  clipboard (paste it again anywhere), the History tab has one-click copy and
  per-item retry, and there's a "Copy last" button on the main view.
- Fail-fast on network/VPN loss: ~15-30s timeout instead of minutes; the audio
  is queued so you can retry when you're back online.

## 0.8.0

- Packaging for distribution: Desktop + Start Menu shortcuts via `install.ps1`,
  windowed launcher (`spokengo-gui`, no console), app icon, `SECURITY.md`,
  `CONTRIBUTING.md`, polished README.

## 0.7.x

- Recording overlay rebuilt as a native Windows layered window (pywin32 +
  `UpdateLayeredWindow`) with correct 64-bit handle types — fixes flicker and
  the "appears once then vanishes" bug.
- Pillow-rendered rounded pill: smooth corners, soft shadow, gradient, animated
  "sonar-ping" dot, live target app, muted `Enter / Esc` hint.
- Dark theme for the main window; instant-apply model (segmented) and hotkey;
  single-line API key row with a clear (×) button; key field prefilled (masked).
- Fixed: phantom `alt` added to captured combos; paste target now taken at stop
  time; clipboard restored only after paste (no more pasting old clipboard).

## 0.6.0

- Recording overlay shows the target app icon + name, live as you switch windows.

## 0.5.x

- File logging + `spokengo logs`. Fixed Cloudflare 403 (User-Agent), and a
  request-flood from the duration auto-stop firing repeatedly.

## 0.4.x

- Single-instance guard, stop-on-Enter / cancel-on-Esc, hotkey capture by
  keypress, clearer API-key state, layout-independent Ctrl+V.

## 0.1.0 – 0.3.0

- Initial release: global hotkey, Groq Whisper transcription, paste into any
  field, local storage, recording overlay, full test suite.
