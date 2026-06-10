# core/scheduler.py — Planificateur proactif pour JARVIS
# Proactive task scheduler: recurring routines, one-shot reminders, alert system
# Fonctionne en daemon thread, vérifie toutes les 30 secondes

import threading
import time
import platform
import subprocess
from datetime import datetime, timedelta
from typing import Callable, Optional, Any


class _ScheduledTask:
    """
    Représente une tâche planifiée (récurrente ou ponctuelle).
    Represents a scheduled task (recurring or one-shot).
    """

    def __init__(
        self,
        name: str,
        callback: Callable,
        interval_seconds: int = 0,
        one_shot: bool = False,
        fire_at: Optional[datetime] = None,
        message: str = "",
    ):
        self.name: str = name
        self.callback: Callable = callback
        self.interval_seconds: int = interval_seconds
        self.one_shot: bool = one_shot
        self.fire_at: Optional[datetime] = fire_at  # For one-shot / delayed tasks
        self.message: str = message
        self.last_run: Optional[datetime] = None
        self.enabled: bool = True

    def is_due(self, now: datetime) -> bool:
        """Vérifie si la tâche doit s'exécuter maintenant. / Check if task should run now."""
        if not self.enabled:
            return False

        # --- One-shot: fire once at a specific time ---
        if self.one_shot and self.fire_at:
            return now >= self.fire_at

        # --- Recurring: interval-based ---
        if self.interval_seconds > 0:
            if self.last_run is None:
                return True
            elapsed = (now - self.last_run).total_seconds()
            return elapsed >= self.interval_seconds

        return False


