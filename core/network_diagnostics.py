# core/network_diagnostics.py
# Module de diagnostics réseau & cybersécurité pour JARVIS
# Network diagnostics: speedtest, port scanner, active connections monitor

import socket
import time
import urllib.request
import threading
from datetime import datetime

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

# ── Ports sensibles à surveiller ─────────────────────────────────────────────
SENSITIVE_PORTS = {
    22:   "SSH",
    23:   "Telnet",
    3389: "RDP",
    5900: "VNC",
    5800: "VNC-HTTP",
    4444: "Metasploit",
    1337: "BackDoor",
    6666: "BackDoor",
    6667: "IRC/C2",
    31337: "BackOrifice",
}

# CDN pairs (url, expected_size_bytes) — petits fichiers pour mesure rapide
_CDN_TEST_TARGETS = [
    ("http://speed.cloudflare.com/__down?bytes=1000000", 1_000_000),
    ("http://www.google.com/generate_204",               0),
]


class NetworkDiagnostics:
    """
    Classe utilitaire pour diagnostics réseau en temps réel.
    Toutes les méthodes sont thread-safe et synchrones (utiliser run_in_executor si besoin).
    """

    # ── Speedtest ─────────────────────────────────────────────────────────────

    def get_speedtest(self, timeout: int = 10) -> dict:
        """
        Mesure le ping (ms) et le débit descendant (Mbps) sans bibliothèque externe.
        Utilise Cloudflare Speed Test CDN — aucune installation requise.
        Retourne un dict { ping_ms, download_mbps, error }.
        """
        result = {"ping_ms": None, "download_mbps": None, "error": None}

        # — Ping (latence HTTP) —
        try:
            ping_url = "http://www.google.com/generate_204"
            req = urllib.request.Request(ping_url, headers={"User-Agent": "JARVIS/1.0"})
            t0 = time.perf_counter()
            with urllib.request.urlopen(req, timeout=5):
                pass
            result["ping_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        except Exception as e:
            result["ping_ms"] = None
            result["error"] = f"Ping failed: {e}"

        # — Download speed —
        try:
            dl_url = "http://speed.cloudflare.com/__down?bytes=2000000"
            req = urllib.request.Request(dl_url, headers={"User-Agent": "JARVIS/1.0"})
            t0 = time.perf_counter()
            total_bytes = 0
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
            elapsed = time.perf_counter() - t0
            if elapsed > 0:
                result["download_mbps"] = round((total_bytes * 8) / (elapsed * 1_000_000), 2)
        except Exception as e:
            result["download_mbps"] = None
            if result["error"]:
                result["error"] += f" | Download failed: {e}"
            else:
                result["error"] = f"Download failed: {e}"

        return result

    def format_speedtest(self, data: dict) -> str:
        """Formate le résultat du speedtest en texte lisible."""
        if data.get("error") and not data.get("download_mbps"):
            return f"Network test failed: {data['error']}"
        lines = []
        if data.get("ping_ms") is not None:
            lines.append(f"Ping: {data['ping_ms']} ms")
        if data.get("download_mbps") is not None:
            lines.append(f"Download: {data['download_mbps']} Mbps")
        if not lines:
            return "Network test inconclusive."
        return " | ".join(lines)

    # ── Active Connections Monitor ────────────────────────────────────────────

    def get_active_connections(self) -> list[dict]:
        """
        Liste les connexions réseau actives (ESTABLISHED) via psutil.
        Retourne une liste de dicts: { pid, name, lport, raddr, rport, status }.
        """
        if not _PSUTIL:
            return []
        connections = []
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.status not in ("ESTABLISHED", "LISTEN"):
                    continue
                raddr = conn.raddr.ip if conn.raddr else None
                rport = conn.raddr.port if conn.raddr else None
                lport = conn.laddr.port if conn.laddr else None
                pid = conn.pid
                name = "unknown"
                try:
                    if pid:
                        p = psutil.Process(pid)
                        name = p.name()
                except Exception:
                    pass
                connections.append({
                    "pid":    pid,
                    "name":   name,
                    "lport":  lport,
                    "raddr":  raddr,
                    "rport":  rport,
                    "status": conn.status,
                })
        except Exception as e:
            print(f"[NetDiag] ⚠️ Connections error: {e}")
        return connections

    # ── Security Alert Scanner ────────────────────────────────────────────────

    def get_security_alerts(self) -> list[dict]:
        """
        Détecte les connexions entrantes ou sortantes vers des ports sensibles.
        Retourne une liste d'alertes: { port, service, raddr, rport, process }.
        """
        alerts = []
        conns = self.get_active_connections()
        for c in conns:
            # Connexion externe vers un port local sensible (LISTEN ou connexion active)
            if c["lport"] in SENSITIVE_PORTS and c["status"] == "ESTABLISHED":
                alerts.append({
                    "port":    c["lport"],
                    "service": SENSITIVE_PORTS[c["lport"]],
                    "raddr":   c.get("raddr"),
                    "rport":   c.get("rport"),
                    "process": c.get("name"),
                    "type":    "inbound_sensitive_port",
                })
            # Connexion sortante vers un port suspect
            if c["rport"] in SENSITIVE_PORTS and c["raddr"] and not _is_local(c["raddr"]):
                alerts.append({
                    "port":    c["rport"],
                    "service": SENSITIVE_PORTS[c["rport"]],
                    "raddr":   c.get("raddr"),
                    "rport":   c.get("rport"),
                    "process": c.get("name"),
                    "type":    "outbound_suspicious",
                })
        return alerts

    # ── Port Scanner ──────────────────────────────────────────────────────────

    def scan_ports(self, host: str = "127.0.0.1", ports: list[int] = None, timeout: float = 0.5) -> dict:
        """
        Scanne une liste de ports sur l'hôte donné.
        Retourne un dict { port: "open"|"closed" }.
        Par défaut scanne les ports sensibles sur localhost.
        """
        if ports is None:
            ports = list(SENSITIVE_PORTS.keys())
        results = {}
        for port in ports:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(timeout)
                res = s.connect_ex((host, port))
                s.close()
                results[port] = "open" if res == 0 else "closed"
            except Exception:
                results[port] = "error"
        return results

    # ── Full Diagnostic Report ────────────────────────────────────────────────

    def full_report(self) -> str:
        """
        Rapport complet : ping, débit, alertes sécurité.
        Synchrone — utiliser run_in_executor depuis main.py.
        """
        lines = [f"[NET DIAGNOSTICS] {datetime.now().strftime('%H:%M:%S')}"]

        # Speedtest
        try:
            spd = self.get_speedtest(timeout=12)
            lines.append("◈ " + self.format_speedtest(spd))
        except Exception as e:
            lines.append(f"◈ Speedtest error: {e}")

        # Security alerts
        try:
            alerts = self.get_security_alerts()
            if alerts:
                lines.append(f"⚠️ {len(alerts)} SECURITY ALERT(S) DETECTED:")
                for a in alerts:
                    lines.append(
                        f"   • [{a['service']} port {a['port']}] "
                        f"Process: {a['process']} ← {a.get('raddr', '?')}:{a.get('rport', '?')}"
                    )
            else:
                lines.append("✅ No suspicious connections detected.")
        except Exception as e:
            lines.append(f"⚠️ Security scan error: {e}")

        return "\n".join(lines)


# ── Singleton ─────────────────────────────────────────────────────────────────

_net_diag: NetworkDiagnostics | None = None
_net_lock = threading.Lock()


def get_network_diagnostics() -> NetworkDiagnostics:
    global _net_diag
    with _net_lock:
        if _net_diag is None:
            _net_diag = NetworkDiagnostics()
    return _net_diag


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_local(ip: str) -> bool:
    """Retourne True si l'adresse IP est locale (127.x, 10.x, 192.168.x, etc.)."""
    if not ip:
        return True
    return (
        ip.startswith("127.")
        or ip.startswith("10.")
        or ip.startswith("192.168.")
        or ip.startswith("172.")
        or ip in ("::1", "localhost")
    )
