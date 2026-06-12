# ui.py
# Modern PyQt5 User Interface for JARVIS MARK XXXVI
# Featuring QPainter-based HUD, thread-safe logging, and custom styling.

import os
import json
import time
import math
import random
import sys
import threading
from collections import deque
from pathlib import Path
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, QRectF, QObject, pyqtSignal, QPointF
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTextBrowser, QLineEdit,
    QPushButton, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame
)
from PyQt5.QtGui import QPainter, QPen, QColor, QFont, QPolygonF, QBrush, QImage, QPainterPath

# ── Color Palette ────────────────────────────────────────────────────────────
C_BG     = QColor(0, 5, 8)
C_PANEL  = QColor(0, 10, 16, 220)
C_PRI    = QColor(0, 229, 255)
C_MID    = QColor(0, 136, 170)
C_DIM    = QColor(0, 68, 85)
C_DIMMER = QColor(0, 26, 34)
C_ACC    = QColor(255, 119, 0)
C_ACC2   = QColor(255, 183, 0)
C_TEXT   = QColor(170, 255, 255)
C_GREEN  = QColor(0, 255, 136)
C_RED    = QColor(255, 51, 51)
C_MUTED  = QColor(255, 34, 68)
C_SLEEP  = QColor(51, 17, 102)

FONT_FAMILY = "Consolas"

def _get_fft_bands():
    """Reads FFT bands from main.py in a thread-safe manner."""
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
MODEL_BADGE = "MARK XXXVI - STARK INDUSTRIES"

# ── Signals for Thread-Safe UI Communication ─────────────────────────────────
class UISignals(QObject):
    log_signal = pyqtSignal(str)
    state_signal = pyqtSignal(str)
    web_log_signal = pyqtSignal(str)
    notification_signal = pyqtSignal(str, str)

# ── API Key Setup Dialog ──────────────────────────────────────────────────────
class SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("JARVIS — System Setup")
        self.setFixedSize(500, 320)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        
        # Cyberpunk styling for setup window
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #000a10;
                border: 2px solid #00e5ff;
            }}
            QLabel {{
                color: #aaffff;
                font-family: {FONT_FAMILY};
                font-size: 12px;
            }}
            QLineEdit {{
                background-color: #00121a;
                border: 1px solid #005577;
                color: #00e5ff;
                font-family: {FONT_FAMILY};
                padding: 6px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 1px solid #00e5ff;
            }}
            QPushButton {{
                background-color: #001a26;
                border: 1px solid #0088aa;
                color: #00e5ff;
                font-family: {FONT_FAMILY};
                font-weight: bold;
                padding: 8px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: #00e5ff;
                color: #000508;
            }}
        """)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        
        title = QLabel("📡 JARVIS INITIAL SETUP REQUIRED")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #ff7700; margin-bottom: 15px;")
        layout.addWidget(title)
        
        layout.addWidget(QLabel("Gemini Live API Key:"))
        self.gemini_input = QLineEdit()
        self.gemini_input.setPlaceholderText("Enter AI Studio API Key...")
        layout.addWidget(self.gemini_input)
        
        layout.addWidget(QLabel("FreeLLMAPI Key (Proxy):"))
        self.freellm_input = QLineEdit()
        self.freellm_input.setPlaceholderText("Enter FreeLLMAPI Key...")
        layout.addWidget(self.freellm_input)
        
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 15, 0, 0)
        
        self.save_btn = QPushButton("INITIALISE CORE")
        self.save_btn.clicked.connect(self.accept)
        self.exit_btn = QPushButton("ABORT")
        self.exit_btn.clicked.connect(self.reject)
        
        btn_layout.addWidget(self.save_btn)
        btn_layout.addWidget(self.exit_btn)
        layout.addLayout(btn_layout)
        
        self.setLayout(layout)

# ── Memory Viewer Dialog ──────────────────────────────────────────────────────
class MemoryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("JARVIS — Core Memory Bank")
        self.resize(600, 450)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #000a10;
                border: 2px solid #ff7700;
            }}
            QTextBrowser {{
                background-color: #00121a;
                border: 1px solid #ff7700;
                color: #aaffff;
                font-family: {FONT_FAMILY};
                font-size: 13px;
                padding: 10px;
            }}
            QPushButton {{
                background-color: #001a26;
                border: 1px solid #ff7700;
                color: #ff7700;
                font-family: {FONT_FAMILY};
                font-weight: bold;
                padding: 8px;
            }}
            QPushButton:hover {{
                background-color: #ff7700;
                color: #000508;
            }}
        """)
        layout = QVBoxLayout()
        
        self.text_browser = QTextBrowser()
        layout.addWidget(self.text_browser)
        
        close_btn = QPushButton("CLOSE DATASTREAM")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
        
        self.setLayout(layout)
        
        # Load facts
        try:
            import main as _m
            facts = _m._telegram_get_memory()
            self.text_browser.setText(facts)
        except Exception as e:
            self.text_browser.setText(f"Error loading memory core: {e}")

