# Contributing to SpokenGo

Thanks for your interest! Contributions of all sizes are welcome.

## Development setup

```bash
git clone https://github.com/svyatrunov/SpokenGo.git
cd SpokenGo
pip install -e ".[dev]"
pytest
```

The whole core (state machine, clipboard cycle, providers, queue, storage) is
unit-tested without Windows, a microphone, or a network — the platform layer is
mocked. Please keep it that way: put OS/hardware code behind an interface and
test the logic with a fake.

## Before opening a pull request

- Run `pytest` — all tests must pass.
- Add tests for new behavior.
- Keep the platform-independent core free of `win32`/`sounddevice` imports
  (lazy-import them inside the platform modules only).
- Match the existing style; no large reformatting in feature PRs.

## Good first contributions

- A new transcription provider (see the example in the README).
- Packaging for macOS / Linux (global hotkeys + text injection).
- A system-tray mode (minimize to tray, hotkey in the background).
- UI polish.

## Reporting bugs

Open an issue with your OS, Python version, and the relevant lines from
`%APPDATA%\SpokenGo\spokengo.log` (run `spokengo logs -n 40`). Never paste your
API key — it is not written to the log, but double-check before sharing.

## Security

Please report security issues privately — see [SECURITY.md](SECURITY.md).
