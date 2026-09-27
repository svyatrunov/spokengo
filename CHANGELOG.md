# Changelog

All notable changes are documented here. This project uses
[semantic versioning](https://semver.org/).

## 0.12.0

- **Redesigned control panel.** Warm graphite instead of indigo on navy, with
  red kept for recording only. One type family in four sizes, a 4/8/12/16/24 px
  spacing scale, flat sections split by hairlines instead of cards inside
  cards, and all text at WCAG AA contrast. The old grey 8-9 pt hints were
  3.4:1. Icons are drawn and antialiased with Pillow, replacing the emoji and
  Unicode glyphs, which rendered inconsistently.
- **Record and copy side by side.** The round record button moved from the
  header into the status panel. Next to it is an icon-only «Копировать
  последний текст» button with a tooltip. History rows copy, retry and keep
  audio with icon buttons too, and the busy state shows a thin progress line.
- **Groq is the main path, offline is the fallback**, and the UI now says so:
  «Groq · основной» / «Офлайн · запасной», with a one-line explanation under
  the switch.
- **Local models: Small is recommended; large models are gone from the
  download list.** On an ordinary CPU, large-v3-turbo (and its q5 build) take
  over a minute per phrase, so dictation is unusable. Small gives roughly half
  of Base's word error rate on Russian and still answers within seconds. Large
  models already on disk, or picked through «Выбрать файл…», keep working and
  are labelled «медленно». Auto-selection prefers a fast model over a bigger,
  slow one. `spokengo local download` now defaults to `small`. Groq model
  choice is unchanged.
- **Fix: long recordings no longer time out uploading to Groq.** The request
  timeout was a flat 15 s for the entire upload, so an ~18 MB WAV failed with
  «The write operation timed out». It now scales with file size: 15 s plus
  time at a pessimistic ~0.8 Mbit/s, capped at 5 min. Short clips still fail
  fast.
- `scripts/ui_shot.py`: `slow` scenario plus `SHOT_STATE=recording|transcribing|error`.

## 0.11.0

- **Local mode that actually works from the .exe.** The frozen build could never
  import a `pip install`ed faster-whisper, so "Локально" was a dead end on any
  machine but the dev one. SpokenGo now ships its own offline engine on demand:
  the official whisper.cpp CLI (`whisper-bin-x64.zip`, ~8 MB, SHA-256 pinned)
  plus GGML models downloaded from the Settings window with a progress bar and
  cancel. No Python, pip, or terminal anywhere in the flow.
- **Model discovery** looks everywhere a model could reasonably be: SpokenGo's
  models folder, every HuggingFace cache location (`HF_HOME`,
  `HUGGINGFACE_HUB_CACHE`, `~/.cache`, `%USERPROFILE%`, `%LOCALAPPDATA%`),
  `~/whisper.cpp/models`, `Downloads`, and any folder you add. Files are
  validated by their GGML magic, not by name. "Выбрать файл…" accepts a model,
  a faster-whisper folder, or your own `whisper-cli.exe`.
- **Robust on other people's PCs**: non-ASCII user names / temp paths are
  routed around (whisper.cpp takes narrow C strings), the console window never
  flashes, a 10-minute timeout guards long dictations, and every failure
  (blocked network, corrupt file, missing VC++ runtime, wrong file) has a
  specific, actionable message instead of a stack trace.
- faster-whisper stays supported as an optional backend when the library is
  importable (source installs); CT2 models appear in the same picker with an
  explanation when they cannot run.
- `spokengo local [status|install|download <id>|models]` — headless setup.
- `spokengo transcribe <file.wav> [--provider groq|local] [--model ...] [--language ru]`
  — re-run an existing recording through any provider and print the text,
  bypassing the recorder, history and paste.
- **UI refresh**: a hero card with live state, the hotkeys as key caps and the
  active engine as a badge; the offline setup is a two-step checklist with a
  status strip; every setting applies instantly (no more "Применить"); nothing
  is clipped at the default window size; empty-state copy in History; a
  version tag in the header.
- Config: `local_model` may now point to a `.bin` file; new `local_model_dirs`
  (list) and `local_threads` (0 = auto).

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
