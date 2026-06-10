# actions/protocols.py
# Module de protocoles d'urgence style Stark Industries pour JARVIS
# Emergency protocols module (Clean Slate, House Party, Sentry) for JARVIS

import os
import subprocess
import ctypes
import psutil
import threading
import time

try:
    import pyperclip
except ImportError:
    pyperclip = None

try:
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    PYCAW_AVAILABLE = True
except ImportError:
    PYCAW_AVAILABLE = False


def _close_browsers():
    """Ferme de manière forcée tous les processus de navigateurs web connus."""
    browsers = ["chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"]
    count = 0
    for proc in psutil.process_iter(["name"]):
        try:
            name = proc.info["name"].lower()
            if any(b == name for b in browsers):
                proc.terminate()
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    print(f"[Protocols] Closed {count} browser processes.")
    return count


def _empty_recycle_bin():
    """Vide la corbeille Windows de façon silencieuse et native."""
    try:
        # SHEmptyRecycleBinW: SHERB_NOCONFIRMATION (0x01) | SHERB_NOPROGRESSUI (0x02) | SHERB_NOSOUND (0x04) = 7
        flags = 7
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        print(f"[Protocols] Empty Recycle Bin result: {result}")
        return result == 0
    except Exception as e:
        print(f"[Protocols] Failed to empty recycle bin: {e}")
        return False


def _lock_workstation():
    """Verrouille immédiatement la session Windows."""
    try:
        ctypes.windll.user32.LockWorkStation()
        print("[Protocols] Workstation locked.")
        return True
    except Exception as e:
        print(f"[Protocols] Failed to lock workstation: {e}")
        return False


def _set_volume_50():
    """Met le volume master Windows à 50%."""
    if not PYCAW_AVAILABLE:
        print("[Protocols] pycaw not available, skipping volume set.")
        return False
    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = interface.QueryInterface(IAudioEndpointVolume)
        volume.SetMasterVolumeLevelScalar(0.5, None)
        print("[Protocols] Master volume set to 50%.")
        return True
    except Exception as e:
        print(f"[Protocols] Failed to set volume: {e}")
        return False


def _open_apps():
    """Ouvre les applications prévues pour le protocole House Party."""
    # Chemins Windows par défaut pour VSCode, Chrome et Discord
    vscode_path = os.path.expandvars(r"%LocalAppData%\Programs\Microsoft VS Code\Code.exe")
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not os.path.exists(chrome_path):
        chrome_path = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    discord_path = os.path.expandvars(r"%LocalAppData%\Discord\Update.exe")

    # Ouvrir VSCode
    if os.path.exists(vscode_path):
        try:
            subprocess.Popen([vscode_path], shell=True)
        except Exception:
            pass

    # Ouvrir Chrome
    if os.path.exists(chrome_path):
        try:
            subprocess.Popen([chrome_path], shell=True)
        except Exception:
            pass

    # Ouvrir Discord
    if os.path.exists(discord_path):
        try:
            subprocess.Popen([discord_path, "--processStart", "Discord.exe"], shell=True)
        except Exception:
            pass

    # Lancer Spotify via URI protocol
    try:
        os.system("start spotify:")
    except Exception:
        pass


# ── Fonctions Publiques de Protocoles ────────────────────────────────────────

def clean_slate(player=None) -> str:
    """
    Exécute le protocole 'Clean Slate'.
    Ferme tous les navigateurs, efface le presse-papier, vide la corbeille, verrouille le PC.
    """
    if player:
        player.write_log("SYS: Protocol Clean Slate initiated! Clearing workstation...")

    # 1. Fermer les navigateurs
    _close_browsers()

    # 2. Vider le presse-papier
    if pyperclip:
        try:
            pyperclip.copy("")
        except Exception:
            pass

    # 3. Vider la corbeille
    _empty_recycle_bin()

    # 4. Verrouiller la session (dans un court thread différé pour laisser le temps de renvoyer le statut)
    threading.Thread(
        target=lambda: (time.sleep(1.5), _lock_workstation()),
        daemon=True
    ).start()

    return "Protocol Clean Slate complete. Workstation secured."


def house_party(player=None) -> str:
    """
    Exécute le protocole 'House Party'.
    Configure le volume à 50%, lance Spotify, ouvre VSCode, Chrome, Discord.
    """
    if player:
        player.write_log("SYS: Protocol House Party initiated! Booting environment...")

    # 1. Ajuster le volume
    _set_volume_50()

    # 2. Ouvrir les applications et lancer Spotify
    _open_apps()

    return "Protocol House Party complete. Welcome back, sir."


def execute_protocol(protocol_name: str, player=None) -> str:
    """Point d'entrée pour l'exécution d'un protocole."""
    name = protocol_name.lower().strip()
    if "clean" in name or "slate" in name:
        return clean_slate(player)
    elif "house" in name or "party" in name:
        return house_party(player)
    else:
        return f"Protocol '{protocol_name}' unrecognized."
