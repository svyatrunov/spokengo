"""Offline engine: discovery, download, extraction, running — no network, no
real whisper.cpp. Everything goes through injectable openers/runners."""
import hashlib
import io
import os
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest

from spokengo.errors import ConfigError, ProviderError
from spokengo.transcribe import local_engine as eng
from spokengo.transcribe import local_provider as lp


# ---------------------------------------------------------------- helpers
class _Resp(io.BytesIO):
    def __init__(self, data: bytes, length=True):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))} if length else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _opener_for(data: bytes, length=True):
    calls = []

    def opener(url, timeout=30.0):
        calls.append(url)
        return _Resp(data, length)
    opener.calls = calls
    return opener


def _ggml_bytes(size=1_200_000) -> bytes:
    return eng.GGML_MAGIC + b"\0" * (size - 4)


def _make_zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Isolate every path the module looks at."""
    monkeypatch.setenv("SPOKENGO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("SPOKENGO_WHISPER_CLI", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(eng.shutil, "which", lambda *_: None)
    (tmp_path / "home").mkdir()
    return tmp_path


# ---------------------------------------------------------------- discovery
def test_ggml_magic_detection(sandbox):
    good = sandbox / "ggml-small.bin"
    good.write_bytes(_ggml_bytes())
    bad = sandbox / "ggml-fake.bin"
    bad.write_bytes(b"nope" + b"\0" * 2_000_000)
    tiny = sandbox / "ggml-tiny.bin"
    tiny.write_bytes(eng.GGML_MAGIC)          # too small to be real
    assert eng.is_ggml_model(good)
    assert not eng.is_ggml_model(bad)
    assert not eng.is_ggml_model(tiny)


def test_finds_models_in_downloads_and_models_dir_and_hf_cache(sandbox):
    (eng.models_dir()).mkdir(parents=True)
    (eng.models_dir() / "ggml-base.bin").write_bytes(_ggml_bytes())
    dl = sandbox / "home" / "Downloads"
    dl.mkdir()
    (dl / "ggml-large-v3-turbo-q5_0.bin").write_bytes(_ggml_bytes())
    (dl / "random.bin").write_bytes(_ggml_bytes())          # name filter skips it
    snap = (sandbox / "home" / ".cache" / "huggingface" / "hub"
            / "models--ggerganov--whisper.cpp" / "snapshots" / "abc")
    snap.mkdir(parents=True)
    (snap / "ggml-tiny.bin").write_bytes(_ggml_bytes())

    names = sorted(Path(m.path).name for m in eng.find_ggml_models())
    assert names == ["ggml-base.bin", "ggml-large-v3-turbo-q5_0.bin", "ggml-tiny.bin"]
    turbo = next(m for m in eng.find_ggml_models() if "turbo" in m.path)
    assert turbo.spec is not None and turbo.spec.recommended
    assert turbo.name == "Large v3 Turbo · q5"


def test_extra_dirs_are_scanned_and_deduped(sandbox):
    d = sandbox / "elsewhere"
    d.mkdir()
    (d / "ggml-small.bin").write_bytes(_ggml_bytes())
    found = eng.find_ggml_models([str(d), str(d), ""])
    assert len(found) == 1 and found[0].kind == "ggml"


def test_finds_ct2_models_in_every_hub_root(sandbox, monkeypatch):
    alt = sandbox / "hfhome"
    monkeypatch.setenv("HF_HOME", str(alt))
    snap = alt / "hub" / "models--Systran--faster-whisper-small" / "snapshots" / "x"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"\0" * 10)
    (snap / "config.json").write_text("{}")
    ms = eng.find_ct2_models()
    assert [m.name for m in ms] == ["Systran/faster-whisper-small"]
    assert ms[0].kind == "ct2"
    # legacy helper still answers with paths
    assert lp.find_local_whisper_models() == [str(snap)]
    assert lp.model_display(str(snap)) == "Systran/faster-whisper-small"


def test_classify_model_path(sandbox):
    f = sandbox / "ggml-medium-q5_0.bin"
    f.write_bytes(_ggml_bytes())
    m = eng.classify_model_path(str(f))
    assert m and m.kind == "ggml" and m.name == "medium-q5_0"
    assert eng.classify_model_path(str(sandbox / "missing.bin")) is None
    assert eng.classify_model_path("") is None


def test_find_engine_prefers_env_then_install_dir_then_extra(sandbox, monkeypatch):
    assert eng.find_engine() is None
    extra = sandbox / "wcpp" / "Release"
    extra.mkdir(parents=True)
    (extra / eng.ENGINE_EXE).write_bytes(b"x")
    assert eng.find_engine([str(sandbox / "wcpp")]) == extra / eng.ENGINE_EXE
    eng.engine_dir().mkdir(parents=True)
    own = eng.engine_dir() / eng.ENGINE_EXE
    own.write_bytes(b"x")
    assert eng.find_engine([str(sandbox / "wcpp")]) == own
    monkeypatch.setenv("SPOKENGO_WHISPER_CLI", str(extra / eng.ENGINE_EXE))
    assert eng.find_engine() == extra / eng.ENGINE_EXE


# ---------------------------------------------------------------- downloads
def test_download_streams_with_progress_and_atomic_rename(sandbox):
    data = b"a" * 700_000
    seen = []
    dest = sandbox / "out" / "file.bin"
    eng.download_file("https://x.test/f", dest, progress=seen.append,
                      opener=_opener_for(data))
    assert dest.read_bytes() == data
    assert not dest.with_suffix(".bin.part").exists()
    assert seen[-1].done == seen[-1].total == len(data)
    assert 0 < seen[0].fraction <= 1


def test_download_truncated_is_rejected(sandbox):
    class Short(_Resp):
        def __init__(self):
            super().__init__(b"a" * 1000)
            self.headers = {"Content-Length": "5000"}
    dest = sandbox / "f.bin"
    with pytest.raises(ProviderError, match="оборвалось"):
        eng.download_file("https://x.test/f", dest, opener=lambda u, timeout=30: Short())
    assert not dest.exists() and not list(sandbox.glob("*.part"))


def test_download_cancel_cleans_up(sandbox):
    cancel = threading.Event()
    cancel.set()
    dest = sandbox / "f.bin"
    with pytest.raises(eng.DownloadCancelled):
        eng.download_file("https://x.test/f", dest, cancel=cancel,
                          opener=_opener_for(b"a" * 10))
    assert not dest.exists()


def test_download_network_error_has_human_message(sandbox):
    def boom(url, timeout=30):
        raise OSError("connection reset")
    with pytest.raises(ProviderError) as ei:
        eng.download_file("https://huggingface.co/x", sandbox / "f", opener=boom)
    assert "huggingface.co" in str(ei.value) and "VPN" in str(ei.value)


def test_install_engine_verifies_sha_and_extracts_only_cli(sandbox):
    z = _make_zip({
        "Release/whisper-cli.exe": b"MZcli", "Release/whisper.dll": b"dll",
        "Release/ggml.dll": b"g", "Release/ggml-cpu-haswell.dll": b"g2",
        "Release/SDL2.dll": b"big", "Release/whisper-talk-llama.exe": b"no",
        "Release/whisper-cli.pdb": b"symbols",
    })
    sha = hashlib.sha256(z).hexdigest()
    phases = []
    exe = eng.install_engine(opener=_opener_for(z), sha256=sha,
                             progress=lambda p: phases.append(p.phase))
    names = sorted(p.name for p in eng.engine_dir().iterdir())
    assert "whisper-cli.exe" in names and "whisper.dll" in names
    assert "ggml-cpu-haswell.dll" in names
    assert "SDL2.dll" not in names and "whisper-talk-llama.exe" not in names
    assert "whisper-cli.pdb" not in names
    assert "whisper-bin.zip" not in names           # zip removed afterwards
    assert eng.engine_version() == eng.ENGINE_VERSION
    assert phases[0] == "download" and phases[-1] == "done"
    assert exe.name == eng.ENGINE_EXE


def test_install_engine_rejects_bad_sha(sandbox):
    z = _make_zip({"Release/whisper-cli.exe": b"MZ"})
    with pytest.raises(ProviderError, match="контрольной"):
        eng.install_engine(opener=_opener_for(z), sha256="00" * 32)
    assert not (eng.engine_dir() / "whisper-cli.exe").exists()


def test_download_model_rejects_non_ggml(sandbox):
    spec = eng.catalog_by_id("tiny")
    with pytest.raises(ProviderError, match="GGML"):
        eng.download_model(spec, opener=_opener_for(b"<html>blocked</html>" * 100_000))
    assert not (eng.models_dir() / spec.filename).exists()


def test_download_model_ok_and_selected_by_controller(sandbox, tmp_path):
    from spokengo.config import Config
    from spokengo.controller import GuiController
    from spokengo.storage import Storage
    spec = eng.catalog_by_id("large-v3-turbo-q5_0")
    ctrl = GuiController(cfg=Config(), root_dir=tmp_path / "cfg",
                         storage=Storage(tmp_path / "cfg"))
    import spokengo.transcribe.local_engine as mod
    real = mod.download_model
    try:
        mod.download_model = lambda s, **kw: real(s, opener=_opener_for(_ggml_bytes()), **kw)
        dest = ctrl.download_local_model(spec)
    finally:
        mod.download_model = real
    assert dest.name == spec.filename and ctrl.cfg.local_model == str(dest)
    st = ctrl.local_status()
    assert st.selected and st.selected.path == str(dest)


def test_catalog_has_one_recommended_and_valid_urls():
    recs = [m for m in eng.MODEL_CATALOG if m.recommended]
    assert len(recs) == 1
    for m in eng.MODEL_CATALOG:
        assert m.url.startswith("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-")
        assert m.filename.endswith(".bin") and m.size_bytes > 10_000_000
    assert eng.ENGINE_URL.endswith("whisper-bin-x64.zip") and len(eng.ENGINE_SHA256) == 64


# ---------------------------------------------------------------- running
def _fake_runner(text="Привет, мир", rc=0, stderr=b""):
    seen = {}

    def run(cmd, cwd=None, capture_output=True, timeout=None, creationflags=0, env=None):
        seen["cmd"], seen["cwd"] = cmd, cwd
        assert Path(cwd, "in.wav").exists()          # wav copied into work dir
        Path(cwd, "out.txt").write_text(text, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, rc, stdout=b"", stderr=stderr)
    run.seen = seen
    return run


def test_run_engine_uses_ascii_work_dir_and_reads_txt(sandbox):
    exe = eng.engine_dir() / eng.ENGINE_EXE
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    model = eng.models_dir() / "ggml-small.bin"
    model.parent.mkdir(parents=True)
    model.write_bytes(_ggml_bytes())
    wav = sandbox / "Запись" / "a.wav"
    wav.parent.mkdir()
    wav.write_bytes(b"RIFF")
    run = _fake_runner("[00:00.000 --> 00:01.000]  Привет,\n мир\n")
    text = eng.run_engine(exe, model, wav, language="ru", threads=3, runner=run)
    assert text == "Привет, мир"
    cmd = run.seen["cmd"]
    assert cmd[cmd.index("-l") + 1] == "ru" and cmd[cmd.index("-t") + 1] == "3"
    assert "-nt" in cmd and "-otxt" in cmd and cmd[cmd.index("-f") + 1] == "in.wav"
    assert not Path(run.seen["cwd"]).exists()          # scratch dir cleaned


def test_run_engine_errors_are_human(sandbox):
    exe = sandbox / "whisper-cli"
    exe.write_bytes(b"x")
    model = sandbox / "ggml-x.bin"
    model.write_bytes(_ggml_bytes())
    wav = sandbox / "a.wav"
    wav.write_bytes(b"RIFF")

    def failing(cmd, cwd=None, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"failed to load model")
    with pytest.raises(ProviderError, match="открыть модель"):
        eng.run_engine(exe, model, wav, runner=failing)

    def slow(cmd, cwd=None, timeout=None, **kw):
        raise subprocess.TimeoutExpired(cmd, timeout)
    with pytest.raises(ProviderError, match="лимит времени"):
        eng.run_engine(exe, model, wav, runner=slow)

    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    with pytest.raises(ConfigError):
        eng.run_engine(exe, model, wav, runner=missing)


# ---------------------------------------------------------------- provider
def test_provider_auto_picks_recommended_ggml_and_runs(sandbox, monkeypatch):
    exe = eng.engine_dir() / eng.ENGINE_EXE
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    md = eng.models_dir()
    md.mkdir(parents=True)
    (md / "ggml-tiny.bin").write_bytes(_ggml_bytes())
    (md / "ggml-large-v3-turbo-q5_0.bin").write_bytes(_ggml_bytes())
    calls = []
    monkeypatch.setattr(eng, "run_engine",
                        lambda e, m, w, **kw: calls.append((e, m)) or "ok text")
    prov = lp.LocalWhisperProvider("")
    wav = sandbox / "a.wav"
    wav.write_bytes(b"RIFF")
    tr = prov.transcribe(str(wav), language=None)
    assert tr.text == "ok text"
    assert calls[0][1].name == "ggml-large-v3-turbo-q5_0.bin"


def test_provider_without_engine_gives_actionable_error(sandbox):
    md = eng.models_dir()
    md.mkdir(parents=True)
    (md / "ggml-tiny.bin").write_bytes(_ggml_bytes())
    prov = lp.LocalWhisperProvider("")
    with pytest.raises(ProviderError) as ei:
        prov.transcribe(str(sandbox / "a.wav"))
    assert "движок" in str(ei.value).lower()
    assert getattr(ei.value, "transient", False) is False   # never auto-retried


def test_provider_with_missing_selected_model(sandbox):
    prov = lp.LocalWhisperProvider(str(sandbox / "gone.bin"))
    with pytest.raises(ProviderError, match="не найдена"):
        prov.transcribe(str(sandbox / "a.wav"))


def test_diagnose_headlines(sandbox, monkeypatch):
    monkeypatch.setattr(lp, "faster_whisper_available", lambda: False)
    st = lp.diagnose()
    assert not st.ready and "движок" in st.headline().lower()
    eng.engine_dir().mkdir(parents=True)
    (eng.engine_dir() / eng.ENGINE_EXE).write_bytes(b"x")
    st = lp.diagnose()
    assert "модель" in st.headline().lower()
    eng.models_dir().mkdir(parents=True)
    (eng.models_dir() / "ggml-small.bin").write_bytes(_ggml_bytes())
    st = lp.diagnose()
    assert st.ready and st.selected.name == "Small" and "whisper.cpp" in st.headline()
    # a CT2 model selected without faster-whisper explains itself
    snap = sandbox / "ct2"
    snap.mkdir()
    (snap / "model.bin").write_bytes(b"\0" * 10)
    (snap / "config.json").write_text("{}")
    st = lp.diagnose(local_model=str(snap))
    assert not st.ready and "faster-whisper" in st.headline()
    assert st.runnable_models and all(m.kind == "ggml" for m in st.runnable_models)


def test_controller_use_local_file_and_dirs(sandbox, tmp_path):
    from spokengo.config import Config, load_config
    from spokengo.controller import GuiController
    from spokengo.storage import Storage
    root = tmp_path / "cfg"
    ctrl = GuiController(cfg=Config(), root_dir=root, storage=Storage(root))
    f = sandbox / "somewhere" / "ggml-base.bin"
    f.parent.mkdir()
    f.write_bytes(_ggml_bytes())
    assert ctrl.use_local_file(str(f))
    assert ctrl.cfg.local_model == str(f)
    assert str(f.parent) in ctrl.cfg.local_model_dirs
    assert not ctrl.use_local_file(str(sandbox / "home"))
    ctrl.add_local_dir(str(f.parent))                 # no duplicates
    assert ctrl.cfg.local_model_dirs.count(str(f.parent)) == 1
    assert load_config(root / "config.toml").local_model_dirs == [str(f.parent)]
    # engine picked by hand
    e = sandbox / "wc" / eng.ENGINE_EXE
    e.parent.mkdir()
    e.write_bytes(b"x")
    assert ctrl.use_local_file(str(e))
    assert ctrl.local_status().engine == e
