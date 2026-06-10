import os, json, time, math, random, threading
import tkinter as tk
from collections import deque
from PIL import Image, ImageTk, ImageDraw
import sys
from pathlib import Path
from datetime import datetime


def _get_fft_bands():
    """Lit les bandes FFT depuis main.py (import lazy pour éviter les imports circulaires)."""
    try:
        import main as _m
        with _m._fft_lock:
            return list(_m._fft_bands)
    except Exception:
        return None


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR   = get_base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

SYSTEM_NAME = "J.A.R.V.I.S"
MODEL_BADGE = "MARK L - STARK INDUSTRIES"

C_BG     = "#000508"
C_PRI    = "#00e5ff"
C_MID    = "#0088aa"
C_DIM    = "#004455"
C_DIMMER = "#001a22"
C_ACC    = "#ff7700"
C_ACC2   = "#ffb700"
C_TEXT   = "#aaffff"
C_PANEL  = "#000a10"
C_GREEN  = "#00ff88"
C_RED    = "#ff3333"
C_MUTED  = "#ff2244"
C_SLEEP  = "#331166"

FONT_MAIN = "Consolas"


class JarvisUI:
    def __init__(self, face_path, size=None):
        self.root = tk.Tk()
        self.root.title("J.A.R.V.I.S — MARK XXXV")
        self.root.resizable(False, False)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        W  = min(sw, 984)
        H  = min(sh, 816)
        self.root.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")
        self.root.configure(bg=C_BG)

        self.W = W
        self.H = H

        self.FACE_SZ = min(int(H * 0.54), 400)
        self.FCX     = W // 2
        self.FCY     = int(H * 0.13) + self.FACE_SZ // 2

        # ── Durum ────────────────────────────────────────────────────────────
        self.speaking     = False
        self.muted        = False          # Mute flag — main.py okur
        self.scale        = 1.0
        self.target_scale = 1.0
        self.halo_a       = 60.0
        self.target_halo  = 60.0
        self.last_t       = time.time()
        self.tick         = 0
        self.scan_angle   = 0.0
        self.scan2_angle  = 180.0
        self.rings_spin   = [0.0, 120.0, 240.0]
        self.pulse_r      = [0.0, self.FACE_SZ * 0.26, self.FACE_SZ * 0.52]
        self.status_text  = "INITIALISING"
        self.status_blink = True

        # Dışarıdan set edilebilen durum (main.py çağırır)
        # Değerler: "LISTENING" | "SPEAKING" | "THINKING" | "MUTED" | "ONLINE"
        self._jarvis_state = "INITIALISING"

        self.typing_queue = deque()
        self.is_typing    = False

        # Klavye girişinden komutu iletmek için callback — main.py atar
        self.on_text_command = None

        self._face_pil         = None
        self._has_face         = False
        self._face_scale_cache = None
        self._load_face(face_path)

        # ── System Monitor, Alerts and Telegram Status ────────────────────────
        self.notifications = deque(maxlen=8)
        self.telegram_active = False
        self.telegram_messages_count = 0
        try:
            from core.system_monitor import get_monitor
            self.monitor = get_monitor()
        except ImportError:
            self.monitor = None

        # ── Canvas (arka plan animasyon) ─────────────────────────────────────
        self.bg = tk.Canvas(self.root, width=W, height=H,
                            bg=C_BG, highlightthickness=0)
        self.bg.place(x=0, y=0)

        # ── Log alanı ────────────────────────────────────────────────────────
        LW = int(W * 0.72)
        LH = 110
        LOG_Y = H - LH - 80   # klavye inputu için yukarı çektik
        self.log_frame = tk.Frame(self.root, bg=C_PANEL,
                                  highlightbackground=C_MID,
                                  highlightthickness=1)
        self.log_frame.place(x=(W - LW) // 2, y=LOG_Y, width=LW, height=LH)
        self.log_text = tk.Text(self.log_frame, fg=C_TEXT, bg=C_PANEL,
                                insertbackground=C_TEXT, borderwidth=0,
                                wrap="word", font=(FONT_MAIN, 10), padx=10, pady=6)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
        self.log_text.tag_config("you", foreground="#e8e8e8")
        self.log_text.tag_config("ai",  foreground=C_PRI)
        self.log_text.tag_config("sys", foreground=C_ACC2)
        self.log_text.tag_config("err", foreground=C_RED)

        # ── Klavye girişi ─────────────────────────────────────────────────────
        INPUT_Y = LOG_Y + LH + 6
        self._build_input_bar(LW, INPUT_Y)

        # ── Mute butonu ───────────────────────────────────────────────────────
        self._build_mute_button()

        # ── Web Agent butonu ──────────────────────────────────────────────────
        self.show_web_agent = False
        self._build_web_button()

        # ── Memory butonu ─────────────────────────────────────────────────────
        self._build_memory_button()

        # ── F4 kısayolu ───────────────────────────────────────────────────────
        self.root.bind("<F4>", lambda e: self._toggle_mute())

        # ── API key ───────────────────────────────────────────────────────────
        self._api_key_ready = self._api_keys_exist()
        if not self._api_key_ready:
            self._show_setup_ui()

        self._animate()
        self.root.protocol("WM_DELETE_WINDOW", lambda: os._exit(0))

    # ── Mute butonu ───────────────────────────────────────────────────────────

    def _build_mute_button(self):
        """Sol alt köşeye mute butonu yerleştirir."""
        BTN_W, BTN_H = 110, 32
        BTN_X = 18
        BTN_Y = self.H - 70

        self._mute_canvas = tk.Canvas(
            self.root, width=BTN_W, height=BTN_H,
            bg=C_BG, highlightthickness=0, cursor="hand2"
        )
        self._mute_canvas.place(x=BTN_X, y=BTN_Y)
        self._mute_canvas.bind("<Button-1>", lambda e: self._toggle_mute())
        self._draw_mute_button()

    def _draw_mute_button(self):
        c = self._mute_canvas
        c.delete("all")
        if self.muted:
            border = C_MUTED
            fill   = "#1a0008"
            icon   = "🔇"
            label  = " MUTED"
            fg     = C_MUTED
        else:
            border = C_MID
            fill   = C_PANEL
            icon   = "🎙"
            label  = " LIVE"
            fg     = C_GREEN

        c.create_rectangle(0, 0, 110, 32, outline=border, fill=fill, width=1)
        c.create_text(55, 16, text=f"{icon}{label}",
                      fill=fg, font=(FONT_MAIN, 10, "bold"))

    def _toggle_mute(self):
        self.muted = not self.muted
        self._draw_mute_button()
        if self.muted:
            self.set_state("MUTED")
            self.write_log("SYS: Microphone muted.")
        else:
            self.set_state("LISTENING")
            self.write_log("SYS: Microphone active.")

    # ── Web Agent butonu ──────────────────────────────────────────────────────

    def _build_web_button(self):
        self.web_window = None
        self.web_log_text = None

        BTN_W, BTN_H = 110, 32
        BTN_X = 18
        BTN_Y = self.H - 108

        self._web_canvas = tk.Canvas(
            self.root, width=BTN_W, height=BTN_H,
            bg=C_BG, highlightthickness=0, cursor="hand2"
        )
        self._web_canvas.place(x=BTN_X, y=BTN_Y)
        self._web_canvas.bind("<Button-1>", lambda e: self._toggle_web_agent())
        self._draw_web_button()

    def _draw_web_button(self):
        c = self._web_canvas
        c.delete("all")
        if self.show_web_agent:
            border = C_ACC
            fill   = "#1a0a00"
            icon   = "🌐"
            label  = " VISIBLE"
            fg     = C_ACC
        else:
            border = C_DIM
            fill   = C_PANEL
            icon   = "🌐"
            label  = " HIDDEN"
            fg     = C_DIM

        c.create_rectangle(0, 0, 110, 32, outline=border, fill=fill, width=1)
        c.create_text(55, 16, text=f"{icon}{label}", fill=fg, font=(FONT_MAIN, 10, "bold"))

    def _toggle_web_agent(self):
        self.show_web_agent = not self.show_web_agent
        self._draw_web_button()
        if self.show_web_agent:
            self.write_log("SYS: Web Agent visual mode ENABLED.")
            self._open_web_window()
        else:
            self.write_log("SYS: Web Agent visual mode DISABLED.")
            self._close_web_window()

    def _open_web_window(self):
        if self.web_window is not None:
            return
        self.web_window = tk.Toplevel(self.root)
        self.web_window.title("J.A.R.V.I.S — WEB AGENT FEED")
        self.web_window.geometry("450x350")
        self.web_window.configure(bg=C_BG)
        self.web_window.protocol("WM_DELETE_WINDOW", self._toggle_web_agent)

        self.web_log_text = tk.Text(
            self.web_window, fg=C_TEXT, bg=C_PANEL,
            insertbackground=C_TEXT, borderwidth=0,
            wrap="word", font=(FONT_MAIN, 9), padx=10, pady=10
        )
        self.web_log_text.pack(fill="both", expand=True)
        self.web_log_text.configure(state="disabled")
        
        self.write_web_log(">>> WEB AGENT INITIALIZED.\n>>> WAITING FOR BACKGROUND TASKS...\n")

    def _close_web_window(self):
        if self.web_window is not None:
            self.web_window.destroy()
            self.web_window = None
            self.web_log_text = None

    def write_web_log(self, text: str):
        if not self.web_log_text:
            return
        def _update():
            if self.web_log_text:
                self.web_log_text.configure(state="normal")
                self.web_log_text.insert(tk.END, text + "\n")
                self.web_log_text.see(tk.END)
                self.web_log_text.configure(state="disabled")
        self.root.after(0, _update)

    # ── Klavye girişi ─────────────────────────────────────────────────────────

    def _build_input_bar(self, lw: int, y: int):
        """Log'un hemen altına tek satır metin giriş alanı."""
        x0    = (self.W - lw) // 2
        BTN_W = 70
        INP_W = lw - BTN_W - 4

        self._input_var = tk.StringVar()

        self._input_entry = tk.Entry(
            self.root,
            textvariable=self._input_var,
            fg=C_TEXT, bg="#000d12",
            insertbackground=C_TEXT,
            borderwidth=0,
            font=(FONT_MAIN, 10),
            highlightthickness=1,
            highlightbackground=C_DIM,
            highlightcolor=C_PRI,
        )
        self._input_entry.place(x=x0, y=y, width=INP_W, height=28)
        self._input_entry.bind("<Return>", self._on_input_submit)
        self._input_entry.bind("<KP_Enter>", self._on_input_submit)

        self._send_btn = tk.Button(
            self.root,
            text="SEND ▸",
            command=self._on_input_submit,
            fg=C_PRI, bg=C_PANEL,
            activeforeground=C_BG, activebackground=C_PRI,
            font=(FONT_MAIN, 9, "bold"),
            borderwidth=0, cursor="hand2",
            highlightthickness=1,
            highlightbackground=C_MID,
        )
        self._send_btn.place(x=x0 + INP_W + 4, y=y, width=BTN_W, height=28)

    def _on_input_submit(self, event=None):
        text = self._input_var.get().strip()
        if not text:
            return
        self._input_var.set("")
        self.write_log(f"You: {text}")
        if self.on_text_command:
            threading.Thread(
                target=self.on_text_command,
                args=(text,),
                daemon=True
            ).start()

    # ── Durum yönetimi ────────────────────────────────────────────────────────

    def set_state(self, state: str):
        """
        main.py'den çağrılır.
        state: LISTENING | SPEAKING | THINKING | MUTED | ONLINE | PROCESSING | SLEEPING
        """
        self._jarvis_state = state
        if state == "MUTED":
            self.status_text = "MUTED"
            self.speaking    = False
        elif state == "SPEAKING":
            self.status_text = "SPEAKING"
            self.speaking    = True
        elif state == "THINKING":
            self.status_text = "THINKING"
            self.speaking    = False
        elif state == "LISTENING":
            self.status_text = "LISTENING"
            self.speaking    = False
        elif state == "PROCESSING":
            self.status_text = "PROCESSING"
            self.speaking    = False
        elif state == "SLEEPING":
            self.status_text = "SLEEPING"
            self.speaking    = False
        else:
            self.status_text = "ONLINE"
            self.speaking    = False

    # ── Yüz yükleme ───────────────────────────────────────────────────────────

    def _load_face(self, path):
        FW = self.FACE_SZ
        try:
            img  = Image.open(path).convert("RGBA").resize((FW, FW), Image.LANCZOS)
            mask = Image.new("L", (FW, FW), 0)
            ImageDraw.Draw(mask).ellipse((2, 2, FW - 2, FW - 2), fill=255)
            img.putalpha(mask)
            self._face_pil = img
            self._has_face = True
        except Exception:
            self._has_face = False

    @staticmethod
    def _ac(r, g, b, a):
        f = a / 255.0
        return f"#{int(r*f):02x}{int(g*f):02x}{int(b*f):02x}"

    # ── Animasyon döngüsü ─────────────────────────────────────────────────────

    def _animate(self):
        self.tick += 1
        t   = self.tick
        now = time.time()

        # Définir sleeping EN PREMIER pour toutes les branches suivantes
        sleeping = (self._jarvis_state == "SLEEPING")

        if now - self.last_t > (0.14 if self.speaking else (1.5 if sleeping else 0.55)):
            if self.speaking:
                self.target_scale = random.uniform(1.05, 1.11)
                self.target_halo  = random.uniform(138, 182)
            elif self.muted:
                self.target_scale = random.uniform(0.998, 1.001)
                self.target_halo  = random.uniform(20, 32)
            elif sleeping:
                # Respiration lente (veille)
                self.target_scale = random.uniform(0.999, 1.002)
                self.target_halo  = random.uniform(12, 22)
            else:
                self.target_scale = random.uniform(1.001, 1.007)
                self.target_halo  = random.uniform(50, 68)
            self.last_t = now

        sp = 0.35 if self.speaking else 0.16
        self.scale  += (self.target_scale - self.scale) * sp
        self.halo_a += (self.target_halo  - self.halo_a) * sp

        for i, spd in enumerate([1.2, -0.8, 1.9] if self.speaking else
                                 [0.1, -0.06, 0.15] if sleeping else
                                 [0.5, -0.3, 0.82]):
            self.rings_spin[i] = (self.rings_spin[i] + spd) % 360

        spin_spd = 2.8 if self.speaking else (0.3 if sleeping else 1.2)
        self.scan_angle  = (self.scan_angle  + spin_spd)       % 360
        self.scan2_angle = (self.scan2_angle + (-spin_spd * 0.6)) % 360

        pspd  = 3.8 if self.speaking else (0.6 if sleeping else 1.8)
        limit = self.FACE_SZ * 0.72
        new_p = [r + pspd for r in self.pulse_r if r + pspd < limit]
        if len(new_p) < 3 and random.random() < (0.06 if self.speaking else 0.022):
            new_p.append(0.0)
        self.pulse_r = new_p

        if t % 40 == 0:
            self.status_blink = not self.status_blink

        self._draw()
        self.root.after(16, self._animate)

    # ── Çizim ─────────────────────────────────────────────────────────────────

    def _draw(self):
        c    = self.bg
        W, H = self.W, self.H
        t    = self.tick
        FCX  = self.FCX
        FCY  = self.FCY
        FW   = self.FACE_SZ
        c.delete("all")

        # Arka plan grid (HUD Crosses)
        for x in range(0, W, 50):
            for y in range(0, H, 50):
                c.create_line(x-2, y, x+3, y, fill=C_DIMMER, width=1)
                c.create_line(x, y-2, x, y+3, fill=C_DIMMER, width=1)

        # ── Diagnostics Panel (Top Right) ────────────────────────────────────
        if hasattr(self, "monitor") and self.monitor:
            try:
                diag = self.monitor.get_ui_data()
            except Exception:
                diag = {}
        else:
            diag = {}

        cpu = diag.get("cpu_pct") or 0.0
        ram = diag.get("ram_pct") or 0.0
        gpu = diag.get("gpu_pct")
        cpu_t = diag.get("cpu_temp")
        gpu_t = diag.get("gpu_temp")

        dx = W - 220
        dy = 85
        
        c.create_text(dx, dy, text="📊 SYSTEM DIAGNOSTICS", fill=C_PRI, font=(FONT_MAIN, 10, "bold"), anchor="w")
        c.create_line(dx, dy + 12, dx + 200, dy + 12, fill=C_MID, width=1)
        
        metrics = [("CPU", cpu), ("RAM", ram)]
        if gpu is not None:
            metrics.append(("GPU", gpu))
            
        bar_y = dy + 25
        for label, val in metrics:
            c.create_text(dx, bar_y, text=f"{label}: {val}%", fill=C_TEXT, font=(FONT_MAIN, 9), anchor="w")
            bx = dx + 60
            c.create_line(bx, bar_y, bx + 120, bar_y, fill=C_DIMMER, width=8)
            if val > 0:
                bar_len = int(120 * (val / 100.0))
                col = C_PRI
                if val > 90:
                    col = C_RED
                elif val > 75:
                    col = C_ACC
                c.create_line(bx, bar_y, bx + bar_len, bar_y, fill=col, width=8)
            bar_y += 18
            
        temp_y = bar_y + 4
        temp_strs = []
        if cpu_t is not None:
            temp_strs.append(f"CPU: {cpu_t}°C")
        if gpu_t is not None:
            temp_strs.append(f"GPU: {gpu_t}°C")
        if temp_strs:
            c.create_text(dx, temp_y, text="Temp: " + " | ".join(temp_strs), fill=C_ACC2, font=(FONT_MAIN, 9), anchor="w")

        # ── Notifications Panel (Top Left) ────────────────────────────────────
        nx = 20
        ny = 85
        c.create_text(nx, ny, text="🔔 SYSTEM ALERTS", fill=C_ACC, font=(FONT_MAIN, 10, "bold"), anchor="w")
        c.create_line(nx, ny + 12, nx + 200, ny + 12, fill=C_ACC, width=1)
        
        if hasattr(self, "monitor") and self.monitor:
            try:
                alerts = self.monitor.get_alerts()
                for alert in alerts:
                    sev = alert.get("severity", "info")
                    icon = "ℹ️"
                    if sev == "critical":
                        icon = "🚨"
                    elif sev == "warning":
                        icon = "⚠️"
                    elif alert.get("type") == "drive_connected":
                        icon = "🔌"
                    elif alert.get("type") == "drive_disconnected":
                        icon = "⏏️"
                    self.add_notification(alert.get("message", ""), icon)
            except Exception:
                pass
                
        if not self.notifications:
            c.create_text(nx, ny + 25, text="All systems nominal.", fill=C_GREEN, font=(FONT_MAIN, 9), anchor="w")
        else:
            alert_y = ny + 25
            for timestamp, icon, text in list(self.notifications):
                time_str = timestamp.strftime("%H:%M:%S")
                display_text = f"[{time_str}] {icon} {text}"
                if len(display_text) > 34:
                    display_text = display_text[:31] + "..."
                c.create_text(nx, alert_y, text=display_text, fill=C_TEXT, font=(FONT_MAIN, 9), anchor="w")
                alert_y += 18

        # HUD Hex Data Sides
        hud_c = self._ac(0, 229, 255, int(self.halo_a * 0.7))
        for i in range(6):
            # Left panel
            c.create_text(25, FCY - 120 + i * 45, text=f"SYS.{i}: {random.randint(1000, 9999):04X}", fill=hud_c, font=(FONT_MAIN, 9), anchor="w")
            c.create_line(25, FCY - 110 + i * 45, 80, FCY - 110 + i * 45, fill=hud_c)
            # Right panel
            c.create_text(W - 25, FCY - 120 + i * 45, text=f"NET.{i}: {random.random():.3f}", fill=hud_c, font=(FONT_MAIN, 9), anchor="e")
            c.create_line(W - 80, FCY - 110 + i * 45, W - 25, FCY - 110 + i * 45, fill=hud_c)

        # Halo halkaları (Inner Core Halos)
        for r in range(int(FW * 0.54), int(FW * 0.28), -22):
            frac = 1.0 - (r - FW * 0.28) / (FW * 0.26)
            ga   = max(0, min(255, int(self.halo_a * 0.1 * frac)))
            if self.muted:
                c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r, outline=f"#{ga:02x}0011", width=2)
            else:
                c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r, outline=self._ac(0, 229, 255, ga), width=2)

        # Pulse dalgaları
        for pr in self.pulse_r:
            pa = max(0, int(220 * (1.0 - pr / (FW * 0.72))))
            r  = int(pr)
            if self.muted:
                c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r, outline=self._ac(255, 34, 68, pa // 3), width=2)
            else:
                c.create_oval(FCX-r, FCY-r, FCX+r, FCY+r, outline=self._ac(0, 229, 255, pa), width=2)

        # Dönen halkalar (Iron Man Complex Rotating Rings)
        for idx, (r_frac, w_ring, arc_l, gap, dash_pat) in enumerate([
                (0.47, 3, 110, 75, ()),
                (0.44, 1, 360, 0, (2, 4)), 
                (0.39, 2, 75, 55, ()),
                (0.35, 1, 360, 0, (8, 6)),
                (0.31, 2, 55, 38, ())]):
            ring_r = int(FW * r_frac)
            base_a = self.rings_spin[idx % len(self.rings_spin)]
            a_val  = max(0, min(255, int(self.halo_a * (1.0 - idx * 0.15))))
            col    = self._ac(255, 34, 68, a_val) if self.muted else self._ac(0, 229, 255, a_val)
            
            if dash_pat:
                c.create_oval(FCX-ring_r, FCY-ring_r, FCX+ring_r, FCY+ring_r, outline=col, width=w_ring, dash=dash_pat)
            else:
                for s in range(360 // (arc_l + gap)):
                    start = (base_a + s * (arc_l + gap)) % 360
                    c.create_arc(FCX-ring_r, FCY-ring_r, FCX+ring_r, FCY+ring_r, start=start, extent=arc_l, outline=col, width=w_ring, style="arc")

        # Tarama yayları
        sr      = int(FW * 0.49)
        scan_a  = min(255, int(self.halo_a * 1.5))
        arc_ext = 80 if self.speaking else 45
        scan_col = self._ac(255, 34, 68, scan_a) if self.muted else self._ac(0, 229, 255, scan_a)
        c.create_arc(FCX-sr, FCY-sr, FCX+sr, FCY+sr, start=self.scan_angle, extent=arc_ext, outline=scan_col, width=4, style="arc")
        c.create_arc(FCX-sr, FCY-sr, FCX+sr, FCY+sr, start=self.scan2_angle, extent=arc_ext, outline=self._ac(255, 119, 0, scan_a // 2), width=2, style="arc")

        # Derecelendirme işaretleri
        t_out = int(FW * 0.495)
        t_in  = int(FW * 0.472)
        a_mk  = self._ac(0, 229, 255, 180)
        for deg in range(0, 360, 5):
            rad = math.radians(deg)
            inn = t_in if deg % 15 == 0 else t_in + 6
            w = 2 if deg % 45 == 0 else 1
            c.create_line(FCX + t_out * math.cos(rad), FCY - t_out * math.sin(rad),
                          FCX + inn  * math.cos(rad), FCY - inn  * math.sin(rad),
                          fill=a_mk, width=w)

        # Crosshair HUD
        ch_r = int(FW * 0.52)
        gap  = int(FW * 0.18)
        ch_a = self._ac(0, 229, 255, int(self.halo_a * 0.6))
        for x1, y1, x2, y2 in [
                (FCX - ch_r, FCY, FCX - gap, FCY), (FCX + gap, FCY, FCX + ch_r, FCY),
                (FCX, FCY - ch_r, FCX, FCY - gap), (FCX, FCY + gap, FCX, FCY + ch_r)]:
            c.create_line(x1, y1, x2, y2, fill=ch_a, width=1)
            # Add target dots
            c.create_oval(x1-2, y1-2, x1+2, y1+2, fill=ch_a, outline="")

        # Köşe braketleri (Targeting Brackets)
        blen = 30
        bc   = self._ac(0, 229, 255, 220)
        hl = FCX - int(FW * 0.55); hr = FCX + int(FW * 0.55)
        ht = FCY - int(FW * 0.55); hb = FCY + int(FW * 0.55)
        for bx, by, sdx, sdy in [(hl, ht, 1, 1), (hr, ht, -1, 1),
                                   (hl, hb, 1, -1), (hr, hb, -1, -1)]:
            c.create_line(bx, by, bx + sdx * blen, by,            fill=bc, width=3)
            c.create_line(bx, by, bx,               by + sdy * blen, fill=bc, width=3)
            c.create_polygon(bx, by, bx + sdx * 8, by, bx, by + sdy * 8, fill=bc, outline="")

        # Yüz / orb (Arc Reactor Core)
        if self._has_face:
            fw = int(FW * self.scale)
            if (self._face_scale_cache is None or abs(self._face_scale_cache[0] - self.scale) > 0.004):
                scaled = self._face_pil.resize((fw, fw), Image.BILINEAR)
                tk_img = ImageTk.PhotoImage(scaled)
                self._face_scale_cache = (self.scale, tk_img)
            c.create_image(FCX, FCY, image=self._face_scale_cache[1])
        else:
            orb_r = int(FW * 0.28 * self.scale)
            orb_color = (255, 34, 68) if self.muted else (0, 180, 255)
            for i in range(8, 0, -1):
                r2   = int(orb_r * i / 8)
                frac = i / 8
                ga   = max(0, min(255, int(self.halo_a * 1.3 * frac)))
                cc = self._ac(int(orb_color[0]*frac + 25*(1-frac)),
                              int(orb_color[1]*frac + 50*(1-frac)),
                              int(orb_color[2]*frac + 60*(1-frac)), ga)
                c.create_oval(FCX-r2, FCY-r2, FCX+r2, FCY+r2, fill=cc, outline="")
            c.create_text(FCX, FCY, text="STARK", fill=self._ac(255, 255, 255, min(255, int(self.halo_a * 2))), font=(FONT_MAIN, 11, "bold"))

        # ── Header ────────────────────────────────────────────────────────────
        HDR = 62
        c.create_rectangle(0, 0, W, HDR, fill="#000a10", outline="")
        c.create_line(0, HDR, W, HDR, fill=C_MID, width=2)
        c.create_text(W // 2, 22, text="STARK INDUSTRIES // J.A.R.V.I.S", fill=C_PRI, font=(FONT_MAIN, 16, "bold"))
        c.create_text(W // 2, 44, text="TACTICAL INTELLIGENCE & HUD INTERFACE", fill=C_MID, font=(FONT_MAIN, 9))
        c.create_text(16, 31, text=MODEL_BADGE, fill=C_DIM, font=(FONT_MAIN, 9), anchor="w")
        c.create_text(W - 16, 31, text=time.strftime("%H:%M:%S"), fill=C_ACC, font=(FONT_MAIN, 14, "bold"), anchor="e")

        # ── Durum göstergesi ──────────────────────────────────────────────────
        sy = FCY + int(FW * 0.6) + 20

        if self.muted:
            stat, sc = "[ ⊘ SYSTEM MUTED ]", C_MUTED
        elif self._jarvis_state == "SLEEPING":
            sym = "[◌]" if self.status_blink else "[○]"
            stat, sc = f"{sym} STANDBY MODE", C_SLEEP
        elif self.speaking:
            stat, sc = "[●] TRANSMITTING", C_ACC
        elif self._jarvis_state == "THINKING":
            sym = "[◈]" if self.status_blink else "[◇]"
            stat, sc = f"{sym} ANALYZING", C_ACC2
        elif self._jarvis_state == "PROCESSING":
            sym = "[▷]" if self.status_blink else "[▶]"
            stat, sc = f"{sym} PROCESSING", C_ACC2
        elif self._jarvis_state == "LISTENING":
            sym = "[●]" if self.status_blink else "[○]"
            stat, sc = f"{sym} LISTENING", C_GREEN
        else:
            sym = "[●]" if self.status_blink else "[○]"
            stat, sc = f"{sym} {self.status_text}", C_PRI

        c.create_text(W // 2, sy, text=stat, fill=sc, font=(FONT_MAIN, 12, "bold"))

        # ── Égaliseur de spectre FFT ──────────────────────────────────────────
        wy = sy + 25
        N, BH, bw = 40, 22, 6
        total_w = N * bw
        wx0 = (W - total_w) // 2
        sleeping_ui = (self._jarvis_state == "SLEEPING")

        # Lire les bandes FFT réelles (None si indisponibles)
        fft_data = None if sleeping_ui or self.muted else _get_fft_bands()
        _has_fft = fft_data is not None and any(v > 0.01 for v in fft_data)

        for i in range(N):
            if self.muted:
                hb, col = 2, C_MUTED
            elif sleeping_ui:
                hb, col = int(2 + 1.5 * math.sin(t * 0.025 + i * 0.45)), C_SLEEP
            elif _has_fft:
                # ── Données FFT réelles ──
                raw = fft_data[i] if i < len(fft_data) else 0.0
                hb = max(2, int(raw * BH))
                # Couleur dégradée selon l'intensité : cyan → orange vif
                if raw > 0.75:
                    col = C_ACC2   # Orange/jaune — pics
                elif raw > 0.45:
                    col = C_PRI    # Cyan — medium
                else:
                    col = C_MID    # Bleu-cyan — faible
            elif self.speaking:
                # Fallback aléatoire si JARVIS parle mais sans données FFT
                hb = random.randint(4, BH)
                col = C_PRI if hb > BH * 0.6 else C_MID
            else:
                hb, col = int(4 + 3 * math.sin(t * 0.1 + i * 0.5)), C_DIM
            bx = wx0 + i * bw
            c.create_rectangle(bx, wy + BH - hb, bx + bw - 2, wy + BH, fill=col, outline="")

        # ── Telegram Remote Status Badge (Bottom Right) ──────────────────────
        tx = W - 140
        ty = H - 52
        if hasattr(self, "telegram_active") and self.telegram_active:
            c.create_rectangle(tx, ty, tx + 120, ty + 20, fill=C_PANEL, outline=C_GREEN, width=1)
            c.create_text(tx + 60, ty + 10, text=f"REMOTE ON ({self.telegram_messages_count})", fill=C_GREEN, font=(FONT_MAIN, 8, "bold"))
        else:
            c.create_rectangle(tx, ty, tx + 120, ty + 20, fill=C_PANEL, outline=C_DIM, width=1)
            c.create_text(tx + 60, ty + 10, text="REMOTE OFFLINE", fill=C_DIM, font=(FONT_MAIN, 8))

        # ── Footer ────────────────────────────────────────────────────────────
        c.create_rectangle(0, H - 28, W, H, fill="#000a10", outline="")
        c.create_line(0, H - 28, W, H - 28, fill=C_DIM, width=1)
        c.create_text(W - 16, H - 14, fill=C_DIM, font=(FONT_MAIN, 8), text="[F4] OVERRIDE MUTE", anchor="e")
        c.create_text(W // 2, H - 14, fill=C_DIM, font=(FONT_MAIN, 8), text="STARK INDUSTRIES  ·  CLASSIFIED  ·  MARK L")

    # ── Log ───────────────────────────────────────────────────────────────────

    def write_log(self, text: str):
        self.typing_queue.append(text)
        tl = text.lower()
        if tl.startswith("you:"):
            self.set_state("PROCESSING")
        elif tl.startswith("jarvis:") or tl.startswith("ai:"):
            self.set_state("SPEAKING")
        if not self.is_typing:
            self._start_typing()

    def _start_typing(self):
        if not self.typing_queue:
            self.is_typing = False
            if not self.speaking and not self.muted:
                self.set_state("LISTENING")
            return
        self.is_typing = True
        text = self.typing_queue.popleft()
        tl   = text.lower()
        if tl.startswith("you:"):
            tag = "you"
        elif tl.startswith("jarvis:") or tl.startswith("ai:"):
            tag = "ai"
        elif tl.startswith("err:") or "error" in tl or "failed" in tl:
            tag = "err"
        else:
            tag = "sys"
        self.log_text.configure(state="normal")
        self._type_char(text, 0, tag)

    def _type_char(self, text, i, tag):
        if i < len(text):
            self.log_text.insert(tk.END, text[i], tag)
            self.log_text.see(tk.END)
            self.root.after(8, self._type_char, text, i + 1, tag)
        else:
            self.log_text.insert(tk.END, "\n")
            self.log_text.configure(state="disabled")
            self.root.after(25, self._start_typing)

    # ── Eski compat metotlar (main.py hâlâ bunları çağırabilir) ──────────────

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")

    # ── API key ───────────────────────────────────────────────────────────────

    def _api_keys_exist(self):
        return API_FILE.exists()

    def wait_for_api_key(self):
        while not self._api_key_ready:
            time.sleep(0.1)

    def _show_setup_ui(self):
        self.setup_frame = tk.Frame(
            self.root, bg="#00080d",
            highlightbackground=C_PRI, highlightthickness=1
        )
        self.setup_frame.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(self.setup_frame, text="◈  INITIALISATION REQUIRED",
                 fg=C_PRI, bg="#00080d", font=(FONT_MAIN, 13, "bold")).pack(pady=(18, 4))
        tk.Label(self.setup_frame,
                 text="Enter your Gemini API key to boot J.A.R.V.I.S.",
                 fg=C_MID, bg="#00080d", font=(FONT_MAIN, 9)).pack(pady=(0, 10))

        tk.Label(self.setup_frame, text="GEMINI API KEY",
                 fg=C_DIM, bg="#00080d", font=(FONT_MAIN, 9)).pack(pady=(8, 2))
        self.gemini_entry = tk.Entry(
            self.setup_frame, width=52, fg=C_TEXT, bg="#000d12",
            insertbackground=C_TEXT, borderwidth=0, font=(FONT_MAIN, 10), show="*"
        )
        self.gemini_entry.pack(pady=(0, 4))

        tk.Button(
            self.setup_frame, text="▸  INITIALISE SYSTEMS",
            command=self._save_api_keys, bg=C_BG, fg=C_PRI,
            activebackground="#003344", font=(FONT_MAIN, 10),
            borderwidth=0, pady=8
        ).pack(pady=14)

    def _save_api_keys(self):
        gemini = self.gemini_entry.get().strip()
        if not gemini:
            return
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(API_FILE, "w", encoding="utf-8") as f:
            json.dump({"gemini_api_key": gemini}, f, indent=4)
        self.setup_frame.destroy()
        self._api_key_ready = True
        self.set_state("LISTENING")
        self.write_log("SYS: Systems initialised. JARVIS online.")

    def add_notification(self, text: str, icon: str = "ℹ️"):
        """Ajoute une notification proactive au panneau latéral."""
        self.notifications.append((datetime.now(), icon, text))

    def _build_memory_button(self):
        self.memory_window = None
        BTN_W, BTN_H = 110, 32
        BTN_X = 18
        BTN_Y = self.H - 146
        self._memory_canvas = tk.Canvas(
            self.root, width=BTN_W, height=BTN_H,
            bg=C_BG, highlightthickness=0, cursor="hand2"
        )
        self._memory_canvas.place(x=BTN_X, y=BTN_Y)
        self._memory_canvas.bind("<Button-1>", lambda e: self._show_memory_popup())
        self._draw_memory_button()

    def _draw_memory_button(self):
        c = self._memory_canvas
        c.delete("all")
        c.create_rectangle(0, 0, 110, 32, outline=C_MID, fill=C_PANEL, width=1)
        c.create_text(55, 16, text="🧠 MEMORY", fill=C_PRI, font=(FONT_MAIN, 10, "bold"))

    def _show_memory_popup(self):
        if self.memory_window is not None:
            self.memory_window.lift()
            return
        self.memory_window = tk.Toplevel(self.root)
        self.memory_window.title("J.A.R.V.I.S — MEMORY CORE DIAGNOSTICS")
        self.memory_window.geometry("400x380")
        self.memory_window.configure(bg=C_BG)
        self.memory_window.resizable(False, False)
        def on_close():
            self.memory_window.destroy()
            self.memory_window = None
        self.memory_window.protocol("WM_DELETE_WINDOW", on_close)

        tk.Label(self.memory_window, text="🧠 MEMORY MATRIX SUMMARY", fg=C_PRI, bg=C_BG, font=(FONT_MAIN, 12, "bold")).pack(pady=15)
        
        from memory.memory_manager import load_memory
        memory_data = load_memory()
        categories = ["identity", "preferences", "projects", "relationships", "wishes", "notes"]
        
        frame = tk.Frame(self.memory_window, bg=C_BG)
        frame.pack(fill="both", expand=True, padx=20)
        
        row = 0
        for cat in categories:
            items = memory_data.get(cat, {})
            count = len(items)
            tk.Label(frame, text=cat.upper(), fg=C_TEXT, bg=C_BG, font=(FONT_MAIN, 9), width=15, anchor="w").grid(row=row, column=0, pady=6)
            bar_canvas = tk.Canvas(frame, width=150, height=14, bg=C_PANEL, highlightthickness=1, highlightbackground=C_DIM)
            bar_canvas.grid(row=row, column=1, pady=6, padx=5)
            max_cap = 15.0
            filled_width = int(150 * (min(count, max_cap) / max_cap))
            if filled_width > 0:
                bar_canvas.create_rectangle(0, 0, filled_width, 14, fill=C_PRI, outline="")
            tk.Label(frame, text=f"{count} items", fg=C_ACC2, bg=C_BG, font=(FONT_MAIN, 9), width=8, anchor="w").grid(row=row, column=2, pady=6)
            row += 1

        try:
            from memory.memory_manager import get_vector_memory
            vm = get_vector_memory()
            if vm and vm.available:
                stats = vm.get_stats()
                total = sum(stats.values())
                vect_text = f"Vector Memory: ENABLED | {total} semantic elements"
            else:
                vect_text = "Vector Memory: OFFLINE (ChromaDB/Sentence-Transformers missing)"
        except Exception:
            vect_text = "Vector Memory: ERROR"
        tk.Label(self.memory_window, text=vect_text, fg=C_GREEN if "ENABLED" in vect_text else C_MUTED, bg=C_BG, font=(FONT_MAIN, 9)).pack(pady=15)
