# Changelog

All notable changes are documented here. This project uses
[semantic versioning](https://semver.org/).

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