# ── Web Agent Feed Window ─────────────────────────────────────────────────────
class WebAgentWindow(QWidget):
    def __init__(self, close_callback):
        super().__init__()
        self.close_callback = close_callback
        self.setWindowTitle("J.A.R.V.I.S — WEB AGENT FEED")
        self.resize(500, 400)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: #000508;
            }}
            QTextBrowser {{
                background-color: #000a10;
                border: 1px solid #ff7700;
                color: #aaffff;
                font-family: {FONT_FAMILY};
                font-size: 12px;
                padding: 10px;
            }}
        """)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        
        self.log_text = QTextBrowser()
        layout.addWidget(self.log_text)
        self.setLayout(layout)
        
        self.log_text.setText(">>> WEB AGENT INITIALIZED.\n>>> WAITING FOR BACKGROUND TASKS...\n")

    def closeEvent(self, event):
        self.close_callback()
        event.accept()

# ── Central MainWindow ────────────────────────────────────────────────────────
class JarvisUI(QMainWindow):
    def __init__(self, face_path, size=None):
        # We need an active QApplication before QWidget instantiation
        self.app = QApplication.instance()
        if not self.app:
            self.app = QApplication(sys.argv)
            
        super().__init__()
        
        self.setWindowTitle("J.A.R.V.I.S — MARK XXXVI")
        self.setFixedSize(984, 816)
        
        # UI State Variables
        self.speaking = False
        self.muted = False
        self.scale = 1.0
        self.target_scale = 1.0
        self.halo_a = 60.0
        self.target_halo = 60.0
        self.last_t = time.time()
        self.tick = 0
        self.scan_angle = 0.0
        self.scan2_angle = 180.0
        self.rings_spin = [0.0, 120.0, 240.0]
        
        self.FACE_SZ = 400
        self.FCX = 984 // 2
        self.FCY = int(816 * 0.13) + self.FACE_SZ // 2
        self.pulse_r = [0.0, self.FACE_SZ * 0.26, self.FACE_SZ * 0.52]
        
        self._jarvis_state = "INITIALISING"
        self.status_text = "INITIALISING"
        self.status_blink = True
        
        # Interactive Callbacks & Queues
        self.on_text_command = None
        self.typing_queue = deque()
        self.current_typing_text = ""
        self.current_typing_idx = 0
        self.current_typing_tag = "sys"
        self.is_typing = False
        
        self.notifications = deque(maxlen=8)
        self.telegram_active = False
        self.telegram_messages_count = 0
        
        # System Monitor Setup
        try:
            from core.system_monitor import get_monitor
            self.monitor = get_monitor()
        except ImportError:
            self.monitor = None
            
        # Expose UI elements
        self._face_image = None
        self._has_face = False
        self._load_face(face_path)
        
        self.web_window = None
        self.show_web_agent = False
        
        # Thread-safe signals
        self.signals = UISignals()
        self.signals.log_signal.connect(self._handle_write_log)
        self.signals.state_signal.connect(self._handle_set_state)
        self.signals.web_log_signal.connect(self._handle_write_web_log)
        self.signals.notification_signal.connect(self._handle_add_notification)
        
        # Build Central Widgets
        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)
        
        # 1. Text Log Area (HTML enabled for cyberpunk tags)
        self.log_browser = QTextBrowser(self.central_widget)
        self.log_browser.setGeometry(137, 510, 710, 110)
        self.log_browser.setStyleSheet(f"""
            QTextBrowser {{
                background-color: #000a10;
                border: 1px solid #0088aa;
                color: #aaffff;
                font-family: {FONT_FAMILY};
                font-size: 13px;
                padding: 6px;
            }}
        """)
        self.log_browser.setContextMenuPolicy(Qt.NoContextMenu)
        
        # 2. Keyboard Input Bar
        self.input_entry = QLineEdit(self.central_widget)
        self.input_entry.setGeometry(137, 626, 636, 28)
        self.input_entry.setPlaceholderText("Command J.A.R.V.I.S...")
        self.input_entry.setStyleSheet(f"""
            QLineEdit {{
                background-color: #000d12;
                border: 1px solid #004455;
                color: #00e5ff;
                font-family: {FONT_FAMILY};
                font-size: 13px;
                padding-left: 5px;
            }}
            QLineEdit:focus {{
                border: 1px solid #00e5ff;
            }}
        """)
        self.input_entry.returnPressed.connect(self._on_input_submit)
        
        # 3. Send Button
        self.send_btn = QPushButton("SEND ▸", self.central_widget)
        self.send_btn.setGeometry(777, 626, 70, 28)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #000a10;
                border: 1px solid #0088aa;
                color: #00e5ff;
                font-family: {FONT_FAMILY};
                font-weight: bold;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #00e5ff;
                color: #000508;
            }}
        """)
        self.send_btn.clicked.connect(self._on_input_submit)
        
        # 4. Mute Button (Custom styled HUD button)
        self.mute_btn = QPushButton("🎙 LIVE", self.central_widget)
        self.mute_btn.setGeometry(18, 702, 110, 32)
        self.mute_btn.setStyleSheet(self._get_mute_btn_style())
        self.mute_btn.clicked.connect(self._toggle_mute)
        
        # 5. Web Agent Button
        self.web_btn = QPushButton("🌐 HIDDEN", self.central_widget)
        self.web_btn.setGeometry(18, 664, 110, 32)
        self.web_btn.setStyleSheet(self._get_web_btn_style())
        self.web_btn.clicked.connect(self._toggle_web_agent)
        
        # 6. Memory Core Button
        self.mem_btn = QPushButton("🧠 MEMORY", self.central_widget)
        self.mem_btn.setGeometry(18, 626, 110, 32)
        self.mem_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #000a10;
                border: 1px solid #ff7700;
                color: #ff7700;
                font-family: {FONT_FAMILY};
                font-weight: bold;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #ff7700;
                color: #000508;
            }}
        """)
        self.mem_btn.clicked.connect(self._show_memory_popup)
        
        # QTimer for HUD animations (~60 fps)
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._animate)
        self.anim_timer.start(16)
        
        # Check API config on load
        self._api_key_ready = self._api_keys_exist()
        if not self._api_key_ready:
            QTimer.singleShot(100, self._show_setup_ui)
            
        self.show()

    # ── Mute Button Management ────────────────────────────────────────────────
    def _get_mute_btn_style(self):
        if self.muted:
            return f"""
                QPushButton {{
                    background-color: #1a0008;
                    border: 1px solid #ff2244;
                    color: #ff2244;
                    font-family: {FONT_FAMILY};
                    font-weight: bold;
                    font-size: 11px;
                }}
            """
        else:
            return f"""
                QPushButton {{
                    background-color: #000a10;
                    border: 1px solid #0088aa;
                    color: #00ff88;
                    font-family: {FONT_FAMILY};
                    font-weight: bold;
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    background-color: #00e5ff;
                    color: #000508;
                }}
            """

    def _toggle_mute(self):
        self.muted = not self.muted
        self.mute_btn.setText("🔇 MUTED" if self.muted else "🎙 LIVE")
        self.mute_btn.setStyleSheet(self._get_mute_btn_style())
        if self.muted:
            self.set_state("MUTED")
            self.write_log("SYS: Microphone muted.")
        else:
            self.set_state("LISTENING")
            self.write_log("SYS: Microphone active.")

    # ── Web Agent Panel Management ────────────────────────────────────────────
    def _get_web_btn_style(self):
        if self.show_web_agent:
            return f"""
                QPushButton {{
                    background-color: #1a0a00;
                    border: 1px solid #ff7700;
                    color: #ff7700;
                    font-family: {FONT_FAMILY};
                    font-weight: bold;
                    font-size: 11px;
                }}
            """
        else:
            return f"""
                QPushButton {{
                    background-color: #000a10;
                    border: 1px solid #004455;
                    color: #004455;
                    font-family: {FONT_FAMILY};
                    font-weight: bold;
                    font-size: 11px;
                }}
            """

    def _toggle_web_agent(self):
        self.show_web_agent = not self.show_web_agent
        self.web_btn.setText("🌐 VISIBLE" if self.show_web_agent else "🌐 HIDDEN")
        self.web_btn.setStyleSheet(self._get_web_btn_style())
        if self.show_web_agent:
            self.write_log("SYS: Web Agent visual feed ENABLED.")
            self._open_web_window()
        else:
            self.write_log("SYS: Web Agent visual feed DISABLED.")
            self._close_web_window()

    def _open_web_window(self):
        if self.web_window:
            return
        self.web_window = WebAgentWindow(self._on_web_window_closed)
        self.web_window.show()

    def _close_web_window(self):
        if self.web_window:
            self.web_window.close()
            self.web_window = None

    def _on_web_window_closed(self):
        self.show_web_agent = False
        self.web_btn.setText("🌐 HIDDEN")
        self.web_btn.setStyleSheet(self._get_web_btn_style())
        self.web_window = None

    def write_web_log(self, text: str):
        self.signals.web_log_signal.emit(text)

    def _handle_write_web_log(self, text: str):
        if self.web_window:
            self.web_window.log_text.append(text)

    # ── Memory Core popup ─────────────────────────────────────────────────────
    def _show_memory_popup(self):
        dlg = MemoryDialog(self)
        dlg.exec_()

    # ── Text Submission ───────────────────────────────────────────────────────
    def _on_input_submit(self):
        text = self.input_entry.text().strip()
        if not text:
            return
        self.input_entry.clear()
        self.write_log(f"You: {text}")
        if self.on_text_command:
            threading.Thread(
                target=self.on_text_command,
                args=(text,),
                daemon=True
            ).start()

    # ── API Compatibility Methods ─────────────────────────────────────────────
    def write_log(self, text: str):
        self.signals.log_signal.emit(text)

    def _handle_write_log(self, text: str):
        self.typing_queue.append(text)
        tl = text.lower()
        if tl.startswith("you:"):
            self.set_state("PROCESSING")
        elif tl.startswith("jarvis:") or tl.startswith("ai:"):
            self.set_state("SPEAKING")
            
        if not self.is_typing:
            self._start_typing()

    def set_state(self, state: str):
        self.signals.state_signal.emit(state)

    def _handle_set_state(self, state: str):
        self._jarvis_state = state
        if state == "MUTED":
            self.status_text = "MUTED"
            self.speaking = False
        elif state == "SPEAKING":
            self.status_text = "SPEAKING"
            self.speaking = True
        elif state == "THINKING":
            self.status_text = "THINKING"
            self.speaking = False
        elif state == "LISTENING":
            self.status_text = "LISTENING"
            self.speaking = False
        elif state == "PROCESSING":
            self.status_text = "PROCESSING"
            self.speaking = False
        elif state == "SLEEPING":
            self.status_text = "SLEEPING"
            self.speaking = False
        else:
            self.status_text = "ONLINE"
            self.speaking = False

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")

    def add_notification(self, text: str, icon: str = "ℹ️"):
        self.signals.notification_signal.emit(text, icon)

    def _handle_add_notification(self, text: str, icon: str):
        self.notifications.append((datetime.now(), icon, text))

    def _api_keys_exist(self):
        try:
            return core.profile_loader.load_api_keys() != {}
        except Exception:
            return API_FILE.exists()

    def wait_for_api_key(self):
        while not self._api_key_ready:
            time.sleep(0.1)

    def _show_setup_ui(self):
        dlg = SetupDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            gemini_key = dlg.gemini_input.text().strip()
            freellm_key = dlg.freellm_input.text().strip()
            
            if gemini_key:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                config = {
                    "gemini_api_key": gemini_key,
                    "freellmapi_key": freellm_key,
                    "freellmapi_url": "http://31.97.197.149:3005"
                }
                with open(API_FILE, "w") as f:
                    json.dump(config, f, indent=4)
                self.write_log("SYS: Core profile initialized. System ready.")
                self._api_key_ready = True
            else:
                sys.exit(0)
        else:
            sys.exit(0)

    # ── Character-By-Character Typing Effect ──────────────────────────────────
    def _start_typing(self):
        if not self.typing_queue:
            self.is_typing = False
            if not self.speaking and not self.muted:
                self.set_state("LISTENING")
            return
            
        self.is_typing = True
        self.current_typing_text = self.typing_queue.popleft()
        self.current_typing_idx = 0
        
        tl = self.current_typing_text.lower()
        if tl.startswith("you:"):
            self.current_typing_tag = "you"
        elif tl.startswith("jarvis:") or tl.startswith("ai:"):
            self.current_typing_tag = "ai"
        elif tl.startswith("err:") or "error" in tl or "failed" in tl:
            self.current_typing_tag = "err"
        else:
            self.current_typing_tag = "sys"

    # Key shortcuts (F4 to toggle mute)
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F4:
            self._toggle_mute()
        super().keyPressEvent(event)

    # ── Animation Updates ─────────────────────────────────────────────────────
    def _animate(self):
        self.tick += 1
        t = self.tick
        now = time.time()
        
        sleeping = (self._jarvis_state == "SLEEPING")
        
        # 1. Typing animation tick
        if self.is_typing:
            if self.current_typing_idx < len(self.current_typing_text):
                char = self.current_typing_text[self.current_typing_idx]
                color_hex = "#ffffff"
                if self.current_typing_tag == "you":
                    color_hex = "#e8e8e8"
                elif self.current_typing_tag == "ai":
                    color_hex = "#00e5ff"
                elif self.current_typing_tag == "err":
                    color_hex = "#ff3333"
                elif self.current_typing_tag == "sys":
                    color_hex = "#ffb700"
                    
                # Append to HTML log browser
                self.log_browser.insertHtml(f"<span style='color: {color_hex}; font-family: {FONT_FAMILY};'>{char}</span>")
                self.log_browser.ensureCursorVisible()
                self.current_typing_idx += 1
            else:
                self.log_browser.insertHtml("<br/>")
                self.log_browser.ensureCursorVisible()
                self._start_typing()

        # 2. Breathing / Glow updates
        if now - self.last_t > (0.14 if self.speaking else (1.5 if sleeping else 0.55)):
            if self.speaking:
                self.target_scale = random.uniform(1.05, 1.11)
                self.target_halo = random.uniform(138, 182)
            elif self.muted:
                self.target_scale = random.uniform(0.998, 1.001)
                self.target_halo = random.uniform(20, 32)
            elif sleeping:
                self.target_scale = random.uniform(0.999, 1.002)
                self.target_halo = random.uniform(12, 22)
            else:
                self.target_scale = random.uniform(1.001, 1.007)
                self.target_halo = random.uniform(50, 68)
            self.last_t = now
            
        sp = 0.35 if self.speaking else 0.16
        self.scale += (self.target_scale - self.scale) * sp
        self.halo_a += (self.target_halo - self.halo_a) * sp
        
        # 3. Rotating rings
        for i, spd in enumerate([1.2, -0.8, 1.9] if self.speaking else
                                 [0.1, -0.06, 0.15] if sleeping else
                                 [0.5, -0.3, 0.82]):
            self.rings_spin[i] = (self.rings_spin[i] + spd) % 360
            
        spin_spd = 2.8 if self.speaking else (0.3 if sleeping else 1.2)
        self.scan_angle = (self.scan_angle + spin_spd) % 360
        self.scan2_angle = (self.scan2_angle + (-spin_spd * 0.6)) % 360
        
        # 4. Pulsing
        pspd = 3.8 if self.speaking else (0.6 if sleeping else 1.8)
        limit = self.FACE_SZ * 0.72
        new_p = [r + pspd for r in self.pulse_r if r + pspd < limit]
        if len(new_p) < 3 and random.random() < (0.06 if self.speaking else 0.022):
            new_p.append(0.0)
        self.pulse_r = new_p
        
        if t % 40 == 0:
            self.status_blink = not self.status_blink
            
        # Trigger Paint Event
        self.update()

    # ── Image Loading ─────────────────────────────────────────────────────────
    def _load_face(self, path):
        if os.path.exists(path):
            self._face_image = QImage(path)
            self._has_face = not self._face_image.isNull()
        else:
            self._has_face = False

    # ── High Performance Painting (HUD Interface) ─────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        W, H = self.width(), self.height()
        FCX, FCY = self.FCX, self.FCY
        FW = self.FACE_SZ
        t = self.tick
        
        # 1. Fill background grid
        painter.fillRect(0, 0, W, H, C_BG)
        grid_pen = QPen(C_DIMMER, 1, Qt.SolidLine)
        painter.setPen(grid_pen)
        for x in range(0, W, 50):
            for y in range(0, H, 50):
                painter.drawLine(x - 2, y, x + 2, y)
                painter.drawLine(x, y - 2, x, y + 2)
                
        # Determine current primary color theme (red when muted, yellow/cyan otherwise)
        theme_color = C_MUTED if self.muted else C_PRI
        theme_a = lambda a: QColor(theme_color.red(), theme_color.green(), theme_color.blue(), a)
        
        # 2. System Diagnostics (Top Right)
        diag = self.monitor.get_ui_data() if self.monitor else {}
        cpu = diag.get("cpu_pct", 0.0)
        ram = diag.get("ram_pct", 0.0)
        gpu = diag.get("gpu_pct")
        cpu_t = diag.get("cpu_temp")
        gpu_t = diag.get("gpu_temp")
        
        dx = W - 220
        dy = 85
        
        painter.setPen(QPen(C_PRI, 1))
        painter.setFont(QFont(FONT_FAMILY, 10, QFont.Bold))
        painter.drawText(dx, dy, "📊 SYSTEM DIAGNOSTICS")
        painter.drawLine(dx, dy + 6, dx + 200, dy + 6)
        
        painter.setFont(QFont(FONT_FAMILY, 9))
        metrics = [("CPU", cpu), ("RAM", ram)]
        if gpu is not None:
            metrics.append(("GPU", gpu))
            
        bar_y = dy + 25
        for label, val in metrics:
            painter.setPen(QPen(C_TEXT, 1))
            painter.drawText(dx, bar_y + 4, f"{label}: {val}%")
            bx = dx + 60
            
            # Draw empty bar
            painter.setPen(QPen(C_DIMMER, 8, Qt.SolidLine, Qt.FlatCap))
            painter.drawLine(bx, bar_y, bx + 120, bar_y)
            
            if val > 0:
                bar_len = int(120 * (val / 100.0))
                col = C_PRI
                if val > 90: col = C_RED
                elif val > 75: col = C_ACC
                painter.setPen(QPen(col, 8, Qt.SolidLine, Qt.FlatCap))
                painter.drawLine(bx, bar_y, bx + bar_len, bar_y)
            bar_y += 18
            
        temp_y = bar_y + 4
        temp_strs = []
        if cpu_t is not None: temp_strs.append(f"CPU: {cpu_t}°C")
        if gpu_t is not None: temp_strs.append(f"GPU: {gpu_t}°C")
        if temp_strs:
            painter.setPen(QPen(C_ACC2, 1))
            painter.drawText(dx, temp_y, "Temp: " + " | ".join(temp_strs))
            
        # 3. System Alerts (Top Left)
        nx = 20
        ny = 85
        painter.setPen(QPen(C_ACC, 1))
        painter.setFont(QFont(FONT_FAMILY, 10, QFont.Bold))
        painter.drawText(nx, ny, "🔔 SYSTEM ALERTS")
        painter.drawLine(nx, ny + 6, nx + 200, ny + 6)
        
        if self.monitor:
            try:
                for alert in self.monitor.get_alerts():
                    sev = alert.get("severity", "info")
                    icon = "🚨" if sev == "critical" else ("⚠️" if sev == "warning" else "ℹ️")
                    self.add_notification(alert.get("message", ""), icon)
            except Exception:
                pass
                
        painter.setFont(QFont(FONT_FAMILY, 9))
        if not self.notifications:
            painter.setPen(QPen(C_GREEN, 1))
            painter.drawText(nx, ny + 25, "All systems nominal.")
        else:
            alert_y = ny + 25
            for timestamp, icon, text in list(self.notifications):
                time_str = timestamp.strftime("%H:%M:%S")
                display_text = f"[{time_str}] {icon} {text}"
                if len(display_text) > 34:
                    display_text = display_text[:31] + "..."
                painter.setPen(QPen(C_TEXT, 1))
                painter.drawText(nx, alert_y, display_text)
                alert_y += 18

        # 4. Hex Data Sides
        hud_pen = QPen(theme_a(int(self.halo_a * 0.7)), 1)
        painter.setPen(hud_pen)
        painter.setFont(QFont(FONT_FAMILY, 9))
        for i in range(6):
            painter.drawText(25, FCY - 120 + i * 45, f"SYS.{i}: {random.randint(1000, 9999):04X}")
            painter.drawLine(25, FCY - 110 + i * 45, 80, FCY - 110 + i * 45)
            
            painter.drawText(W - 80, FCY - 120 + i * 45, f"NET.{i}: {random.random():.3f}")
            painter.drawLine(W - 80, FCY - 110 + i * 45, W - 25, FCY - 110 + i * 45)

        # 5. Inner Core Halos
        for r in range(int(FW * 0.54), int(FW * 0.28), -22):
            frac = 1.0 - (r - FW * 0.28) / (FW * 0.26)
            ga = max(0, min(255, int(self.halo_a * 0.1 * frac)))
            painter.setPen(QPen(theme_a(ga), 2))
            painter.drawEllipse(QPointF(FCX, FCY), r, r)

        # 6. Pulse Waves
        for pr in self.pulse_r:
            pa = max(0, int(220 * (1.0 - pr / (FW * 0.72))))
            painter.setPen(QPen(theme_a(pa), 2))
            painter.drawEllipse(QPointF(FCX, FCY), pr, pr)

        # 7. Iron Man Complex Rotating Rings
        for idx, (r_frac, w_ring, arc_l, gap, dash_pat) in enumerate([
                (0.47, 3, 110, 75, ()),
                (0.44, 1, 360, 0, (2, 4)),
                (0.39, 2, 75, 55, ()),
                (0.35, 1, 360, 0, (8, 6)),
                (0.31, 2, 55, 38, ())]):
            ring_r = int(FW * r_frac)
            base_a = self.rings_spin[idx % len(self.rings_spin)]
            a_val = max(0, min(255, int(self.halo_a * (1.0 - idx * 0.15))))
            col = theme_a(a_val)
            
            pen = QPen(col, w_ring)
            if dash_pat:
                pen.setDashPattern(list(dash_pat))
                painter.setPen(pen)
                painter.drawEllipse(QPointF(FCX, FCY), ring_r, ring_r)
            else:
                painter.setPen(pen)
                # Draw rotating arcs
                slices = 360 // (arc_l + gap)
                for s in range(slices):
                    start = (base_a + s * (arc_l + gap))
                    # Qt uses 1/16th of a degree for arcs
                    painter.drawArc(int(FCX - ring_r), int(FCY - ring_r), ring_r * 2, ring_r * 2, int(start * 16), int(arc_l * 16))

        # 8. Scanning Arcs
        sr = int(FW * 0.49)
        scan_a = min(255, int(self.halo_a * 1.5))
        arc_ext = 80 if self.speaking else 45
        painter.setPen(QPen(theme_a(scan_a), 4))
        painter.drawArc(int(FCX - sr), int(FCY - sr), sr * 2, sr * 2, int(self.scan_angle * 16), int(arc_ext * 16))
        
        painter.setPen(QPen(QColor(255, 119, 0, scan_a // 2), 2))
        painter.drawArc(int(FCX - sr), int(FCY - sr), sr * 2, sr * 2, int(self.scan2_angle * 16), int(arc_ext * 16))

        # 9. Dial tick marks
        t_out = int(FW * 0.495)
        t_in  = int(FW * 0.472)
        painter.setPen(QPen(C_PRI, 1))
        for deg in range(0, 360, 5):
            rad = math.radians(deg)
            inn = t_in if deg % 15 == 0 else t_in + 6
            w = 2 if deg % 45 == 0 else 1
            painter.setPen(QPen(C_PRI, w))
            painter.drawLine(
                int(FCX + t_out * math.cos(rad)), int(FCY - t_out * math.sin(rad)),
                int(FCX + inn  * math.cos(rad)), int(FCY - inn  * math.sin(rad))
            )

        # 10. Crosshair HUD
        ch_r = int(FW * 0.52)
        gap  = int(FW * 0.18)
        painter.setPen(QPen(QColor(0, 229, 255, int(self.halo_a * 0.6)), 1))
        painter.setBrush(QBrush(QColor(0, 229, 255, int(self.halo_a * 0.6))))
        
        # Horizontal
        painter.drawLine(int(FCX - ch_r), int(FCY), int(FCX - gap), int(FCY))
        painter.drawLine(int(FCX + gap), int(FCY), int(FCX + ch_r), int(FCY))
        # Vertical
        painter.drawLine(int(FCX), int(FCY - ch_r), int(FCX), int(FCY - gap))
        painter.drawLine(int(FCX), int(FCY + gap), int(FCX), int(FCY + ch_r))
        
        # Crosshair dots
        painter.drawEllipse(QPointF(FCX - ch_r, FCY), 2, 2)
        painter.drawEllipse(QPointF(FCX + ch_r, FCY), 2, 2)
        painter.drawEllipse(QPointF(FCX, FCY - ch_r), 2, 2)
        painter.drawEllipse(QPointF(FCX, FCY + ch_r), 2, 2)
        
        # 11. Targeting Corner Brackets
        blen = 30
        painter.setPen(QPen(QColor(0, 229, 255, 220), 3))
        hl = FCX - int(FW * 0.55); hr = FCX + int(FW * 0.55)
        ht = FCY - int(FW * 0.55); hb = FCY + int(FW * 0.55)
        for bx, by, sdx, sdy in [(hl, ht, 1, 1), (hr, ht, -1, 1),
                                   (hl, hb, 1, -1), (hr, hb, -1, -1)]:
            painter.drawLine(bx, by, bx + sdx * blen, by)
            painter.drawLine(bx, by, bx, by + sdy * blen)
            
            poly = QPolygonF([
                QPointF(bx, by),
                QPointF(bx + sdx * 8, by),
                QPointF(bx, by + sdy * 8)
            ])
            painter.setBrush(QBrush(QColor(0, 229, 255, 220)))
            painter.drawPolygon(poly)

        # 12. Arc Reactor Core / User Face
        if self._has_face:
            fw = int(FW * self.scale)
            scaled = self._face_image.scaled(fw, fw, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            
            # Mask image to circle
            mask_pix = QPixmap(scaled.size())
            mask_pix.fill(Qt.transparent)
            mask_painter = QPainter(mask_pix)
            mask_painter.setRenderHint(QPainter.Antialiasing)
            mask_painter.setBrush(Qt.black)
            mask_painter.drawEllipse(0, 0, scaled.width(), scaled.height())
            mask_painter.end()
            
            final_img = QPixmap(scaled.size())
            final_img.fill(Qt.transparent)
            final_painter = QPainter(final_img)
            final_painter.drawImage(0, 0, scaled)
            final_painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            final_painter.drawPixmap(0, 0, mask_pix)
            final_painter.end()
            
            painter.drawPixmap(int(FCX - final_img.width() // 2), int(FCY - final_img.height() // 2), final_img)
        else:
            orb_r = int(FW * 0.28 * self.scale)
            orb_color = (255, 34, 68) if self.muted else (0, 180, 255)
            for i in range(8, 0, -1):
                r2 = int(orb_r * i / 8)
                frac = i / 8
                ga = max(0, min(255, int(self.halo_a * 1.3 * frac)))
                cc = QColor(int(orb_color[0]*frac + 25*(1-frac)),
                            int(orb_color[1]*frac + 50*(1-frac)),
                            int(orb_color[2]*frac + 60*(1-frac)), ga)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(cc))
                painter.drawEllipse(QPointF(FCX, FCY), r2, r2)
                
            painter.setFont(QFont(FONT_FAMILY, 11, QFont.Bold))
            painter.setPen(QPen(QColor(255, 255, 255, min(255, int(self.halo_a * 2))), 1))
            painter.drawText(FCX - 25, FCY + 4, "STARK")

        # 13. System Header & Time Info
        painter.fillRect(0, 0, W, 62, QBrush(QColor(0, 10, 16)))
        painter.setPen(QPen(C_MID, 2))
        painter.drawLine(0, 62, W, 62)
        
        painter.setFont(QFont(FONT_FAMILY, 16, QFont.Bold))
        painter.setPen(QPen(C_PRI, 1))
        painter.drawText(QRectF(0, 8, W, 25), Qt.AlignCenter, "STARK INDUSTRIES // J.A.R.V.I.S")
        painter.setFont(QFont(FONT_FAMILY, 9))
        painter.setPen(QPen(C_MID, 1))
        painter.drawText(QRectF(0, 36, W, 20), Qt.AlignCenter, "TACTICAL INTELLIGENCE & HUD INTERFACE")
        
        painter.setPen(QPen(C_DIM, 1))
        painter.drawText(16, 38, MODEL_BADGE)
        painter.setPen(QPen(C_ACC, 1))
        painter.setFont(QFont(FONT_FAMILY, 14, QFont.Bold))
        painter.drawText(W - 120, 38, time.strftime("%H:%M:%S"))

        # 14. Status Text (Cyberpunk blinkers)
        sy = FCY + int(FW * 0.6) + 20
        painter.setFont(QFont(FONT_FAMILY, 12, QFont.Bold))
        
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
            
        painter.setPen(QPen(sc, 1))
        painter.drawText(QRectF(0, sy, W, 22), Qt.AlignCenter, stat)

        # 15. FFT Audio Equalizer
        wy = sy + 30
        N, BH, bw = 40, 22, 6
        total_w = N * bw
        wx0 = (W - total_w) // 2
        sleeping_ui = (self._jarvis_state == "SLEEPING")
        
        fft_data = None if sleeping_ui or self.muted else _get_fft_bands()
        _has_fft = fft_data is not None and any(v > 0.01 for v in fft_data)
        
        for i in range(N):
            if self.muted:
                hb, col = 2, C_MUTED
            elif sleeping_ui:
                hb, col = int(2 + 1.5 * math.sin(t * 0.025 + i * 0.45)), C_SLEEP
            elif _has_fft:
                raw = fft_data[i] if i < len(fft_data) else 0.0
                hb = max(2, int(raw * BH))
                col = C_ACC2 if raw > 0.75 else (C_PRI if raw > 0.45 else C_MID)
            elif self.speaking:
                hb = random.randint(4, BH)
                col = C_PRI if hb > BH * 0.6 else C_MID
            else:
                hb, col = int(4 + 3 * math.sin(t * 0.1 + i * 0.5)), C_DIM
                
            bx = wx0 + i * bw
            painter.fillRect(bx, wy + BH - hb, bw - 2, hb, QBrush(col))

        # 16. Telegram Status Badge (Bottom Right)
        tx = W - 140
        ty = H - 52
        badge_pen = QPen(C_GREEN if self.telegram_active else C_DIM, 1)
        painter.setPen(badge_pen)
        painter.setBrush(QBrush(C_PANEL))
        painter.drawRect(tx, ty, 120, 20)
        
        painter.setFont(QFont(FONT_FAMILY, 8, QFont.Bold))
        if self.telegram_active:
            painter.setPen(QPen(C_GREEN, 1))
            painter.drawText(tx + 12, ty + 14, f"REMOTE ON ({self.telegram_messages_count})")
        else:
            painter.setPen(QPen(C_DIM, 1))
            painter.drawText(tx + 18, ty + 14, "REMOTE OFFLINE")

        # 17. System Footer
        painter.fillRect(0, H - 28, W, 28, QBrush(QColor(0, 10, 16)))
        painter.setPen(QPen(C_DIM, 1))
        painter.drawLine(0, H - 28, W, H - 28)
        
        painter.setFont(QFont(FONT_FAMILY, 8))
        painter.drawText(W - 140, H - 10, "[F4] OVERRIDE MUTE")
        painter.drawText(W // 2 - 130, H - 10, "STARK INDUSTRIES  ·  CLASSIFIED  ·  MARK L")

    # Compatibility mainloop replacement
    @property
    def root(self):
        return self

    def mainloop(self):
        # Fallback for main.py wait, PyQt5 app runner manages this
        pass
