"""Self-contained offline engine: whisper.cpp + GGML models, no Python deps.

Why this exists. The one-file SpokenGo.exe cannot import anything a user
``pip install``s afterwards — a frozen build only sees what was bundled. So
"install faster-whisper, then switch to Local" silently never worked from the
.exe. Instead of shipping a 300 MB binary, the app fetches the official
whisper.cpp command-line build (~8 MB, from ggml-org's GitHub releases) into
``%LOCALAPPDATA%\\SpokenGo\\engine`` and a GGML model of the user's choice into
``…\\SpokenGo\\models``. Both steps happen from the Settings window with a
progress bar. Everything here works identically from source and from the .exe.

Nothing in this module touches Tk or Windows-only APIs at import time, so it
is fully unit-testable on Linux CI with a fake ``whisper-cli``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from ..errors import ConfigError, ProviderError

log = logging.getLogger("spokengo.local_engine")

# ---------------------------------------------------------------------------
# Pinned engine build. Verified by SHA-256 so a tampered/mirrored zip can never
# be executed. Update all three together when bumping.
# ---------------------------------------------------------------------------
ENGINE_VERSION = "v1.9.2"
ENGINE_URL = ("https://github.com/ggml-org/whisper.cpp/releases/download/"
              f"{ENGINE_VERSION}/whisper-bin-x64.zip")
ENGINE_SHA256 = "49dcc16de826f20bd53d44f947a1ae49dfa81f86cad67a64d80820cb192d674a"
ENGINE_ZIP_BYTES = 8_194_445
# Only these files are extracted from the zip (the rest are demos and tests).
ENGINE_EXE = "whisper-cli.exe" if sys.platform == "win32" else "whisper-cli"
_ENGINE_KEEP_PREFIXES = ("whisper-cli", "whisper.dll", "ggml")

GGML_MAGIC = b"lmgg"          # 0x67676d6c little-endian — every GGML whisper model
HF_MODEL_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"

USER_AGENT = "SpokenGo (+https://github.com/svyatrunov/spokengo)"


@dataclass(frozen=True)
class ModelSpec:
    """A GGML model from the official ggerganov/whisper.cpp repo."""
    id: str                # short id used in UI / config, e.g. "small"
    filename: str          # e.g. "ggml-small.bin"
    size_bytes: int        # approximate, for the progress bar before headers arrive
    title: str             # human label
    blurb: str             # one line: what it is good for
    speed: int             # 1..5 (5 = fastest)
    quality: int           # 1..5 (5 = best)
    recommended: bool = False
    slow: bool = False     # too slow for dictation on an ordinary CPU

    @property
    def url(self) -> str:
        return HF_MODEL_BASE + self.filename

    @property
    def size_label(self) -> str:
        return human_size(self.size_bytes)


# Offered for download in the UI. Local mode is the offline fallback; Groq is
# the fast path. On a typical laptop CPU whisper.cpp needs a few seconds per
# phrase with Small, but a minute or more with the large models, which makes
# dictation unusable. So only models that stay interactive on a CPU are listed.
# Small is recommended: for Russian its word error rate is roughly half of
# Base's, at about 2-3x Base's run time, still a few seconds per phrase.
MODEL_CATALOG: List[ModelSpec] = [
    ModelSpec("tiny", "ggml-tiny.bin", 77_700_000,
              "Tiny", "мгновенно, но для русского слабовато", 5, 1),
    ModelSpec("base", "ggml-base.bin", 148_000_000,
              "Base", "быстро, терпимо для русского", 4, 2),
    ModelSpec("small", "ggml-small.bin", 488_000_000,
              "Small", "лучший баланс скорости и точности на CPU", 3, 3,
              recommended=True),
]

# Recognised when found on disk (or picked by hand) and still fully supported,
# but never offered for download: on a CPU a short phrase takes over a minute.
SLOW_MODELS: List[ModelSpec] = [
    ModelSpec("large-v3-turbo-q5_0", "ggml-large-v3-turbo-q5_0.bin", 574_000_000,
              "Large v3 Turbo · q5", "точно, но медленно на CPU", 1, 5, slow=True),
    ModelSpec("large-v3-turbo", "ggml-large-v3-turbo.bin", 1_620_000_000,
              "Large v3 Turbo", "точно, но очень медленно на CPU", 1, 5, slow=True),
]


def catalog_by_id(model_id: str) -> Optional[ModelSpec]:
    """Any known model, downloadable or not, by id or file name."""
    for m in MODEL_CATALOG + SLOW_MODELS:
        if m.id == model_id or m.filename == model_id:
            return m
    return None


_SLOW_NAME_HINTS = ("large", "medium")


def is_slow_name(name: str) -> bool:
    """Heuristic for models we have no spec for (user-picked files, CT2 dirs)."""
    low = name.lower()
    return any(h in low for h in _SLOW_NAME_HINTS)


def human_size(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f} ГБ"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f} МБ"
    return f"{max(n, 0) // 1000} КБ"


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
def data_dir() -> Path:
    """Large, machine-local files (engine + models). Not the roaming config dir."""
    override = os.environ.get("SPOKENGO_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "SpokenGo"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "SpokenGo"
    return Path.home() / ".local" / "share" / "SpokenGo"


def engine_dir() -> Path:
    return data_dir() / "engine"


def models_dir() -> Path:
    return data_dir() / "models"


def _hf_hub_roots() -> List[Path]:
    """Every place a HuggingFace hub cache may live on this machine.

    The old code checked exactly one path. Real machines differ: HF_HOME set by
    another tool, a cache under %USERPROFILE% when HOME is redirected, etc.
    """
    roots: List[Path] = []
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    if os.environ.get("HF_HOME"):
        roots.append(Path(os.environ["HF_HOME"]) / "hub")
    if os.environ.get("XDG_CACHE_HOME"):
        roots.append(Path(os.environ["XDG_CACHE_HOME"]) / "huggingface" / "hub")
    homes = {Path.home()}
    for var in ("USERPROFILE", "HOME"):
        if os.environ.get(var):
            homes.add(Path(os.environ[var]))
    for h in homes:
        roots.append(h / ".cache" / "huggingface" / "hub")
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "huggingface" / "hub")
    return _dedupe(roots)


def _dedupe(paths: Iterable[Path]) -> List[Path]:
    seen, out = set(), []
    for p in paths:
        try:
            key = str(p.resolve()).lower() if sys.platform == "win32" else str(p.resolve())
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def ggml_search_roots(extra_dirs: Iterable[str] = ()) -> List[Path]:
    """Directories scanned (non-recursively, plus one level) for GGML models."""
    roots: List[Path] = [models_dir()]
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "SpokenGo" / "models")
    home = Path.home()
    roots += [home / "whisper.cpp" / "models", home / "models",
              home / "Downloads", home / "Загрузки"]
    if os.environ.get("USERPROFILE"):
        roots.append(Path(os.environ["USERPROFILE"]) / "Downloads")
    # ggerganov/whisper.cpp downloaded through huggingface-cli
    for hub in _hf_hub_roots():
        snaps = hub / "models--ggerganov--whisper.cpp" / "snapshots"
        if snaps.is_dir():
            roots += [d for d in snaps.iterdir() if d.is_dir()]
    roots += [Path(d) for d in extra_dirs if d]
    return _dedupe(roots)


# ---------------------------------------------------------------------------
# Models on disk
# ---------------------------------------------------------------------------
def is_ggml_model(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 1_000_000:
            return False
        with open(path, "rb") as f:
            return f.read(4) == GGML_MAGIC
    except OSError:
        return False


@dataclass
class LocalModel:
    """A model found on disk, of either backend."""
    path: str
    kind: str               # "ggml" (whisper.cpp) | "ct2" (faster-whisper)
    name: str
    size_bytes: int
    spec: Optional[ModelSpec] = None

    @property
    def size_label(self) -> str:
        return human_size(self.size_bytes)

    @property
    def slow(self) -> bool:
        """Large/medium models: supported, but minutes per phrase on a CPU."""
        if self.spec is not None:
            return self.spec.slow
        return is_slow_name(self.name) or is_slow_name(Path(self.path).name)


def ggml_display_name(path: Path) -> str:
    stem = path.stem
    if stem.startswith("ggml-"):
        stem = stem[5:]
    spec = catalog_by_id(path.name)
    return spec.title if spec else stem


def find_ggml_models(extra_dirs: Iterable[str] = ()) -> List[LocalModel]:
    found: List[LocalModel] = []
    for root in ggml_search_roots(extra_dirs):
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.suffix.lower() != ".bin" or p.name.endswith(".part"):
                continue
            # Cheap name filter first so a large Downloads folder stays fast;
            # anything named ggml-* or whisper* still gets the magic check.
            low = p.name.lower()
            if not (low.startswith("ggml") or "whisper" in low):
                continue
            if is_ggml_model(p):
                found.append(LocalModel(str(p), "ggml", ggml_display_name(p),
                                        p.stat().st_size, catalog_by_id(p.name)))
    # A user-picked file that lives anywhere else is added by the caller.
    return _dedupe_models(found)


def _dedupe_models(models: List[LocalModel]) -> List[LocalModel]:
    seen, out = set(), []
    for m in models:
        try:
            key = os.path.normcase(str(Path(m.path).resolve()))
        except OSError:
            key = m.path
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def find_ct2_models(extra_dirs: Iterable[str] = ()) -> List[LocalModel]:
    """faster-whisper (CTranslate2) snapshot dirs: model.bin + config.json."""
    out: List[LocalModel] = []
    for hub in _hf_hub_roots():
        if not hub.is_dir():
            continue
        try:
            dirs = sorted(hub.iterdir())
        except OSError:
            continue
        for d in dirs:
            if not d.is_dir() or not d.name.startswith("models--"):
                continue
            snapshots = d / "snapshots"
            if not snapshots.is_dir():
                continue
            for snap in sorted(snapshots.iterdir(), reverse=True):
                if _is_ct2_dir(snap):
                    name = "/".join(d.name.replace("models--", "").split("--", 1))
                    out.append(LocalModel(str(snap), "ct2", name,
                                          (snap / "model.bin").stat().st_size))
                    break
    for extra in extra_dirs:
        p = Path(extra)
        if _is_ct2_dir(p):
            out.append(LocalModel(str(p), "ct2", p.name, (p / "model.bin").stat().st_size))
    return _dedupe_models(out)


def _is_ct2_dir(p: Path) -> bool:
    return p.is_dir() and (p / "model.bin").is_file() and (p / "config.json").is_file()


def classify_model_path(path: str) -> Optional[LocalModel]:
    """Turn a config value / user pick into a LocalModel, or None if unusable."""
    if not path:
        return None
    p = Path(path)
    if p.is_file() and is_ggml_model(p):
        return LocalModel(str(p), "ggml", ggml_display_name(p), p.stat().st_size,
                          catalog_by_id(p.name))
    if _is_ct2_dir(p):
        name = p.name
        parent = p.parent.parent.name
        if parent.startswith("models--"):
            name = "/".join(parent.replace("models--", "").split("--", 1))
        return LocalModel(str(p), "ct2", name, (p / "model.bin").stat().st_size)
    return None


# ---------------------------------------------------------------------------
# Engine on disk
# ---------------------------------------------------------------------------
def find_engine(extra_dirs: Iterable[str] = ()) -> Optional[Path]:
    """The whisper-cli binary, if any: env override, our install, next to the
    exe, user-supplied dirs, PATH."""
    env = os.environ.get("SPOKENGO_WHISPER_CLI")
    if env and Path(env).is_file():
        return Path(env)
    cands = [engine_dir() / ENGINE_EXE]
    try:
        cands.append(Path(sys.executable).resolve().parent / ENGINE_EXE)
    except OSError:
        pass
    for d in extra_dirs:
        if d:
            cands.append(Path(d) / ENGINE_EXE)
            cands.append(Path(d) / "Release" / ENGINE_EXE)
    for c in cands:
        if c.is_file():
            return c
    w = shutil.which("whisper-cli")
    return Path(w) if w else None


def engine_version() -> str:
    try:
        meta = json.loads((engine_dir() / "engine.json").read_text("utf-8"))
        return str(meta.get("version", ""))
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Downloads (stdlib only; progress + cancel)
# ---------------------------------------------------------------------------
@dataclass
class Progress:
    phase: str                 # "download" | "verify" | "extract" | "done"
    done: int = 0
    total: int = 0
    label: str = ""

    @property
    def fraction(self) -> float:
        return (self.done / self.total) if self.total else 0.0


ProgressCb = Callable[[Progress], None]


class DownloadCancelled(Exception):
    pass


def _http_open(url: str, timeout: float = 30.0):  # pragma: no cover - network
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def download_file(url: str, dest: Path, *, expected_size: int = 0,
                  progress: Optional[ProgressCb] = None,
                  cancel: Optional[threading.Event] = None,
                  opener: Callable = _http_open, label: str = "") -> Path:
    """Stream ``url`` to ``dest`` via a ``.part`` file; atomic rename at the end.

    Raises ProviderError with a human message on network failure (the caller
    shows it verbatim in the UI), DownloadCancelled if ``cancel`` is set.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    total = expected_size
    done = 0
    last_emit = 0.0
    try:
        with opener(url) as resp, open(part, "wb") as out:
            try:
                total = int(resp.headers.get("Content-Length") or total)
            except (TypeError, ValueError, AttributeError):
                pass
            while True:
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled()
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if progress and (now - last_emit > 0.1 or done == total):
                    last_emit = now
                    progress(Progress("download", done, total, label))
    except DownloadCancelled:
        _unlink(part)
        raise
    except Exception as exc:
        _unlink(part)
        raise ProviderError(_network_message(url, exc)) from exc
    if total and done < total:
        _unlink(part)
        raise ProviderError(
            f"Скачивание оборвалось на {human_size(done)} из {human_size(total)}. "
            "Проверьте соединение и попробуйте ещё раз.")
    os.replace(part, dest)
    if progress:
        progress(Progress("download", done, total or done, label))
    return dest


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def _network_message(url: str, exc: Exception) -> str:
    host = url.split("/")[2] if "//" in url else url
    return (f"Не удалось скачать с {host}: {exc}. "
            "Если сайт заблокирован в вашей сети — включите VPN или положите файл "
            "вручную (кнопка «Выбрать файл…»).")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def install_engine(*, progress: Optional[ProgressCb] = None,
                   cancel: Optional[threading.Event] = None,
                   opener: Callable = _http_open,
                   url: str = ENGINE_URL, sha256: str = ENGINE_SHA256,
                   target: Optional[Path] = None) -> Path:
    """Download the pinned whisper.cpp build, verify it, extract the CLI.

    Returns the path to whisper-cli. Idempotent: re-running replaces the files.
    """
    if sys.platform != "win32" and opener is _http_open and url == ENGINE_URL:
        raise ProviderError(
            "Автоустановка движка есть только для Windows. На этой ОС соберите "
            "whisper.cpp сами и укажите папку с whisper-cli через «Папку…».")
    target = target or engine_dir()
    target.mkdir(parents=True, exist_ok=True)
    zip_path = target / "whisper-bin.zip"
    download_file(url, zip_path, expected_size=ENGINE_ZIP_BYTES,
                  progress=progress, cancel=cancel, opener=opener,
                  label="Движок whisper.cpp")
    try:
        if progress:
            progress(Progress("verify", 0, 1, "Проверка подписи"))
        if sha256:
            got = sha256_of(zip_path)
            if got.lower() != sha256.lower():
                raise ProviderError(
                    "Скачанный архив движка не совпадает с ожидаемой контрольной "
                    "суммой — файл повреждён или подменён. Попробуйте ещё раз.")
        if progress:
            progress(Progress("extract", 0, 1, "Распаковка"))
        extracted = extract_engine(zip_path, target)
        if not extracted:
            raise ProviderError("В архиве не найден whisper-cli — неожиданный формат релиза.")
        (target / "engine.json").write_text(json.dumps(
            {"version": ENGINE_VERSION, "url": url, "installed": time.time()}),
            encoding="utf-8")
    finally:
        _unlink(zip_path)
    exe = target / ENGINE_EXE
    if progress:
        progress(Progress("done", 1, 1, "Готово"))
    return exe


