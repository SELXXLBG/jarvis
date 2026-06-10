# core/system_monitor.py
# Module de monitoring système pour JARVIS — collecte métriques hardware en temps réel
# System health monitoring module — collects real-time hardware metrics

import threading
import time
import string
from collections import deque
from datetime import datetime
from pathlib import Path

# === Imports optionnels avec fallback gracieux ===
# === Optional imports with graceful degradation ===

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False
    print("[SystemMonitor] psutil not installed — CPU/RAM/Disk/Network/Battery monitoring disabled")

try:
    import GPUtil
    GPUTIL_AVAILABLE = True
except ImportError:
    GPUtil = None
    GPUTIL_AVAILABLE = False
    print("[SystemMonitor] GPUtil not installed — GPU monitoring disabled")

try:
    import wmi as wmi_module
    WMI_AVAILABLE = True
except ImportError:
    wmi_module = None
    WMI_AVAILABLE = False
    print("[SystemMonitor] WMI not available — CPU temperature via WMI disabled")

try:
    import pythoncom
    PYTHONCOM_AVAILABLE = True
except ImportError:
    pythoncom = None
    PYTHONCOM_AVAILABLE = False


# ──────────────────────────────────────────────
#  Constantes / Constants
# ──────────────────────────────────────────────

POLL_INTERVAL_S: float = 2.0
HISTORY_MAX_SAMPLES: int = 900          # 900 × 2s = 30 minutes
CPU_ALERT_THRESHOLD_PCT: float = 90.0
CPU_ALERT_SUSTAIN_S: float = 30.0       # CPU doit rester > seuil pendant 30s
RAM_ALERT_THRESHOLD_PCT: float = 90.0
GPU_TEMP_ALERT_C: float = 85.0
DISK_FREE_ALERT_PCT: float = 10.0       # Alerte si < 10 % libre
BATTERY_ALERT_PCT: float = 15.0
SUSPICIOUS_CPU_THRESHOLD: float = 50.0  # Processus inconnu > 50 % CPU
TOP_PROCESSES_COUNT: int = 5

# Processus système Windows connus (liste non-exhaustive)
KNOWN_PROCESS_NAMES: set[str] = {
    "system", "svchost.exe", "csrss.exe", "wininit.exe", "services.exe",
    "lsass.exe", "smss.exe", "explorer.exe", "dwm.exe", "taskhostw.exe",
    "runtimebroker.exe", "searchhost.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "ctfmon.exe", "conhost.exe",
    "fontdrvhost.exe", "sihost.exe", "dllhost.exe", "audiodg.exe",
    "spoolsv.exe", "winlogon.exe", "wudfhost.exe", "msdtc.exe",
    "searchindexer.exe", "securityhealthservice.exe", "sgrmbroker.exe",
    "systemsettingsbroker.exe", "textinputhost.exe", "widgetservice.exe",
    "msedge.exe", "chrome.exe", "firefox.exe", "code.exe", "python.exe",
    "pythonw.exe", "python3.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
    "windowsterminal.exe", "wt.exe", "notepad.exe", "mspaint.exe",
    "discord.exe", "spotify.exe", "steam.exe", "obs64.exe", "slack.exe",
    "teams.exe", "msteams.exe", "onedrive.exe", "dropbox.exe",
    "vmware.exe", "virtualbox.exe", "idea64.exe", "pycharm64.exe",
    "devenv.exe", "rider64.exe", "java.exe", "javaw.exe", "node.exe",
    "git.exe", "ssh.exe", "wsl.exe", "wmiprvse.exe", "taskmgr.exe",
    "nvidia-smi.exe", "nvcontainer.exe", "nvdisplay.container.exe",
    "amdrsserv.exe", "atiesrxx.exe", "igfxem.exe",
    "securityhealthsystray.exe", "registry", "idle", "comet.exe",
}


def _safe_round(value: float | None, decimals: int = 1) -> float | None:
    """Arrondi sécurisé — retourne None si la valeur est None."""
    if value is None:
        return None
    try:
        return round(float(value), decimals)
    except (TypeError, ValueError):
        return None


