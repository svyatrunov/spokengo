# Security policy

## Reporting a vulnerability

Please report security issues privately via a [GitHub security advisory](https://github.com/svyatrunov/SpokenGo/security/advisories/new)
or by opening an issue marked **[security]**. We aim to respond within a few days.

## What SpokenGo does and does not do

**Secrets**
- The API key is stored in the OS credential store (Windows Credential Manager)
  through `keyring`, under the service name `SpokenGo`. It is never written to
  config files, the repository, or logs (only a masked hint like `gsk_…1f2A` is
  ever displayed).

**Network**
- The only outbound connection is to `https://api.groq.com` to transcribe audio,
  authenticated with your key. There is no telemetry, analytics, auto-update, or
  any other network activity.

**Local data**
- Recordings and transcript history are stored under `%APPDATA%\SpokenGo`.
  Audio storage can be disabled, and old audio is auto-deleted after a
  configurable retention period. The log file may contain transcribed text;
  it stays local.

**System access (and why)**
- Global hotkey registration (to start/stop from any app).
- Reading the foreground window (to know where to paste and to show its icon).
- Synthesizing `Ctrl+V` / keystrokes (to insert the transcript).
- Microphone capture, only for the duration of a dictation (shared mode; we do
  not hold the device open in the background).

**Code safety**
- No `eval`, `exec`, `os.system`, `subprocess`, shell invocation, or `pickle`.
- All SQL is parameterized.
- The clipboard is snapshotted and restored after each paste.

## Supported versions

The latest release on the default branch is supported.