def extract_engine(zip_path: Path, target: Path) -> Optional[Path]:
    """Pull only the CLI and its DLLs out of the release zip (flattening
    ``Release/``). Returns the CLI path or None if the zip had no CLI."""
    exe: Optional[Path] = None
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            low = name.lower()
            if not low.startswith(_ENGINE_KEEP_PREFIXES):
                continue
            if low.startswith("whisper-cli") and not (low.endswith(".exe") or low == "whisper-cli"):
                continue
            dest = target / name
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            if sys.platform != "win32" and low.startswith("whisper-cli"):
                os.chmod(dest, 0o755)
            if low.startswith("whisper-cli"):
                exe = dest
    return exe


def download_model(spec: ModelSpec, *, progress: Optional[ProgressCb] = None,
                   cancel: Optional[threading.Event] = None,
                   opener: Callable = _http_open,
                   target_dir: Optional[Path] = None) -> Path:
    target_dir = target_dir or models_dir()
    dest = target_dir / spec.filename
    download_file(spec.url, dest, expected_size=spec.size_bytes, progress=progress,
                  cancel=cancel, opener=opener, label=spec.title)
    if not is_ggml_model(dest):
        _unlink(dest)
        raise ProviderError(
            "Скачанный файл не похож на модель Whisper (нет сигнатуры GGML). "
            "Возможно, сеть подменила ответ страницей — попробуйте через VPN.")
    if progress:
        progress(Progress("done", 1, 1, spec.title))
    return dest


