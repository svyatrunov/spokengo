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

# palette
BG = "#161b29"; BAR = "#11151f"; SURF = "#1b2130"; SURF2 = "#262d40"
BORDER = "#2a3147"; BORDER2 = "#343c56"
TXT = "#eef1f7"; MUT = "#9aa3bd"; SUB = "#c4cadb"
ACC = "#5b5ef0"; ACC_H = "#6d70f4"; REC = "#ef4444"; REC_H = "#f26d6d"
GREEN = "#5fce86"; AMBER = "#f6be4f"; BLUE = "#5b8def"

_DOT = {
    State.IDLE: (GREEN, "Готов"),
    State.RECORDING: (REC, "Запись"),
    State.TRANSCRIBING: (AMBER, "Распознаю"),
    State.INJECTING: (BLUE, "Вставка"),
    State.ERROR: (REC, "Ошибка"),
}


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
        self._hover = False
        self._btn_imgs = {}

        self.root = tk.Tk()
        self.root.title("SpokenGo")
        self.root.geometry("452x600")
        self.root.minsize(430, 560)
        self.root.configure(bg=BG)
        try:
            import os
            ico = os.path.join(os.path.dirname(__file__), "assets", "spokengo.ico")
            if os.path.exists(ico):
                self.root.iconbitmap(ico)
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

        self._refresh_history()
        self._refresh_key_status()
        self._render_hero()
        self._on_status(State.IDLE, "Готов. Нажмите Запись или хоткей")
        self._start_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- small helpers ----------
    def _card(self, parent):
        f = self.tk.Frame(parent, bg=SURF, highlightthickness=1,
                          highlightbackground=BORDER, highlightcolor=BORDER)
        return f

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
        bar = self.tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=20, pady=(18, 4))
        self._label(bar, "SpokenGo", size=19, bold=True).pack(side="left")
        chip = self.tk.Frame(bar, bg=SURF, highlightthickness=1,
                             highlightbackground=BORDER)
        chip.pack(side="right")
        self.dot = self.tk.Canvas(chip, width=10, height=10, bg=SURF,
                                  highlightthickness=0)
        self._dot_id = self.dot.create_oval(1, 1, 9, 9, fill=GREEN, outline="")
        self.dot.pack(side="left", padx=(10, 6), pady=5)
        self.state_lbl = self._label(chip, "Готов", color=MUT, size=10, bg=SURF)
        self.state_lbl.pack(side="left", padx=(0, 11))

    # ---------- hero record button ----------
    def _build_hero(self):
        wrap = self.tk.Frame(self.root, bg=BG)
        wrap.pack(fill="x", padx=20, pady=(10, 4))
        self.hero = self.tk.Label(wrap, bg=BG, cursor="hand2", bd=0)
        self.hero.pack(fill="x")
        self.hero.bind("<Button-1>", lambda e: self._on_record_button())
        self.hero.bind("<Enter>", lambda e: self._set_hover(True))
        self.hero.bind("<Leave>", lambda e: self._set_hover(False))
        row = self.tk.Frame(wrap, bg=BG)
        row.pack(fill="x", pady=(8, 0))
        self.status_lbl = self._label(row, "", color=MUT, size=9)
        self.status_lbl.pack(side="left", anchor="w")
        self._btn(row, "⧉ Копировать последний", self._copy_last).pack(side="right")

    def _hero_image(self, text, sub, bg):
        from PIL import Image, ImageDraw, ImageTk
        w, h, s, r = 412, 64, 2, 14
        im = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([0, 0, w * s - 1, h * s - 1], radius=r * s,
                            fill=self._hex(bg) + (255,))
        f1 = _load_font(15 * s); f2 = _load_font(10 * s)
        dot = 10 * s; gap = 10 * s
        tw = d.textlength(text, font=f1)
        total = dot + gap + tw
        x = (w * s - total) // 2; cy = int(h * s * 0.40)
        d.ellipse([x, cy - dot // 2, x + dot, cy + dot // 2], fill=(255, 255, 255, 255))
        d.text((x + dot + gap, cy), text, font=f1, fill=(255, 255, 255, 255), anchor="lm")
        d.text((w * s // 2, int(h * s * 0.73)), sub, font=f2,
               fill=(255, 255, 255, 205), anchor="mm")
        im = im.resize((w, h), Image.LANCZOS)
        return ImageTk.PhotoImage(im)

    @staticmethod
    def _hex(h):
        h = h.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    def _render_hero(self):
        rec = self.ctrl.state.state is State.RECORDING
        hk = self.ctrl.cfg.hotkey
        if rec:
            text, sub = "Стоп", "Enter — отправить   ·   Esc — отмена"
            bg = REC_H if self._hover else REC
        else:
            text, sub = "Запись", f"{hk}   ·   стоп Enter   ·   отмена Esc"
            bg = ACC_H if self._hover else ACC
        key = (text, sub, bg)
        if key not in self._btn_imgs:
            try:
                self._btn_imgs[key] = self._hero_image(text, sub, bg)
            except Exception:
                self._btn_imgs[key] = None
        img = self._btn_imgs[key]
        if img is not None:
            self.hero.configure(image=img)
            self.hero.image = img

    def _set_hover(self, on):
        self._hover = on
        self._render_hero()

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

    # ---------- settings ----------
    def _build_settings(self):
        tk = self.tk
        s = tk.Frame(self._body, bg=BG)
        self._settings = s

        # API key — single row: [entry] [Сохранить] [×]
        key_card = self._card(s); key_card.pack(fill="x", pady=(0, 11))
        kp = tk.Frame(key_card, bg=SURF); kp.pack(fill="x", padx=13, pady=12)
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

        # hotkey — applies immediately on capture
        hk_card = self._card(s); hk_card.pack(fill="x", pady=(0, 11))
        hp = tk.Frame(hk_card, bg=SURF); hp.pack(fill="x", padx=13, pady=12)
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

        # model — instant-apply segmented control
        md_card = self._card(s); md_card.pack(fill="x")
        mp = tk.Frame(md_card, bg=SURF); mp.pack(fill="x", padx=13, pady=12)
        self._label(mp, "Модель", color=SUB, size=10, bg=SURF).pack(anchor="w")
        seg = tk.Frame(mp, bg=BORDER2); seg.pack(fill="x", pady=(9, 0))
        self._seg = {}
        options = [("Turbo · быстрее", "whisper-large-v3-turbo"),
                   ("Large v3 · точнее", "whisper-large-v3")]
        for i, (label, mid) in enumerate(options):
            b = tk.Label(seg, text=label, bg=SURF2, fg=MUT, font=("Segoe UI", 10),
                         cursor="hand2", pady=7)
            b.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 1, 0))
            seg.grid_columnconfigure(i, weight=1)
            b.bind("<Button-1>", lambda e, m=mid: self._select_model(m))
            self._seg[mid] = b
        self._update_seg()

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
            side="left", expand=True, fill="x", padx=(4, 0))

        wrap = tk.Frame(h, bg=BG); wrap.pack(fill="both", expand=True)
        self._hist_canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=self._hist_canvas.yview,
                          troughcolor=BG, bg=SURF2, activebackground=ACC, bd=0,
                          highlightthickness=0)
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
        self._hist_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._hist_canvas.yview_scroll(int(-e.delta / 120), "units"))

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
                self.key_var.set(self.ctrl.stored_key())   # masked dots, not empty
        else:
            self.key_status.configure(text="● не задан", fg=AMBER)
            self.key_var.set("")

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

    def _retry_queue(self):
        import threading
        threading.Thread(target=lambda: (self.ctrl.retry_queue(),
                                         self.root.after(0, self._refresh_history)),
                         daemon=True).start()

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
            card = tk.Frame(self._hist_inner, bg=SURF, highlightthickness=1,
                            highlightbackground=BORDER)
            card.pack(fill="x", pady=4, padx=2)
            top = tk.Frame(card, bg=SURF); top.pack(fill="x", padx=10, pady=(8, 2))
            ts = time.strftime("%H:%M", time.localtime(r.ts))
            self._label(top, ts, color=MUT, size=9, bg=SURF).pack(side="left")
            if r.status in tagmap:
                label, col = tagmap[r.status]
                self._label(top, "· " + label, color=col, size=9, bg=SURF).pack(
                    side="left", padx=(6, 0))
            body = r.text if r.text else "(нет текста — можно повторить)"
            tk.Label(card, text=body, bg=SURF, fg=TXT if r.text else MUT,
                     font=("Segoe UI", 10), wraplength=360, justify="left"
                     ).pack(fill="x", padx=10, anchor="w")
            btns = tk.Frame(card, bg=SURF); btns.pack(fill="x", padx=10, pady=(4, 8))
            if r.text:
                self._btn(btns, "⧉ Копировать",
                          lambda t=r.text: self._copy_text(t)).pack(side="left")
            if r.status in ("pending", "failed") and r.audio_path:
                self._btn(btns, "↻ Повторить",
                          lambda i=r.id: self._retry_one(i)).pack(side="left", padx=(6, 0))

    # ---------- status ----------
    def _on_status(self, state: State, message: str):
        self.root.after(0, lambda: self._apply_status(state, message))

    def _apply_status(self, state: State, message: str):
        color, label = _DOT.get(state, (GREEN, "Готов"))
        self.dot.itemconfig(self._dot_id, fill=color)
        self.state_lbl.configure(text=label)
        self._set_status_text(message)
        self._render_hero()
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
