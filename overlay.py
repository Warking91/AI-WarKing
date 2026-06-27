"""
Overlay kontrolni panel — transparentni, draggable, uvijek na vrhu.
Komunicira sa server.py putem HTTP (127.0.0.1:5000).

Pokretanje: python overlay.py
"""

import tkinter as tk
import threading
import time
import json
import urllib.request
import urllib.error
import subprocess
import sys
import os

SERVER_URL = "http://127.0.0.1:5000"

# ── Stanja ────────────────────────────────────────────────────────────────────
STATE_RUNNING  = "RADI"
STATE_PAUSED   = "PAUZIRAN"
STATE_STOPPED  = "STOPIRAN"
STATE_ERROR    = "GREŠKA"
STATE_OFFLINE  = "OFFLINE"
STATE_STARTING = "POKRETANJE..."

# ── Boje po stanju ────────────────────────────────────────────────────────────
STATE_COLORS = {
    STATE_RUNNING:  "#00e676",   # zelena
    STATE_PAUSED:   "#ffab40",   # narandžasta
    STATE_STOPPED:  "#90a4ae",   # siva
    STATE_ERROR:    "#ff5252",   # crvena
    STATE_OFFLINE:  "#546e7a",   # tamno siva
    STATE_STARTING: "#40c4ff",   # plava
}

BG_MAIN   = "#0d0d0d"
BG_BTN    = "#1a1a1a"
FG_MAIN   = "#e0e0e0"
ACCENT    = "#007acc"

class OverlayApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Control")
        self.root.overrideredirect(True)          # bez chrome/titlebar
        self.root.wm_attributes("-topmost", True) # uvijek na vrhu
        self.root.wm_attributes("-alpha", 0.92)   # 92% opacitet
        self.root.configure(bg=BG_MAIN)

        # Pozicija — desni gornji kut
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"230x220+{sw-250}+20")

        self._drag_x = 0
        self._drag_y = 0

        self.state       = STATE_STARTING
        self.llm_status  = "?"
        self.session_id  = "?"
        self.msg_count   = 0
        self.paused      = False
        self.error_msg   = ""
        self._running    = True

        self._build_ui()
        self._start_monitor()
        self.root.mainloop()

    # ── UI ─────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = self.root

        # Drag bindings na cijeli prozor
        root.bind("<ButtonPress-1>",   self._on_drag_start)
        root.bind("<B1-Motion>",       self._on_drag_move)

        # ── Header ─────────────────────────────────────────────────────────
        hdr = tk.Frame(root, bg="#111111", height=28)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        hdr.bind("<ButtonPress-1>", self._on_drag_start)
        hdr.bind("<B1-Motion>",     self._on_drag_move)

        tk.Label(hdr, text="⚡ AI WarKing", bg="#111111", fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=8)

        tk.Button(hdr, text="✕", bg="#111111", fg="#666",
                  activebackground="#ff5252", activeforeground="#fff",
                  relief="flat", bd=0, font=("Segoe UI", 9),
                  cursor="hand2",
                  command=self._close).pack(side="right", padx=4)

        tk.Button(hdr, text="—", bg="#111111", fg="#666",
                  activebackground="#333", activeforeground="#fff",
                  relief="flat", bd=0, font=("Segoe UI", 9),
                  cursor="hand2",
                  command=self._minimize).pack(side="right")

        # ── Status indikator ───────────────────────────────────────────────
        status_frame = tk.Frame(root, bg=BG_MAIN)
        status_frame.pack(fill="x", padx=10, pady=(8, 2))

        self.dot_lbl = tk.Label(status_frame, text="●", font=("Segoe UI", 16),
                                 bg=BG_MAIN, fg=STATE_COLORS[STATE_STARTING])
        self.dot_lbl.pack(side="left")

        self.state_lbl = tk.Label(status_frame, text=STATE_STARTING,
                                   bg=BG_MAIN, fg=STATE_COLORS[STATE_STARTING],
                                   font=("Segoe UI", 11, "bold"))
        self.state_lbl.pack(side="left", padx=6)

        # ── Info linije ────────────────────────────────────────────────────
        info_frame = tk.Frame(root, bg=BG_MAIN)
        info_frame.pack(fill="x", padx=10, pady=2)

        self.llm_lbl = tk.Label(info_frame, text="Memory: ...",
                                 bg=BG_MAIN, fg="#607d8b",
                                 font=("Consolas", 8), anchor="w")
        self.llm_lbl.pack(fill="x")

        self.sess_lbl = tk.Label(info_frame, text="Sesija: ...",
                                  bg=BG_MAIN, fg="#607d8b",
                                  font=("Consolas", 8), anchor="w")
        self.sess_lbl.pack(fill="x")

        self.msg_lbl = tk.Label(info_frame, text="Poruke: 0",
                                 bg=BG_MAIN, fg="#607d8b",
                                 font=("Consolas", 8), anchor="w")
        self.msg_lbl.pack(fill="x")

        self.err_lbl = tk.Label(info_frame, text="",
                                 bg=BG_MAIN, fg="#ff5252",
                                 font=("Consolas", 7), anchor="w", wraplength=200)
        self.err_lbl.pack(fill="x")

        # ── Separator ─────────────────────────────────────────────────────
        tk.Frame(root, bg="#222", height=1).pack(fill="x", padx=6, pady=4)

        # ── Kontrolni gumbi ────────────────────────────────────────────────
        btn_frame = tk.Frame(root, bg=BG_MAIN)
        btn_frame.pack(fill="x", padx=8, pady=4)

        self.pause_btn = self._mk_btn(btn_frame, "⏸ Pauza",   self._toggle_pause, "#e65100")
        self.stop_btn  = self._mk_btn(btn_frame, "⏹ Stop",    self._stop_server,  "#b71c1c")
        self.reset_btn = self._mk_btn(btn_frame, "↺ Reset",   self._reset_server, "#1565c0")

        self.pause_btn.pack(side="left", padx=2, expand=True, fill="x")
        self.stop_btn.pack( side="left", padx=2, expand=True, fill="x")
        self.reset_btn.pack(side="left", padx=2, expand=True, fill="x")

        btn_frame2 = tk.Frame(root, bg=BG_MAIN)
        btn_frame2.pack(fill="x", padx=8, pady=(0, 6))

        self._mk_btn(btn_frame2, "🔁 Restart sve",  self._restart_all,  "#2e7d32").pack(
            fill="x", pady=2)

    def _mk_btn(self, parent, text, cmd, hover_color="#333"):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=BG_BTN, fg=FG_MAIN,
                      activebackground=hover_color, activeforeground="#fff",
                      relief="flat", bd=0, pady=5,
                      font=("Segoe UI", 8, "bold"), cursor="hand2")
        b.bind("<Enter>", lambda e, c=hover_color: b.config(bg=c))
        b.bind("<Leave>", lambda e: b.config(bg=BG_BTN))
        return b

    # ── Drag ──────────────────────────────────────────────────────────────────
    def _on_drag_start(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _on_drag_move(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    # ── UI update (thread-safe) ───────────────────────────────────────────────
    def _set_state(self, state, error=""):
        self.state = state
        self.error_msg = error
        color = STATE_COLORS.get(state, "#fff")

        def _do():
            self.dot_lbl.config(fg=color)
            self.state_lbl.config(text=state, fg=color)
            self.err_lbl.config(text=error[:80] if error else "")
            # Pauza dugme tekst
            if state == STATE_PAUSED:
                self.pause_btn.config(text="▶ Nastavi")
            else:
                self.pause_btn.config(text="⏸ Pauza")
            # Gumbi disabled ako server nije online
            s = "normal" if state not in (STATE_OFFLINE, STATE_STOPPED) else "disabled"
            self.pause_btn.config(state=s)
            self.stop_btn.config(state=s)
            self.reset_btn.config(state=s)

        self.root.after(0, _do)

    def _update_info(self, llm, session_id, msg_count):
        def _do():
            self.llm_lbl.config(text=f"LLM: {llm}")
            self.sess_lbl.config(text=f"Sesija: {session_id[:8] if session_id else '?'}")
            self.msg_lbl.config(text=f"Poruke: {msg_count}")
        self.root.after(0, _do)

    # ── HTTP helpers ──────────────────────────────────────────────────────────
    def _get(self, path, timeout=4):
        try:
            req = urllib.request.Request(f"{SERVER_URL}{path}")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception:
            return None

    def _post(self, path, body=None, timeout=6):
        try:
            data = json.dumps(body or {}).encode()
            req = urllib.request.Request(
                f"{SERVER_URL}{path}", data=data,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception:
            return None

    # ── Monitor thread ────────────────────────────────────────────────────────
    def _start_monitor(self):
        def _loop():
            while self._running:
                r = self._get("/status")
                if r is None:
                    self._set_state(STATE_OFFLINE)
                else:
                    if self.paused:
                        self._set_state(STATE_PAUSED)
                    elif r.get("error"):
                        self._set_state(STATE_ERROR, r["error"])
                    else:
                        self._set_state(STATE_RUNNING)

                    mem = r.get("memory_count", 0)
                    self._update_info(
                        f"memory: {mem}/20",
                        r.get("session_id", "?"),
                        r.get("session_messages", 0)
                    )
                time.sleep(2)
        threading.Thread(target=_loop, daemon=True).start()

    # ── Kontrole ──────────────────────────────────────────────────────────────
    def _toggle_pause(self):
        if self.state == STATE_OFFLINE:
            return
        self.paused = not self.paused
        action = "pause" if self.paused else "resume"
        self._post(f"/control/{action}")
        self._set_state(STATE_PAUSED if self.paused else STATE_RUNNING)

    def _stop_server(self):
        if self.state == STATE_OFFLINE:
            return
        self._post("/control/stop")
        self._set_state(STATE_STOPPED)

    def _reset_server(self):
        r = self._post("/control/reset")
        if r:
            self.paused = False
            self._set_state(STATE_RUNNING)
        else:
            self._set_state(STATE_ERROR, "Reset nije uspio — server offline?")

    def _restart_all(self):
        self._set_state(STATE_STARTING)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        bat = os.path.join(script_dir, "start_all.bat")
        if os.path.exists(bat):
            subprocess.Popen(["cmd", "/c", bat], cwd=script_dir,
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            self._set_state(STATE_ERROR, "start_all.bat nije pronađen.")

    def _minimize(self):
        self.root.withdraw()
        # Vrati se nakon 3s
        self.root.after(3000, self.root.deiconify)

    def _close(self):
        self._running = False
        self.root.destroy()


if __name__ == "__main__":
    OverlayApp()
