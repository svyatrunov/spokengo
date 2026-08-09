"""CLI: open the GUI, run headless, manage the API key, inspect history/logs."""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .secrets_store import set_key


def main(argv=None) -> int:
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


def gui_entry() -> int:
    """Windowed launcher used by the desktop shortcut (no console window)."""
    from .logging_setup import setup_logging
    from .single_instance import acquire
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
