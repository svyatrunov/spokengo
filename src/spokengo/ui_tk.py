"""Tkinter control panel: warm graphite, one signal colour, hand-styled widgets.

Layout (top → bottom):
  header   — wordmark
  status   — the one raised panel: what the app is doing, the engine in use,
             copy-last and the round record button side by side, the hotkeys
  tabs     — Настройки | История
  settings — flat sections split by hairlines: Распознавание (Groq is the
             main, fast path; Офлайн is the fallback), Горячая клавиша,
             Запись и хранилище. Every control applies instantly; there is no
             "Apply" button anywhere.
  local    — the offline setup lives entirely in the window: install the
             whisper.cpp engine, pick or download a model from one compact
             dropdown, or point at a file/folder. Nothing ever tells the user
             to open a terminal.

Design system (kept deliberately small):
  colour   — neutrals BG < SURF < SURF2 < SURF3 < SEL, text TXT > SUB > MUT;
             REC is the only saturated hue; GREEN / AMBER only for state.
  type     — one family (Segoe UI), four sizes: 16 / 13 / 10 / 9 pt;
             Semibold only for the wordmark, the status title and headings.
  space    — 4 / 8 / 12 / 16 / 24 px, 20 px window gutter.
  shape    — status panel 12 px radius, icon-button hover 6 px, record button
             round; form controls stay square like native Windows fields.
  icons    — drawn line icons from ui_icons (no emoji / Unicode glyphs).

All app logic lives in GuiController; this module only draws and dispatches.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Callable, List, Optional

from . import ui_icons
from .controller import GuiController
from .hotkeys import clipboard_action, combo_from_state
from .state import State
from .transcribe import local_engine as eng

# ---------------------------------------------------------------- palette
# Warm graphite plus one signal colour. SpokenGo is a recorder, so the only
# saturated hue is the red REC lamp; everything else stays neutral and the
# state colours (ready / working / needs attention) read at a glance.
# Contrast (WCAG): TXT ≥10.7:1 and SUB ≥6.3:1 on every surface; MUT ≥4.7:1 on
# BG / SURF / SURF2 (never used on SURF3 and lighter); REC_T ≥5.4:1 on BG–SURF2.
BG = "#161615"      # window
SURF = "#1e1e1c"    # the one raised panel (status)
SURF2 = "#282826"   # controls: fields, segmented track, secondary buttons
SURF3 = "#312f2c"   # hover
SEL = "#3d3b37"     # selected segment / pressed
LINE = "#2a2a27"    # hairline separators
LINE2 = "#3d3c38"   # control outline, popup border
TXT = "#eeede9"; SUB = "#b9b7b0"; MUT = "#95928a"
PRIMARY = TXT; PRIMARY_H = "#ffffff"; ON_PRIMARY = BG      # primary button
REC = "#e5484d"; REC_H = "#ec5f63"; REC_T = "#f2777a"    # fill / hover / text
GREEN = "#5fc48d"
AMBER = "#e8b04e"

# ---------------------------------------------------------------- type & space
FONT = "Segoe UI"
FONT_SEMI = "Segoe UI Semibold"      # a separate family on Windows
DISPLAY, TITLE, BODY, SMALL = 16, 13, 10, 9
S1, S2, S3, S4, S5 = 4, 8, 12, 16, 24
PAD = 20

_semibold_ok = False

_STATE = {
    # state:           (lamp,  title)
    State.IDLE:         (REC,   "Готов к диктовке"),
    State.RECORDING:    (REC,   "Слушаю…"),
    State.TRANSCRIBING: (AMBER, "Распознаю речь…"),
    State.INJECTING:    (GREEN, "Вставляю текст…"),
    State.ERROR:        (REC,   "Что-то пошло не так"),
}


def _f(size=BODY, bold=False, underline=False):
    if bold and _semibold_ok:
        return (FONT_SEMI, size) + (("underline",) if underline else ())
    style = " ".join(s for s in ("bold" if bold else "", "underline" if underline else "") if s)
    return (FONT, size, style or "normal")


def _init_fonts(root) -> None:
    global _semibold_ok
    try:
        import tkinter.font as tkfont
        _semibold_ok = FONT_SEMI in set(tkfont.families(root))
    except Exception:
        _semibold_ok = False


def _photo(tk_mod, b64: str):
    return tk_mod.PhotoImage(data=b64)


def _smooth_rect(canvas, x0, y0, x1, y1, r, **kw):
    """Rounded polygon (fallback when Pillow is unavailable)."""
    pts = (x0+r, y0,  x1-r, y0,  x1, y0,  x1, y0+r,
           x1, y1-r,  x1, y1,  x1-r, y1,  x0+r, y1,
           x0, y1,  x0, y1-r,  x0, y0+r,  x0, y0)
    return canvas.create_polygon(pts, smooth=True, **kw)


class _RCard:
    """Rounded, borderless panel. ``.inner`` is the content frame.

    The rounded background is an antialiased image (ui_icons), redrawn when
    the panel is resized, so the corners stay smooth on Windows too.
    """
    def __init__(self, tk_mod, parent, *, bg=SURF, radius=12, outer=BG):
        self.tk = tk_mod
        self._cv = tk_mod.Canvas(parent, bg=outer, highlightthickness=0, bd=0)
        self.inner = tk_mod.Frame(self._cv, bg=bg)
        self._bg, self._outer, self._r = bg, outer, radius
        self._img = None
        self._size = (0, 0)
        self._win = self._cv.create_window(0, 0, window=self.inner, anchor="nw")
        self._cv.bind("<Configure>", lambda e: self._cv.after_idle(self._draw))
        self.inner.bind("<Configure>", lambda e: self._cv.after_idle(self._draw))

    def _draw(self):
        try:
            w = self._cv.winfo_width()
            if w <= 1:
                return
            h = max(self.inner.winfo_reqheight(), 24)
            self._cv.configure(height=h)
            self._cv.itemconfigure(self._win, width=w)
            if self._size == (w, h):
                return
            self._size = (w, h)
            self._cv.delete("r")
            if ui_icons.available():
                self._img = _photo(self.tk, ui_icons.rounded_panel_png(
                    w, h, self._r, self._bg, self._outer))
                self._cv.create_image(0, 0, image=self._img, anchor="nw", tags="r")
            else:
                _smooth_rect(self._cv, 0, 0, w, h, self._r, fill=self._bg, outline="", tags="r")
            self._cv.tag_lower("r")
        except Exception:
            pass

    def pack(self, **kw): self._cv.pack(**kw)


class _Progress:
    """Thin progress line on a canvas (determinate + indeterminate sweep)."""
    def __init__(self, tk_mod, parent, height=4, bg=BG, track=SURF2, fill=SUB):
        self.cv = tk_mod.Canvas(parent, height=height, bg=bg, highlightthickness=0, bd=0)
        self._h, self._track, self._fill = height, track, fill
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
        if self._track:
            cv.create_rectangle(0, 0, w, h, fill=self._track, outline="")
        if offset >= 0:                      # indeterminate sweep
            seg = w * 0.3
            x0 = -seg + (w + seg) * offset
            cv.create_rectangle(max(0, x0), 0, min(w, x0 + seg), h, fill=self._fill, outline="")
        elif self._frac > 0:
            cv.create_rectangle(0, 0, max(h, w * self._frac), h, fill=self._fill, outline="")

    def pulse(self, on: bool, fill: Optional[str] = None):
        if fill:
            self._fill = fill
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


class _Tooltip:
    """Small delayed hint next to a widget (for icon-only buttons)."""
    DELAY_MS = 450

    def __init__(self, tk_mod, widget, text: str):
        self.tk, self.w, self.text = tk_mod, widget, text
        self._tip = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.w.after(self.DELAY_MS, self._show)

    def _cancel(self):
        if self._after:
            try: self.w.after_cancel(self._after)
            except Exception: pass
            self._after = None

    def _show(self):
        if self._tip or not self.text:
            return
        try:
            x = self.w.winfo_rootx()
            y = self.w.winfo_rooty() + self.w.winfo_height() + S1
            tip = self.tk.Toplevel(self.w)
            tip.overrideredirect(True)
            tip.configure(bg=LINE2)
            self.tk.Label(tip, text=self.text, bg=SURF2, fg=TXT, font=_f(SMALL),
                          padx=S2, pady=S1, justify="left").pack(padx=1, pady=1)
            tip.update_idletasks()
            # keep it on screen: right-align to the widget if it would overflow
            sw = tip.winfo_screenwidth()
            if x + tip.winfo_reqwidth() > sw - 8:
                x = self.w.winfo_rootx() + self.w.winfo_width() - tip.winfo_reqwidth()
            tip.geometry(f"+{x}+{y}")
            self._tip = tip
        except Exception:
            self._tip = None

    def _hide(self, _e=None):
        self._cancel()
        if self._tip is not None:
            try: self._tip.destroy()
            except Exception: pass
            self._tip = None


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
        self._status_msg: Optional[tuple] = None       # (text, error) while shown
        self._icons_ok = ui_icons.available()
        self._wrap_settings: list = []                  # (label, margin) re-wrapped on resize
        self._wrap_history: list = []

        try:  # Windows taskbar: our own icon, not Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("wind.slw.SpokenGo")
        except Exception:
            pass

        self.root = tk.Tk()
        _init_fonts(self.root)
        self.root.title("SpokenGo")
        self.root.geometry("500x690")
        self.root.minsize(460, 600)
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
        self._build_status()
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
    def _label(self, parent, text, color=TXT, size=BODY, bold=False, bg=BG, **kw):
        return self.tk.Label(parent, text=text, bg=bg, fg=color, font=_f(size, bold), **kw)

    def _btn(self, parent, text, cmd, primary=False, bg=BG, small=False):
        """Text button. Primary = light fill (one per view); secondary = field grey."""
        b = self.tk.Button(parent, text=text, command=cmd, relief="flat", bd=0,
                           cursor="hand2", padx=(S3 if small else S4), pady=(3 if small else 5),
                           font=_f(SMALL if small else BODY), highlightthickness=1,
                           highlightbackground=bg, highlightcolor=SUB, takefocus=1)
        (self._style_primary if primary else self._style_secondary)(b)
        return b

    def _style_primary(self, b):
        b.configure(bg=PRIMARY, fg=ON_PRIMARY, activebackground=PRIMARY_H,
                    activeforeground=ON_PRIMARY)
        b.bind("<Enter>", lambda e: b.configure(bg=PRIMARY_H))
        b.bind("<Leave>", lambda e: b.configure(bg=PRIMARY))

    def _style_secondary(self, b):
        b.configure(bg=SURF2, fg=TXT, activebackground=SEL, activeforeground=TXT)
        b.bind("<Enter>", lambda e: b.configure(bg=SURF3))
        b.bind("<Leave>", lambda e: b.configure(bg=SURF2))

    def _icon_btn(self, parent, name, cmd, tip, bg=BG, color=MUT, hover_color=TXT,
                  hover_fill=None, fallback="•", box=28, rest_fill=None, radius=6):
        """Icon-only button (16 px glyph in a ``box`` px target) with a tooltip.
        ``rest_fill`` gives it a visible resting shape (e.g. round, next to REC)."""
        tk = self.tk
        hover_fill = hover_fill or (SURF3 if bg == SURF else SURF2)
        if self._icons_ok:
            img = _photo(tk, ui_icons.icon_png(name, color, box=box, fill=rest_fill,
                                               radius=radius))
            img_h = _photo(tk, ui_icons.icon_png(name, hover_color, box=box, fill=hover_fill,
                                                 radius=radius))
            b = tk.Button(parent, image=img, command=cmd, relief="flat", bd=0,
                          bg=bg, activebackground=bg, cursor="hand2",
                          highlightthickness=1, highlightbackground=bg, highlightcolor=SUB,
                          width=box, height=box, takefocus=1)
            b._imgs = (img, img_h)
            b.bind("<Enter>", lambda e: b.configure(image=img_h), add="+")
            b.bind("<Leave>", lambda e: b.configure(image=img), add="+")
        else:
            b = tk.Button(parent, text=fallback, command=cmd, relief="flat", bd=0,
                          bg=bg, fg=color, activebackground=hover_fill, activeforeground=hover_color,
                          font=_f(BODY), cursor="hand2", padx=S2, highlightthickness=0)
        _Tooltip(tk, b, tip)
        return b

    def _link(self, parent, text, cmd, bg=BG, size=SMALL):
        lk = self.tk.Label(parent, text=text, bg=bg, fg=SUB, font=_f(size, underline=True),
                           cursor="hand2")
        lk.bind("<Button-1>", lambda e: cmd())
        lk.bind("<Enter>", lambda e: lk.configure(fg=TXT))
        lk.bind("<Leave>", lambda e: lk.configure(fg=SUB))
        return lk

    def _entry(self, parent, textvariable, show=None, width=None, center=False):
        return self.tk.Entry(parent, textvariable=textvariable, show=show, width=width,
                             bg=SURF2, fg=TXT, insertbackground=TXT, relief="flat",
                             highlightthickness=1, highlightbackground=LINE2,
                             highlightcolor=SUB, font=_f(BODY),
                             selectbackground=SEL, selectforeground=TXT,
                             justify="center" if center else "left")

    def _segment(self, parent, items, on_pick):
        """Segmented control on a field-grey track; the pick is a lighter tile."""
        tk = self.tk
        seg = tk.Frame(parent, bg=SURF2, padx=3, pady=3)
        seg.pack(fill="x")
        out = {}
        for i, (label, sid) in enumerate(items):
            b = tk.Label(seg, text=label, bg=SURF2, fg=MUT, font=_f(BODY),
                         cursor="hand2", pady=5)
            b.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 3, 0))
            seg.grid_columnconfigure(i, weight=1, uniform="seg")
            b.bind("<Button-1>", lambda e, s=sid: on_pick(s))
            b.bind("<Enter>", lambda e, w=b: w._on or w.configure(bg=SURF3, fg=SUB))
            b.bind("<Leave>", lambda e, w=b: w._on or w.configure(bg=SURF2, fg=MUT))
            b._on = False
            out[sid] = b
        return out

    @staticmethod
    def _paint_segment(seg: dict, current: str):
        for sid, b in seg.items():
            b._on = sid == current
            b.configure(bg=SEL if b._on else SURF2, fg=TXT if b._on else MUT)

    def _wrap(self, label, margin=0, history=False):
        (self._wrap_history if history else self._wrap_settings).append((label, margin))
        return label

    @staticmethod
    def _rewrap(items, width):
        for lbl, margin in list(items):
            try:
                lbl.configure(wraplength=max(120, width - margin))
            except Exception:
                items.remove((lbl, margin))

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
        bar.pack(fill="x", padx=PAD, pady=(S4 + 2, 0))
        self._label(bar, "SpokenGo", size=DISPLAY, bold=True).pack(side="left")
        from . import __version__
        self._label(bar, f"v{__version__}", color=MUT, size=SMALL).pack(
            side="left", padx=(S2, 0), pady=(S1 + 2, 0))

    # ================================================================ status panel
    def _build_status(self):
        tk = self.tk
        self._hero = _RCard(tk, self.root, bg=SURF, radius=12)
        self._hero.pack(fill="x", padx=PAD, pady=(S3, 0))
        h = tk.Frame(self._hero.inner, bg=SURF)
        h.pack(fill="x", padx=S4, pady=(S4, 0))

        top = tk.Frame(h, bg=SURF); top.pack(fill="x")
        actions = tk.Frame(top, bg=SURF); actions.pack(side="right")
        self._copy_btn = self._icon_btn(actions, "copy", self._copy_last,
                                        "Копировать последний текст", bg=SURF, color=SUB,
                                        rest_fill=SURF2, hover_fill=SURF3, box=36, radius=18,
                                        fallback="Копировать")
        self._copy_btn.pack(side="left", padx=(0, S2))
        self._rec_imgs = {}
        self._rec_btn = tk.Button(actions, command=lambda: self.ctrl.toggle(), relief="flat", bd=0,
                                  bg=SURF, activebackground=SURF, cursor="hand2",
                                  highlightthickness=1, highlightbackground=SURF,
                                  highlightcolor=SUB, takefocus=1)
        if not self._icons_ok:
            self._rec_btn.configure(text="Запись", fg=TXT, font=_f(BODY), padx=S3, pady=S1,
                                    bg=SURF2, activebackground=SURF3)
        self._rec_btn.pack(side="left")
        self._rec_tip = _Tooltip(tk, self._rec_btn, "")
        self._rec_btn.bind("<Enter>", lambda e: self._paint_rec(hover=True), add="+")
        self._rec_btn.bind("<Leave>", lambda e: self._paint_rec(hover=False), add="+")
        self._rec_state = State.IDLE

        text = tk.Frame(top, bg=SURF); text.pack(side="left", fill="x", expand=True)
        self._hero_title = self._label(text, "Готов к диктовке", size=TITLE, bold=True, bg=SURF)
        self._hero_title.pack(anchor="w")
        self.status_lbl = self._label(text, "", color=MUT, size=SMALL, bg=SURF,
                                      anchor="w", justify="left", wraplength=300)
        self.status_lbl.pack(anchor="w", fill="x", pady=(2, 0))
        text.bind("<Configure>", lambda e: self.status_lbl.configure(
            wraplength=max(160, e.width - S2)))

        self._hero_keys = tk.Frame(h, bg=SURF); self._hero_keys.pack(fill="x", pady=(S3, 0))
        self._key_caps = {}
        for name in ("start", "stop", "cancel"):
            cap = tk.Label(self._hero_keys, text="", bg=SURF2, fg=SUB, font=_f(SMALL),
                           padx=6, pady=1)
            hint = tk.Label(self._hero_keys, text="", bg=SURF, fg=MUT, font=_f(SMALL))
            self._key_caps[name] = (cap, hint)
        self._layout_keycaps()
        # Busy line at the panel's bottom edge; invisible (panel-coloured) when idle.
        self._hero_bar = _Progress(tk, h, height=2, bg=SURF, track=SURF, fill=AMBER)
        self._hero_bar.pack(fill="x", pady=(S3, S2))

    def _paint_rec(self, hover: bool = False) -> None:
        if not self._icons_ok:
            return
        st = self._rec_state
        if st is State.RECORDING:
            key = ("stop", REC_H if hover else REC, "#ffffff")
            b64 = ui_icons.record_png("stop", fill=key[1], dot=key[2])
        else:
            lamp = _STATE.get(st, _STATE[State.IDLE])[0]
            key = ("dot", SEL if hover else SURF3, lamp)
            b64 = ui_icons.record_png("dot", ring=key[1], dot=key[2])
        img = self._rec_imgs.get(key)
        if img is None:
            img = self._rec_imgs[key] = _photo(self.tk, b64)
        self._rec_btn.configure(image=img)

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
            cap.pack(side="left", padx=((0 if i == 0 else S4), 0))
            hint.pack(side="left", padx=(6, 0))

    @staticmethod
    def _pretty_key(k: str) -> str:
        names = {"escape": "Esc", "enter": "Enter", "return": "Enter", "space": "Space",
                 "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
        return " + ".join(names.get(p, p.upper() if len(p) == 1 else p.capitalize())
                          for p in k.split("+"))

    def _mode_text(self):
        """(text, colour) naming the engine that will handle the next dictation."""
        cfg = self.ctrl.cfg
        if cfg.provider == "groq":
            if not self.ctrl.has_key():
                return "Groq · нужен ключ API", AMBER
            return f"Groq · {'Turbo' if 'turbo' in cfg.model else 'Large v3'}", MUT
        st = getattr(self, "_local_st", None) or self.ctrl.local_status()
        if not st.ready:
            return "Офлайн · не настроен", AMBER
        name = st.selected.name
        if len(name) > 22:
            name = name[:21] + "…"
        if st.selected.slow:
            return f"Офлайн · {name} · медленно", AMBER
        return f"Офлайн · {name}", MUT

    def _refresh_hero(self):
        if self._status_msg is None:
            txt, fg = self._mode_text()
            self.status_lbl.configure(text=txt, fg=fg)
        self._layout_keycaps()

    # ================================================================ tabs
    def _build_tabs(self):
        tk = self.tk
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=PAD, pady=(S5, 0))
        self._tabs = {}
        for key, title in (("settings", "Настройки"), ("history", "История")):
            holder = tk.Frame(bar, bg=BG)
            holder.pack(side="left", padx=(0, S5))
            lbl = self._label(holder, title, color=MUT, size=BODY, cursor="hand2")
            lbl.pack()
            ind = tk.Frame(holder, bg=BG, height=2)
            ind.pack(fill="x", pady=(S2, 0))
            lbl.bind("<Button-1>", lambda e, k=key: self._show_tab(k))
            lbl.bind("<Enter>", lambda e, k=key, w=lbl: self._tab_now != k and w.configure(fg=SUB))
            lbl.bind("<Leave>", lambda e, k=key, w=lbl: self._tab_now != k and w.configure(fg=MUT))
            self._tabs[key] = (lbl, ind)
        self._tab_now = None
        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x", padx=PAD)
        self._body = tk.Frame(self.root, bg=BG)
        self._body.pack(fill="both", expand=True, padx=(PAD, PAD - S2), pady=(S1, S3))
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
        self._tab_now = key
        for k, (lbl, ind) in self._tabs.items():
            active = k == key
            lbl.configure(fg=TXT if active else MUT)
            ind.configure(bg=TXT if active else BG)
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

    def _scrollable(self, parent, on_width=None):
        """Canvas + thin auto-hiding scrollbar; returns (canvas, inner_frame)."""
        import tkinter.ttk as ttk
        tk = self.tk
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Thin.Vertical.TScrollbar", background=SURF3, troughcolor=BG,
                     bordercolor=BG, lightcolor=SURF3, darkcolor=SURF3, arrowcolor=BG,
                     gripcount=0, relief="flat", width=6, arrowsize=6)
        st.map("Thin.Vertical.TScrollbar", background=[("active", LINE2), ("pressed", MUT)])
        try:
            st.layout("Thin.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": 1, "sticky": "nswe"})]})])
        except Exception:
            pass
        wrap = tk.Frame(parent, bg=BG); wrap.pack(fill="both", expand=True)
        cv = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=cv.yview,
                           style="Thin.Vertical.TScrollbar")

        def _sb_set(first, last):
            sb.set(first, last)
            need = not (float(first) <= 0.0 and float(last) >= 1.0)
            if need and not sb.winfo_ismapped():
                sb.pack(side="right", fill="y", padx=(S1, 0), before=cv)
            elif not need and sb.winfo_ismapped():
                sb.pack_forget()
        cv.configure(yscrollcommand=_sb_set)
        cv.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(cv, bg=BG)
        win = cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))

        def _cv_conf(e):
            cv.itemconfig(win, width=e.width - S2)          # breathing room before the bar
            if on_width:
                on_width(e.width - S2)
        cv.bind("<Configure>", _cv_conf)
        return cv, inner

    # ================================================================ settings
    def _section(self, parent, title, first=False):
        tk = self.tk
        wrap = tk.Frame(parent, bg=BG); wrap.pack(fill="x")
        if not first:
            tk.Frame(wrap, bg=LINE, height=1).pack(fill="x", pady=(S5, 0))
        self._label(wrap, title, bold=True).pack(anchor="w", pady=(S4, S3))
        body = tk.Frame(wrap, bg=BG); body.pack(fill="x")
        return body

    def _caption(self, parent, text, margin=S3, color=MUT, bg=BG):
        return self._wrap(self._label(parent, text, color=color, size=SMALL, bg=bg,
                                      justify="left", anchor="w"), margin)

    def _build_settings(self):
        tk = self.tk
        self._settings = tk.Frame(self._body, bg=BG)
        self._set_canvas, si = self._scrollable(
            self._settings, on_width=lambda w: self._rewrap(self._wrap_settings, w))

        # ---- 1. Распознавание -------------------------------------------
        pp = self._section(si, "Распознавание", first=True)
        self._prov_seg = self._segment(pp, [("Groq · основной", "groq"),
                                            ("Офлайн · запасной", "local")],
                                       self._select_provider)
        self._prov_caption = self._caption(pp, "")
        self._prov_caption.pack(fill="x", pady=(S2, 0))
        self._prov_sub = tk.Frame(pp, bg=BG); self._prov_sub.pack(fill="x", pady=(S4, 0))
        self._build_groq_panel()
        self._build_local_panel()

        # ---- 2. Горячая клавиша -------------------------------------------
        hp = self._section(si, "Горячая клавиша")
        hrow = tk.Frame(hp, bg=BG); hrow.pack(fill="x")
        self.hotkey_var = tk.StringVar(value=self._pretty_key(self.ctrl.cfg.hotkey))
        self.hotkey_chip = tk.Label(hrow, textvariable=self.hotkey_var, bg=SURF2, fg=TXT,
                                    font=_f(BODY), padx=S3, pady=5,
                                    highlightthickness=1, highlightbackground=SURF2)
        self.hotkey_chip.pack(side="left")
        self.capture_btn = self._btn(hrow, "Изменить…", self._begin_capture)
        self.capture_btn.pack(side="right")
        self._caption(hp, "Запускает запись в любом окне. Остановить и вставить: "
                          "клавиши в панели выше.").pack(fill="x", pady=(S2, 0))

        # ---- 3. Запись и хранилище --------------------------------------
        lp = self._section(si, "Запись и хранилище")
        self._max_sec_row = tk.Frame(lp, bg=BG)
        self._label(self._max_sec_row, "Авто-стоп через", color=SUB).pack(side="left")
        self.max_sec_var = tk.StringVar(value=str(self.ctrl.cfg.max_seconds))
        e1 = self._entry(self._max_sec_row, self.max_sec_var, width=5, center=True)
        e1.pack(side="left", padx=(S2, 6), ipady=3)
        self._label(self._max_sec_row, "сек", color=MUT).pack(side="left")
        self._label(self._max_sec_row, "Groq принимает до ~350 с", color=MUT,
                    size=SMALL).pack(side="right")
        self._bind_apply(e1, self._save_limits)

        mb_row = tk.Frame(lp, bg=BG); mb_row.pack(fill="x")
        self._mb_row = mb_row
        self._label(mb_row, "Хранить аудио до", color=SUB).pack(side="left")
        self.max_mb_var = tk.StringVar(value=str(self.ctrl.cfg.max_storage_mb))
        e2 = self._entry(mb_row, self.max_mb_var, width=5, center=True)
        e2.pack(side="left", padx=(S2, 6), ipady=3)
        self._label(mb_row, "МБ", color=MUT).pack(side="left")
        self._label(mb_row, "0 = без лимита", color=MUT, size=SMALL).pack(side="right")
        self._bind_apply(e2, self._save_limits)
        self._storage_usage = self._label(lp, "", color=MUT, size=SMALL)
        self._storage_usage.pack(anchor="w", pady=(S2, S4))

        self._update_prov_seg()

    def _bind_apply(self, entry, fn):
        entry.bind("<Return>", lambda e: (fn(), self.root.focus_set()))
        entry.bind("<FocusOut>", lambda e: fn())

    # ---------------------------------------------------------- Groq panel
    def _build_groq_panel(self):
        tk = self.tk
        g = tk.Frame(self._prov_sub, bg=BG)
        self._groq_sub = g
        self._label(g, "Модель", color=SUB, size=SMALL).pack(anchor="w", pady=(0, S2))
        mw = tk.Frame(g, bg=BG); mw.pack(fill="x")
        self._seg = self._segment(mw, [("Turbo · быстрее", "whisper-large-v3-turbo"),
                                       ("Large v3 · точнее", "whisper-large-v3")],
                                  self._select_model)
        krow_head = tk.Frame(g, bg=BG); krow_head.pack(fill="x", pady=(S4, S2))
        self._label(krow_head, "Ключ API", color=SUB, size=SMALL).pack(side="left")
        self.key_status = self._label(krow_head, "", color=MUT, size=SMALL)
        self.key_status.pack(side="right")
        row = tk.Frame(g, bg=BG); row.pack(fill="x")
        self.key_var = tk.StringVar()
        self.key_entry = self._entry(row, self.key_var, show="•")
        self.key_entry.pack(side="left", expand=True, fill="x", ipady=5)
        self._enable_clipboard(self.key_entry)
        self._icon_btn(row, "close", self._clear_key, "Удалить сохранённый ключ",
                       hover_color=REC_T, fallback="✕").pack(side="right", padx=(S1, 0))
        self._btn(row, "Сохранить", self._save_key, primary=True).pack(side="right", padx=(S2, 0))
        self._groq_link_lbl = self._link(g, "Получить бесплатный ключ на console.groq.com",
                                         self._open_groq_keys)

    # --------------------------------------------------------- Local panel
    def _build_local_panel(self):
        tk = self.tk
        l = tk.Frame(self._prov_sub, bg=BG)
        self._local_sub = l

        # status line
        strip = tk.Frame(l, bg=BG); strip.pack(fill="x")
        self._loc_dot = tk.Canvas(strip, width=8, height=8, bg=BG, highlightthickness=0)
        self._loc_dot.pack(side="left", anchor="n", pady=(5, 0))
        self._loc_dot_id = self._loc_dot.create_oval(0, 0, 8, 8, fill=AMBER, outline="")
        self._loc_head = self._wrap(self._label(strip, "", color=SUB, size=SMALL, anchor="w",
                                                justify="left"), margin=S4)
        self._loc_head.pack(side="left", padx=(S2, 0), fill="x", expand=True)

        # engine
        er = tk.Frame(l, bg=BG); er.pack(fill="x", pady=(S4, 0))
        self._eng_btn = self._btn(er, "Установить · 8 МБ", self._install_engine, primary=True)
        self._eng_btn.pack(side="right", anchor="n")
        ecol = tk.Frame(er, bg=BG); ecol.pack(side="left", fill="x", expand=True)
        self._label(ecol, "Движок whisper.cpp").pack(anchor="w")
        self._eng_state = self._label(ecol, "", color=MUT, size=SMALL, anchor="w", justify="left")
        self._eng_state.pack(fill="x", pady=(2, 0))
        ecol.bind("<Configure>", lambda e: self._eng_state.configure(
            wraplength=max(120, e.width - S3)))
        self._eng_tip = _Tooltip(tk, self._eng_state, "")

        # model: one compact dropdown row (name · slow tag · size · chevron)
        mh = tk.Frame(l, bg=BG); mh.pack(fill="x", pady=(S4, S2))
        self._label(mh, "Модель").pack(side="left")
        self._icon_btn(mh, "retry", lambda: self._refresh_local(announce=True),
                       "Найти модели на диске", fallback="Найти").pack(side="right")
        self._model_combo = tk.Frame(l, bg=SURF2, cursor="hand2",
                                     highlightthickness=1, highlightbackground=LINE2)
        self._model_combo.pack(fill="x")
        self._mc_name = tk.Label(self._model_combo, text="", bg=SURF2, fg=TXT, font=_f(BODY),
                                 anchor="w", padx=S3, pady=6, cursor="hand2")
        self._mc_name.pack(side="left", fill="x", expand=True)
        if self._icons_ok:
            self._chev_img = _photo(tk, ui_icons.icon_png("chevron", SUB, box=20, glyph=14))
            self._mc_chev = tk.Label(self._model_combo, image=self._chev_img, bg=SURF2,
                                     padx=S2, cursor="hand2")
        else:
            self._mc_chev = tk.Label(self._model_combo, text="▾", bg=SURF2, fg=SUB,
                                     font=_f(BODY), padx=S2, cursor="hand2")
        self._mc_chev.pack(side="right", padx=(0, S1))
        self._mc_size = tk.Label(self._model_combo, text="", bg=SURF2, fg=MUT, font=_f(SMALL),
                                 cursor="hand2")
        self._mc_size.pack(side="right", padx=(S2, 0))
        self._mc_tag = tk.Label(self._model_combo, text="", bg=SURF2, fg=AMBER, font=_f(SMALL),
                                cursor="hand2")
        self._mc_tag.pack(side="right")
        combo_parts = (self._model_combo, self._mc_name, self._mc_chev, self._mc_size, self._mc_tag)
        for w in combo_parts:
            w.bind("<Button-1>", lambda e: self._show_model_picker())
            w.bind("<Enter>", lambda e: [x.configure(bg=SURF3) for x in combo_parts])
            w.bind("<Leave>", lambda e: [x.configure(bg=SURF2) for x in combo_parts])
        self._mc_tip = _Tooltip(tk, self._mc_name, "")

        # progress (hidden until a download starts; shown right under the picker)
        self._dl_frame = tk.Frame(l, bg=BG)
        self._dl_label = self._label(self._dl_frame, "", color=SUB, size=SMALL, anchor="w")
        self._dl_label.pack(side="left", fill="x", expand=True)
        self._dl_cancel_btn = self._link(self._dl_frame, "Отменить", self._cancel_download)
        self._dl_cancel_btn.pack(side="right")
        self._dl_bar = _Progress(tk, l, height=4, bg=BG, track=SURF2, fill=TXT)
        self._dl_hint = self._caption(
            l, "В списке моделей можно скачать Small (рекомендуем) или модель поменьше.")

        # manual
        man = tk.Frame(l, bg=BG); man.pack(fill="x", pady=(S3, 0))
        self._man_row = man
        self._label(man, "Уже есть модель?", color=MUT, size=SMALL).pack(side="left")
        lk1 = self._link(man, "Выбрать файл…", self._pick_file)
        lk1.pack(side="left", padx=(S2, 0))
        lk2 = self._link(man, "Папку…", self._pick_folder)
        lk2.pack(side="left", padx=(S2, 0))
        for w in (lk1, lk2):
            _Tooltip(tk, w, "Файл ggml-*.bin, папка модели faster-whisper\n"
                            "или папка с whisper-cli.exe")

    # ----------------------------------------------------- Local: actions
    def _refresh_local(self, announce=False):
        st = self.ctrl.local_status()
        self._local_st = st
        ok = st.ready
        slow = ok and st.selected.slow
        self._loc_dot.itemconfigure(self._loc_dot_id, fill=(AMBER if slow else GREEN) if ok else AMBER)
        self._loc_head.configure(text=st.headline(), fg=SUB if ok else AMBER)
        # engine
        if st.engine is not None:
            ver = eng.engine_version()
            self._eng_state.configure(text=f"Установлен{(' · ' + ver) if ver else ''}", fg=MUT)
            self._eng_tip.text = str(st.engine)
            self._eng_btn.configure(text="Переустановить")
            self._style_secondary(self._eng_btn)
        else:
            self._eng_state.configure(text="Официальная сборка ggml-org, ~8 МБ. "
                                           "Работает на любом процессоре.", fg=MUT)
            self._eng_tip.text = ""
            self._eng_btn.configure(text="Установить · 8 МБ")
            self._style_primary(self._eng_btn)
        # selected model
        if st.selected is not None:
            runnable = st.selected in st.runnable_models
            self._mc_name.configure(text=st.selected.name, fg=TXT if runnable else AMBER)
            self._mc_size.configure(text=st.selected.size_label)
            self._mc_tag.configure(text="медленно" if st.selected.slow else "")
            self._mc_tip.text = (
                "На обычном процессоре эта модель распознаёт фразу дольше минуты.\n"
                "Для диктовки лучше Small или Groq." if st.selected.slow else "")
        else:
            self._mc_name.configure(text="Модель не выбрана", fg=MUT)
            self._mc_size.configure(text="")
            self._mc_tag.configure(text="")
            self._mc_tip.text = ""
        if st.models:
            self._dl_hint.pack_forget()
        elif not self._dl_hint.winfo_ismapped():
            self._dl_hint.pack(fill="x", pady=(S2, 0), before=self._man_row)
        if announce:
            n = len(st.models)
            self._set_status_text(f"Найдено моделей: {n}" if n else
                                  "Модели не найдены. Скачайте Small из списка")
        self._refresh_hero()

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
        rx, rw = anchor.winfo_rootx(), anchor.winfo_width()
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.configure(bg=LINE2)
        self._popup = popup
        body = tk.Frame(popup, bg=SURF2); body.pack(fill="both", expand=True, padx=1, pady=1)
        cur = st.selected.path if st.selected else None
        if self._icons_ok:
            check = _photo(tk, ui_icons.icon_png("check", TXT, box=16, glyph=14))
            blank = tk.PhotoImage(width=16, height=16)
            popup._imgs = (check, blank)

        def row(title, meta, on_click, *, selected=False, dim=False, tag="", tag_fg=GREEN):
            r = tk.Frame(body, bg=SURF2, cursor="hand2"); r.pack(fill="x")
            if self._icons_ok:
                mark = tk.Label(r, bg=SURF2, image=check if selected else blank, cursor="hand2")
            else:
                mark = tk.Label(r, bg=SURF2, fg=TXT, font=_f(SMALL), width=2,
                                text="✓" if selected else "", cursor="hand2")
            mark.pack(side="left", padx=(S2, 0))
            a = tk.Label(r, text=title, bg=SURF2, fg=MUT if dim else TXT, font=_f(BODY),
                         anchor="w", padx=S1, pady=6, cursor="hand2")
            a.pack(side="left")
            parts = [r, mark, a]
            if tag:
                t = tk.Label(r, text=tag, bg=SURF2, fg=tag_fg, font=_f(SMALL), cursor="hand2")
                t.pack(side="left", padx=(S1, 0))
                parts.append(t)
            for i, (text, fg) in enumerate(reversed(meta)):     # rightmost first
                m = tk.Label(r, text=text, bg=SURF2, fg=fg, font=_f(SMALL), cursor="hand2")
                m.pack(side="right", padx=(S2, S3 if i == 0 else 0))
                parts.append(m)
            for w in parts:
                w.bind("<Button-1>", lambda e: on_click())
                w.bind("<Enter>", lambda e, ws=parts: [x.configure(bg=SURF3) for x in ws])
                w.bind("<Leave>", lambda e, ws=parts: [x.configure(bg=SURF2) for x in ws])

        # already on disk: pick to select
        for m in st.models:
            runnable = m in st.runnable_models
            meta = [(m.size_label, MUT)]
            if not runnable:
                meta.insert(0, ("нет движка" if m.kind == "ggml" else "нет faster-whisper", AMBER))
            row(m.name, meta, lambda p=m.path: self._pick_model(p),
                selected=m.path == cur, dim=not runnable,
                tag="медленно" if m.slow else "", tag_fg=AMBER)

        # not on disk yet: pick to download
        if to_download:
            if st.models:
                tk.Frame(body, bg=LINE2, height=1).pack(fill="x", pady=(S1, 0))
            tk.Label(body, text="Скачать", bg=SURF2, fg=MUT, font=_f(SMALL),
                     anchor="w", padx=S3 + S1, pady=S1).pack(fill="x", pady=(S1, 0))
            for spec in to_download:
                row(spec.title, [(spec.size_label, MUT)],
                    lambda s=spec: self._pick_download(s),
                    tag="рекомендуем" if spec.recommended else "")
        tk.Frame(body, bg=SURF2, height=S1).pack(fill="x")

        popup.update_idletasks()
        ph = popup.winfo_reqheight()
        below = anchor.winfo_rooty() + anchor.winfo_height() + 2
        # Flip above the field when the list would run off the bottom of the screen.
        if below + ph > popup.winfo_screenheight() - 48:
            below = max(0, anchor.winfo_rooty() - ph - 2)
        popup.geometry(f"{rw}x{ph}+{rx}+{below}")
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
            self._set_status_text("Не забудьте установить движок: без него модель не запустится")

    def _run_download(self, title: str, job):
        self._dl_running = True
        self._dl_cancel = threading.Event()
        self._dl_frame.pack(fill="x", pady=(S3, S1), after=self._model_combo)
        self._dl_bar.pack(fill="x", after=self._dl_frame)
        self._dl_bar.set(0); self._dl_bar.pulse(True)
        self._dl_label.configure(text=f"{title}: подключаюсь…")
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
                    self._dl_label.configure(text=f"{title}: {eng.human_size(p.done)} из {tot}{speed}")
                elif p.phase in ("verify", "extract"):
                    self._dl_bar.pulse(True)
                    self._dl_label.configure(text=f"{title}: {p.label.lower()}…")
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
    _PROV_CAPTION = {
        "groq": "Основной режим: Whisper Large v3 в облаке Groq, фраза распознаётся "
                "за секунду-две. Нужен бесплатный ключ.",
        "local": "Запасной режим на случай, когда нет интернета. Распознаёт на вашем "
                 "процессоре и заметно медленнее Groq.",
    }

    def _select_provider(self, pid: str) -> None:
        self.ctrl.save_settings(provider=pid)
        self._update_prov_seg()
        if pid == "local":
            st = self.ctrl.local_status()
            self._set_status_text("Офлайн-режим включён" if st.ready else
                                  "Офлайн-режим: " + st.headline())
        else:
            self._set_status_text("Режим: Groq")

    def _update_prov_seg(self) -> None:
        cur = self.ctrl.cfg.provider
        self._paint_segment(self._prov_seg, cur)
        self._prov_caption.configure(text=self._PROV_CAPTION.get(cur, ""))
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
        bar = tk.Frame(h, bg=BG); bar.pack(fill="x", pady=(S3, S1), padx=(0, S2))
        self._btn(bar, "Повторить очередь", self._retry_queue, small=True).pack(side="left")
        self._icon_btn(bar, "trash", self._clear_history, "Очистить историю",
                       hover_color=REC_T, fallback="Очистить").pack(side="right")
        self._hist_canvas, self._hist_inner = self._scrollable(
            h, on_width=lambda w: self._rewrap(self._wrap_history, w))

    def _refresh_history(self):
        tk = self.tk
        for w in self._hist_inner.winfo_children():
            w.destroy()
        self._wrap_history = []
        width = max(self._hist_canvas.winfo_width() - S2, 300)
        rows = self.ctrl.recent(self.HISTORY_LIMIT)
        if not rows:
            e = tk.Frame(self._hist_inner, bg=BG); e.pack(fill="x", pady=(S5 * 2, 0))
            self._label(e, "Здесь появятся ваши диктовки", color=SUB).pack()
            self._label(e, f"Нажмите {self._pretty_key(self.ctrl.cfg.hotkey)} в любом поле "
                           "и говорите", color=MUT, size=SMALL).pack(pady=(S1, 0))
            return
        tagmap = {"pending": ("в очереди", AMBER), "failed": ("ошибка", REC_T)}
        for i, r in enumerate(rows):
            if i:
                tk.Frame(self._hist_inner, bg=LINE, height=1).pack(fill="x")
            item = tk.Frame(self._hist_inner, bg=BG); item.pack(fill="x", pady=(S2, S3))
            top = tk.Frame(item, bg=BG); top.pack(fill="x")
            ts = time.strftime("%H:%M", time.localtime(r.ts))
            day = time.strftime("%d.%m", time.localtime(r.ts))
            today = time.strftime("%d.%m")
            meta = [(ts if day == today else f"{day} {ts}", MUT)]
            if r.status in tagmap:
                meta.append(tagmap[r.status])
            elif r.status == "cancelled":
                has_audio = bool(r.audio_path)
                meta.append(("отменено" + (", аудио ещё ~60 с" if has_audio else ""),
                             AMBER if has_audio else MUT))
            if r.provider:
                meta.append(("офлайн" if r.provider == "local" else "Groq", MUT))
            for j, (text, fg) in enumerate(meta):
                if j:
                    self._label(top, "·", color=MUT, size=SMALL).pack(side="left", padx=S1)
                self._label(top, text, color=fg, size=SMALL).pack(side="left")
            acts = tk.Frame(top, bg=BG); acts.pack(side="right")
            if r.status == "cancelled" and r.audio_path:
                self._icon_btn(acts, "keep", lambda i=r.id: self._keep_cancelled(i),
                               "Оставить аудио, чтобы распознать позже",
                               fallback="Оставить").pack(side="left")
            elif r.status in ("pending", "failed") and r.audio_path:
                self._icon_btn(acts, "retry", lambda i=r.id: self._retry_one(i),
                               "Распознать ещё раз", fallback="Повторить").pack(side="left")
            if r.text:
                self._icon_btn(acts, "copy", lambda t=r.text: self._copy_text(t),
                               "Копировать", fallback="Копировать").pack(side="left")
            body = "Запись отменена" if r.status == "cancelled" else (
                r.text or "Нет текста. Можно распознать ещё раз")
            lbl = tk.Label(item, text=body, bg=BG, fg=TXT if r.text else MUT, font=_f(BODY),
                           wraplength=width, justify="left", anchor="w")
            lbl.pack(fill="x", pady=(2, 0))
            self._wrap(lbl, 0, history=True)

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
            self.key_status.configure(text=f"Сохранён · {self.ctrl.key_hint()}", fg=GREEN)
            if not self.key_var.get():
                self.key_var.set(self.ctrl.stored_key())
            self._groq_link_lbl.pack_forget()
        else:
            self.key_status.configure(text="Не задан", fg=AMBER)
            self.key_var.set("")
            self._groq_link_lbl.pack(anchor="w", pady=(S2, 0))

    def _enable_clipboard(self, widget):
        widget.bind("<Key>", self._on_clip_key)
        menu = self.tk.Menu(widget, tearoff=0, bg=SURF2, fg=TXT,
                            activebackground=SEL, activeforeground=TXT, bd=0)
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
        self.hotkey_chip.configure(highlightbackground=SUB)
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
        self.hotkey_chip.configure(highlightbackground=SURF2)

    def _start_hotkeys(self):
        try:
            from .hotkeys import make_hotkey_manager
            self._hk = make_hotkey_manager()
            self._hk.start(on_error=self._hotkey_error)
            self._register_start_hotkey()
        except Exception:
            self._hk = None
            self._set_status_text("Глобальная клавиша недоступна: запускайте запись кнопкой")

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
                self._set_status_text(f"«{combo}» не удалось назначить. Остановите кнопкой в окне")

    def _unregister_rec_keys(self):
        if not self._hk:
            return
        for hk in self._rec_hk_ids:
            try: self._hk.unregister(hk)
            except Exception: pass
        self._rec_hk_ids = []

    def _hotkey_error(self, combo):
        self.root.after(0, lambda: self._set_status_text(
            f"Сочетание «{combo}» занято другой программой. Задайте другое.", error=True))

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
            self._max_sec_row.pack(fill="x", pady=(0, S3), before=self._mb_row)

    def _refresh_storage_usage(self):
        try:
            used = self.ctrl.storage.audio_size_mb()
            limit = self.ctrl.cfg.max_storage_mb
            if limit > 0:
                self._storage_usage.configure(
                    text=f"Занято {used:.1f} МБ из {limit} МБ",
                    fg=REC_T if used > limit * 0.9 else MUT)
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
            self._set_status_text("Лимит хранилища: целое число МБ, 0 = без лимита", error=True); return
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
                           "Все распознанные тексты и сохранённые аудио будут удалены. "
                           "Это нельзя отменить.", parent=self.root):
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
        _lamp, title = _STATE.get(state, _STATE[State.IDLE])
        self._rec_state = state
        self._paint_rec()
        if not self._icons_ok:
            self._rec_btn.configure(text="Стоп" if state is State.RECORDING else "Запись")
        self._rec_tip.text = {
            State.IDLE: f"Начать запись ({self._pretty_key(self.ctrl.cfg.hotkey)})",
            State.RECORDING: "Остановить и вставить текст",
            State.ERROR: "Начать запись",
        }.get(state, title)
        self._hero_title.configure(text=title)
        busy = state in (State.TRANSCRIBING, State.INJECTING)
        self._hero_bar.pulse(busy, fill=AMBER if state is State.TRANSCRIBING else GREEN)
        if message and not message.startswith("Готов."):
            self._set_status_text(message)
        elif state is State.IDLE:
            self._set_status_text("")
        if state is State.IDLE:
            self.root.after(900, self._refresh_history)

    def _set_status_text(self, text: str, error: bool = False):
        """Show ``text`` under the status title; empty text restores the engine line.

        Info messages fade back to the engine line after 6 s; errors stay until
        the next message so they can be read in full."""
        if self._status_after:
            try: self.root.after_cancel(self._status_after)
            except Exception: pass
            self._status_after = None
        if not text:
            self._status_msg = None
            self._refresh_hero()
            return
        self._status_msg = (text, error)
        self.status_lbl.configure(text=text, fg=REC_T if error else SUB)
        if not error and self.ctrl.state.state is State.IDLE:
            self._status_after = self.root.after(6000, lambda: self._set_status_text(""))

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