# ---------------------------------------------------------------------------
# Running the engine
# ---------------------------------------------------------------------------
def default_threads() -> int:
    n = os.cpu_count() or 4
    return max(1, min(n, 8))


def _is_ascii(s: str) -> bool:
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _short_path(p: Path) -> str:  # pragma: no cover - Windows only
    """8.3 short name — the classic fix for C programs + Cyrillic user names."""
    if sys.platform != "win32":
        return str(p)
    try:
        import ctypes
        from ctypes import wintypes
        GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        buf = ctypes.create_unicode_buffer(1024)
        n = GetShortPathNameW(str(p), buf, 1024)
        if 0 < n < 1024:
            return buf.value
    except Exception:
        pass
    return str(p)


def _safe_arg(p: Path, cwd: Path) -> str:
    """A form of ``p`` that a narrow-string C program can open from ``cwd``.

    whisper-cli takes ``char*`` paths; on Windows those are decoded in the
    ANSI code page, so ``C:\\Users\\Слава\\…`` fails to open. Prefer a relative
    ASCII path, then an 8.3 short path, then give up and pass it as-is.
    """
    s = str(p)
    if _is_ascii(s):
        return s
    try:
        rel = os.path.relpath(s, str(cwd))
        if _is_ascii(rel):
            return rel
    except ValueError:
        pass  # different drives
    short = _short_path(p)
    return short


