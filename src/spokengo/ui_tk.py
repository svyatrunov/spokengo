"""Tkinter control panel — dark theme, hand-styled widgets.

Layout (top → bottom):
  header   — wordmark + live record chip
  hero     — big state card: what the app is doing, the hotkeys, current engine
  tabs     — Настройки | История
  settings — one card per concern: Распознавание (Groq / Локально), Хоткей,
             Запись и хранилище. Every control applies instantly; there is no
             "Apply" button anywhere.
  local    — the offline setup lives entirely in the window: install the
             whisper.cpp engine, download a model with a progress bar, or point
             at a file/folder. Nothing ever tells the user to open a terminal.

All app logic lives in GuiController; this module only draws and dispatches.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Callable, List, Optional

from .controller import GuiController
from .hotkeys import clipboard_action, combo_from_state
from .state import State
from .transcribe import local_engine as eng

# ---------------------------------------------------------------- palette
BG = "#141826"; BAR = "#0f1320"; SURF = "#1b2133"; SURF2 = "#262e45"; SURF3 = "#303a55"
BORDER = "#2a3249"; BORDER2 = "#39425e"
TXT = "#eef1f7"; SUB = "#c4cadb"; MUT = "#8f98b3"; DIM = "#6b7390"
ACC = "#6366f1"; ACC_H = "#787bf5"; ACC_SOFT = "#2b2f6b"
REC = "#ef4444"; REC_H = "#f26d6d"
GREEN = "#4ade80"; GREEN_SOFT = "#173a2a"
AMBER = "#fbbf24"; AMBER_SOFT = "#3d3216"
BLUE = "#60a5fa"
FONT = "Segoe UI"

_STATE = {
    State.IDLE:         (GREEN, "Запись",    "Готов к диктовке"),
    State.RECORDING:    (REC,   "Стоп",      "Слушаю…"),
    State.TRANSCRIBING: (AMBER, "Распознаю", "Распознаю речь…"),
    State.INJECTING:    (BLUE,  "Вставка",   "Вставляю текст…"),
    State.ERROR:        (REC,   "Ошибка",    "Что-то пошло не так"),
}


def _smooth_rect(canvas, x0, y0, x1, y1, r, **kw):
    """Rounded polygon — B-spline smoothing gives soft corners."""
    pts = (x0+r, y0,  x1-r, y0,  x1, y0,  x1, y0+r,
           x1, y1-r,  x1, y1,  x1-r, y1,  x0+r, y1,
           x0, y1,  x0, y1-r,  x0, y0+r,  x0, y0)
    return canvas.create_polygon(pts, smooth=True, **kw)


def _f(size=10, bold=False):
    return (FONT, size, "bold" if bold else "normal")


class _RCard:
    """Canvas-backed card with rounded corners. ``.inner`` is the content frame."""
    def __init__(self, tk_mod, parent, *, bg=SURF, border=BORDER, radius=12, outer=BG):
        self._cv = tk_mod.Canvas(parent, bg=outer, highlightthickness=0, bd=0)
        self.inner = tk_mod.Frame(self._cv, bg=bg)
        self._bg, self._bdr, self._r = bg, border, radius
        self._win = self._cv.create_window(1, 1, window=self.inner, anchor="nw")
        self._cv.bind("<Configure>", lambda e: self._cv.after_idle(self._draw))
        self.inner.bind("<Configure>", lambda e: self._cv.after_idle(self._draw))

    def recolor(self, bg=None, border=None):
        if bg: self._bg = bg; self.inner.configure(bg=bg)
        if border: self._bdr = border
        self._draw()

    def _draw(self):
        try:
            w = self._cv.winfo_width()
            if w <= 1:
                return
            h = self.inner.winfo_reqheight() + 2
            self._cv.configure(height=max(h, 24))
            self._cv.itemconfigure(self._win, width=max(w - 2, 1))
            self._cv.delete("r")
            _smooth_rect(self._cv, 1, 1, w - 1, h - 1, self._r,
                         fill=self._bg, outline=self._bdr, tags="r")
            self._cv.tag_lower("r")
        except Exception:
            pass

    def pack(self, **kw): self._cv.pack(**kw)
    def pack_forget(self): self._cv.pack_forget()
    def grid(self, **kw): self._cv.grid(**kw)


class _Progress:
    """Thin rounded progress bar on a canvas (determinate + indeterminate)."""
    def __init__(self, tk_mod, parent, height=6, bg=SURF):
        self.tk = tk_mod
        self.cv = tk_mod.Canvas(parent, height=height, bg=bg, highlightthickness=0, bd=0)
        self._h = height
        self._frac = 0.0
        self._pulse = None
        self.cv.bind("<Configure>", lambda e: self._draw())

    def set(self, frac: float):
        self._frac = max(0.0, min(1.0, frac))
        self._draw()

    def _draw(self, offset: float = -1):
        cv, h = self.cv, self._h
        w = cv.winfo_width()
        if w <= 2:
            return
        cv.delete("all")
        _smooth_rect(cv, 0, 0, w, h, h // 2, fill=SURF3, outline="")
        if offset >= 0:                      # indeterminate sweep
            seg = w * 0.3
            x0 = -seg + (w + seg) * offset
            _smooth_rect(cv, max(0, x0), 0, min(w, x0 + seg), h, h // 2, fill=ACC, outline="")
        elif self._frac > 0:
            _smooth_rect(cv, 0, 0, max(h, w * self._frac), h, h // 2, fill=ACC, outline="")

    def pulse(self, on: bool):
        if on and self._pulse is None:
            t0 = time.monotonic()

            def tick():
                self._draw(((time.monotonic() - t0) * 0.6) % 1.0)
                self._pulse = self.cv.after(40, tick)
            tick()
        elif not on and self._pulse is not None:
            try:
                self.cv.after_cancel(self._pulse)
            except Exception:
                pass
            self._pulse = None
            self._draw()

    def pack(self, **kw): self.cv.pack(**kw)


class ControlPanel:
    HISTORY_LIMIT = 50

    def __init__(self, controller: Optional[GuiController] = None):
        import tkinter as tk
        self.tk = tk
        self.ctrl = controller or GuiController()
        self.ctrl.on_status = self._on_status
        self.ctrl.on_recording_started = self._register_rec_keys
        self.ctrl.on_recording_stopped = self._unregister_rec_keys
        self._hk = None
        self._start_hk_id = None
        self._rec_hk_ids: List[int] = []
        self._capturing = False
        self._popup = None
        self._ui_q: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._dl_cancel: Optional[threading.Event] = None
        self._dl_running = False
        self._status_after = None

        try:  # Windows taskbar: our own icon, not Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("wind.slw.SpokenGo")
        except Exception:
            pass

        self.root = tk.Tk()
        self.root.title("SpokenGo")
        self.root.geometry("500x680")
        self.root.minsize(470, 600)
        self.root.configure(bg=BG)
        try:
            from .resources import icon_path
            ico = icon_path()
            if ico.exists():
                self.root.iconbitmap(default=str(ico))
        except Exception:
            pass

        from .overlay import TkOverlay
        ov = TkOverlay(self.root)
        ov.target_provider = self.ctrl.peek_target
        self.ctrl.overlay = ov

        self._build_header()
        self._build_hero()
        self._build_tabs()
        self._build_settings()
        self._build_history()
        self._show_tab("settings")

        self.ctrl.on_cancelled = self._on_cancelled
        self._refresh_history()
        self._refresh_key_status()
        self._refresh_hero()
        self._apply_status(State.IDLE, "")
        self._start_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_ui_queue)

    # ================================================================ helpers
    def _card(self, parent, **kw):
        return _RCard(self.tk, parent, **kw)

    def _label(self, parent, text, color=TXT, size=10, bold=False, bg=BG, **kw):
        return self.tk.Label(parent, text=text, bg=bg, fg=color, font=_f(size, bold), **kw)

    def _btn(self, parent, text, cmd, primary=False, danger=False, small=False, bg=None):
        base = ACC if primary else (bg or SURF2)
        hover = ACC_H if primary else SURF3
        fg = "#ffffff" if primary else (REC_H if danger else TXT)
        b = self.tk.Button(parent, text=text, command=cmd, relief="flat", bd=0,
                           cursor="hand2", padx=(10 if small else 14), pady=(4 if small else 7),
                           bg=base, fg=fg, activebackground=hover, activeforeground=fg,
                           font=_f(9 if small else 10), highlightthickness=0)
        b.bind("<Enter>", lambda e: b.configure(bg=hover))
        b.bind("<Leave>", lambda e: b.configure(bg=base))
        return b

    def _entry(self, parent, textvariable, show=None, width=None, center=False):
        e = self.tk.Entry(parent, textvariable=textvariable, show=show, width=width,
                          bg=BAR, fg=TXT, insertbackground=TXT, relief="flat",
                          highlightthickness=1, highlightbackground=BORDER2,
                          highlightcolor=ACC, font=_f(11),
                          justify="center" if center else "left")
        return e

    def _segment(self, parent, items, on_pick, bg=SURF):
        """Segmented control: list of (label, id). Returns dict id → label widget."""
        seg = self.tk.Frame(parent, bg=BORDER2)
        seg.pack(fill="x")
        out = {}
        for i, (label, sid) in enumerate(items):
            b = self.tk.Label(seg, text=label, bg=SURF2, fg=MUT, font=_f(10),
                              cursor="hand2", pady=8)
            b.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 1, 0))
            seg.grid_columnconfigure(i, weight=1)
            b.bind("<Button-1>", lambda e, s=sid: on_pick(s))
            out[sid] = b
        return out

    @staticmethod
    def _paint_segment(seg: dict, current: str):
        for sid, b in seg.items():
            on = sid == current
            b.configure(bg=ACC if on else SURF2, fg="#ffffff" if on else MUT)

    def _pill(self, parent, text, fg, soft, bg=SURF):
        """Small rounded status badge: ● text."""
        f = self.tk.Frame(parent, bg=soft, padx=9, pady=2)
        lbl = self.tk.Label(f, text=text, bg=soft, fg=fg, font=_f(9))
        lbl.pack()
        f._lbl = lbl
        return f

    def _post(self, fn: Callable[[], None]):
        """Run ``fn`` on the Tk thread (from worker threads)."""
        self._ui_q.put(fn)

    def _drain_ui_queue(self):
        try:
            while True:
                fn = self._ui_q.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.root.after(60, self._drain_ui_queue)

    # ================================================================ header
    def _build_header(self):
        tk = self.tk
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=22, pady=(18, 2))
        left = tk.Frame(bar, bg=BG); left.pack(side="left")
        self._label(left, "SpokenGo", size=19, bold=True).pack(side="left")
        from . import __version__
        self._label(left, f"v{__version__}", color=DIM, size=8).pack(side="left", padx=(8, 0), pady=(8, 0))

        CHIP_H, CHIP_W = 36, 136
        self._chip_cv = tk.Canvas(bar, width=CHIP_W, height=CHIP_H, bg=BG,
                                  highlightthickness=0, bd=0, cursor="hand2")
        self._chip_cv.pack(side="right")
        self._chip_pill = _smooth_rect(self._chip_cv, 0, 1, CHIP_W, CHIP_H - 1, CHIP_H // 2,
                                       fill=ACC, outline="")
        self._chip_oval = self._chip_cv.create_oval(0, 0, 1, 1, fill=GREEN, outline="")
        self._chip_sq = self._chip_cv.create_rectangle(0, 0, 1, 1, fill="#ffffff",
                                                       outline="", state="hidden")
        self._chip_txt = self._chip_cv.create_text(77, CHIP_H // 2, text="Запись",
                                                   fill="#ffffff", anchor="center", font=_f(11))
        self._chip_cv.bind("<Button-1>", lambda e: self.ctrl.toggle())
        self._chip_layout("Запись")

    def _chip_layout(self, label: str) -> None:
        try:
            import tkinter.font as tkfont
            tw = tkfont.Font(family=FONT, size=11).measure(label)
        except Exception:
            tw = max(1, len(label) * 7)
        DOT_R, GAP, CHIP_W, CHIP_H = 5, 8, 136, 36
        x0 = (CHIP_W - DOT_R * 2 - GAP - tw) / 2
        dx, dy = x0 + DOT_R, CHIP_H // 2
        self._chip_cv.coords(self._chip_oval, dx - DOT_R, dy - DOT_R, dx + DOT_R, dy + DOT_R)
        self._chip_cv.coords(self._chip_sq, dx - 4, dy - 4, dx + 4, dy + 4)
        self._chip_cv.coords(self._chip_txt, x0 + DOT_R * 2 + GAP + tw / 2, dy)

    # ================================================================ hero
    def _build_hero(self):
        tk = self.tk
        self._hero = self._card(self.root, bg=SURF, border=BORDER, radius=14)
        self._hero.pack(fill="x", padx=22, pady=(12, 0))
        h = tk.Frame(self._hero.inner, bg=SURF); h.pack(fill="x", padx=16, pady=14)

        top = tk.Frame(h, bg=SURF); top.pack(fill="x")
        self._hero_dot = tk.Canvas(top, width=14, height=14, bg=SURF, highlightthickness=0)
        self._hero_dot.pack(side="left", pady=(3, 0))
        self._hero_dot_id = self._hero_dot.create_oval(2, 2, 12, 12, fill=GREEN, outline="")
        self._hero_title = self._label(top, "Готов к диктовке", size=13, bold=True, bg=SURF)
        self._hero_title.pack(side="left", padx=(8, 0))
        self._hero_mode = self._pill(top, "", ACC_H, ACC_SOFT)
        self._hero_mode.pack(side="right")

        self._hero_keys = tk.Frame(h, bg=SURF); self._hero_keys.pack(fill="x", pady=(10, 0))
        self._key_caps = {}
        for name in ("start", "stop", "cancel"):
            cap = tk.Label(self._hero_keys, text="", bg=BAR, fg=SUB, font=(FONT, 9),
                           padx=7, pady=2, highlightthickness=1, highlightbackground=BORDER2)
            hint = tk.Label(self._hero_keys, text="", bg=SURF, fg=MUT, font=_f(9))
            self._key_caps[name] = (cap, hint)
        self._layout_keycaps()

        bottom = tk.Frame(h, bg=SURF); bottom.pack(fill="x", pady=(10, 0))
        self.status_lbl = tk.Label(bottom, text="", bg=SURF, fg=MUT, font=_f(9),
                                   anchor="w", justify="left", wraplength=300)
        self.status_lbl.pack(side="left", fill="x", expand=True)
        self._btn(bottom, "⧉ Последний текст", self._copy_last, small=True).pack(side="right")

    def _layout_keycaps(self):
        cfg = self.ctrl.cfg
        for w in self._hero_keys.winfo_children():
            w.pack_forget()
        pairs = (("start", cfg.hotkey, "старт"), ("stop", cfg.stop_key, "вставить"),
                 ("cancel", cfg.cancel_key, "отмена"))
        for i, (name, key, what) in enumerate(pairs):
            cap, hint = self._key_caps[name]
            cap.configure(text=self._pretty_key(key))
            hint.configure(text=what)
            cap.pack(side="left", padx=((0 if i == 0 else 14), 0))
            hint.pack(side="left", padx=(5, 0))

    @staticmethod
    def _pretty_key(k: str) -> str:
        names = {"escape": "Esc", "enter": "Enter", "return": "Enter", "space": "Space",
                 "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
        return " + ".join(names.get(p, p.upper() if len(p) == 1 else p.capitalize())
                          for p in k.split("+"))

    def _refresh_hero(self):
        cfg = self.ctrl.cfg
        if cfg.provider == "groq":
            model = "Turbo" if "turbo" in cfg.model else "Large v3"
            txt, fg, soft = f"☁  Groq · {model}", ACC_H, ACC_SOFT
            if not self.ctrl.has_key():
                txt, fg, soft = "☁  Groq · нет ключа", AMBER, AMBER_SOFT
        else:
            st = self.ctrl.local_status()
            if st.ready:
                name = st.selected.name.replace("Large v3 ", "").replace(" · ", " ")
                if len(name) > 16:
                    name = name[:15] + "…"
                txt, fg, soft = f"🖥  Локально · {name}", GREEN, GREEN_SOFT
            else:
                txt, fg, soft = "🖥  Локально · не настроено", AMBER, AMBER_SOFT
        self._hero_mode.configure(bg=soft)
        self._hero_mode._lbl.configure(text=txt, bg=soft, fg=fg)
        self._layout_keycaps()

    # ================================================================ tabs
    def _build_tabs(self):
        bar = self.tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=22, pady=(16, 0))
        self._tabs = {}
        for key, title in (("settings", "Настройки"), ("history", "История")):
            holder = self.tk.Frame(bar, bg=BG)
            holder.pack(side="left", padx=(0, 22))
            lbl = self._label(holder, title, color=MUT, size=11, cursor="hand2")
            lbl.pack()
            ind = self.tk.Frame(holder, bg=BG, height=2)
            ind.pack(fill="x", pady=(6, 0))
            lbl.bind("<Button-1>", lambda e, k=key: self._show_tab(k))
            self._tabs[key] = (lbl, ind)
        self.tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x", padx=22)
        self._body = self.tk.Frame(self.root, bg=BG)
        self._body.pack(fill="both", expand=True, padx=22, pady=(12, 14))
        self._active_scroll = None
        self.root.bind_all("<MouseWheel>", self._on_scroll)
        self.root.bind_all("<Button-4>", lambda e: self._scroll_by(-1))
        self.root.bind_all("<Button-5>", lambda e: self._scroll_by(1))

    def _on_scroll(self, event):
        self._scroll_by(int(-event.delta / 120))

    def _scroll_by(self, units):
        if self._active_scroll:
            self._active_scroll.yview_scroll(units, "units")

    def _show_tab(self, key):
        for k, (lbl, ind) in self._tabs.items():
            active = k == key
            lbl.configure(fg=TXT if active else MUT)
            ind.configure(bg=ACC if active else BG)
        for w in (getattr(self, "_settings", None), getattr(self, "_history", None)):
            if w is not None:
                w.pack_forget()
        (self._settings if key == "settings" else self._history).pack(fill="both", expand=True)
        if key == "settings":
            self._active_scroll = getattr(self, "_set_canvas", None)
            self._refresh_storage_usage()
        else:
            self._active_scroll = getattr(self, "_hist_canvas", None)
            self._refresh_history()

    def _scrollable(self, parent):
        """Canvas + dark scrollbar; returns (canvas, inner_frame)."""
        import tkinter.ttk as ttk
        tk = self.tk
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Dark.Vertical.TScrollbar", background=SURF2, troughcolor=BG,
                     bordercolor=BG, lightcolor=SURF2, darkcolor=SURF2, arrowcolor=BG,
                     gripcount=0, relief="flat", width=6)
        st.map("Dark.Vertical.TScrollbar", background=[("active", BORDER2), ("pressed", ACC)])
        try:
            st.layout("Dark.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": 1, "sticky": "nswe"})]})])
        except Exception:
            pass
        wrap = tk.Frame(parent, bg=BG); wrap.pack(fill="both", expand=True)
        cv = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=cv.yview,
                           style="Dark.Vertical.TScrollbar")
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(6, 0))
        cv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(cv, bg=BG)
        win = cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfig(win, width=e.width))
        return cv, inner

    # ================================================================ settings
    def _section(self, parent, title, subtitle=None):
        card = self._card(parent)
        body = self.tk.Frame(card.inner, bg=SURF)
        body.pack(fill="x", padx=14, pady=(12, 13))
        head = self.tk.Frame(body, bg=SURF); head.pack(fill="x")
        self._label(head, title, color=TXT, size=10, bold=True, bg=SURF).pack(side="left")
        if subtitle:
            self._label(head, subtitle, color=DIM, size=9, bg=SURF).pack(side="left", padx=(8, 0))
        return card, body, head

    def _build_settings(self):
        tk = self.tk
        self._settings = tk.Frame(self._body, bg=BG)
        self._set_canvas, si = self._scrollable(self._settings)

        # ---- 1. Распознавание -------------------------------------------
        self._prov_card, pp, _ = self._section(si, "Распознавание")
        self._prov_card.pack(fill="x", pady=(0, 10))
        segwrap = tk.Frame(pp, bg=SURF); segwrap.pack(fill="x", pady=(10, 0))
        self._prov_seg = self._segment(segwrap, [("☁  Groq — в облаке", "groq"),
                                                 ("🖥  Локально — офлайн", "local")],
                                       self._select_provider)
        self._prov_sub = tk.Frame(pp, bg=SURF); self._prov_sub.pack(fill="x", pady=(12, 0))
        self._build_groq_panel()
        self._build_local_panel()

        # ---- 2. Хоткей --------------------------------------------------
        self._hk_card, hp, hhead = self._section(si, "Сочетание для старта")
        self._hk_card.pack(fill="x", pady=(0, 10))
        hrow = tk.Frame(hp, bg=SURF); hrow.pack(fill="x", pady=(10, 0))
        self.hotkey_var = tk.StringVar(value=self._pretty_key(self.ctrl.cfg.hotkey))
        self.hotkey_chip = tk.Label(hrow, textvariable=self.hotkey_var, bg=BAR, fg=TXT,
                                    font=_f(11), padx=12, pady=6,
                                    highlightthickness=1, highlightbackground=BORDER2)
        self.hotkey_chip.pack(side="left")
        self.capture_btn = self._btn(hrow, "Изменить…", self._begin_capture)
        self.capture_btn.pack(side="right")
        self._label(hp, "Работает в любом окне. Во время записи: Enter — вставить, Esc — отменить.",
                    color=MUT, size=9, bg=SURF, wraplength=400, justify="left").pack(anchor="w", pady=(8, 0))

        # ---- 3. Запись и хранилище --------------------------------------
        self._limits_card, lp, _ = self._section(si, "Запись и хранилище")
        self._limits_card.pack(fill="x")
        self._max_sec_row = tk.Frame(lp, bg=SURF)
        self._label(self._max_sec_row, "Авто-стоп через", color=SUB, size=10, bg=SURF).pack(side="left")
        self.max_sec_var = tk.StringVar(value=str(self.ctrl.cfg.max_seconds))
        e1 = self._entry(self._max_sec_row, self.max_sec_var, width=5, center=True)
        e1.pack(side="left", padx=(8, 6), ipady=3)
        self._label(self._max_sec_row, "сек", color=MUT, size=10, bg=SURF).pack(side="left")
        self._label(self._max_sec_row, "· Groq принимает до ~350 с", color=DIM, size=9,
                    bg=SURF).pack(side="left", padx=(8, 0))
        self._bind_apply(e1, self._save_limits)

        mb_row = tk.Frame(lp, bg=SURF); mb_row.pack(fill="x", pady=(10, 0))
        self._mb_row = mb_row
        self._label(mb_row, "Хранить аудио до", color=SUB, size=10, bg=SURF).pack(side="left")
        self.max_mb_var = tk.StringVar(value=str(self.ctrl.cfg.max_storage_mb))
        e2 = self._entry(mb_row, self.max_mb_var, width=5, center=True)
        e2.pack(side="left", padx=(8, 6), ipady=3)
        self._label(mb_row, "МБ", color=MUT, size=10, bg=SURF).pack(side="left")
        self._label(mb_row, "· 0 — без лимита", color=DIM, size=9, bg=SURF).pack(side="left", padx=(8, 0))
        self._bind_apply(e2, self._save_limits)
        self._storage_usage = self._label(lp, "", color=MUT, size=9, bg=SURF)
        self._storage_usage.pack(anchor="w", pady=(8, 0))

        self._update_prov_seg()

    def _bind_apply(self, entry, fn):
        entry.bind("<Return>", lambda e: (fn(), self.root.focus_set()))
        entry.bind("<FocusOut>", lambda e: fn())

    # ---------------------------------------------------------- Groq panel
    def _build_groq_panel(self):
        tk = self.tk
        g = tk.Frame(self._prov_sub, bg=SURF)
        self._groq_sub = g
        self._label(g, "Модель", color=MUT, size=9, bg=SURF).pack(anchor="w", pady=(0, 6))
        mw = tk.Frame(g, bg=SURF); mw.pack(fill="x")
        self._seg = self._segment(mw, [("Turbo · быстрее", "whisper-large-v3-turbo"),
                                       ("Large v3 · точнее", "whisper-large-v3")],
                                  self._select_model)
        krow_head = tk.Frame(g, bg=SURF); krow_head.pack(fill="x", pady=(14, 6))
        self._label(krow_head, "Ключ API", color=MUT, size=9, bg=SURF).pack(side="left")
        self.key_status = self._label(krow_head, "", color=MUT, size=9, bg=SURF)
        self.key_status.pack(side="right")
        row = tk.Frame(g, bg=SURF); row.pack(fill="x")
        self.key_var = tk.StringVar()
        self.key_entry = self._entry(row, self.key_var, show="•")
        self.key_entry.pack(side="left", expand=True, fill="x", ipady=4)
        self._enable_clipboard(self.key_entry)
        self._btn(row, "Сохранить", self._save_key, primary=True).pack(side="left", padx=(8, 4))
        clr = tk.Label(row, text="✕", bg=SURF2, fg=MUT, font=_f(11), cursor="hand2", padx=10)
        clr.pack(side="left", fill="y")
        clr.bind("<Button-1>", lambda e: self._clear_key())
        clr.bind("<Enter>", lambda e: clr.configure(fg=REC_H))
        clr.bind("<Leave>", lambda e: clr.configure(fg=MUT))
        self._groq_link_lbl = tk.Label(g, text="Бесплатный ключ — console.groq.com/keys →",
                                       font=(FONT, 9, "underline"), bg=SURF, fg=ACC_H, cursor="hand2")
        self._groq_link_lbl.bind("<Button-1>", lambda e: self._open_groq_keys())

    # --------------------------------------------------------- Local panel
    def _build_local_panel(self):
        tk = self.tk
        l = tk.Frame(self._prov_sub, bg=SURF)
        self._local_sub = l

        # status strip
        strip = tk.Frame(l, bg=SURF2, padx=10, pady=8)
        strip.pack(fill="x")
        self._loc_dot = tk.Canvas(strip, width=10, height=10, bg=SURF2, highlightthickness=0)
        self._loc_dot.pack(side="left", pady=(2, 0))
        self._loc_dot_id = self._loc_dot.create_oval(1, 1, 9, 9, fill=AMBER, outline="")
        self._loc_head = tk.Label(strip, text="", bg=SURF2, fg=TXT, font=_f(9),
                                  anchor="w", justify="left", wraplength=380)
        self._loc_head.pack(side="left", padx=(8, 0), fill="x", expand=True)

        # step 1 — engine
        s1 = tk.Frame(l, bg=SURF); s1.pack(fill="x", pady=(12, 0))
        self._step_num(s1, "1")
        col1 = tk.Frame(s1, bg=SURF); col1.pack(side="left", fill="x", expand=True, padx=(10, 0))
        r1 = tk.Frame(col1, bg=SURF); r1.pack(fill="x")
        self._label(r1, "Движок whisper.cpp", color=TXT, size=10, bg=SURF).pack(side="left")
        self._eng_btn = self._btn(r1, "Установить · 8 МБ", self._install_engine, primary=True, small=True)
        self._eng_btn.pack(side="right")
        self._eng_state = self._label(col1, "", color=MUT, size=9, bg=SURF, anchor="w",
                                      justify="left", wraplength=380)
        self._eng_state.pack(fill="x", pady=(3, 0))

        # step 2 — model
        s2 = tk.Frame(l, bg=SURF); s2.pack(fill="x", pady=(12, 0))
        self._step_num(s2, "2")
        col2 = tk.Frame(s2, bg=SURF); col2.pack(side="left", fill="x", expand=True, padx=(10, 0))
        r2 = tk.Frame(col2, bg=SURF); r2.pack(fill="x")
        self._label(r2, "Модель", color=TXT, size=10, bg=SURF).pack(side="left")
        rescan = tk.Label(r2, text="⟳ найти на диске", bg=SURF, fg=ACC_H, font=_f(9), cursor="hand2")
        rescan.pack(side="right")
        rescan.bind("<Button-1>", lambda e: self._refresh_local(announce=True))
        self._model_combo = tk.Frame(col2, bg=SURF2, cursor="hand2",
                                     highlightthickness=1, highlightbackground=BORDER2)
        self._model_combo.pack(fill="x", pady=(6, 0))
        self._mc_name = tk.Label(self._model_combo, text="", bg=SURF2, fg=TXT, font=_f(10),
                                 anchor="w", padx=10, pady=8, cursor="hand2")
        self._mc_name.pack(side="left", fill="x", expand=True)
        self._mc_chev = tk.Label(self._model_combo, text="▾", bg=SURF2, fg=MUT, font=_f(12),
                                 padx=10, cursor="hand2")
        self._mc_chev.pack(side="right")
        self._mc_size = tk.Label(self._model_combo, text="", bg=SURF2, fg=MUT, font=_f(9), cursor="hand2")
        self._mc_size.pack(side="right")
        for w in (self._model_combo, self._mc_name, self._mc_chev, self._mc_size):
            w.bind("<Button-1>", lambda e: self._show_model_picker())

        # progress (hidden until a download starts; shown right under the picker)
        self._dl_frame = tk.Frame(col2, bg=SURF)
        self._dl_label = tk.Label(self._dl_frame, text="", bg=SURF, fg=SUB, font=_f(9), anchor="w")
        self._dl_label.pack(side="left", fill="x", expand=True)
        self._dl_cancel_btn = tk.Label(self._dl_frame, text="Отменить", bg=SURF, fg=REC_H,
                                       font=_f(9), cursor="hand2")
        self._dl_cancel_btn.pack(side="right")
        self._dl_cancel_btn.bind("<Button-1>", lambda e: self._cancel_download())
        self._dl_bar = _Progress(tk, col2)
        self._dl_hint = self._label(
            col2, "Модели, которых ещё нет на диске, можно скачать прямо из этого списка ↑",
            color=DIM, size=8, bg=SURF, anchor="w", justify="left", wraplength=380)
        self._dl_hint.pack(fill="x", pady=(6, 0))

        # manual
        man = tk.Frame(col2, bg=SURF); man.pack(fill="x", pady=(12, 0))
        self._label(man, "Уже есть файл?", color=DIM, size=9, bg=SURF).pack(side="left")
        lk1 = tk.Label(man, text="Выбрать файл…", bg=SURF, fg=ACC_H, font=_f(9), cursor="hand2")
        lk1.pack(side="left", padx=(6, 0)); lk1.bind("<Button-1>", lambda e: self._pick_file())
        self._label(man, "·", color=DIM, size=9, bg=SURF).pack(side="left", padx=4)
        lk2 = tk.Label(man, text="Папку…", bg=SURF, fg=ACC_H, font=_f(9), cursor="hand2")
        lk2.pack(side="left"); lk2.bind("<Button-1>", lambda e: self._pick_folder())
        self._label(man, "(ggml-*.bin, папка faster-whisper или whisper-cli.exe)",
                    color=DIM, size=8, bg=SURF).pack(side="left", padx=(8, 0))

    def _step_num(self, parent, n):
        c = self.tk.Canvas(parent, width=22, height=22, bg=SURF, highlightthickness=0)
        c.pack(side="left", anchor="n", pady=(1, 0))
        c.create_oval(1, 1, 21, 21, fill=SURF3, outline="")
        c.create_text(11, 11, text=n, fill=SUB, font=_f(9, True))
        return c

    # ----------------------------------------------------- Local: actions
    def _refresh_local(self, announce=False):
        st = self.ctrl.local_status()
        self._local_st = st
        ok = st.ready
        self._loc_dot.itemconfigure(self._loc_dot_id, fill=GREEN if ok else AMBER)
        self._loc_head.configure(text=st.headline(), fg=TXT if ok else AMBER)
        # engine
        if st.engine is not None:
            ver = eng.engine_version()
            self._eng_state.configure(text=f"✓ установлен{(' · ' + ver) if ver else ''}  ·  {st.engine}", fg=GREEN)
            self._eng_btn.configure(text="Переустановить")
            self._eng_btn.pack_forget()
            self._eng_btn.pack(side="right")
            self._style_secondary(self._eng_btn)
        else:
            self._eng_state.configure(text="Официальная сборка ggml-org (~8 МБ), работает на любом CPU. "
                                           "Без Python и pip.", fg=DIM)
            self._eng_btn.configure(text="Установить · 8 МБ")
            self._style_primary(self._eng_btn)
        # selected model
        if st.selected is not None:
            runnable = st.selected in st.runnable_models
            self._mc_name.configure(text=st.selected.name, fg=TXT if runnable else AMBER)
            self._mc_size.configure(text=st.selected.size_label)
        else:
            self._mc_name.configure(text="Модель не выбрана", fg=MUT)
            self._mc_size.configure(text="")
        if announce:
            n = len(st.models)
            self._set_status_text(f"Найдено моделей: {n}" if n else
                                  "Модели не найдены — скачайте одну из списка")
        self._refresh_hero()

    def _style_primary(self, b):
        b.configure(bg=ACC, fg="#ffffff", activebackground=ACC_H)
        b.bind("<Enter>", lambda e: b.configure(bg=ACC_H)); b.bind("<Leave>", lambda e: b.configure(bg=ACC))

    def _style_secondary(self, b):
        b.configure(bg=SURF2, fg=TXT, activebackground=SURF3)
        b.bind("<Enter>", lambda e: b.configure(bg=SURF3)); b.bind("<Leave>", lambda e: b.configure(bg=SURF2))

    def _show_model_picker(self):
        tk = self.tk
        if self._popup is not None:
            try: self._popup.destroy()
            except Exception: pass
            self._popup = None
            return
        st = getattr(self, "_local_st", None) or self.ctrl.local_status()
        have_ids = {m.spec.id for m in st.models if m.spec}
        to_download = [s for s in eng.MODEL_CATALOG if s.id not in have_ids]
        if not st.models and not to_download:
            self._set_status_text("Список моделей пуст")
            return
        anchor = self._model_combo
        anchor.update_idletasks()
        rx, ry, rw = anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height(), anchor.winfo_width()
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.configure(bg=BORDER2)
        self._popup = popup
        cur = st.selected.path if st.selected else None

        def _row(bgr):
            r = tk.Frame(popup, bg=bgr, cursor="hand2"); r.pack(fill="x", pady=(0, 1))
            return r

        # already on disk — pick to select
        for m in st.models:
            runnable = m in st.runnable_models
            active = m.path == cur
            bgr = ACC if active else SURF2
            fgr = "#ffffff" if active else (TXT if runnable else MUT)
            row = _row(bgr)
            via = "whisper.cpp" if m.kind == "ggml" else "faster-whisper"
            if not runnable:
                via += " · нет движка" if m.kind == "ggml" else " · нет библиотеки"
            a = tk.Label(row, text=m.name, bg=bgr, fg=fgr, font=_f(10), anchor="w", padx=10, pady=6)
            a.pack(side="left", fill="x", expand=True)
            b = tk.Label(row, text=f"{m.size_label}  ·  {via}", bg=bgr,
                         fg="#ffffff" if active else DIM, font=_f(8), padx=10)
            b.pack(side="right")
            for w in (row, a, b):
                w.bind("<Button-1>", lambda e, p=m.path: self._pick_model(p))

        # not on disk yet — pick to download
        if to_download:
            if st.models:
                sep = tk.Frame(popup, bg=SURF3, height=1); sep.pack(fill="x", pady=2)
            hdr = tk.Frame(popup, bg=BORDER2); hdr.pack(fill="x")
            tk.Label(hdr, text="СКАЧАТЬ", bg=BORDER2, fg=DIM, font=_f(8, True),
                     anchor="w", padx=10, pady=(4 if st.models else 8)).pack(fill="x")
            for spec in to_download:
                row = _row(SURF2)
                title = spec.title + (" ★" if spec.recommended else "")
                a = tk.Label(row, text=title, bg=SURF2, fg=TXT if not spec.recommended else AMBER,
                             font=_f(10), anchor="w", padx=10, pady=6)
                a.pack(side="left", fill="x", expand=True)
                b = tk.Label(row, text=f"Скачать · {spec.size_label}", bg=SURF2,
                             fg=ACC_H, font=_f(9), padx=10)
                b.pack(side="right")
                for w in (row, a, b):
                    w.bind("<Button-1>", lambda e, s=spec: self._pick_download(s))
                    w.bind("<Enter>", lambda e, ws=(row, a, b): [x.configure(bg=SURF3) for x in ws])
                    w.bind("<Leave>", lambda e, ws=(row, a, b): [x.configure(bg=SURF2) for x in ws])

        popup.update_idletasks()
        popup.geometry(f"{rw}x{popup.winfo_reqheight()}+{rx}+{ry}")
        popup.bind("<Escape>", lambda e: popup.destroy())
        popup.bind("<Destroy>", lambda e: setattr(self, "_popup", None), add="+")

        def _dismiss(e):
            try:
                if not str(popup.winfo_containing(e.x_root, e.y_root)).startswith(str(popup)):
                    popup.destroy()
            except Exception:
                pass
            self.root.unbind("<Button-1>")
        popup.after(50, lambda: self.root.bind("<Button-1>", _dismiss))

    def _pick_model(self, path):
        try:
            if self._popup: self._popup.destroy()
        except Exception:
            pass
        self.ctrl.select_local_model(path)
        self._refresh_local()

    def _pick_download(self, spec: eng.ModelSpec):
        try:
            if self._popup: self._popup.destroy()
        except Exception:
            pass
        self._download_model(spec)

    def _install_engine(self):
        if self._dl_running:
            return
        self._run_download("Движок whisper.cpp", lambda prog, cancel:
                           self.ctrl.install_local_engine(progress=prog, cancel=cancel))

    def _download_model(self, spec: eng.ModelSpec):
        if self._dl_running:
            self._set_status_text("Дождитесь окончания текущей загрузки")
            return
        st = getattr(self, "_local_st", None) or self.ctrl.local_status()
        if any(m.spec and m.spec.id == spec.id for m in st.models):
            for m in st.models:
                if m.spec and m.spec.id == spec.id:
                    self._pick_model(m.path)
            return
        self._run_download(spec.title, lambda prog, cancel:
                           self.ctrl.download_local_model(spec, progress=prog, cancel=cancel))
        if st.engine is None:
            self._set_status_text("Не забудьте установить движок (шаг 1) — модель без него не запустится")

    def _run_download(self, title: str, job):
        self._dl_running = True
        self._dl_cancel = threading.Event()
        self._dl_frame.pack(fill="x", pady=(10, 4), before=self._dl_hint)
        self._dl_bar.pack(fill="x", before=self._dl_hint)
        self._dl_bar.set(0); self._dl_bar.pulse(True)
        self._dl_label.configure(text=f"{title} — подключаюсь…")
        t0 = time.monotonic()
        state = {"last": (0, t0)}

        def on_progress(p: eng.Progress):
            def ui():
                if p.phase == "download":
                    self._dl_bar.pulse(False)
                    self._dl_bar.set(p.fraction)
                    done_b, t_last = state["last"]
                    now = time.monotonic()
                    speed = ""
                    if now - t_last >= 1.0:
                        rate = (p.done - done_b) / max(now - t_last, 1e-6)
                        state["last"] = (p.done, now)
                        state["rate"] = rate
                    rate = state.get("rate")
                    if rate:
                        left = (p.total - p.done) / rate if p.total else 0
                        speed = f" · {eng.human_size(int(rate))}/с" + (
                            f" · ещё ~{int(left // 60)} мин" if left > 90 else
                            (f" · ещё ~{int(left)} с" if left > 0 else ""))
                    tot = eng.human_size(p.total) if p.total else "?"
                    self._dl_label.configure(text=f"{title} — {eng.human_size(p.done)} из {tot}{speed}")
                elif p.phase in ("verify", "extract"):
                    self._dl_bar.pulse(True)
                    self._dl_label.configure(text=f"{title} — {p.label.lower()}…")
            self._post(ui)

        def worker():
            try:
                job(on_progress, self._dl_cancel)
                self._post(lambda: self._download_done(None))
            except eng.DownloadCancelled:
                self._post(lambda: self._download_done("cancelled"))
            except Exception as exc:  # noqa: BLE001 — shown to the user verbatim
                msg = str(exc)
                self._post(lambda: self._download_done(msg))
        threading.Thread(target=worker, daemon=True).start()

    def _download_done(self, error: Optional[str]):
        self._dl_running = False
        self._dl_bar.pulse(False)
        self._dl_frame.pack_forget(); self._dl_bar.cv.pack_forget()
        if error == "cancelled":
            self._set_status_text("Загрузка отменена")
        elif error:
            self._set_status_text(error, error=True)
        self._refresh_local()

    def _cancel_download(self):
        if self._dl_cancel:
            self._dl_cancel.set()

    def _pick_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self.root, title="Модель Whisper или whisper-cli.exe",
            filetypes=[("Модель GGML / движок", "*.bin *.exe"), ("Все файлы", "*.*")])
        if path:
            self.ctrl.use_local_file(path)
            self._refresh_local()

    def _pick_folder(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(parent=self.root, title="Папка с моделями или движком")
        if not path:
            return
        if not self.ctrl.use_local_file(path):      # not a CT2 dir → just remember it
            self.ctrl.add_local_dir(path)
            self._set_status_text("Папка добавлена в поиск")
        self._refresh_local(announce=True)

    # ----------------------------------------------------- provider switch
    def _select_provider(self, pid: str) -> None:
        self.ctrl.save_settings(provider=pid)
        self._update_prov_seg()
        if pid == "local":
            st = self.ctrl.local_status()
            self._set_status_text("Локальный режим включён" if st.ready else
                                  "Локальный режим: " + st.headline())
        else:
            self._set_status_text("Режим: Groq (облако)")

    def _update_prov_seg(self) -> None:
        cur = self.ctrl.cfg.provider
        self._paint_segment(self._prov_seg, cur)
        if cur == "groq":
            self._local_sub.pack_forget()
            self._groq_sub.pack(fill="x")
            self._paint_segment(self._seg, self.ctrl.cfg.model)
            self._refresh_key_status()
        else:
            self._groq_sub.pack_forget()
            self._local_sub.pack(fill="x")
            self._refresh_local()
        self._refresh_limits_visibility()
        self._refresh_hero()

    def _select_model(self, mid):
        self.ctrl.save_settings(model=mid)
        self._paint_segment(self._seg, mid)
        self._refresh_hero()
        self._set_status_text(f"Модель Groq: {mid}")

    # ================================================================ history
    def _build_history(self):
        tk = self.tk
        h = tk.Frame(self._body, bg=BG)
        self._history = h
        bar = tk.Frame(h, bg=BG); bar.pack(fill="x", pady=(0, 8))
        self._btn(bar, "↻ Повторить очередь", self._retry_queue, small=True).pack(side="left")
        self._btn(bar, "Очистить", self._clear_history, small=True, danger=True).pack(side="right")
        self._hist_canvas, self._hist_inner = self._scrollable(h)

    def _refresh_history(self):
        tk = self.tk
        for w in self._hist_inner.winfo_children():
            w.destroy()
        rows = self.ctrl.recent(self.HISTORY_LIMIT)
        if not rows:
            e = tk.Frame(self._hist_inner, bg=BG); e.pack(fill="x", pady=30)
            self._label(e, "Здесь появятся ваши диктовки", color=SUB, size=11).pack()
            self._label(e, f"Нажмите {self._pretty_key(self.ctrl.cfg.hotkey)} в любом поле и говорите",
                        color=DIM, size=9).pack(pady=(4, 0))
            return
        tagmap = {"pending": ("в очереди", AMBER), "failed": ("ошибка", REC)}
        for r in rows:
            rc = _RCard(tk, self._hist_inner, radius=10)
            rc.pack(fill="x", pady=3, padx=1)
            card = rc.inner
            top = tk.Frame(card, bg=SURF); top.pack(fill="x", padx=12, pady=(9, 2))
            ts = time.strftime("%H:%M", time.localtime(r.ts))
            day = time.strftime("%d.%m", time.localtime(r.ts))
            today = time.strftime("%d.%m")
            self._label(top, ts if day == today else f"{day} {ts}", color=MUT, size=9, bg=SURF).pack(side="left")
            if r.status in tagmap:
                label, col = tagmap[r.status]
                self._label(top, "· " + label, color=col, size=9, bg=SURF).pack(side="left", padx=(6, 0))
            elif r.status == "cancelled":
                has_audio = bool(r.audio_path)
                self._label(top, "· отменено" + (" · аудио ещё ~60 с" if has_audio else ""),
                            color=AMBER if has_audio else MUT, size=9, bg=SURF).pack(side="left", padx=(6, 0))
            if r.provider:
                self._label(top, "локально" if r.provider == "local" else "groq",
                            color=DIM, size=8, bg=SURF).pack(side="right")
            body = "(запись отменена)" if r.status == "cancelled" else (
                r.text or "(нет текста — можно повторить)")
            tk.Label(card, text=body, bg=SURF, fg=TXT if r.text else MUT, font=_f(10),
                     wraplength=390, justify="left", anchor="w").pack(fill="x", padx=12)
            btns = tk.Frame(card, bg=SURF); btns.pack(fill="x", padx=12, pady=(6, 9))
            if r.text:
                self._btn(btns, "⧉ Копировать", lambda t=r.text: self._copy_text(t), small=True).pack(side="left")
            if r.status == "cancelled" and r.audio_path:
                self._btn(btns, "Оставить аудио", lambda i=r.id: self._keep_cancelled(i), small=True).pack(side="left", padx=(6, 0))
            elif r.status in ("pending", "failed") and r.audio_path:
                self._btn(btns, "↻ Повторить", lambda i=r.id: self._retry_one(i), small=True).pack(side="left", padx=(6, 0))

    # ================================================================ key
    def _save_key(self):
        self.ctrl.set_api_key(self.key_var.get()); self.key_var.set("")
        self._refresh_key_status(); self._refresh_hero()

    def _clear_key(self):
        self.ctrl.clear_api_key(); self._refresh_key_status(); self._refresh_hero()

    def _refresh_key_status(self):
        if not hasattr(self, "key_status"):
            return
        if self.ctrl.has_key():
            self.key_status.configure(text=f"● сохранён · {self.ctrl.key_hint()}", fg=GREEN)
            if not self.key_var.get():
                self.key_var.set(self.ctrl.stored_key())
            self._groq_link_lbl.pack_forget()
        else:
            self.key_status.configure(text="● не задан", fg=AMBER)
            self.key_var.set("")
            self._groq_link_lbl.pack(anchor="w", pady=(8, 0))

    def _enable_clipboard(self, widget):
        widget.bind("<Key>", self._on_clip_key)
        menu = self.tk.Menu(widget, tearoff=0, bg=SURF, fg=TXT,
                            activebackground=ACC, activeforeground="#ffffff", bd=0)
        menu.add_command(label="Вставить", command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_command(label="Копировать", command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="Вырезать", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_separator()
        menu.add_command(label="Очистить поле", command=lambda: self.key_var.set(""))
        widget.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    def _on_clip_key(self, event):
        action = clipboard_action(int(event.keycode), int(event.state))
        if action is None:
            return None
        w = event.widget
        if action == "paste": w.event_generate("<<Paste>>")
        elif action == "copy": w.event_generate("<<Copy>>")
        elif action == "cut": w.event_generate("<<Cut>>")
        elif action == "select_all":
            try: w.select_range(0, "end"); w.icursor("end")
            except Exception: pass
        return "break"

    # ================================================================ hotkey
    def _begin_capture(self):
        self._capturing = True
        self.capture_btn.configure(text="Нажмите сочетание…")
        self.hotkey_chip.configure(highlightbackground=ACC)
        self.root.bind("<KeyPress>", self._on_capture_key)
        self.root.focus_set()

    def _on_capture_key(self, event):
        if not self._capturing:
            return
        combo = combo_from_state(int(event.state), event.keysym)
        if not combo:
            return
        self._end_capture()
        try:
            self.ctrl.save_settings(hotkey=combo)
            self.hotkey_var.set(self._pretty_key(combo))
            self._restart_start_hotkey()
            self._refresh_hero()
            self._set_status_text(f"Новое сочетание: {self._pretty_key(combo)}")
        except Exception as exc:
            self._set_status_text(f"Ошибка: {exc}", error=True)

    def _end_capture(self):
        self._capturing = False
        try: self.root.unbind("<KeyPress>")
        except Exception: pass
        self.capture_btn.configure(text="Изменить…")
        self.hotkey_chip.configure(highlightbackground=BORDER2)

    def _start_hotkeys(self):
        try:
            from .hotkeys import make_hotkey_manager
            self._hk = make_hotkey_manager()
            self._hk.start(on_error=self._hotkey_error)
            self._register_start_hotkey()
        except Exception:
            self._hk = None
            self._set_status_text("Глобальный хоткей недоступен — используйте кнопку «Запись»")

    def _register_start_hotkey(self):
        if not self._hk:
            return
        self._start_hk_id = self._hk.register(
            self.ctrl.cfg.hotkey, lambda: self.root.after(0, self.ctrl.start_recording))

    def _restart_start_hotkey(self):
        if self._hk and self._start_hk_id is not None:
            try: self._hk.unregister(self._start_hk_id)
            except Exception: pass
        self._register_start_hotkey()

    def _register_rec_keys(self):
        if not self._hk:
            return
        self._unregister_rec_keys()
        for combo, action in ((self.ctrl.cfg.stop_key, self.ctrl.stop_recording),
                              (self.ctrl.cfg.cancel_key, self.ctrl.cancel_recording)):
            try:
                self._rec_hk_ids.append(
                    self._hk.register(combo, lambda a=action: self.root.after(0, a)))
            except Exception:
                self._set_status_text(f"«{combo}» не удалось назначить — остановите кнопкой в окне")

    def _unregister_rec_keys(self):
        if not self._hk:
            return
        for hk in self._rec_hk_ids:
            try: self._hk.unregister(hk)
            except Exception: pass
        self._rec_hk_ids = []

    def _hotkey_error(self, combo):
        self.root.after(0, lambda: self._set_status_text(
            f"Сочетание «{combo}» занято другой программой — задайте другое.", error=True))

    # ================================================================ misc actions
    def _copy_text(self, text: str):
        if not text:
            self._set_status_text("Пока нечего копировать"); return
        try:
            self.root.clipboard_clear(); self.root.clipboard_append(text); self.root.update()
            self._set_status_text("Скопировано в буфер обмена")
        except Exception as exc:
            self._set_status_text(f"Не удалось скопировать: {exc}", error=True)

    def _copy_last(self):
        self._copy_text(self.ctrl.last_text())

    def _open_groq_keys(self):
        import webbrowser
        threading.Thread(target=lambda: webbrowser.open("https://console.groq.com/keys"),
                         daemon=True).start()

    def _refresh_limits_visibility(self):
        if self.ctrl.cfg.provider == "local":
            self._max_sec_row.pack_forget()
        else:
            self._max_sec_row.pack(fill="x", pady=(10, 0), before=self._mb_row)

    def _refresh_storage_usage(self):
        try:
            used = self.ctrl.storage.audio_size_mb()
            limit = self.ctrl.cfg.max_storage_mb
            if limit > 0:
                self._storage_usage.configure(
                    text=f"Занято {used:.1f} МБ из {limit} МБ",
                    fg=REC if used > limit * 0.9 else MUT)
            else:
                self._storage_usage.configure(text=f"Занято {used:.1f} МБ · аудио удаляется через "
                                                   f"{self.ctrl.cfg.audio_retention_days} дн.", fg=MUT)
        except Exception:
            pass

    def _save_limits(self):
        try:
            ms = int(self.max_sec_var.get())
            if ms <= 0: raise ValueError
        except ValueError:
            self._set_status_text("Авто-стоп: нужно целое число секунд больше 0", error=True); return
        try:
            mb = int(self.max_mb_var.get())
            if mb < 0: raise ValueError
        except ValueError:
            self._set_status_text("Лимит хранилища: целое число МБ, 0 — без лимита", error=True); return
        if ms == self.ctrl.cfg.max_seconds and mb == self.ctrl.cfg.max_storage_mb:
            return
        self.ctrl.save_settings(max_seconds=ms, max_storage_mb=mb)
        if mb > 0:
            threading.Thread(target=lambda: (
                self.ctrl.storage.evict_oldest(mb),
                self._post(self._refresh_storage_usage),
                self._post(self._refresh_history)), daemon=True).start()
        self._refresh_storage_usage()
        self._set_status_text("Сохранено")

    def _on_cancelled(self, rid: int) -> None:
        try:
            self.root.after(0, self._refresh_history)
            rec = self.ctrl.storage.get(rid)
            if rec and rec.audio_path:
                self.root.after(62000, self._refresh_history)
        except Exception:
            pass

    def _keep_cancelled(self, rid: int):
        self.ctrl.keep_cancelled(rid); self._refresh_history()

    def _retry_queue(self):
        threading.Thread(target=lambda: (self.ctrl.retry_queue(),
                                         self._post(self._refresh_history)), daemon=True).start()

    def _clear_history(self):
        import tkinter.messagebox as mb
        if not mb.askyesno("Очистить историю?",
                           "Все распознанные тексты и сохранённые аудио будут удалены. Это нельзя отменить.",
                           parent=self.root):
            return
        self.ctrl.clear_history(); self._refresh_history()

    def _retry_one(self, rec_id):
        threading.Thread(target=lambda: (self.ctrl.retry_one(rec_id),
                                         self._post(self._refresh_history)), daemon=True).start()

    # ================================================================ status
    def _on_status(self, state: State, message: str):
        try:
            self.root.after(0, lambda: self._apply_status(state, message))
        except Exception:
            pass

    def _apply_status(self, state: State, message: str):
        color, label, title = _STATE.get(state, _STATE[State.IDLE])
        if state is State.IDLE:
            chip_fill, dot_fill, text_fill, outline = ACC, GREEN, "#ffffff", ""
        elif state is State.RECORDING:
            chip_fill, dot_fill, text_fill, outline = REC, None, "#ffffff", ""
        else:
            chip_fill, dot_fill, text_fill, outline = SURF2, color, TXT, BORDER2
        self._chip_cv.itemconfigure(self._chip_pill, fill=chip_fill, outline=outline)
        self._chip_cv.itemconfigure(self._chip_txt, text=label, fill=text_fill)
        self._chip_layout(label)
        if state is State.RECORDING:
            self._chip_cv.itemconfigure(self._chip_oval, state="hidden")
            self._chip_cv.itemconfigure(self._chip_sq, state="normal")
        else:
            self._chip_cv.itemconfigure(self._chip_oval, fill=dot_fill, state="normal")
            self._chip_cv.itemconfigure(self._chip_sq, state="hidden")
        self._hero_dot.itemconfigure(self._hero_dot_id, fill=color)
        self._hero_title.configure(text=title)
        if message and not message.startswith("Готов."):
            self._set_status_text(message)
        elif state is State.IDLE:
            self._set_status_text("")
        if state is State.IDLE:
            self.root.after(900, self._refresh_history)

    def _set_status_text(self, text: str, error: bool = False):
        self.status_lbl.configure(text=text, fg=REC_H if error else MUT)
        if self._status_after:
            try: self.root.after_cancel(self._status_after)
            except Exception: pass
            self._status_after = None
        if text and not error and self.ctrl.state.state is State.IDLE:
            self._status_after = self.root.after(6000, lambda: self.status_lbl.configure(text=""))

    def _on_close(self):
        try:
            if self._hk: self._hk.stop()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    ControlPanel().run()


if __name__ == "__main__":
    main()
