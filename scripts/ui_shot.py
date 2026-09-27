"""Dev aid: render the control panel headlessly and save a screenshot.

Usage (Linux, needs python3-tk + ImageMagick):
  xvfb-run -a python scripts/ui_shot.py settings out.png [local] [dl|picker|hist]
"""
import os, sys, subprocess, time
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("APPDATA", "/tmp/spokengo-appdata")
os.environ.setdefault("SPOKENGO_GROQ_API_KEY", os.environ.get("FAKE_KEY", ""))
from spokengo.ui_tk import ControlPanel  # noqa: E402

tab = sys.argv[1] if len(sys.argv) > 1 else "settings"
out = sys.argv[2] if len(sys.argv) > 2 else f"/tmp/shot_{tab}.png"
provider = sys.argv[3] if len(sys.argv) > 3 else None

p = ControlPanel()
if provider:
    p.ctrl.save_settings(provider=provider)
    if hasattr(p, "_update_prov_seg"):
        p._update_prov_seg()
scenario = sys.argv[4] if len(sys.argv) > 4 else ""
if scenario:
    from pathlib import Path
    from spokengo.transcribe import local_engine as eng
    eng.engine_dir().mkdir(parents=True, exist_ok=True)
    eng.models_dir().mkdir(parents=True, exist_ok=True)
    (eng.engine_dir() / eng.ENGINE_EXE).write_bytes(b"x")
    (eng.engine_dir() / "engine.json").write_text('{"version": "v1.9.2"}')
    for n in ("ggml-small.bin", "ggml-large-v3-turbo-q5_0.bin"):
        (eng.models_dir() / n).write_bytes(eng.GGML_MAGIC + b"\0" * 1_500_000)
    p._refresh_local()
    if scenario == "dl":
        def job(prog, cancel):
            for i in range(0, 101, 5):
                prog(eng.Progress("download", i * 5_000_000, 488_000_000, "Small"))
                time.sleep(0.05)
            time.sleep(5)
        p._run_download("Small", job)
        for _ in range(12):
            p.root.update(); time.sleep(0.1)
    if scenario == "picker":
        p.root.update(); p._show_model_picker()
    if scenario == "hist":
        for i, t in enumerate(["Привет, это тестовая диктовка в Телеграм.", "", "Ещё одна запись подлиннее, чтобы посмотреть перенос строк в карточке истории."]):
            p.ctrl.storage.add(t, "local" if i else "groq", "m", 3.0, status=("pending" if not t else "done"))
p._show_tab(tab)
p.root.update()
time.sleep(0.6)
p.root.update()
subprocess.run(["import", "-window", "root", out], check=True)
print("saved", out)
p.root.destroy()
