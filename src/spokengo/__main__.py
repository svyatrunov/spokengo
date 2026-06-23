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
    from .ui_tk import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
