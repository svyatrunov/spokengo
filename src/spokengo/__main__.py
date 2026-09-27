"""CLI: open the GUI, run headless, manage the API key, inspect history/logs."""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .secrets_store import set_key


def _fix_console_encoding() -> None:
    """Windows consoles often use a legacy codepage (cp1251 on Russian Windows,
    cp866, ...) that cannot encode the arrows/checkmarks/stars this CLI prints,
    crashing with UnicodeEncodeError the moment output isn't plain ASCII —
    including when stdout is redirected to a file. UTF-8 with replacement is
    always safe and never raises."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv=None) -> int:
    _fix_console_encoding()
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="spokengo", description="Voice input anywhere")
    p.add_argument("--version", action="version", version=f"SpokenGo {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sk = sub.add_parser("set-key", help="store an API key in the OS credential store")
    sk.add_argument("provider")
    sk.add_argument("key")

    sub.add_parser("gui", help="open the control panel window (default)")
    sub.add_parser("run", help="start the background app (headless, hotkey only)")
    sub.add_parser("history", help="show recent transcripts")
    lg = sub.add_parser("logs", help="show the log file (path + last lines)")
    lg.add_argument("-n", "--lines", type=int, default=100)

    inst = sub.add_parser("install", help="create a desktop shortcut (Windows)")
    inst.add_argument("--icon", metavar="FILE",
                      help="custom icon: .ico, .png, .jpg, etc. (auto-converted)")
    inst.add_argument("--start-menu", action="store_true",
                      help="also add to Start Menu / SpokenGo")

    loc = sub.add_parser("local", help="offline mode: status / install engine / download model")
    loc.add_argument("action", nargs="?", default="status",
                     choices=["status", "install", "download", "models"])
    loc.add_argument("model", nargs="?", help="model id for download (see `spokengo local models`)")

    tc = sub.add_parser("transcribe", help="transcribe one .wav file directly "
                        "(bypasses recording/history/paste — handy for re-running an old recording "
                        "through a different provider)")
    tc.add_argument("path", help="path to a .wav file")
    tc.add_argument("--provider", default=None,
                    help="groq | local (default: whichever is set in Настройки)")
    tc.add_argument("--model", default=None,
                    help="e.g. whisper-large-v3-turbo, whisper-large-v3 (groq); "
                         "ignored for local, which uses the selected local model")
    tc.add_argument("--language", default=None, help="ISO code, e.g. ru (default: auto)")

    args = p.parse_args(argv)

    from .logging_setup import log_path, setup_logging, tail
    setup_logging(console=False)

    if args.cmd == "logs":
        print(f"Лог: {log_path()}\n")
        out = tail(lines=args.lines)
        print(out if out else "(лог пуст — запустите gui/run и воспроизведите ошибку)")
        return 0
    if args.cmd == "set-key":
        set_key(args.provider, args.key)
        print(f"Ключ для '{args.provider}' сохранён.")
        return 0
    if args.cmd == "history":
        from .config import default_config_dir
        from .storage import Storage
        st = Storage(default_config_dir())
        for r in st.recent(20):
            print(f"{r.ts:.0f}  [{r.status}]  {r.text[:80]}")
        return 0
    if args.cmd == "install":
        if sys.platform != "win32":
            print("Ярлык поддерживается только на Windows.")
            return 1
        from pathlib import Path
        from .install import create_shortcut, make_ico
        from .config import default_config_dir
        icon: Path | None = None
        if args.icon:
            src = Path(args.icon)
            if not src.exists():
                print(f"Файл иконки не найден: {src}")
                return 1
            if src.suffix.lower() != ".ico":
                dst = default_config_dir() / "icon.ico"
                dst.parent.mkdir(parents=True, exist_ok=True)
                make_ico(src, dst)
                print(f"Иконка конвертирована: {dst}")
                icon = dst
            else:
                icon = src
        try:
            paths = create_shortcut(icon, start_menu=args.start_menu)
            for p in paths:
                print(f"Ярлык создан: {p}")
        except Exception as exc:
            print(f"Ошибка при создании ярлыка: {exc}")
            return 1
        return 0
    if args.cmd == "local":
        return _local_cmd(args)
    if args.cmd == "transcribe":
        return _transcribe_cmd(args)
    if args.cmd == "run":
        from .app import App
        App().run()
        return 0
    if args.cmd == "gui" or args.cmd is None:  # default: show the window
        from .single_instance import acquire
        if not acquire():
            print("SpokenGo уже запущен (см. трей/панель задач).")
            return 0
        from .ui_tk import main as gui_main
        gui_main()
        return 0
    p.print_help()
    return 1


def _local_cmd(args) -> int:
    """Headless counterpart of Settings → Локально (handy over RDP/SSH)."""
    from .transcribe import local_engine as eng
    from .transcribe.local_provider import diagnose
    from .config import load_config, save_config

    def bar(p: eng.Progress):
        if p.phase == "download" and p.total:
            print(f"\r  {p.label}: {eng.human_size(p.done)} / {eng.human_size(p.total)}"
                  f"  {p.fraction * 100:5.1f}%", end="", flush=True)
        elif p.phase != "download":
            print(f"\n  {p.label}…", end="", flush=True)

    cfg = load_config()
    if args.action == "models":
        for m in eng.MODEL_CATALOG:
            print(f"  {m.id:<22} {m.size_label:>8}  {m.title}{'  ★' if m.recommended else ''}  — {m.blurb}")
        print("\n  Медленные на CPU (скачиваются только явно, по id):")
        for m in eng.SLOW_MODELS:
            print(f"  {m.id:<22} {m.size_label:>8}  {m.title}  — {m.blurb}")
        return 0
    if args.action == "install":
        exe = eng.install_engine(progress=bar)
        print(f"\nДвижок установлен: {exe}  (работает: {eng.engine_selftest(exe)})")
        return 0
    if args.action == "download":
        spec = eng.catalog_by_id(args.model or "small")
        if spec is None:
            print(f"Неизвестная модель: {args.model}. Список: spokengo local models")
            return 1
        dest = eng.download_model(spec, progress=bar)
        cfg.local_model = str(dest)
        cfg.provider = "local"
        save_config(cfg)
        print(f"\nМодель сохранена и выбрана: {dest}")
        return 0
    st = diagnose(local_model=cfg.local_model, extra_dirs=tuple(cfg.local_model_dirs))
    print(f"Движок:  {st.engine or 'не установлен'}")
    print(f"faster-whisper: {'есть' if st.faster_whisper else 'нет'}")
    print("Модели:")
    for m in st.models or []:
        mark = "→" if st.selected and m.path == st.selected.path else " "
        print(f"  {mark} {m.name:<28} {m.size_label:>8}  {m.kind:<4}  {m.path}")
    if not st.models:
        print("  (не найдено)")
    print(f"Статус:  {st.headline()}")
    return 0 if st.ready else 2


def _transcribe_cmd(args) -> int:
    """`spokengo transcribe <path.wav> [--provider ...]` — one-off transcription
    of an existing audio file, e.g. to re-run a stuck/failed local recording
    through Groq. Bypasses the recorder, history and text injection entirely;
    just prints the resulting text."""
    from pathlib import Path
    from .config import load_config
    from .secrets_store import get_key
    from .transcribe import get_provider

    path = Path(args.path)
    if not path.exists():
        print(f"Файл не найден: {path}")
        return 1

    cfg = load_config()
    provider_name = args.provider or cfg.provider
    try:
        if provider_name == "local":
            provider = get_provider("local")(cfg.local_model,
                                             extra_dirs=tuple(cfg.local_model_dirs),
                                             threads=cfg.local_threads)
            model = args.model or cfg.model
        else:
            key = get_key(provider_name)
            if not key:
                print(f"API-ключ для '{provider_name}' не задан (spokengo set-key {provider_name} ...).")
                return 1
            provider = get_provider(provider_name)(key)
            model = args.model or "whisper-large-v3-turbo"
    except Exception as exc:
        print(f"Не удалось подготовить провайдер '{provider_name}': {exc}")
        return 1

    try:
        tr = provider.transcribe(str(path), model=model, language=args.language)
    except Exception as exc:
        print(f"Ошибка распознавания: {exc}")
        return 2

    print(tr.text)
    return 0


def gui_entry() -> int:
    """Windowed launcher used by the desktop shortcut (no console window)."""
    from .logging_setup import setup_logging
    from .single_instance import acquire
    _fix_console_encoding()
    setup_logging(console=False)
    if not acquire():
        return 0
    if sys.platform == "win32":
        from .install import maybe_create_shortcut_once
        maybe_create_shortcut_once()
    from .ui_tk import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