class JarvisScheduler:
    """
    Planificateur proactif de JARVIS.
    Gère les routines récurrentes (briefing matinal, résumé du soir, checks système)
    et les rappels ponctuels ("rappelle-moi dans 2 heures").

    Proactive scheduler for JARVIS.
    Manages recurring routines (morning briefing, evening summary, system checks)
    and one-shot reminders ("remind me in 2 hours").
    """

    # --- Tick interval: how often the scheduler loop checks tasks ---
    TICK_INTERVAL: float = 30.0  # seconds

    def __init__(self):
        # Thread safety / Sécurité des threads
        self._lock: threading.Lock = threading.Lock()
        self._stop_event: threading.Event = threading.Event()

        # Registered tasks / Tâches enregistrées
        self._tasks: list[_ScheduledTask] = []

        # Pending alerts queue (thread-safe) / File d'alertes en attente
        self._pending_alerts: list[dict[str, Any]] = []

        # Alert callback: (alert_type: str, message: str, data: dict) -> None
        self._on_alert: Optional[Callable[[str, str, dict], None]] = None

        # Scheduler thread
        self._thread: Optional[threading.Thread] = None
        self._running: bool = False

        # Morning briefing state / État du briefing matinal
        self._morning_done_today: Optional[str] = None  # date string "YYYY-MM-DD"

        # Register built-in routines / Enregistrement des routines intégrées
        self._register_builtins()

        print("[Scheduler] ✅ Initialisé / Initialized")

    # ──────────────────────────────────────────────
    #  Built-in routines / Routines intégrées
    # ──────────────────────────────────────────────

    def _register_builtins(self) -> None:
        """Enregistre les routines par défaut. / Register default routines."""

        # Morning briefing — triggered on first interaction between 7:00–9:00 AM
        # This is checked manually in the tick loop, not via interval
        self._tasks.append(
            _ScheduledTask(
                name="morning_briefing",
                callback=self._routine_morning_briefing,
                interval_seconds=0,  # Not interval-based; uses time-window logic
                one_shot=False,
            )
        )

        # Evening summary — triggers at 22:00
        self._tasks.append(
            _ScheduledTask(
                name="evening_summary",
                callback=self._routine_evening_summary,
                interval_seconds=0,  # Uses time-window logic
                one_shot=False,
            )
        )

        # System check — every 5 minutes
        self._tasks.append(
            _ScheduledTask(
                name="system_check",
                callback=self._routine_system_check,
                interval_seconds=300,  # 5 minutes
                one_shot=False,
            )
        )

        print("[Scheduler]   → Routines intégrées enregistrées / Built-in routines registered")

    def _routine_morning_briefing(self) -> Optional[dict]:
        """
        Briefing matinal: conditions système, heure, date.
        Morning briefing: system conditions, time, date.
        Triggered between 7:00–9:00 on first user interaction.
        """
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        # Already done today? / Déjà fait aujourd'hui ?
        if self._morning_done_today == today_str:
            return None

        # Only between 7:00 and 9:00 / Seulement entre 7h et 9h
        if not (7 <= now.hour < 9):
            return None

        # Mark as done / Marquer comme fait
        self._morning_done_today = today_str

        # Collect system status / Récupérer l'état du système
        system_status = self._get_system_status()

        greeting = "morning"
        data = {
            "routine_type": "morning",
            "greeting": greeting,
            "time": now.strftime("%H:%M"),
            "date": now.strftime("%A, %B %d, %Y"),
            "weather": "Weather data not yet integrated",  # Placeholder for weather module
            "system_status": system_status,
            "pending_reminders": self._count_pending_reminders(),
        }

        print(f"[Scheduler] 🌅 Morning briefing triggered at {now.strftime('%H:%M')}")
        return data

    def _routine_evening_summary(self) -> Optional[dict]:
        """
        Résumé du soir: déclenché à 22h.
        Evening summary: triggered at 22:00.
        """
        now = datetime.now()

        # Only at 22:xx, within the first tick window / Seulement à 22h
        if now.hour != 22:
            return None

        # Avoid duplicate: check if we already ran this hour
        today_key = now.strftime("%Y-%m-%d-22")
        task = self._find_task("evening_summary")
        if task and task.last_run:
            last_key = task.last_run.strftime("%Y-%m-%d-%H")
            if last_key == today_key:
                return None

        data = {
            "routine_type": "evening",
            "time": now.strftime("%H:%M"),
            "date": now.strftime("%A, %B %d, %Y"),
            "summary": "Day summary not yet integrated",  # Placeholder
            "system_status": self._get_system_status(),
        }

        print(f"[Scheduler] 🌙 Evening summary triggered at {now.strftime('%H:%M')}")
        return data

    def _routine_system_check(self) -> Optional[dict]:
        """
        Vérification système: santé CPU, RAM, disque.
        System check: CPU, RAM, disk health.
        Attempts to import SystemMonitor; falls back to psutil or basic checks.
        """
        alerts: list[str] = []

        # Try psutil for detailed monitoring / Essayer psutil
        try:
            import psutil

            # CPU check / Vérification CPU
            cpu_percent = psutil.cpu_percent(interval=0)
            if cpu_percent > 90:
                alerts.append(f"⚠️ CPU usage critical: {cpu_percent}%")

            # RAM check / Vérification RAM
            memory = psutil.virtual_memory()
            if memory.percent > 85:
                alerts.append(f"⚠️ RAM usage high: {memory.percent}%")

            # Disk check / Vérification disque
            disk = psutil.disk_usage("C:\\")
            disk_percent = disk.percent
            if disk_percent > 90:
                alerts.append(f"⚠️ Disk C: nearly full: {disk_percent}%")

            # Battery check (laptops) / Vérification batterie
            battery = psutil.sensors_battery()
            if battery and not battery.power_plugged and battery.percent < 15:
                alerts.append(f"🔋 Battery critical: {battery.percent}%")

        except ImportError:
            # Fallback: basic Windows checks / Solution de secours
            try:
                if platform.system() == "Windows":
                    # Basic disk check via wmic
                    result = subprocess.run(
                        'wmic logicaldisk where "DeviceID=\'C:\'" get FreeSpace,Size /format:value',
                        shell=True,
                        capture_output=True,
                        timeout=5,
                    )
                    output = result.stdout.decode('cp850', errors='ignore').strip()
                    if output:
                        lines = [l.strip() for l in output.split("\n") if "=" in l]
                        values = {}
                        for line in lines:
                            key, val = line.split("=", 1)
                            values[key.strip()] = val.strip()
                        if "FreeSpace" in values and "Size" in values:
                            try:
                                free = int(values["FreeSpace"])
                                total = int(values["Size"])
                                used_pct = ((total - free) / total) * 100
                                if used_pct > 90:
                                    alerts.append(f"⚠️ Disk C: nearly full: {used_pct:.0f}%")
                            except (ValueError, ZeroDivisionError):
                                pass
            except Exception as e:
                print(f"[Scheduler] ⚠️ System check fallback error: {e}")

        except Exception as e:
            print(f"[Scheduler] ⚠️ System check error: {e}")

        # Only return data if there are alerts / Retourner seulement s'il y a des alertes
        if alerts:
            data = {
                "routine_type": "system_check",
                "alerts": alerts,
                "timestamp": datetime.now().isoformat(),
            }
            print(f"[Scheduler] 🔔 System alerts detected: {len(alerts)}")
            return data

        return None  # No alerts = no notification

    # ──────────────────────────────────────────────
    #  Public API / API publique
    # ──────────────────────────────────────────────

    def start(self, on_alert_callback: Optional[Callable[[str, str, dict], None]] = None) -> None:
        """
        Démarre le planificateur dans un thread daemon.
        Start the scheduler in a daemon thread.

        Args:
            on_alert_callback: Called when a routine triggers or an alert fires.
                               Signature: (alert_type: str, message: str, data: dict) -> None
        """
        with self._lock:
            if self._running:
                print("[Scheduler] ⚠️ Déjà en cours / Already running")
                return

            self._on_alert = on_alert_callback
            self._running = True

        self._thread = threading.Thread(
            target=self._scheduler_loop,
            name="JarvisScheduler",
            daemon=True,
        )
        self._thread.start()
        print("[Scheduler] 🚀 Planificateur démarré / Scheduler started")

    def stop(self) -> None:
        """Arrête le planificateur proprement. / Stop the scheduler gracefully."""
        with self._lock:
            if not self._running:
                return
            self._running = False
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            print("[Scheduler] 🛑 Planificateur arrêté / Scheduler stopped")

    def add_reminder(self, message: str, delay_seconds: int) -> str:
        """
        Ajoute un rappel ponctuel. / Add a one-shot reminder.

        Args:
            message: Reminder message to deliver.
            delay_seconds: Seconds from now until the reminder fires.

        Returns:
            Confirmation string with fire time.
        """
        fire_at = datetime.now() + timedelta(seconds=delay_seconds)
        task_name = f"reminder_{int(time.time())}_{hash(message) & 0xFFFF:04x}"

        task = _ScheduledTask(
            name=task_name,
            callback=lambda: {
                "routine_type": "reminder",
                "message": message,
                "scheduled_for": fire_at.isoformat(),
            },
            one_shot=True,
            fire_at=fire_at,
            message=message,
        )

        with self._lock:
            self._tasks.append(task)

        fire_str = fire_at.strftime("%H:%M:%S")
        print(f"[Scheduler] ⏰ Reminder added: '{message}' → fires at {fire_str}")
        return f"Reminder set for {fire_at.strftime('%I:%M %p')}: {message}"

    def add_recurring(self, name: str, interval_seconds: int, callback: Callable) -> None:
        """
        Ajoute une tâche récurrente personnalisée. / Add a custom recurring task.

        Args:
            name: Unique task name.
            interval_seconds: Interval between executions.
            callback: Callable that returns a dict or None.
        """
        task = _ScheduledTask(
            name=name,
            callback=callback,
            interval_seconds=interval_seconds,
            one_shot=False,
        )

        with self._lock:
            # Remove existing task with same name / Supprimer tâche existante
            self._tasks = [t for t in self._tasks if t.name != name]
            self._tasks.append(task)

        print(f"[Scheduler] 🔄 Recurring task added: '{name}' every {interval_seconds}s")

    def mark_morning_done(self) -> None:
        """
        Marque le briefing matinal comme fait pour aujourd'hui.
        Prevents duplicate morning briefings for the current day.
        """
        with self._lock:
            self._morning_done_today = datetime.now().strftime("%Y-%m-%d")
        print("[Scheduler] ✓ Morning briefing marked as done for today")

    def get_pending_alerts(self) -> list[dict[str, Any]]:
        """
        Retourne et vide la file d'alertes en attente.
        Returns and clears the pending alerts queue.

        Returns:
            List of alert dicts: [{alert_type, message, data, timestamp}, ...]
        """
        with self._lock:
            alerts = list(self._pending_alerts)
            self._pending_alerts.clear()
        return alerts

    # ──────────────────────────────────────────────
    #  Internal / Fonctions internes
    # ──────────────────────────────────────────────

    def _scheduler_loop(self) -> None:
        """
        Boucle principale du planificateur. Vérifie les tâches toutes les 30 secondes.
        Main scheduler loop. Checks tasks every 30 seconds.
        """
        print("[Scheduler] 🔁 Scheduler loop started")

        while True:
            with self._lock:
                if not self._running:
                    break

            try:
                self._tick()
            except Exception as e:
                print(f"[Scheduler] ❌ Tick error: {e}")

            # Wait efficiently; stop_event wakes us up early on shutdown
            self._stop_event.wait(timeout=self.TICK_INTERVAL)
            if self._stop_event.is_set():
                return

        print("[Scheduler] 🔁 Scheduler loop ended")

    def _tick(self) -> None:
        """
        Un cycle du planificateur: vérifie toutes les tâches.
        One scheduler cycle: checks all tasks.
        """
        now = datetime.now()
        tasks_to_remove: list[str] = []

        with self._lock:
            tasks_snapshot = list(self._tasks)

        for task in tasks_snapshot:
            if not task.enabled:
                continue

            should_run = False

            # Special handling for time-window routines
            if task.name == "morning_briefing":
                # Morning briefing uses its own time-window check internally
                should_run = (7 <= now.hour < 9) and (self._morning_done_today != now.strftime("%Y-%m-%d"))
            elif task.name == "evening_summary":
                # Evening summary at 22:xx
                if now.hour == 22:
                    if task.last_run is None or task.last_run.strftime("%Y-%m-%d") != now.strftime("%Y-%m-%d"):
                        should_run = True
            else:
                should_run = task.is_due(now)

            if not should_run:
                continue

            # Execute the task / Exécuter la tâche
            try:
                result = task.callback()
                task.last_run = now

                if result is not None:
                    # Determine alert type and message
                    alert_type = result.get("routine_type", task.name) if isinstance(result, dict) else task.name
                    message = result.get("message", f"Routine '{task.name}' triggered") if isinstance(result, dict) else str(result)
                    data = result if isinstance(result, dict) else {"result": result}

                    # Queue the alert / Mettre en file d'attente l'alerte
                    alert_entry = {
                        "alert_type": alert_type,
                        "message": message,
                        "data": data,
                        "timestamp": now.isoformat(),
                    }

                    with self._lock:
                        self._pending_alerts.append(alert_entry)

                    # Fire callback if registered / Déclencher le callback s'il est enregistré
                    if self._on_alert:
                        try:
                            self._on_alert(alert_type, message, data)
                        except Exception as cb_err:
                            print(f"[Scheduler] ⚠️ Alert callback error: {cb_err}")

                # Remove one-shot tasks after execution
                if task.one_shot:
                    tasks_to_remove.append(task.name)
                    print(f"[Scheduler] ✓ One-shot task '{task.name}' completed and removed")

            except Exception as e:
                print(f"[Scheduler] ❌ Task '{task.name}' error: {e}")

        # Clean up one-shot tasks / Nettoyer les tâches ponctuelles
        if tasks_to_remove:
            with self._lock:
                self._tasks = [t for t in self._tasks if t.name not in tasks_to_remove]

    def _find_task(self, name: str) -> Optional[_ScheduledTask]:
        """Trouve une tâche par nom. / Find a task by name."""
        with self._lock:
            for task in self._tasks:
                if task.name == name:
                    return task
        return None

    def _count_pending_reminders(self) -> int:
        """Compte les rappels en attente. / Count pending reminders."""
        with self._lock:
            return sum(1 for t in self._tasks if t.one_shot and t.enabled)

    def _get_system_status(self) -> dict[str, Any]:
        """
        Collecte l'état du système pour le briefing.
        Collect system status for the briefing.
        """
        status: dict[str, Any] = {
            "platform": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "uptime": "unknown",
        }

        # Try to get uptime on Windows / Essayer d'obtenir l'uptime sous Windows
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    "net statistics workstation",
                    shell=True,
                    capture_output=True,
                    timeout=5,
                )
                output_str = result.stdout.decode('cp850', errors='ignore')
                for line in output_str.split("\n"):
                    if "Statistics since" in line or "Statistiques depuis" in line:
                        status["uptime"] = line.strip()
                        break
        except Exception:
            pass

        # Try psutil for richer data / Essayer psutil pour des données plus riches
        try:
            import psutil

            status["cpu_percent"] = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            status["ram_total_gb"] = round(mem.total / (1024 ** 3), 1)
            status["ram_used_percent"] = mem.percent
            disk = psutil.disk_usage("C:\\")
            status["disk_c_used_percent"] = disk.percent
            status["disk_c_free_gb"] = round(disk.free / (1024 ** 3), 1)

            battery = psutil.sensors_battery()
            if battery:
                status["battery_percent"] = battery.percent
                status["battery_plugged"] = battery.power_plugged
        except ImportError:
            status["note"] = "Install psutil for detailed system metrics"

        return status