def _creationflags() -> int:
    # Never flash a console window behind the GUI.
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _env_for(exe: Path) -> dict:
    """On Linux/macOS the release build keeps its shared libs next to the CLI."""
    env = dict(os.environ)
    if sys.platform != "win32":
        key = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
        env[key] = str(exe.parent) + (os.pathsep + env[key] if env.get(key) else "")
    return env


def engine_selftest(exe: Path, timeout: float = 20.0) -> bool:
    """True if the binary starts at all (missing DLL / wrong arch fail here)."""
    try:
        r = subprocess.run([str(exe), "--help"], capture_output=True, timeout=timeout,
                           creationflags=_creationflags(), env=_env_for(exe))
        return r.returncode in (0, 1)   # some builds exit 1 after printing usage
    except Exception:
        return False


def run_engine(exe: Path, model: Path, wav: Path, *, language: Optional[str] = None,
               threads: int = 0, timeout: float = 600.0,
               runner: Callable = subprocess.run) -> str:
    """Transcribe ``wav`` with whisper-cli; returns plain text.

    The work happens in a scratch dir next to the engine with ASCII names, so
    non-ASCII user profiles and temp dirs cannot break the C program.
    """
    work = Path(tempfile.mkdtemp(prefix="sg-", dir=_work_root(exe)))
    try:
        local_wav = work / "in.wav"
        shutil.copyfile(wav, local_wav)
        out_base = work / "out"
        cmd = [str(exe), "-m", _safe_arg(model, work), "-f", "in.wav",
               "-otxt", "-of", "out", "-nt", "-np",
               "-t", str(threads or default_threads()),
               "-l", (language or "auto")]
        try:
            r = runner(cmd, cwd=str(work), capture_output=True, timeout=timeout,
                       creationflags=_creationflags(), env=_env_for(exe))
        except subprocess.TimeoutExpired:
            raise ProviderError(
                "Локальное распознавание не уложилось в лимит времени. "
                "Выберите модель поменьше (Small или Base) или переключитесь на Groq.")
        except FileNotFoundError:
            raise ConfigError("Движок whisper.cpp не найден — переустановите его в Настройках.")
        except OSError as exc:
            raise ProviderError(f"Не удалось запустить движок: {exc}")
        txt_path = Path(str(out_base) + ".txt")
        if r.returncode != 0 and not txt_path.exists():
            raise ProviderError(_engine_error_message(r))
        if txt_path.exists():
            text = txt_path.read_text(encoding="utf-8", errors="replace")
        else:
            text = _decode(r.stdout)
        return _clean_text(text)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _work_root(exe: Path) -> Optional[str]:
    root = exe.parent / "work"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return str(root)
    except OSError:
        return None


def _decode(b) -> str:
    if isinstance(b, str):
        return b
    return (b or b"").decode("utf-8", errors="replace")


def _engine_error_message(r) -> str:
    err = (_decode(r.stderr) + "\n" + _decode(r.stdout)).strip()
    tail = "\n".join(err.splitlines()[-4:])
    low = err.lower()
    if ("failed to load model" in low or "failed to initialize whisper context" in low
            or ("failed to open" in low and "model" in low)):
        return ("Движок не смог открыть модель — файл повреждён или это не GGML-модель. "
                "Скачайте модель заново в Настройках.")
    if "dll" in low or r.returncode in (-1073741515, 3221225781):
        return ("Не хватает системной библиотеки для движка (обычно Visual C++ "
                "Redistributable 2015-2022 x64). Установите её с сайта Microsoft.")
    return f"whisper.cpp завершился с ошибкой ({r.returncode}): {tail or 'без вывода'}"


def _clean_text(text: str) -> str:
    lines = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        # Defensive: strip "[00:00.000 --> 00:02.000]  " prefixes if -nt was ignored
        if ln.startswith("[") and "-->" in ln and "]" in ln:
            ln = ln.split("]", 1)[1].strip()
        lines.append(ln)
    return " ".join(lines).strip()