class SystemMonitor:
    """
    Singleton de monitoring système pour JARVIS.
    Collecte CPU, RAM, GPU, disque, réseau, batterie, processus et drives USB.
    Thread-safe, daemon thread, historique glissant de 30 min.

    Singleton system monitor for JARVIS.
    Collects CPU, RAM, GPU, disk, network, battery, processes and USB drives.
    Thread-safe, daemon thread, rolling 30-min history.
    """

    _instance: "SystemMonitor | None" = None
    _instance_lock = threading.Lock()

    # ── Singleton ────────────────────────────────
    def __new__(cls, *args, **kwargs) -> "SystemMonitor":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        # Verrou principal / Main lock
        self._lock = threading.Lock()

        # Thread de collecte / Collection thread
        self._thread: threading.Thread | None = None
        self._running = threading.Event()

        # Dernier snapshot complet / Latest full snapshot
        self._current: dict = {}

        # Historique glissant / Rolling history
        self._history: deque[dict] = deque(maxlen=HISTORY_MAX_SAMPLES)

        # Alertes en attente (vidées après lecture) / Pending alerts (cleared after read)
        self._alerts: list[dict] = []

        # Compteur CPU haute pour alerte soutenue / Sustained high CPU counter
        self._cpu_high_start: float | None = None

        # Drives connectés pour détection de changement / Connected drives for change detection
        self._known_drives: set[str] = set()

        # Derniers compteurs réseau / Last network counters (for delta)
        self._last_net_io: object | None = None

        # WMI instance (initialisée dans le thread) / WMI instance (initialised in thread)
        self._wmi_conn = None

        print("[SystemMonitor] Instance créée / Instance created")

    # ══════════════════════════════════════════════
    #  Public API
    # ══════════════════════════════════════════════

    def start(self) -> None:
        """Démarre le thread de monitoring / Start the monitoring thread."""
        if self._running.is_set():
            print("[SystemMonitor] Déjà en cours / Already running")
            return

        self._running.set()
        self._thread = threading.Thread(target=self._loop, name="SystemMonitor", daemon=True)
        self._thread.start()
        print("[SystemMonitor] Monitoring démarré (intervalle = {:.1f}s)".format(POLL_INTERVAL_S))

    def stop(self) -> None:
        """Arrête le monitoring proprement / Stop monitoring gracefully."""
        if not self._running.is_set():
            return
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=POLL_INTERVAL_S + 1)
            self._thread = None
        print("[SystemMonitor] Monitoring arrêté / Monitoring stopped")

    def get_status(self) -> dict:
        """Retourne le snapshot complet actuel / Return full current snapshot."""
        with self._lock:
            return dict(self._current)

    def get_alerts(self) -> list[dict]:
        """
        Retourne et vide les alertes en attente.
        Returns and clears pending alerts.
        """
        with self._lock:
            alerts = list(self._alerts)
            self._alerts.clear()
            return alerts

    def get_summary_text(self) -> str:
        """
        Résumé lisible pour sortie vocale.
        Human-readable summary for voice output.
        """
        with self._lock:
            s = dict(self._current)

        if not s:
            return "System monitor has no data yet."

        parts: list[str] = []

        # CPU
        cpu_pct = s.get("cpu_percent")
        if cpu_pct is not None:
            parts.append(f"CPU is at {cpu_pct:.0f}%")
        cpu_temp = s.get("cpu_temp")
        if cpu_temp is not None:
            parts.append(f"CPU temperature {cpu_temp:.0f}°C")

        # RAM
        ram = s.get("ram")
        if ram:
            parts.append(f"RAM usage {ram['percent']:.0f}% ({ram['used_gb']:.1f} of {ram['total_gb']:.1f} GB)")

        # GPU
        gpu = s.get("gpu")
        if gpu and gpu.get("available"):
            parts.append(
                f"GPU at {gpu['load_percent']:.0f}%, {gpu['temperature']}°C, "
                f"VRAM {gpu['vram_used_mb']:.0f}/{gpu['vram_total_mb']:.0f} MB"
            )

        # Batterie / Battery
        bat = s.get("battery")
        if bat and bat.get("percent") is not None:
            plug = "plugged in" if bat.get("plugged") else "on battery"
            parts.append(f"Battery {bat['percent']:.0f}% ({plug})")

        # Disque / Disk
        disks = s.get("disks", [])
        for d in disks:
            parts.append(f"Drive {d['mountpoint']}: {d['free_gb']:.1f} GB free of {d['total_gb']:.1f} GB")

        # Réseau / Network
        net = s.get("network")
        if net:
            parts.append(
                f"Network: {net['sent_delta_kb']:.1f} KB sent, {net['recv_delta_kb']:.1f} KB received this interval"
            )

        return ". ".join(parts) + "." if parts else "No system data available."

    def get_ui_data(self) -> dict:
        """
        Données formatées pour les barres UI de JARVIS.
        Formatted data for JARVIS UI bars.
        Returns dict with keys: cpu_pct, ram_pct, gpu_pct, gpu_temp, cpu_temp
        """
        with self._lock:
            s = dict(self._current)

        gpu = s.get("gpu", {})
        ram = s.get("ram", {})

        return {
            "cpu_pct": _safe_round(s.get("cpu_percent"), 1),
            "ram_pct": _safe_round(ram.get("percent"), 1),
            "gpu_pct": _safe_round(gpu.get("load_percent"), 1) if gpu.get("available") else None,
            "gpu_temp": _safe_round(gpu.get("temperature"), 0) if gpu.get("available") else None,
            "cpu_temp": _safe_round(s.get("cpu_temp"), 0),
        }

    # ══════════════════════════════════════════════
    #  Boucle principale du daemon / Main daemon loop
    # ══════════════════════════════════════════════

    def _loop(self) -> None:
        """Boucle de collecte exécutée dans le daemon thread."""
        # Initialiser COM pour WMI (obligatoire dans les threads secondaires sur Windows)
        if PYTHONCOM_AVAILABLE:
            pythoncom.CoInitialize()

        try:
            # Initialiser WMI dans ce thread (COM thread-affinity)
            if WMI_AVAILABLE:
                try:
                    self._wmi_conn = wmi_module.WMI(namespace=r"root\OpenHardwareMonitor")
                    print("[SystemMonitor] WMI OpenHardwareMonitor connecté")
                except Exception:
                    try:
                        self._wmi_conn = wmi_module.WMI(namespace=r"root\cimv2")
                        print("[SystemMonitor] WMI cimv2 connecté (fallback)")
                    except Exception as exc:
                        self._wmi_conn = None
                        print(f"[SystemMonitor] WMI indisponible: {exc}")

            # Détection initiale des drives / Initial drive detection
            self._known_drives = self._detect_drives()

            while self._running.is_set():
                try:
                    snapshot = self._collect_all()
                    with self._lock:
                        self._current = snapshot
                        self._history.append(snapshot)
                    self._evaluate_alerts(snapshot)
                except Exception as exc:
                    print(f"[SystemMonitor] Erreur dans la boucle: {exc}")
                time.sleep(POLL_INTERVAL_S)
        finally:
            if PYTHONCOM_AVAILABLE:
                pythoncom.CoUninitialize()

    # ══════════════════════════════════════════════
    #  Collecteurs individuels / Individual collectors
    # ══════════════════════════════════════════════

    def _collect_all(self) -> dict:
        """Collecte toutes les métriques et retourne un snapshot horodaté."""
        snapshot: dict = {
            "timestamp": datetime.now().isoformat(),
        }

        snapshot["cpu_percent"] = self._collect_cpu()
        snapshot["cpu_freq"] = self._collect_cpu_freq()
        snapshot["cpu_cores"] = self._collect_cpu_cores()
        snapshot["cpu_temp"] = self._collect_cpu_temp()
        snapshot["ram"] = self._collect_ram()
        snapshot["gpu"] = self._collect_gpu()
        snapshot["disks"] = self._collect_disks()
        snapshot["network"] = self._collect_network()
        snapshot["battery"] = self._collect_battery()
        snapshot["top_processes"] = self._collect_top_processes()
        snapshot["connected_drives"] = self._detect_and_notify_drives()

        return snapshot

    # ── CPU ───────────────────────────────────────

    def _collect_cpu(self) -> float | None:
        if not PSUTIL_AVAILABLE:
            return None
        try:
            return psutil.cpu_percent(interval=0)
        except Exception as exc:
            print(f"[SystemMonitor] CPU percent error: {exc}")
            return None

    def _collect_cpu_freq(self) -> dict | None:
        if not PSUTIL_AVAILABLE:
            return None
        try:
            freq = psutil.cpu_freq()
            if freq is None:
                return None
            return {
                "current_mhz": _safe_round(freq.current, 0),
                "min_mhz": _safe_round(freq.min, 0),
                "max_mhz": _safe_round(freq.max, 0),
            }
        except Exception as exc:
            print(f"[SystemMonitor] CPU freq error: {exc}")
            return None

    def _collect_cpu_cores(self) -> dict | None:
        if not PSUTIL_AVAILABLE:
            return None
        try:
            return {
                "physical": psutil.cpu_count(logical=False),
                "logical": psutil.cpu_count(logical=True),
            }
        except Exception as exc:
            print(f"[SystemMonitor] CPU cores error: {exc}")
            return None

    def _collect_cpu_temp(self) -> float | None:
        """
        Tente de lire la température CPU via WMI (OpenHardwareMonitor ou cimv2).
        Falls back gracefully if unavailable.
        """
        if not WMI_AVAILABLE or self._wmi_conn is None:
            return None
        try:
            # Tentative OpenHardwareMonitor
            sensors = self._wmi_conn.query(
                "SELECT Value FROM Sensor WHERE SensorType='Temperature' AND Name LIKE '%CPU%'"
            )
            if sensors:
                temps = [float(s.Value) for s in sensors if s.Value is not None]
                if temps:
                    return _safe_round(max(temps), 1)
        except Exception:
            pass

        try:
            # Fallback MSAcpi_ThermalZoneTemperature (requires admin)
            zones = self._wmi_conn.query(
                "SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature"
            )
            if zones:
                # Valeur en dixièmes de Kelvin
                temps_c = [(float(z.CurrentTemperature) / 10.0) - 273.15 for z in zones]
                valid = [t for t in temps_c if 0 < t < 150]
                if valid:
                    return _safe_round(max(valid), 1)
        except Exception:
            pass

        return None

    # ── RAM ───────────────────────────────────────

    def _collect_ram(self) -> dict | None:
        if not PSUTIL_AVAILABLE:
            return None
        try:
            vm = psutil.virtual_memory()
            return {
                "total_gb": _safe_round(vm.total / (1024 ** 3), 2),
                "used_gb": _safe_round(vm.used / (1024 ** 3), 2),
                "available_gb": _safe_round(vm.available / (1024 ** 3), 2),
                "percent": _safe_round(vm.percent, 1),
            }
        except Exception as exc:
            print(f"[SystemMonitor] RAM error: {exc}")
            return None

    # ── GPU ───────────────────────────────────────

    def _collect_gpu(self) -> dict:
        if not GPUTIL_AVAILABLE:
            return {"available": False}
        try:
            gpus = GPUtil.getGPUs()
            if not gpus:
                return {"available": False}
            # On prend le premier GPU
            g = gpus[0]
            return {
                "available": True,
                "name": g.name,
                "load_percent": _safe_round((g.load or 0) * 100, 1),
                "temperature": _safe_round(g.temperature, 0),
                "vram_used_mb": _safe_round(g.memoryUsed, 0),
                "vram_total_mb": _safe_round(g.memoryTotal, 0),
                "vram_free_mb": _safe_round(g.memoryFree, 0),
            }
        except Exception as exc:
            print(f"[SystemMonitor] GPU error: {exc}")
            return {"available": False}

    # ── Disk ──────────────────────────────────────

    def _collect_disks(self) -> list[dict]:
        if not PSUTIL_AVAILABLE:
            return []
        results: list[dict] = []
        try:
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    results.append({
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "fstype": part.fstype,
                        "total_gb": _safe_round(usage.total / (1024 ** 3), 2),
                        "used_gb": _safe_round(usage.used / (1024 ** 3), 2),
                        "free_gb": _safe_round(usage.free / (1024 ** 3), 2),
                        "percent_used": _safe_round(usage.percent, 1),
                    })
                except (PermissionError, OSError):
                    # Certains drives ne sont pas accessibles (CD-ROM vide, etc.)
                    pass
        except Exception as exc:
            print(f"[SystemMonitor] Disk error: {exc}")
        return results

    # ── Network ───────────────────────────────────

    def _collect_network(self) -> dict | None:
        if not PSUTIL_AVAILABLE:
            return None
        try:
            counters = psutil.net_io_counters()
            sent_delta = 0.0
            recv_delta = 0.0

            if self._last_net_io is not None:
                sent_delta = counters.bytes_sent - self._last_net_io.bytes_sent
                recv_delta = counters.bytes_recv - self._last_net_io.bytes_recv
                # Protéger contre les compteurs qui rollover (rare)
                if sent_delta < 0:
                    sent_delta = 0.0
                if recv_delta < 0:
                    recv_delta = 0.0

            self._last_net_io = counters

            return {
                "bytes_sent_total": counters.bytes_sent,
                "bytes_recv_total": counters.bytes_recv,
                "sent_delta_kb": _safe_round(sent_delta / 1024, 2),
                "recv_delta_kb": _safe_round(recv_delta / 1024, 2),
            }
        except Exception as exc:
            print(f"[SystemMonitor] Network error: {exc}")
            return None

    # ── Battery ───────────────────────────────────

    def _collect_battery(self) -> dict | None:
        if not PSUTIL_AVAILABLE:
            return None
        try:
            bat = psutil.sensors_battery()
            if bat is None:
                # Desktop / pas de batterie
                return {"percent": None, "plugged": None, "secs_left": None}
            return {
                "percent": _safe_round(bat.percent, 0),
                "plugged": bat.power_plugged,
                "secs_left": bat.secsleft if bat.secsleft != psutil.POWER_TIME_UNLIMITED else None,
            }
        except Exception as exc:
            print(f"[SystemMonitor] Battery error: {exc}")
            return None

    # ── Top Processes ─────────────────────────────

    def _collect_top_processes(self) -> list[dict]:
        if not PSUTIL_AVAILABLE:
            return []
        try:
            procs: list[dict] = []
            for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                try:
                    info = p.info
                    if info and info.get("cpu_percent") is not None:
                        procs.append({
                            "pid": info["pid"],
                            "name": info.get("name", "unknown"),
                            "cpu_percent": _safe_round(info["cpu_percent"], 1),
                            "memory_percent": _safe_round(info.get("memory_percent"), 1),
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            procs.sort(key=lambda x: x.get("cpu_percent", 0) or 0, reverse=True)
            return procs[:TOP_PROCESSES_COUNT]
        except Exception as exc:
            print(f"[SystemMonitor] Processes error: {exc}")
            return []

    # ── Drive / USB Detection ─────────────────────

    def _detect_drives(self) -> set[str]:
        """Détecte les lettres de lecteur disponibles sous Windows."""
        drives: set[str] = set()
        try:
            if PSUTIL_AVAILABLE:
                for part in psutil.disk_partitions(all=False):
                    drives.add(part.device)
            else:
                # Fallback : itérer les lettres de lecteur
                for letter in string.ascii_uppercase:
                    path = Path(f"{letter}:\\")
                    if path.exists():
                        drives.add(f"{letter}:\\")
        except Exception as exc:
            print(f"[SystemMonitor] Drive detection error: {exc}")
        return drives

    def _detect_and_notify_drives(self) -> list[str]:
        """
        Compare les drives actuels aux connus, émet alerte si nouveau.
        Compare current drives to known set, emit alert if new.
        """
        current = self._detect_drives()

        new_drives = current - self._known_drives
        removed_drives = self._known_drives - current

        for d in new_drives:
            alert = {
                "type": "drive_connected",
                "severity": "info",
                "message": f"New drive detected: {d}",
                "timestamp": datetime.now().isoformat(),
            }
            with self._lock:
                self._alerts.append(alert)
            print(f"[SystemMonitor] 🔌 Nouveau drive détecté: {d}")

        for d in removed_drives:
            alert = {
                "type": "drive_disconnected",
                "severity": "info",
                "message": f"Drive removed: {d}",
                "timestamp": datetime.now().isoformat(),
            }
            with self._lock:
                self._alerts.append(alert)
            print(f"[SystemMonitor] ⏏️ Drive retiré: {d}")

        self._known_drives = current
        return sorted(current)

    # ══════════════════════════════════════════════
    #  Évaluation des alertes / Alert evaluation
    # ══════════════════════════════════════════════

    def _evaluate_alerts(self, snapshot: dict) -> None:
        """Vérifie les seuils et génère les alertes appropriées."""
        now = time.monotonic()

        # ── CPU soutenu > 90 % pendant 30s ────────
        cpu_pct = snapshot.get("cpu_percent")
        if cpu_pct is not None:
            if cpu_pct > CPU_ALERT_THRESHOLD_PCT:
                if self._cpu_high_start is None:
                    self._cpu_high_start = now
                elif (now - self._cpu_high_start) >= CPU_ALERT_SUSTAIN_S:
                    self._push_alert(
                        "cpu_high",
                        "warning",
                        f"CPU usage above {CPU_ALERT_THRESHOLD_PCT}% for over {CPU_ALERT_SUSTAIN_S:.0f}s "
                        f"(current: {cpu_pct:.1f}%)"
                    )
                    # Reset pour ne pas spam / Reset to avoid spamming
                    self._cpu_high_start = now
            else:
                self._cpu_high_start = None

        # ── RAM > 90 % ────────────────────────────
        ram = snapshot.get("ram")
        if ram and ram.get("percent") is not None:
            if ram["percent"] > RAM_ALERT_THRESHOLD_PCT:
                self._push_alert(
                    "ram_high",
                    "warning",
                    f"RAM usage critical: {ram['percent']:.1f}% "
                    f"({ram['used_gb']:.1f}/{ram['total_gb']:.1f} GB)"
                )

        # ── GPU temp > 85 °C ──────────────────────
        gpu = snapshot.get("gpu", {})
        if gpu.get("available") and gpu.get("temperature") is not None:
            if gpu["temperature"] > GPU_TEMP_ALERT_C:
                self._push_alert(
                    "gpu_temp_high",
                    "warning",
                    f"GPU temperature high: {gpu['temperature']}°C (threshold: {GPU_TEMP_ALERT_C}°C)"
                )

        # ── Disk < 10 % libre ─────────────────────
        for disk in snapshot.get("disks", []):
            free_pct = 100.0 - (disk.get("percent_used") or 0)
            if free_pct < DISK_FREE_ALERT_PCT:
                self._push_alert(
                    f"disk_low_{disk['mountpoint']}",
                    "warning",
                    f"Disk {disk['mountpoint']} low on space: {disk['free_gb']:.1f} GB free "
                    f"({free_pct:.1f}% remaining)"
                )

        # ── Batterie < 15 % ───────────────────────
        bat = snapshot.get("battery")
        if bat and bat.get("percent") is not None and not bat.get("plugged"):
            if bat["percent"] < BATTERY_ALERT_PCT:
                self._push_alert(
                    "battery_low",
                    "critical",
                    f"Battery low: {bat['percent']:.0f}% — plug in soon!"
                )

        # ── Processus suspect ─────────────────────
        for proc in snapshot.get("top_processes", []):
            name = (proc.get("name") or "").lower()
            cpu = proc.get("cpu_percent") or 0
            
            # Ignorer l'idle process, les instances python et wmi
            is_idle = "idle" in name or name == "system idle process"
            is_python = name.startswith("python") or "python" in name
            
            if cpu > SUSPICIOUS_CPU_THRESHOLD and name not in KNOWN_PROCESS_NAMES and not is_idle and not is_python:
                self._push_alert(
                    f"suspicious_process_{proc.get('pid')}",
                    "info",
                    f"Suspicious process: '{proc['name']}' (PID {proc['pid']}) "
                    f"using {cpu:.1f}% CPU"
                )

    def _push_alert(self, alert_type: str, severity: str, message: str) -> None:
        """
        Ajoute une alerte en évitant les doublons identiques consécutifs.
        Adds an alert, avoiding identical consecutive duplicates.
        """
        alert = {
            "type": alert_type,
            "severity": severity,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }
        with self._lock:
            # Éviter le spam : ne pas remettre la même alerte type si déjà présente
            for existing in self._alerts:
                if existing["type"] == alert_type:
                    return
            self._alerts.append(alert)
        print(f"[SystemMonitor] ⚠️  Alert [{severity}] {message}")

    # ══════════════════════════════════════════════
    #  Représentation / Representation
    # ══════════════════════════════════════════════

    def __repr__(self) -> str:
        running = self._running.is_set()
        samples = len(self._history)
        return f"<SystemMonitor running={running} samples={samples}/{HISTORY_MAX_SAMPLES}>"


# ──────────────────────────────────────────────
#  Instantiation rapide / Quick instantiation
# ──────────────────────────────────────────────

def get_monitor() -> SystemMonitor:
    """Raccourci pour obtenir le singleton / Shortcut to get the singleton."""
    return SystemMonitor()


# ──────────────────────────────────────────────
#  Exécution directe pour test / Direct run for testing
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("[SystemMonitor] === Test direct ===")
    monitor = get_monitor()
    monitor.start()

    try:
        for i in range(5):
            time.sleep(3)
            print(f"\n--- Tick {i + 1} ---")
            print("UI Data:", monitor.get_ui_data())
            alerts = monitor.get_alerts()
            if alerts:
                print("Alerts:", alerts)
            print("Summary:", monitor.get_summary_text())
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        print("[SystemMonitor] === Test terminé ===")