# ──────────────────────────────────────────────
#  Module-level convenience / Singleton pratique
# ──────────────────────────────────────────────

_default_scheduler: Optional[JarvisScheduler] = None


def get_scheduler() -> JarvisScheduler:
    """
    Retourne le planificateur singleton. / Return the singleton scheduler.
    Creates it on first call.
    """
    global _default_scheduler
    if _default_scheduler is None:
        _default_scheduler = JarvisScheduler()
    return _default_scheduler


if __name__ == "__main__":
    # Quick test / Test rapide
    def test_alert(alert_type: str, message: str, data: dict) -> None:
        print(f"\n{'='*50}")
        print(f"🔔 ALERT: [{alert_type}] {message}")
        print(f"   Data: {data}")
        print(f"{'='*50}\n")

    scheduler = JarvisScheduler()
    scheduler.start(on_alert_callback=test_alert)
    scheduler.add_reminder("Test reminder - check this!", delay_seconds=35)
    scheduler.add_recurring(
        "test_heartbeat",
        interval_seconds=60,
        callback=lambda: {"routine_type": "heartbeat", "message": "Still alive!"},
    )

    print("[Scheduler] 🧪 Test mode — running for 2 minutes (Ctrl+C to stop)")
    try:
        time.sleep(120)
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()
        print("[Scheduler] 🧪 Test complete")
