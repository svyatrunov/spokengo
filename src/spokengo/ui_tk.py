"""Tkinter control panel — dark theme. Hand-styled tk widgets (full control over
the dark look), a Pillow-rendered rounded "hero" record button, custom tabs, and
dark cards for settings + history. All app logic lives in GuiController.
"""
from __future__ import annotations

import time
from typing import List, Optional

from .controller import GuiController
from .hotkeys import clipboard_action, combo_from_state
from .overlay import _load_font
from .state import State
from .transcribe.groq_provider import KNOWN_MODELS
from .transcribe.local_provider import (find_local_whisper_models,
                                        model_display, model_size,
                                        faster_whisper_available)

# palette
BG = "#161b29"; BAR = "#11151f"; SURF = "#1b2130"; SURF2 = "#262d40"
BORDER = "#2a3147"; BORDER2 = "#343c56"
TXT = "#eef1f7"; MUT = "#9aa3bd"; SUB = "#c4cadb"
ACC = "#5b5ef0"; ACC_H = "#6d70f4"; REC = "#ef4444"; REC_H = "#f26d6d"
GREEN = "#5fce86"; AMBER = "#f6be4f"; BLUE = "#5b8def"

_DOT = {
    State.IDLE: (GREEN, "Запись"),
    State.RECORDING: (REC, "Стоп"),
    State.TRANSCRIBING: (AMBER, "Распознаю"),
    State.INJECTING: (BLUE, "Вставка"),
    State.ERROR: (REC, "Ошибка"),
}


def _smooth_rect(canvas, x0, y0, x1, y1, r, **kw):
    """Rounded polygon — B-spline smoothing gives soft corners."""
    pts = (x0+r, y0,  x1-r, y0,  x1, y0,  x1, y0+r,
           x1, y1-r,  x1, y1,  x1-r, y1,  x0+r, y1,
           x0, y1,  x0, y1-r,  x0, y0+r,  x0, y0)
    return canvas.create_polygon(pts, smooth=True, **kw)


class _RCard:
    """Canvas-backed card with rounded corners.

    .inner          — tk.Frame where you pack content
    .pack(**kw)     — proxy to underlying canvas
    .pack_forget()  — proxy
    """
    def __init__(self, tk_mod, parent, *, bg=SURF, border=BORDER, radius=10):
        self._cv = tk_mod.Canvas(parent, bg=BG, highlightthickness=0, bd=0)
        self.inner = tk_mod.Frame(self._cv, bg=bg)
        self._bg = bg
        self._bdr = border
        self._r = radius
        self._win = self._cv.create_window(1, 1, window=self.inner, anchor="nw")
        self._cv.bind("<Configure>", lambda e: self._cv.after_idle(self._draw))
        self.inner.bind("<Configure>", lambda e: self._cv.after_idle(self._draw))

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

    def pack(self, **kw):
        self._cv.pack(**kw)

    def pack_forget(self):
        self._cv.pack_forget()


class ControlPanel:
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
        self._model_popup: object = None

        try:  # set AppUserModelID so Windows taskbar shows the SpokenGo icon
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "wind.slw.SpokenGo")
        except Exception:
            pass

        self.root = tk.Tk()
        self.root.title("SpokenGo")
        self.root.geometry("452x600")
        self.root.minsize(430, 560)
        self.root.configure(bg=BG)
        try:
            import os
            ico = os.path.join(os.path.dirname(__file__), "assets", "spokengo.ico")
            if os.path.exists(ico):
                self.root.iconbitmap(default=ico)
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
        self._render_hero()
        self._on_status(State.IDLE, "Готов. Нажмите Запись или хоткей")
        self._start_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- small helpers ----------
    def _card(self, parent):
        return _RCard(self.tk, parent)

    def _label(self, parent, text, color=TXT, size=10, bold=False, bg=BG):
        return self.tk.Label(parent, text=text, bg=bg, fg=color,
                             font=("Segoe UI", size, "bold" if bold else "normal"))

    def _btn(self, parent, text, cmd, primary=False):
        b = self.tk.Button(parent, text=text, command=cmd, relief="flat", bd=0,
                           cursor="hand2", padx=12, pady=6,
                           bg=(ACC if primary else SURF2),
                           fg=("#ffffff" if primary else TXT),
                           activebackground=(ACC_H if primary else SURF),
                           activeforeground="#ffffff",
                           font=("Segoe UI", 10))
        return b

    def _entry(self, parent, textvariable, show=None):
        e = self.tk.Entry(parent, textvariable=textvariable, show=show,
                          bg=BAR, fg=TXT, insertbackground=TXT, relief="flat",
                          highlightthickness=1, highlightbackground=BORDER2,
                          highlightcolor=ACC, font=("Segoe UI", 11))
        return e

    # ---------- header ----------
    def _build_header(self):
        tk = self.tk
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=20, pady=(18, 4))
        self._label(bar, "SpokenGo", size=19, bold=True).pack(side="left")

        CHIP_H, CHIP_W = 34, 132
        r = CHIP_H // 2
        self._chip_cv = tk.Canvas(bar, width=CHIP_W, height=CHIP_H,
                                   bg=BG, highlightthickness=0, bd=0, cursor="hand2")
        self._chip_cv.pack(side="right")
        self._chip_pill = _smooth_rect(
            self._chip_cv, 0, 1, CHIP_W, CHIP_H - 1, r, fill=ACC, outline="")
        # Items created at placeholder coords; _chip_layout positions them dynamically
        self._chip_oval = self._chip_cv.create_oval(0, 0, 1, 1, fill=GREEN, outline="")
        self._chip_sq = self._chip_cv.create_rectangle(0, 0, 1, 1,
                                                        fill="#ffffff", outline="", state="hidden")
        # Text is always at fixed x (math: CHIP_W/2 + (DOT_R*2+gap)/2 = 66+9 = 75)
        self._chip_txt = self._chip_cv.create_text(
            75, CHIP_H // 2, text="Запись",
            fill="#ffffff", anchor="center", font=("Segoe UI", 11))
        self._chip_cv.bind("<Button-1>", lambda e: self._on_record_button())
        self._chip_layout("Запись")  # position dot to balance with initial text

    # ---------- status row (below header) ----------
    def _build_hero(self):
        tk = self.tk
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="x", padx=20, pady=(6, 4))
        self.status_lbl = self._label(wrap, "", color=MUT, size=9)
        self.status_lbl.pack(side="left", anchor="w")
        self._btn(wrap, "⧉ Копировать последний", self._copy_last).pack(side="right")

    def _render_hero(self):
        pass

    # ---------- tabs ----------
    def _build_tabs(self):
        bar = self.tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=20, pady=(14, 0))
        self._tabs = {}
        for key, title in (("settings", "Настройки"), ("history", "История")):
            holder = self.tk.Frame(bar, bg=BG)
            holder.pack(side="left", padx=(0, 20))
            lbl = self._label(holder, title, color=MUT, size=11)
            lbl.pack()
            ind = self.tk.Frame(holder, bg=BG, height=2)
            ind.pack(fill="x", pady=(7, 0))
            lbl.bind("<Button-1>", lambda e, k=key: self._show_tab(k))
            self._tabs[key] = (lbl, ind)
        sep = self.tk.Frame(self.root, bg=BORDER, height=1)
        sep.pack(fill="x", padx=20)
        self._body = self.tk.Frame(self.root, bg=BG)
        self._body.pack(fill="both", expand=True, padx=20, pady=14)

    def _on_scroll(self, event):
        if self._active_scroll:
            self._active_scroll.yview_scroll(int(-event.delta / 120), "units")

    def _show_tab(self, key):
        for k, (lbl, ind) in self._tabs.items():
            active = k == key
            lbl.configure(fg=TXT if active else MUT)
            ind.configure(bg=ACC if active else BG)
        if hasattr(self, "_settings"):
            self._settings.pack_forget()
        if hasattr(self, "_history"):
            self._history.pack_forget()
        (self._settings if key == "settings" else self._history).pack(
            fill="both", expand=True)
        if key == "settings":
            self._active_scroll = self._set_canvas if hasattr(self, "_set_canvas") else None
            if hasattr(self, "_storage_usage"):
                self._refresh_storage_usage()
        else:
            self._active_scroll = self._hist_canvas if hasattr(self, "_hist_canvas") else None

    # ---------- settings ----------
    def _build_settings(self):
        tk = self.tk
        s = tk.Frame(self._body, bg=BG)
        self._settings = s

        # Scrollable canvas with scrollbar (same visual style as history)
        import tkinter.ttk as ttk
        _st = ttk.Style()
        _st.theme_use("clam")
        _st.configure("Dark.Vertical.TScrollbar",
                      background=SURF2, troughcolor=BAR,
                      bordercolor=BG, arrowcolor=BORDER2,
                      gripcount=0, relief="flat")
        _st.map("Dark.Vertical.TScrollbar",
                background=[("active", BORDER2), ("pressed", ACC)],
                arrowcolor=[("active", MUT)])
        set_wrap = tk.Frame(s, bg=BG); set_wrap.pack(fill="both", expand=True)
        self._set_canvas = tk.Canvas(set_wrap, bg=BG, highlightthickness=0, bd=0)
        set_sb = ttk.Scrollbar(set_wrap, orient="vertical", command=self._set_canvas.yview,
                               style="Dark.Vertical.TScrollbar")
        self._set_canvas.configure(yscrollcommand=set_sb.set)
        set_sb.pack(side="right", fill="y")
        self._set_canvas.pack(side="left", fill="both", expand=True)
        si = tk.Frame(self._set_canvas, bg=BG)
        self._set_win = self._set_canvas.create_window((0, 0), window=si, anchor="nw")
        si.bind("<Configure>",
                lambda e: self._set_canvas.configure(scrollregion=self._set_canvas.bbox("all")))
        self._set_canvas.bind("<Configure>",
                              lambda e: self._set_canvas.itemconfig(self._set_win, width=e.width))

        # 1. Provider + model (first; _repack_settings handles actual packing)
        self._prov_card = self._card(si)
        pp = tk.Frame(self._prov_card.inner, bg=SURF); pp.pack(fill="x", padx=13, pady=12)
        self._label(pp, "Провайдер", color=SUB, size=10, bg=SURF).pack(anchor="w")
        pseg = tk.Frame(pp, bg=BORDER2); pseg.pack(fill="x", pady=(9, 0))
        self._prov_seg = {}
        for i, (label, pid) in enumerate([("☁  Groq", "groq"),
                                           ("🖥  Локально", "local")]):
            b = tk.Label(pseg, text=label, bg=SURF2, fg=MUT,
                         font=("Segoe UI", 10), cursor="hand2", pady=7)
            b.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 1, 0))
            pseg.grid_columnconfigure(i, weight=1)
            b.bind("<Button-1>", lambda e, p=pid: self._select_provider(p))
            self._prov_seg[pid] = b
        sub_wrap = tk.Frame(pp, bg=SURF); sub_wrap.pack(fill="x", pady=(10, 0))
        self._groq_sub = tk.Frame(sub_wrap, bg=SURF)
        self._label(self._groq_sub, "Модель", color=MUT, size=9,
                    bg=SURF).pack(anchor="w", pady=(0, 6))
        seg = tk.Frame(self._groq_sub, bg=BORDER2); seg.pack(fill="x")
        self._seg = {}
        for i, (label, mid) in enumerate([("Turbo · быстрее", "whisper-large-v3-turbo"),
                                           ("Large v3 · точнее", "whisper-large-v3")]):
            b = tk.Label(seg, text=label, bg=SURF2, fg=MUT,
                         font=("Segoe UI", 10), cursor="hand2", pady=7)
            b.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 1, 0))
            seg.grid_columnconfigure(i, weight=1)
            b.bind("<Button-1>", lambda e, m=mid: self._select_model(m))
            self._seg[mid] = b
        self._local_sub = tk.Frame(sub_wrap, bg=SURF)
        self._label(self._local_sub, "Модель", color=MUT, size=9,
                    bg=SURF).pack(anchor="w", pady=(0, 5))
        self._local_model_frame = tk.Frame(self._local_sub, bg=SURF)
        self._local_model_frame.pack(fill="x")
        self._local_no_model = self._label(self._local_sub, "", color=AMBER,
                                           size=8, bg=SURF)
        self._local_no_model.pack(anchor="w", pady=(4, 0))
        self._local_model_btns: dict = {}

        # 2. API key — only packed when provider == "groq"
        self._key_card = self._card(si)
        kp = tk.Frame(self._key_card.inner, bg=SURF); kp.pack(fill="x", padx=13, pady=12)
        top = tk.Frame(kp, bg=SURF); top.pack(fill="x")
        self._label(top, "Ключ API (Groq)", color=SUB, size=10, bg=SURF).pack(side="left")
        self.key_status = self._label(top, "", color=MUT, size=9, bg=SURF)
        self.key_status.pack(side="right")
        row = tk.Frame(kp, bg=SURF); row.pack(fill="x", pady=(9, 0))
        self.key_var = tk.StringVar()
        self.key_entry = self._entry(row, self.key_var, show="•")
        self.key_entry.pack(side="left", expand=True, fill="x", ipady=4)
        self._enable_clipboard(self.key_entry)
        self._btn(row, "Сохранить", self._save_key, primary=True).pack(side="left", padx=(8, 5))
        clr = tk.Label(row, text="\u2715", bg=SURF2, fg=MUT, font=("Segoe UI", 11),
                       cursor="hand2", padx=10)
        clr.pack(side="left", fill="y")
        clr.bind("<Button-1>", lambda e: self._clear_key())
        clr.bind("<Enter>", lambda e: clr.configure(fg=REC_H))
        clr.bind("<Leave>", lambda e: clr.configure(fg=MUT))
        self._groq_link_lbl = tk.Label(
            kp, text="\u041f\u043e\u043b\u0443\u0447\u0438\u0442\u0435 \u0431\u0435\u0441\u043f\u043b\u0430\u0442\u043d\u044b\u0439 \u043a\u043b\u044e\u0447 \u043d\u0430 Groq Console \u2192",
            font=("Segoe UI", 9, "underline"), bg=SURF, fg=ACC, cursor="hand2")
        self._groq_link_lbl.bind("<Button-1>", lambda e: self._open_groq_keys())
        self._groq_link_lbl.bind("<Enter>", lambda e: self._groq_link_lbl.configure(fg=ACC_H))
        self._groq_link_lbl.bind("<Leave>", lambda e: self._groq_link_lbl.configure(fg=ACC))

        # 3. Limits: max recording length + storage quota
        self._limits_card = self._card(si)
        lp = tk.Frame(self._limits_card.inner, bg=SURF); lp.pack(fill="x", padx=13, pady=12)
        self._label(lp, "Длительность и хранилище", color=SUB, size=10, bg=SURF).pack(anchor="w")

        # — max seconds row (hidden for local provider)
        self._max_sec_row = tk.Frame(lp, bg=SURF)
        ms_inner = tk.Frame(self._max_sec_row, bg=SURF); ms_inner.pack(fill="x", pady=(9, 0))
        self._label(ms_inner, "Авто-стоп:", color=MUT, size=9, bg=SURF).pack(side="left")
        self.max_sec_var = tk.StringVar(value=str(self.ctrl.cfg.max_seconds))
        ms_entry = tk.Entry(ms_inner, textvariable=self.max_sec_var, width=6,
                            bg=BAR, fg=TXT, insertbackground=TXT, relief="flat",
                            highlightthickness=1, highlightbackground=BORDER2,
                            highlightcolor=ACC, font=("Segoe UI", 10), justify="center")
        ms_entry.pack(side="left", padx=(6, 4), ipady=3)
        self._label(ms_inner, "сек", color=MUT, size=9, bg=SURF).pack(side="left")
        self._max_sec_hint = self._label(
            self._max_sec_row,
            "рекомендуем ≤ 350 с для Groq",
            color=MUT, size=8, bg=SURF)
        self._max_sec_hint.pack(anchor="w", pady=(3, 0))

        # — storage quota row (always visible)
        mb_row = tk.Frame(lp, bg=SURF); mb_row.pack(fill="x", pady=(10, 0))
        self._label(mb_row, "Лимит хранилища:", color=MUT, size=9, bg=SURF).pack(side="left")
        self.max_mb_var = tk.StringVar(value=str(self.ctrl.cfg.max_storage_mb))
        mb_entry = tk.Entry(mb_row, textvariable=self.max_mb_var, width=6,
                            bg=BAR, fg=TXT, insertbackground=TXT, relief="flat",
                            highlightthickness=1, highlightbackground=BORDER2,
                            highlightcolor=ACC, font=("Segoe UI", 10), justify="center")
        mb_entry.pack(side="left", padx=(6, 4), ipady=3)
        self._label(mb_row, "МБ  (0 = без ограничений)", color=MUT, size=9, bg=SURF).pack(side="left")
        self._storage_usage = self._label(lp, "", color=MUT, size=8, bg=SURF)
        self._storage_usage.pack(anchor="w", pady=(4, 0))

        save_row = tk.Frame(lp, bg=SURF); save_row.pack(fill="x", pady=(10, 0))
        self._btn(save_row, "Применить", self._save_limits, primary=True).pack(side="right")

        # 4. Hotkey (always last)
        self._hk_card = self._card(si)
        hp = tk.Frame(self._hk_card.inner, bg=SURF); hp.pack(fill="x", padx=13, pady=12)
        self._label(hp, "Сочетание для старта", color=SUB, size=10, bg=SURF).pack(anchor="w")
        hrow = tk.Frame(hp, bg=SURF); hrow.pack(fill="x", pady=(9, 0))
        self.hotkey_var = tk.StringVar(value=self.ctrl.cfg.hotkey)
        self.hotkey_chip = tk.Label(hrow, textvariable=self.hotkey_var, bg=BAR, fg=TXT,
                                    font=("Segoe UI", 11), padx=12, pady=6,
                                    highlightthickness=1, highlightbackground=BORDER2)
        self.hotkey_chip.pack(side="left")
        self.capture_btn = self._btn(hrow, "Задать клавишей", self._begin_capture)
        self.capture_btn.pack(side="right")
        self._label(hp, "Стоп — Enter, отмена — Esc (во время записи).",
                    color=MUT, size=8, bg=SURF).pack(anchor="w", pady=(7, 0))

        self._update_prov_seg()

    def _repack_settings(self):
        for card in [self._prov_card, self._key_card, self._limits_card, self._hk_card]:
            card.pack_forget()
        self._prov_card.pack(fill="x", pady=(0, 11))
        if self.ctrl.cfg.provider == "groq":
            self._key_card.pack(fill="x", pady=(0, 11))
        self._limits_card.pack(fill="x", pady=(0, 11))
        self._hk_card.pack(fill="x")
        self._refresh_limits_visibility()

    def _select_model(self, mid):
        self.ctrl.save_settings(model=mid)
        self._update_seg()
        self._set_status_text(f"Модель: {mid}")

    def _update_seg(self):
        cur = self.ctrl.cfg.model
        for mid, b in self._seg.items():
            if mid == cur:
                b.configure(bg=ACC, fg="#ffffff")
            else:
                b.configure(bg=SURF2, fg=MUT)

    def _select_provider(self, pid: str) -> None:
        if pid == "local":
            models = find_local_whisper_models()
            if not models and not faster_whisper_available():
                self._set_status_text(
                    "⚠ pip install faster-whisper, "
                    "затем: huggingface-cli download Systran/faster-whisper-tiny")
                return
            # Keep existing model selection; default to first found if none saved.
            current = self.ctrl.cfg.local_model
            local_model = current if (current and current in models) else (models[0] if models else "")
            self.ctrl.save_settings(provider="local", local_model=local_model)
        else:
            self.ctrl.save_settings(provider="groq")
        self._update_prov_seg()
        self._set_status_text(
            "Провайдер: локальная модель" if pid == "local"
            else "Провайдер: Groq")

    def _update_prov_seg(self) -> None:
        cur = self.ctrl.cfg.provider
        for pid, b in self._prov_seg.items():
            b.configure(bg=(ACC if pid == cur else SURF2),
                        fg=("#ffffff" if pid == cur else MUT))
        if cur == "groq":
            self._local_sub.pack_forget()
            self._groq_sub.pack(fill="x")
            self._update_seg()
        else:
            self._groq_sub.pack_forget()
            self._local_sub.pack(fill="x")
            self._refresh_local_status()
        self._repack_settings()

    def _refresh_local_status(self) -> None:
        tk = self.tk
        for w in self._local_model_frame.winfo_children():
            w.destroy()

        if not faster_whisper_available():
            self._local_no_model.configure(
                text="⚠ faster-whisper не установлен.  pip install faster-whisper")
            return

        models = find_local_whisper_models()
        if not models:
            self._local_no_model.configure(
                text="⚠ Нет моделей.  hf download Systran/faster-whisper-tiny")
            return

        self._local_no_model.configure(text="")
        current = self.ctrl.cfg.local_model
        if not current or current not in models:
            current = models[0]

        name = model_display(current)
        sz = model_size(current)

        # Combobox-style row: fills width, ▾ chevron on right
        self._local_combo_row = tk.Frame(
            self._local_model_frame, bg=SURF2, cursor="hand2",
            highlightthickness=1, highlightbackground=BORDER2)
        self._local_combo_row.pack(fill="x")

        chev = tk.Label(self._local_combo_row, text="▾", bg=SURF2, fg=MUT,
                        font=("Segoe UI", 13), padx=10, cursor="hand2")
        chev.pack(side="right", pady=6)
        tk.Frame(self._local_combo_row, bg=BORDER2, width=1).pack(
            side="right", fill="y", pady=3)
        sz_lbl = tk.Label(self._local_combo_row, text=sz, bg=SURF2, fg=MUT,
                          font=("Segoe UI", 9), cursor="hand2")
        sz_lbl.pack(side="right", pady=8, padx=(0, 8))
        name_lbl = tk.Label(self._local_combo_row, text=f"  {name}", bg=SURF2,
                            fg=TXT, font=("Segoe UI", 10), anchor="w",
                            cursor="hand2")
        name_lbl.pack(side="left", fill="x", expand=True, pady=8)

        for w in list(self._local_combo_row.winfo_children()) + [self._local_combo_row]:
            w.bind("<Button-1>", lambda e: self._show_model_picker())

    def _show_model_picker(self) -> None:
        tk = self.tk
        # Toggle: close if already open
        if self._model_popup is not None:
            try:
                self._model_popup.destroy()
            except Exception:
                pass
            return

        models = find_local_whisper_models()
        if not models:
            return

        combo = self._local_combo_row
        combo.update_idletasks()
        rx = combo.winfo_rootx()
        ry = combo.winfo_rooty() + combo.winfo_height()
        rw = combo.winfo_width()

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.configure(bg=BORDER2)
        self._model_popup = popup
        popup.bind("<Destroy>", lambda e: setattr(self, "_model_popup", None), add="+")

        current = self.ctrl.cfg.local_model

        for path in models:
            name = model_display(path)
            sz = model_size(path)
            active = path == current or (not current and path == models[0])
            bg_row = ACC if active else SURF2
            fg_row = "#ffffff" if active else TXT
            fg_sz = "#ffffff" if active else MUT

            row = tk.Frame(popup, bg=bg_row, cursor="hand2")
            row.pack(fill="x", pady=(0, 1))
            name_lbl = tk.Label(row, text=f"  {name}", bg=bg_row, fg=fg_row,
                                font=("Segoe UI", 10), anchor="w", pady=9)
            name_lbl.pack(side="left", fill="x", expand=True)
            size_lbl = tk.Label(row, text=f"{sz}  ", bg=bg_row, fg=fg_sz,
                                font=("Segoe UI", 9), pady=9)
            size_lbl.pack(side="right")

            def make_h(p):
                def h(e=None):
                    self.ctrl.save_settings(local_model=p)
                    self._refresh_local_status()
                    self._set_status_text(f"Модель: {model_display(p)}")
                    try: popup.destroy()
                    except Exception: pass
                return h

            fn = make_h(path)
            for w in [row, name_lbl, size_lbl]:
                w.bind("<Button-1>", fn)

        popup.update_idletasks()
        ph = popup.winfo_reqheight()
        popup.geometry(f"{rw}x{ph}+{rx}+{ry}")

        # Follow combo row width + position live as window is resized
        def _reposition(e):
            try:
                new_rx = combo.winfo_rootx()
                new_ry = combo.winfo_rooty() + combo.winfo_height()
                popup.geometry(f"{e.width}x{ph}+{new_rx}+{new_ry}")
            except Exception:
                pass

        conf_id = combo.bind("<Configure>", _reposition, add="+")
        popup.bind("<Destroy>", lambda e: combo.unbind("<Configure>", conf_id))

        popup.bind("<Escape>", lambda e: popup.destroy())

        def _dismiss(e):
            try:
                target = str(popup.winfo_containing(e.x_root, e.y_root))
                if not target.startswith(str(popup)):
                    popup.destroy()
                    self.root.unbind("<Button-1>")
            except Exception:
                pass

        popup.after(50, lambda: self.root.bind("<Button-1>", _dismiss))

    # ---------- history (scrollable cards, one-click copy / retry) ----------
    HISTORY_LIMIT = 50

    def _build_history(self):
        tk = self.tk
        h = tk.Frame(self._body, bg=BG)
        self._history = h
        bar = tk.Frame(h, bg=BG); bar.pack(fill="x", pady=(0, 8))
        self._btn(bar, "Обновить", self._refresh_history).pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        self._btn(bar, "Повторить очередь", self._retry_queue).pack(
            side="left", expand=True, fill="x", padx=(4, 4))
        self._btn(bar, "Очистить историю", self._clear_history).pack(
            side="left", expand=True, fill="x", padx=(4, 0))

        wrap = tk.Frame(h, bg=BG); wrap.pack(fill="both", expand=True)
        self._hist_canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        import tkinter.ttk as ttk
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self._hist_canvas.yview,
                           style="Dark.Vertical.TScrollbar")
        self._hist_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._hist_canvas.pack(side="left", fill="both", expand=True)
        self._hist_inner = tk.Frame(self._hist_canvas, bg=BG)
        self._hist_win = self._hist_canvas.create_window((0, 0), window=self._hist_inner,
                                                         anchor="nw")
        self._hist_inner.bind(
            "<Configure>",
            lambda e: self._hist_canvas.configure(scrollregion=self._hist_canvas.bbox("all")))
        self._hist_canvas.bind(
            "<Configure>",
            lambda e: self._hist_canvas.itemconfig(self._hist_win, width=e.width))
        # Shared scroll handler: routes to whichever canvas is active
        self._active_scroll = None
        self.root.bind_all("<MouseWheel>", self._on_scroll)

    # ---------- key actions ----------
    def _save_key(self):
        self.ctrl.set_api_key(self.key_var.get()); self.key_var.set("")
        self._refresh_key_status()

    def _clear_key(self):
        self.ctrl.clear_api_key(); self._refresh_key_status()

    def _refresh_key_status(self):
        if self.ctrl.has_key():
            self.key_status.configure(text=f"● задан · {self.ctrl.key_hint()}", fg=GREEN)
            if not self.key_var.get():
                self.key_var.set(self.ctrl.stored_key())
            self._groq_link_lbl.pack_forget()
        else:
            self.key_status.configure(text="● не задан", fg=AMBER)
            self.key_var.set("")
            self._groq_link_lbl.pack(anchor="w", pady=(8, 0))

    # ---------- clipboard (layout-independent) ----------
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
        if action == "paste":
            w.event_generate("<<Paste>>")
        elif action == "copy":
            w.event_generate("<<Copy>>")
        elif action == "cut":
            w.event_generate("<<Cut>>")
        elif action == "select_all":
            try:
                w.select_range(0, "end"); w.icursor("end")
            except Exception:
                pass
        return "break"

    # ---------- hotkey capture ----------
    def _begin_capture(self):
        self._capturing = True
        self.capture_btn.configure(text="Нажмите сочетание…")
        self.root.bind("<KeyPress>", self._on_capture_key)
        self.root.focus_set()

    def _on_capture_key(self, event):
        if not self._capturing:
            return
        combo = combo_from_state(int(event.state), event.keysym)
        if not combo:
            return
        self.hotkey_var.set(combo)
        self._end_capture()
        try:
            self.ctrl.save_settings(hotkey=combo)   # apply immediately
            self._restart_start_hotkey()
            self._render_hero()
            self._set_status_text(f"Сочетание: {combo}")
        except Exception as exc:
            self._set_status_text(f"Ошибка: {exc}")

    def _end_capture(self):
        self._capturing = False
        try:
            self.root.unbind("<KeyPress>")
        except Exception:
            pass
        self.capture_btn.configure(text="Задать клавишей")

    # ---------- record / hotkeys ----------
    def _on_record_button(self):
        self.ctrl.toggle()

    def _start_hotkeys(self):
        try:
            from .hotkeys import make_hotkey_manager
            self._hk = make_hotkey_manager()
            self._hk.start(on_error=self._hotkey_error)
            self._register_start_hotkey()
        except Exception:
            self._hk = None
            self._set_status_text("Глобальный хоткей недоступен — используйте кнопку")

    def _register_start_hotkey(self):
        if not self._hk:
            return
        self._start_hk_id = self._hk.register(
            self.ctrl.cfg.hotkey, lambda: self.root.after(0, self.ctrl.start_recording))

    def _restart_start_hotkey(self):
        if self._hk and self._start_hk_id is not None:
            try:
                self._hk.unregister(self._start_hk_id)
            except Exception:
                pass
        self._register_start_hotkey()

    def _register_rec_keys(self):
        if not self._hk:
            return
        try:
            self._rec_hk_ids = [
                self._hk.register(self.ctrl.cfg.stop_key,
                                  lambda: self.root.after(0, self.ctrl.stop_recording)),
                self._hk.register(self.ctrl.cfg.cancel_key,
                                  lambda: self.root.after(0, self.ctrl.cancel_recording)),
            ]
        except Exception:
            self._rec_hk_ids = []

    def _unregister_rec_keys(self):
        if not self._hk:
            return
        for hk in self._rec_hk_ids:
            try:
                self._hk.unregister(hk)
            except Exception:
                pass
        self._rec_hk_ids = []

    def _hotkey_error(self, combo):
        self.root.after(0, lambda: self._set_status_text(
            f"Сочетание «{combo}» занято — задайте другое в «Настройках»."))

    # ---------- copy / history ----------
    def _copy_text(self, text: str):
        if not text:
            self._set_status_text("Пусто — нечего копировать"); return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()            # keep it on the clipboard after we exit
            self._set_status_text("Скопировано в буфер обмена")
        except Exception as exc:
            self._set_status_text(f"Не удалось скопировать: {exc}")

    def _copy_last(self):
        self._copy_text(self.ctrl.last_text())

    def _open_groq_keys(self):
        import webbrowser, threading
        threading.Thread(target=lambda: webbrowser.open("https://console.groq.com/keys"),
                         daemon=True).start()

    def _refresh_limits_visibility(self):
        """Show/hide max_seconds row depending on provider."""
        if not hasattr(self, "_max_sec_row"):
            return
        if self.ctrl.cfg.provider == "local":
            self._max_sec_row.pack_forget()
        else:
            self._max_sec_row.pack(fill="x")

    def _refresh_storage_usage(self):
        try:
            used = self.ctrl.storage.audio_size_mb()
            limit = self.ctrl.cfg.max_storage_mb
            if limit > 0:
                self._storage_usage.configure(
                    text=f"Используется: {used:.1f} МБ из {limit} МБ",
                    fg=REC if used > limit * 0.9 else MUT)
            else:
                self._storage_usage.configure(
                    text=f"Используется: {used:.1f} МБ", fg=MUT)
        except Exception:
            pass

    def _save_limits(self):
        try:
            ms = int(self.max_sec_var.get())
            if ms <= 0:
                raise ValueError("должно быть > 0")
        except ValueError:
            self._set_status_text("Авто-стоп: введите целое число секунд > 0"); return
        try:
            mb = int(self.max_mb_var.get())
            if mb < 0:
                raise ValueError("должно быть ≥ 0")
        except ValueError:
            self._set_status_text("Лимит: введите целое число МБ ≥ 0 (0 = без ограничений)"); return
        self.ctrl.save_settings(max_seconds=ms, max_storage_mb=mb)
        if mb > 0:
            import threading
            threading.Thread(target=lambda: (
                self.ctrl.storage.evict_oldest(mb),
                self.root.after(0, self._refresh_storage_usage),
                self.root.after(0, self._refresh_history),
            ), daemon=True).start()
        self._refresh_storage_usage()
        self._set_status_text(
            f"Авто-стоп: {ms} с · лимит: {mb} МБ" if mb else f"Авто-стоп: {ms} с · без ограничений")

    def _on_cancelled(self, rid: int) -> None:
        try:
            self.root.after(0, self._refresh_history)
            rec = self.ctrl.storage.get(rid)
            if rec and rec.audio_path:
                self.root.after(62000, self._refresh_history)
        except Exception:
            pass

    def _keep_cancelled(self, rid: int):
        self.ctrl.keep_cancelled(rid)
        self._refresh_history()

    def _retry_queue(self):
        import threading
        threading.Thread(target=lambda: (self.ctrl.retry_queue(),
                                         self.root.after(0, self._refresh_history)),
                         daemon=True).start()

    def _clear_history(self):
        import tkinter.messagebox as mb
        if not mb.askyesno("Очистить историю",
                           "Удалить все записи истории и аудиофайлы?",
                           parent=self.root):
            return
        self.ctrl.clear_history()
        self._refresh_history()

    def _retry_one(self, rec_id):
        import threading
        threading.Thread(target=lambda: (self.ctrl.retry_one(rec_id),
                                         self.root.after(0, self._refresh_history)),
                         daemon=True).start()

    def _refresh_history(self):
        tk = self.tk
        for w in self._hist_inner.winfo_children():
            w.destroy()
        rows = self.ctrl.recent(self.HISTORY_LIMIT)
        if not rows:
            self._label(self._hist_inner, "Пока нет распознаваний.", color=MUT,
                        size=10).pack(anchor="w", padx=4, pady=8)
            return
        tagmap = {"pending": ("в очереди", AMBER), "failed": ("ошибка", REC)}
        for r in rows:
            _rc = _RCard(self.tk, self._hist_inner, radius=8)
            _rc.pack(fill="x", pady=4, padx=2)
            card = _rc.inner
            top = tk.Frame(card, bg=SURF); top.pack(fill="x", padx=10, pady=(8, 2))
            ts = time.strftime("%H:%M", time.localtime(r.ts))
            self._label(top, ts, color=MUT, size=9, bg=SURF).pack(side="left")
            if r.status in tagmap:
                label, col = tagmap[r.status]
                self._label(top, "· " + label, color=col, size=9, bg=SURF).pack(
                    side="left", padx=(6, 0))
            elif r.status == "cancelled":
                has_audio = bool(r.audio_path)
                hint = "· отменено · аудио ~60 с" if has_audio else "· отменено"
                self._label(top, hint, color=AMBER if has_audio else MUT,
                            size=9, bg=SURF).pack(side="left", padx=(6, 0))
            if r.status == "cancelled":
                body = "(запись отменена)"
            else:
                body = r.text if r.text else "(нет текста — можно повторить)"
            tk.Label(card, text=body, bg=SURF, fg=TXT if r.text else MUT,
                     font=("Segoe UI", 10), wraplength=360, justify="left"
                     ).pack(fill="x", padx=10, anchor="w")
            btns = tk.Frame(card, bg=SURF); btns.pack(fill="x", padx=10, pady=(4, 8))
            if r.text:
                self._btn(btns, "⧉ Копировать",
                          lambda t=r.text: self._copy_text(t)).pack(side="left")
            if r.status == "cancelled" and r.audio_path:
                self._btn(btns, "Сохранить аудио",
                          lambda i=r.id: self._keep_cancelled(i)).pack(side="left", padx=(6, 0))
            elif r.status in ("pending", "failed") and r.audio_path:
                self._btn(btns, "↻ Повторить",
                          lambda i=r.id: self._retry_one(i)).pack(side="left", padx=(6, 0))

    # ---------- status ----------
    def _on_status(self, state: State, message: str):
        try:
            self.root.after(0, lambda: self._apply_status(state, message))
        except Exception:
            pass

    def _chip_layout(self, label: str) -> None:
        """Reposition the dot (oval/square) so dot+text sit centered as a group."""
        try:
            import tkinter.font as tkfont
            tw = tkfont.Font(family="Segoe UI", size=11).measure(label)
        except Exception:
            tw = max(1, len(label) * 7)
        DOT_R, GAP, CHIP_W, CHIP_H = 5, 8, 132, 34
        x0 = (CHIP_W - DOT_R * 2 - GAP - tw) / 2
        dx, dy = x0 + DOT_R, CHIP_H // 2
        self._chip_cv.coords(self._chip_oval, dx - DOT_R, dy - DOT_R, dx + DOT_R, dy + DOT_R)
        self._chip_cv.coords(self._chip_sq, dx - 4, dy - 4, dx + 4, dy + 4)

    def _apply_status(self, state: State, message: str):
        color, label = _DOT.get(state, (GREEN, "Запись"))
        if state is State.IDLE:
            chip_fill, dot_fill, text_fill, outline = ACC, GREEN, "#ffffff", ""
        elif state is State.RECORDING:
            chip_fill, dot_fill, text_fill, outline = REC, None, "#ffffff", ""
        else:
            chip_fill, dot_fill, text_fill, outline = SURF, color, TXT, BORDER
        self._chip_cv.itemconfigure(self._chip_pill, fill=chip_fill, outline=outline)
        self._chip_cv.itemconfigure(self._chip_txt, text=label, fill=text_fill)
        self._chip_layout(label)
        if state is State.RECORDING:
            self._chip_cv.itemconfigure(self._chip_oval, state="hidden")
            self._chip_cv.itemconfigure(self._chip_sq, state="normal")
        else:
            self._chip_cv.itemconfigure(self._chip_oval, fill=dot_fill, state="normal")
            self._chip_cv.itemconfigure(self._chip_sq, state="hidden")
        self._set_status_text(message)
        if state is State.IDLE:
            self.root.after(900, self._refresh_history)

    def _set_status_text(self, text: str):
        self.status_lbl.configure(text=text)

    def _on_close(self):
        try:
            if self._hk:
                self._hk.stop()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    ControlPanel().run()


if __name__ == "__main__":
    main()
