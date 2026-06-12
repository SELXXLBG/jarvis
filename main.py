import asyncio
import threading
import queue as thread_queue
import json
import sys
import traceback
import re
import time
import struct
import math
import array
from pathlib import Path
import requests
import backoff
from loguru import logger

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False
    print("[JARVIS] ⚠️ numpy non disponible — FFT désactivé")

try:
    import pyttsx3 as _pyttsx3_mod
    _PYTTSX3_AVAILABLE = True
except ImportError:
    _PYTTSX3_AVAILABLE = False
    print("[JARVIS] ⚠️ pyttsx3 non disponible — TTS offline désactivé")

# ── FFT spectrum shared buffer (40 bandes, mis à jour en temps réel) ──────────
FFT_BANDS      = 40
_fft_bands     = [0.0] * FFT_BANDS   # amplitudes normalisées [0.0 … 1.0]
_fft_lock      = threading.Lock()

# ── pyttsx3 engine (lazy init, thread-safe via lock) ─────────────────────────
_tts_engine    = None
_tts_lock      = threading.RLock()


def _get_tts_engine():
    """Retourne le moteur pyttsx3 (créé une fois, thread-safe)."""
    global _tts_engine
    if not _PYTTSX3_AVAILABLE:
        return None
    with _tts_lock:
        if _tts_engine is None:
            try:
                _tts_engine = _pyttsx3_mod.init()
                # Choisir une voix masculine anglaise si disponible
                voices = _tts_engine.getProperty("voices")
                for v in voices:
                    if "male" in v.name.lower() or "david" in v.name.lower() or "mark" in v.name.lower():
                        _tts_engine.setProperty("voice", v.id)
                        break
                _tts_engine.setProperty("rate", 170)
                _tts_engine.setProperty("volume", 0.9)
            except Exception as e:
                print(f"[JARVIS] ❌ pyttsx3 init failed: {e}")
                _tts_engine = None
    return _tts_engine


def _speak_offline(text: str) -> None:
    """Synthèse vocale locale via pyttsx3 (bloquant, appeler dans un thread)."""
    engine = _get_tts_engine()
    if engine is None:
        print(f"[TTS-Offline] ⚠️ pyttsx3 unavailable. Message: {text}")
        return
    try:
        with _tts_lock:
            engine.say(text)
            engine.runAndWait()
    except Exception as e:
        print(f"[TTS-Offline] ❌ {e}")


def _compute_fft_bands(pcm_bytes: bytes, sample_rate: int = 16000) -> list:
    """
    Calcule FFT sur les données PCM int16 et retourne FFT_BANDS amplitudes
    normalisées entre 0.0 et 1.0. Ignore les fréquences > 8000 Hz.
    """
    if not _NUMPY_AVAILABLE or len(pcm_bytes) < 2:
        return [0.0] * FFT_BANDS
    try:
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        N = len(samples)
        if N < 64:
            return [0.0] * FFT_BANDS
        # FFT magnitude (moitié positive du spectre)
        fft_mag = np.abs(np.fft.rfft(samples)) / N
        # Fréquences correspondantes
        freqs = np.fft.rfftfreq(N, d=1.0 / sample_rate)
        # Répartir en FFT_BANDS bandes de fréquences (log-scale, 20 Hz – 8000 Hz)
        f_min, f_max = 20.0, min(8000.0, sample_rate / 2.0)
        log_min = math.log10(max(f_min, 1))
        log_max = math.log10(f_max)
        bands = []
        for i in range(FFT_BANDS):
            lo = 10 ** (log_min + (log_max - log_min) * (i / FFT_BANDS))
            hi = 10 ** (log_min + (log_max - log_min) * ((i + 1) / FFT_BANDS))
            mask = (freqs >= lo) & (freqs < hi)
            if mask.any():
                val = float(np.mean(fft_mag[mask]))
            else:
                val = 0.0
            bands.append(val)
        # Normalisation : max observé sur cette frame (+ protection zéro)
        max_val = max(bands) if max(bands) > 0 else 1.0
        bands = [min(b / max_val, 1.0) for b in bands]
        return bands
    except Exception:
        return [0.0] * FFT_BANDS

try:
    import speech_recognition as sr
    _SR_AVAILABLE = True
    _wake_recognizer = sr.Recognizer()   # singleton — avoids repeated init overhead
except ImportError:
    _SR_AVAILABLE = False
    _wake_recognizer = None
    print("[WakeWord] [!] SpeechRecognition non installe - wake word local desactive")

import sounddevice as sd
import core.llm_patcher
from google import genai
from google.genai import types
from ui import JarvisUI
from core.vad_local import LocalVAD
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    should_extract_memory, extract_memory
)

# === Imports optionnels JARVIS Mark XXXVI ===
try:
    from core.system_monitor import SystemMonitor, get_monitor
except ImportError as e:
    print(f"[JARVIS] ⚠️ system_monitor unavailable: {e}")
    SystemMonitor = None; get_monitor = lambda: None  # noqa

try:
    from core.scheduler import JarvisScheduler
except ImportError as e:
    print(f"[JARVIS] ⚠️ scheduler unavailable: {e}")
    JarvisScheduler = None  # noqa

try:
    from core.presence_detector import PresenceDetector
except ImportError as e:
    print(f"[JARVIS] ⚠️ presence_detector unavailable: {e}")
    PresenceDetector = None  # noqa

try:
    from core.telegram_bot import JarvisTelegramBot, get_telegram_bot
except ImportError as e:
    print(f"[JARVIS] ⚠️ telegram_bot unavailable: {e}")
    JarvisTelegramBot = None; get_telegram_bot = lambda: None  # noqa

try:
    from memory.vector_memory import VectorMemory, format_vector_results
except ImportError as e:
    print(f"[JARVIS] ⚠️ vector_memory unavailable: {e}")
    VectorMemory = None; format_vector_results = lambda x, **kw: ""  # noqa

from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.cmd_control       import cmd_control
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.web_agent         import web_agent
from actions.spotify_control   import spotify_control
from actions.proactive_agent   import proactive_check
from actions.protocols         import execute_protocol


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


import core.profile_loader

BASE_DIR        = get_base_dir()
LIVE_MODEL          = "gemini-2.5-flash"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# ── VAD (Voice Activity Detection) — inspiré d'ADA V2 ────────────────────────
VAD_THRESHOLD       = 1200   # RMS seuil pour détection de parole (16-bit) — assez haut pour ignorer l'écho
SILENCE_DURATION    = 0.7    # Secondes de silence pour considérer "fin de parole"
MIC_ECHO_GUARD_S    = 0.35   # Délai anti-écho après la fin de lecture audio
PLAYBACK_BLOCKSIZE  = 2048   # Taille de buffer sortie (frames) — lecture plus fluide

# ── Outils non-bloquants (s'exécutent en arrière-plan) ────────────────────────
NON_BLOCKING_TOOLS  = {
    "web_search", "browser_control", "dev_agent", "agent_task",
    "code_helper", "flight_finder", "game_updater", "web_agent",
}


def _get_api_key() -> str:
    return core.profile_loader.load_api_keys().get("gemini_api_key", "")


def _load_system_prompt() -> str:
    return core.profile_loader.get_system_prompt()


# ── Mémoire ───────────────────────────────────────────────────────────────────
_last_memory_input = ""


def _update_memory_async(user_text: str, jarvis_text: str) -> None:
    global _last_memory_input

    user_text   = (user_text   or "").strip()
    jarvis_text = (jarvis_text or "").strip()

    if len(user_text) < 5 or user_text == _last_memory_input:
        return
    _last_memory_input = user_text

    try:
        api_key = _get_api_key()
        if not should_extract_memory(user_text, jarvis_text, api_key):
            return
        data = extract_memory(user_text, jarvis_text, api_key)
        if data:
            update_memory(data)
            print(f"[Memory] ✅ {list(data.keys())}")
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")


# ── Tool declarations ─────────────────────────────────────────────────────────
TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the Windows computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gets real-time weather information for a city.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Windows Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos. "
            "CRITICAL: NEVER use this tool if the user wants to play music on Spotify or other platforms. "
            "CRITICAL: For the 'play' action, the 'query' parameter MUST be the FULL phrase the user asked for (e.g. 'dernière vidéo de squeezie' and not just 'dernière')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Full search query for play action exactly as the user requested"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls the user's VISIBLE browser using their real mouse and keyboard. "
            "WARNING: This tool INTERRUPTS the user — it moves their mouse and types on their keyboard! "
            "ONLY use when the user explicitly asks to open a website or control their browser. "
            "For research or web browsing, ALWAYS use web_agent instead (hidden browser, no interruption)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | press | close"},
                "url":         {"type": "STRING", "description": "URL for go_to action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up or down for scroll"},
                "key":         {"type": "STRING", "description": "Key name for press action"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "cmd_control",
        "description": (
            "Runs CMD/terminal commands via natural language: disk space, processes, "
            "system info, network, find files, or anything in the command line."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task":    {"type": "STRING", "description": "Natural language description of what to do"},
                "visible": {"type": "BOOLEAN", "description": "Open visible CMD window. Default: true"},
                "command": {"type": "STRING", "description": "Optional: exact command if already known"},
            },
            "required": ["task"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "web_agent",
        "description": (
            "Autonomous AI-powered web browser agent. Uses vision to see the page "
            "and decides clicks, typing, scrolling autonomously. "
            "Use for complex web tasks: multi-page research, form filling, website navigation. "
            "Much smarter than browser_control for complex tasks. "
            "This tool runs in the BACKGROUND — JARVIS stays responsive."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {"type": "STRING", "description": "Detailed instructions for the web task"},
                "show_result_on_screen": {"type": "BOOLEAN", "description": "If true, opens the resulting web page in user's visible browser when done."}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "spotify_control",
        "description": "Controls Spotify. Use this to search and play a specific song, artist, or album on Spotify.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for what to play"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "proactive_check",
        "description": "Performs proactive tasks like daily briefings, system status checks, or suggestions.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "briefing | status | suggestion (default: briefing)"}
            },
            "required": []
        }
    },
    {
        "name": "sleep_mode",
        "description": (
            "Put JARVIS into sleep mode. Call this tool when the user says goodbye, "
            "thanks you and wants to end the conversation, or explicitly asks you to sleep/stop. "
            "Trigger phrases: 'merci Jarvis', 'thank you Jarvis', 'bonne nuit', 'au revoir', "
            "'stop Jarvis', 'arrête', 'c'est bon', 'dors', 'repose-toi'. "
            "Say a SHORT goodbye BEFORE calling this tool (e.g. 'À votre service, sir.')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "reason": {"type": "STRING", "description": "Why entering sleep mode (e.g. user said goodbye)"}
            },
            "required": []
        }
    },
    {
        "name": "system_diagnostics",
        "description": "Retrieves the real-time system metrics (CPU, RAM, GPU, temperature, disks, etc.) to analyze system health.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "search_memory",
        "description": "Searches sémantiquement through the vector long-term memory for past conversations, documents, and facts.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Semantic search query"},
                "collection": {"type": "STRING", "description": "Optional: 'conversations', 'facts', or 'documents'. Defaults to all."}
            },
            "required": ["query"]
        }
    },
    {
        "name": "presence_detection_control",
        "description": "Enables or disables webcam-based presence detection.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "enable": {"type": "BOOLEAN", "description": "True to enable detection, False to disable."}
            },
            "required": ["enable"]
        }
    },
    {
        "name": "execute_protocol",
        "description": (
            "Executes a Stark Industries emergency protocol. "
            "'clean_slate': closes all browsers, clears clipboard, empties recycle bin, locks the PC. "
            "'house_party': sets volume to 50%, opens VSCode/Chrome/Discord, launches Spotify. "
            "'sentry': enables continuous webcam monitoring, alerting via Telegram if an intruder is detected. "
            "Use when the user says 'activate protocol X', 'initiate X', or 'launch X protocol'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "protocol_name": {
                    "type": "STRING",
                    "description": "Name of the protocol: 'clean_slate', 'house_party', or 'sentry'"
                }
            },
            "required": ["protocol_name"]
        }
    },
    {
        "name": "network_diagnostics",
        "description": (
            "Runs a network diagnostic: measures internet ping and download speed, "
            "scans for suspicious active connections on sensitive ports (SSH, RDP, VNC, etc.), "
            "and reports potential cybersecurity threats. "
            "Use when user asks: 'test my connection', 'what is my internet speed', "
            "'is my network secure', 'check for intrusions', etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {
                    "type": "STRING",
                    "description": "'full' (default): speed + security | 'speed': speedtest only | 'security': security scan only"
                }
            },
            "required": []
        }
    },
]


# ── Wake word config ─────────────────────────────────────────────────────────
# Phrases qui réveillent JARVIS (insensible à la casse)
WAKE_PHRASES  = [
    "ok jarvis", "okay jarvis", "hey jarvis", "oi jarvis", "ok, jarvis",
    "salut jarvis", "bonjour jarvis", "allô jarvis", "allo jarvis",
]
WAKE_RMS_THRESHOLD = 400   # seuil plus bas en veille (micro principal)
# Phrases qui remettent JARVIS en veille
SLEEP_PHRASES = ["merci jarvis", "merci, jarvis", "thank you jarvis",
                 "thanks jarvis", "dors jarvis", "repose-toi jarvis",
                 "repose toi jarvis", "bonne nuit jarvis", "au revoir jarvis",
                 "jarvis dors", "jarvis repose", "stop jarvis", "arrete jarvis",
                 "arrête jarvis", "cest bon jarvis", "c'est bon jarvis",
                 "ok merci jarvis", "merci beaucoup jarvis"]


def _matches_phrase(text: str, phrases: list[str]) -> bool:
    """Vérifie si text contient l'une des phrases (insensible casse + ponctuation)."""
    t = re.sub(r"[^\w\s]", "", text.lower()).strip()
    for p in phrases:
        p_norm = re.sub(r"[^\w\s]", "", p.lower()).strip()
        if p_norm and p_norm in t:
            return True
    return False


class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()

        # ── Wake word state ───────────────────────────────────────────────────
        # True  = JARVIS écoute et répond (mode actif)
        # False = JARVIS en veille (micro actif mais audio non envoyé à l'API)
        self._awake         = False
        self._awake_lock    = threading.Lock()

        # ── VAD state (Voice Activity Detection) ──────────
        self._vad_is_speaking   = False
        self._vad_silence_start = None
        self._local_vad         = LocalVAD()
        self._mic_resume_at     = 0.0
        self._playback_q: thread_queue.Queue | None = None
        self._playback_thread: threading.Thread | None = None
        self._playback_stop    = threading.Event()
        self._playback_lock    = threading.Lock()
        self._playback_pending = 0
        self._wake_pcm_buf     = bytearray()
        self._wake_lock        = threading.Lock()

        # ── Historique de conversation (pour reconnexion intelligente) ────────
        self._chat_history  = []          # list of {"role": "user"|"jarvis", "text": str}
        self._max_history   = 20          # garder les 20 derniers échanges

        self.ui.on_text_command = self._on_text_command

        # ── Mark XXXVI services state ────────────────────────────────────────
        self._telegram_request = None
        self._telegram_lock = threading.Lock()
        self._system_monitor = None
        self._scheduler = None
        self._presence_detector = None
        self._telegram_bot = None
        
        # Start background services
        self._start_services()

    # ── Wake word helpers ─────────────────────────────────────────────────────

    @property
    def awake(self) -> bool:
        with self._awake_lock:
            return self._awake

    def _wake_up(self):
        """Active JARVIS. Appelé quand le mot de réveil est détecté."""
        with self._awake_lock:
            if self._awake:
                return  # déjà actif
            self._awake = True
        with self._wake_lock:
            self._wake_pcm_buf.clear()
        print("[JARVIS] 🟢 Wake word détecté — JARVIS actif")
        self.ui.set_state("LISTENING")
        self.ui.write_log("SYS: ✅ JARVIS — mode actif")
        # Vérifier que la session est toujours vivante avant de parler
        if self._loop and self.session:
            self.speak("Oui, je vous écoute.")
        else:
            # Session morte (1011) — TTS offline pour signaler le problème
            threading.Thread(
                target=_speak_offline,
                args=("Session reconnecting, one moment sir.",),
                daemon=True
            ).start()

    def _go_to_sleep(self):
        """Met JARVIS en veille. Appelé quand le mot de mise en veille est détecté."""
        with self._awake_lock:
            if not self._awake:
                return  # déjà en veille
            self._awake = False
        print("[JARVIS] 💤 Mise en veille — tokens économisés")
        self.ui.set_state("SLEEPING")
        self.ui.write_log("SYS: 💤 JARVIS — mode veille (dites 'Ok Jarvis' pour réveiller)")

    # ── Wake word listener (mode veille — local, gratuit, zéro tokens) ────────

    def _try_wake_from_pcm(self, pcm_bytes: bytes) -> None:
        """Reconnaissance locale du wake word sur le flux micro principal (pas de 2e micro)."""
        if self.awake or not _SR_AVAILABLE or len(pcm_bytes) < 8000:
            return
        try:
            # Réutiliser le recognizer singleton (plus rapide que d'en créer un nouveau)
            audio = sr.AudioData(pcm_bytes, SEND_SAMPLE_RATE, 2)
            try:
                text = _wake_recognizer.recognize_google(audio, language="fr-FR").lower()
            except (sr.UnknownValueError, sr.RequestError):
                text = _wake_recognizer.recognize_google(audio, language="en-US").lower()

            text = re.sub(r"[^\w\s]", "", text).strip()
            if not text:
                return

            print(f"[WakeWord] 🗣️ Entendu: '{text}'")
            if _matches_phrase(text, WAKE_PHRASES):
                self._wake_up()
        except sr.UnknownValueError:
            pass
        except Exception as e:
            print(f"[WakeWord] ⚠️ {e}")

    def _wake_word_listener(self):
        """
        Wake word intégré au flux sounddevice (_listen_audio).
        Ce thread évite d'ouvrir un 2e micro (PyAudio) en conflit avec sounddevice.
        """
        print("[WakeWord] 🎤 Détection intégrée au micro principal (sounddevice)")
        while True:
            time.sleep(3600)

    def _on_text_command(self, text: str):
        """Commande texte depuis l'UI — toujours transmise même en veille."""
        if not self._loop or not self.session:
            return
        # Une commande texte réveille aussi JARVIS
        if not self.awake:
            self._wake_up()
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    # ── Chat history for reconnection ─────────────────────────────────────────

    def _add_to_history(self, role: str, text: str):
        """Ajoute un échange à l'historique pour la restauration de contexte."""
        if text and len(text.strip()) > 2:
            self._chat_history.append({"role": role, "text": text.strip()})
            if len(self._chat_history) > self._max_history:
                self._chat_history = self._chat_history[-self._max_history:]

    def _get_reconnection_context(self) -> str:
        """Construit un message de contexte pour la reconnexion."""
        if not self._chat_history:
            return ""
        lines = []
        for entry in self._chat_history[-10:]:
            sender = "User" if entry["role"] == "user" else "JARVIS"
            lines.append(f"[{sender}]: {entry['text']}")
        context = "\n".join(lines)
        return (
            "System Notification: Connection was lost and re-established. "
            "Here is the recent conversation to resume seamlessly:\n\n"
            f"{context}\n\n"
            "Please acknowledge the reconnection briefly and continue."
        )

    # ── Audio helpers ─────────────────────────────────────────────────────────

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            if self.awake:
                self.ui.set_state("LISTENING")
            else:
                self.ui.set_state("SLEEPING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _clear_audio_queue(self):
        """Vide la queue audio pour interrompre JARVIS — inspiré d'ADA V2."""
        count = 0
        if self._playback_q is not None:
            try:
                while True:
                    self._playback_q.get_nowait()
                    count += 1
            except thread_queue.Empty:
                pass
        with self._playback_lock:
            self._playback_pending = 0
        if count > 0:
            print(f"[JARVIS] 🔇 Audio queue vidée ({count} chunks) — interruption utilisateur")
        self.set_speaking(False)
        self._mic_resume_at = time.time() + MIC_ECHO_GUARD_S

    def _start_playback_thread(self) -> None:
        """Thread dédié : écriture PCM continue sans await entre chunks (évite le bégaiement)."""
        if self._playback_thread and self._playback_thread.is_alive():
            return

        self._playback_q = thread_queue.Queue(maxsize=64)
        self._playback_stop.clear()
        loop = self._loop

        def _worker():
            stream = sd.RawOutputStream(
                samplerate=RECEIVE_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=PLAYBACK_BLOCKSIZE,
            )
            stream.start()
            fft_tick = 0
            try:
                while not self._playback_stop.is_set():
                    try:
                        chunk = self._playback_q.get(timeout=0.25)
                    except thread_queue.Empty:
                        with self._playback_lock:
                            pending = self._playback_pending
                        if pending == 0:
                            if loop:
                                loop.call_soon_threadsafe(self._on_playback_idle)
                        continue

                    if not chunk:
                        continue

                    if len(chunk) % 2:
                        chunk = chunk[: len(chunk) - 1]
                    if not chunk:
                        with self._playback_lock:
                            self._playback_pending = max(0, self._playback_pending - 1)
                        continue

                    stream.write(chunk)

                    fft_tick += 1
                    if _NUMPY_AVAILABLE and fft_tick % 20 == 0 and loop:
                        bands = _compute_fft_bands(chunk, RECEIVE_SAMPLE_RATE)
                        with _fft_lock:
                            for k in range(FFT_BANDS):
                                _fft_bands[k] = bands[k]

                    with self._playback_lock:
                        self._playback_pending = max(0, self._playback_pending - 1)
                        pending = self._playback_pending

                    if pending == 0 and self._playback_q.empty():
                        time.sleep(0.04)
                        if self._playback_q.empty() and loop:
                            loop.call_soon_threadsafe(self._on_playback_idle)
            finally:
                stream.stop()
                stream.close()
                if loop:
                    loop.call_soon_threadsafe(self._on_playback_idle)

        self._playback_thread = threading.Thread(
            target=_worker, daemon=True, name="JarvisAudioPlayback"
        )
        self._playback_thread.start()
        print("[JARVIS] 🔊 Playback thread started")

    def _enqueue_playback(self, chunk: bytes) -> None:
        if not chunk or self._playback_q is None:
            return
        with self._playback_lock:
            self._playback_pending += 1
        self.set_speaking(True)
        try:
            self._playback_q.put_nowait(chunk)
        except thread_queue.Full:
            try:
                self._playback_q.get_nowait()
            except thread_queue.Empty:
                pass
            try:
                self._playback_q.put_nowait(chunk)
            except thread_queue.Full:
                with self._playback_lock:
                    self._playback_pending = max(0, self._playback_pending - 1)

    def _on_playback_idle(self) -> None:
        """Relâche le micro une fois la file audio vide."""
        with self._playback_lock:
            if self._playback_pending > 0:
                return
            if self._playback_q and not self._playback_q.empty():
                return
        self.set_speaking(False)
        self._mic_resume_at = time.time() + MIC_ECHO_GUARD_S

    # ── Config ────────────────────────────────────────────────────────────────

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]

        # ── Mémoire : limitée à 1500 caractères pour ne pas surcharger le prompt ──
        if mem_str:
            if len(mem_str) > 1500:
                mem_str = mem_str[:1500] + "\n... (memory truncated for performance)"
            parts.append(mem_str)

        # ── Status système : injecté UNIQUEMENT si déjà disponible (sans bloquer) ──
        # Un appel bloquant ici retarde la connexion et peut causer l'erreur 1011.
        try:
            from core.system_monitor import get_monitor
            mon = get_monitor()
            # Ne récupérer le status que si le monitor tourne déjà (pas de cold-start)
            if hasattr(mon, '_running') and mon._running:
                status_text = mon.get_summary_text()
                # Limiter à 300 chars pour rester léger
                if len(status_text) > 300:
                    status_text = status_text[:300] + "..."
                parts.append(f"[SYSTEM STATUS]\n{status_text}\n\n")
        except Exception:
            pass

        parts.append(sys_prompt)
        parts.append(
            "[LANGUAGE]\n"
            "The user's primary language is French. "
            "Always respond in French unless the user clearly speaks another language."
        )

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            # output_audio_transcription retiré — gemini-2.5-flash-native-audio-latest
            # ne supporte pas la combinaison AUDIO+TEXT (erreur 1007).
            # La transcription vocale reste disponible via input_transcription dans receive().
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    # ── Tool execution ────────────────────────────────────────────────────────

    async def _execute_tool_sync(self, fc) -> str:
        """Exécution synchrone d'un outil (dans un thread)."""
        name = fc.name
        args = dict(fc.args or {})
        loop = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "spotify_control":
                r = await loop.run_in_executor(None, lambda: spotify_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "proactive_check":
                r = await loop.run_in_executor(None, lambda: proactive_check(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "cmd_control":
                r = await loop.run_in_executor(None, lambda: cmd_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(goal=args.get("goal", ""), priority=priority, speak=self.speak)
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "web_agent":
                r = await loop.run_in_executor(None, lambda: web_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "system_diagnostics":
                if self._system_monitor:
                    result = self._system_monitor.get_summary_text()
                else:
                    result = "System Monitor is not initialized."

            elif name == "search_memory":
                query = args.get("query", "")
                collection = args.get("collection")
                if collection == "all":
                    collection = None
                from memory.vector_memory import format_vector_results
                from memory.memory_manager import get_vector_memory
                vm = get_vector_memory()
                if vm and vm.available:
                    res = vm.search(query, collection=collection)
                    result = format_vector_results(res) or "No relevant semantic memories found."
                else:
                    result = "Vector memory system is offline."

            elif name == "presence_detection_control":
                enable = args.get("enable", False)
                if self._presence_detector:
                    self._presence_detector.set_enabled(enable)
                    state = "enabled" if enable else "disabled"
                    self.ui.write_log(f"SYS: Presence detection {state}.")
                    result = f"Presence detection has been {state}."
                else:
                    result = "Presence detector is offline."

            elif name == "execute_protocol":
                protocol_name = args.get("protocol_name", "")
                if protocol_name.lower() in ("sentry",):
                    # Sentry mode: activer la surveillance via activate_sentry()
                    if self._presence_detector:
                        r = self._presence_detector.activate_sentry()
                        result = r
                        self.ui.write_log("SYS: 🛡️ Protocol SENTRY activated.")
                    else:
                        result = "Sentry mode unavailable: presence detector offline."
                else:
                    r = await loop.run_in_executor(
                        None,
                        lambda: execute_protocol(protocol_name, player=self.ui)
                    )
                    result = r or f"Protocol '{protocol_name}' executed."
                    self.ui.write_log(f"SYS: 🛡️ Protocol {protocol_name.upper()} executed.")

            elif name == "network_diagnostics":
                mode = args.get("mode", "full").lower()
                from core.network_diagnostics import get_network_diagnostics
                nd = get_network_diagnostics()
                if mode == "speed":
                    spd = await loop.run_in_executor(None, nd.get_speedtest)
                    result = nd.format_speedtest(spd)
                elif mode == "security":
                    alerts = await loop.run_in_executor(None, nd.get_security_alerts)
                    if alerts:
                        lines = [f"{len(alerts)} security alert(s) detected:"]
                        for a in alerts:
                            lines.append(
                                f"  [{a['service']} port {a['port']}] "
                                f"process={a['process']} remote={a.get('raddr','?')}:{a.get('rport','?')}"
                            )
                        result = "\n".join(lines)
                    else:
                        result = "No suspicious connections detected. Network appears secure."
                else:
                    result = await loop.run_in_executor(None, nd.full_report)

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()

        return result

    async def _run_background_tool(self, fc):
        """
        Exécute un outil en arrière-plan et envoie le résultat au modèle.
        Inspiré du pattern asyncio.create_task() d'ADA V2.
        """
        name = fc.name
        print(f"[JARVIS] 🔄 Background task started: {name}")
        self.ui.write_log(f"SYS: ⏳ {name} running in background...")

        try:
            result = await self._execute_tool_sync(fc)
            print(f"[JARVIS] ✅ Background task done: {name} → {str(result)[:80]}")
            self.ui.write_log(f"SYS: ✅ {name} completed.")

            # Envoyer le résultat au modèle pour qu'il en parle
            if self.session:
                await self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": (
                        f"System Notification: Background task '{name}' completed.\n"
                        f"Result: {result}\n"
                        f"Please summarize the result to the user."
                    )}]},
                    turn_complete=True
                )
        except Exception as e:
            print(f"[JARVIS] ❌ Background task failed: {name} — {e}")
            self.ui.write_log(f"ERR: {name} failed — {str(e)[:80]}")
            traceback.print_exc()

            if self.session:
                try:
                    await self.session.send_client_content(
                        turns={"role": "user", "parts": [{"text": f"System Notification: Background task '{name}' failed: {e}"}]},
                        turn_complete=True
                    )
                except Exception:
                    pass

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        # ── save_memory: silencieux, rapide ──────────────────────────────────
        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                if self.awake:
                    self.ui.set_state("LISTENING")
                else:
                    self.ui.set_state("SLEEPING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        # ── sleep_mode: mettre JARVIS en veille ────────────────────────────
        if name == "sleep_mode":
            reason = args.get("reason", "user request")
            print(f"[JARVIS] 💤 sleep_mode tool called: {reason}")
            threading.Thread(target=self._go_to_sleep, daemon=True).start()
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "Entering sleep mode. Goodbye."}
            )

        # ── Outils NON-BLOQUANTS : lancer en arrière-plan ────────────────────
        if name in NON_BLOCKING_TOOLS:
            asyncio.create_task(self._run_background_tool(fc))
            if not self.ui.muted:
                if self.awake:
                    self.ui.set_state("LISTENING")
                else:
                    self.ui.set_state("SLEEPING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": f"Task '{name}' started in background. Do not reply to this message, wait for the result notification."}
            )

        # ── Outils BLOQUANTS (rapides) : exécution immédiate ─────────────────
        result = await self._execute_tool_sync(fc)

        if not self.ui.muted:
            if self.awake:
                self.ui.set_state("LISTENING")
            else:
                self.ui.set_state("SLEEPING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")

        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    # ── Audio pipeline ────────────────────────────────────────────────────────

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    def _safe_put_audio_chunk(self, blob):
        """Put an audio blob in the queue, dropping the oldest chunk if full."""
        if self.out_queue is None:
            return
        try:
            self.out_queue.put_nowait(blob)
        except asyncio.QueueFull:
            try:
                # Drop oldest chunk to prevent lagging and QueueFull exceptions
                self.out_queue.get_nowait()
                self.out_queue.put_nowait(blob)
            except Exception:
                pass

    async def _keepalive_session(self):
        """
        Envoie un silence PCM minuscule toutes les 25 secondes quand JARVIS est en veille,
        pour éviter que le serveur Gemini ferme la session (erreur 1011 Deadline Expired).
        La session Gemini Live expire après ~2-3 minutes d'inactivité complète.
        """
        # 160ms de silence PCM int16 @ 16kHz = 160ms * 16000 * 2 octets = 5120 octets
        SILENCE_CHUNK = bytes(5120)  # Zeros = silence parfait
        KEEPALIVE_INTERVAL = 25      # secondes entre chaque envoi

        print("[JARVIS] 📶 Keepalive task started (interval: 25s)")

        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)

            # N'envoyer le keepalive QUE si JARVIS est en veille
            # (Quand actif, le micro envoie déjà de l'audio régulier)
            if not self.awake:
                try:
                    silence_blob = types.Blob(
                        data=SILENCE_CHUNK,
                        mime_type="audio/pcm;rate=16000"
                    )
                    await self.session.send_realtime_input(media=silence_blob)
                except Exception as e:
                    # Si ça échoue, la session est morte — la boucle principale
                    # gèrera la reconnexion via le gestionnaire d'erreurs de run()
                    print(f"[JARVIS] ⚠️ Keepalive failed (session dead?): {e}")
                    raise  # Propager l'erreur pour déclencher la reconnexion

    async def _listen_audio(self):
        """
        Capture audio et envoie à Gemini.
        Intègre un VAD (Voice Activity Detection) inspiré d'ADA V2 pour :
        - Économiser les tokens quand personne ne parle
        - Détecter quand l'utilisateur commence/arrête de parler
        """
        print("[JARVIS] 🎤 Mic started (with VAD)")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            if self.ui.muted:
                return
            if time.time() < self._mic_resume_at:
                return
            with self._speaking_lock:
                if self._is_speaking:
                    return  # JARVIS parle → pas d'envoi micro (anti-écho)

            data = indata.tobytes()
            asleep = not self.awake

            # ── FFT Mic spectrum ─────────────────────────────────────────────
            if _NUMPY_AVAILABLE and self.awake:
                bands = _compute_fft_bands(data, SEND_SAMPLE_RATE)
                with _fft_lock:
                    for k in range(FFT_BANDS):
                        _fft_bands[k] = bands[k]

            # ── VAD : IA Neuronal (Silero) ou Mathématique (RMS) ─────────────
            is_voice_detected = False
            vad_thr = 0.35 if asleep else 0.5

            if self._local_vad.enabled:
                is_voice_detected = self._local_vad.is_speech(data, threshold=vad_thr)
            else:
                if _NUMPY_AVAILABLE and len(data) >= 2:
                    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                    rms = int(np.sqrt(np.mean(samples ** 2))) if len(samples) > 0 else 0
                elif len(data) >= 2:
                    count = len(data) // 2
                    shorts = struct.unpack(f"<{count}h", data)
                    rms = int(math.sqrt(sum(s * s for s in shorts) / count))
                else:
                    rms = 0
                rms_thr = WAKE_RMS_THRESHOLD if asleep else VAD_THRESHOLD
                is_voice_detected = (rms > rms_thr)

            wake_utterance_done = False

            if is_voice_detected:
                self._vad_silence_start = None

                if not self._vad_is_speaking:
                    self._vad_is_speaking = True
                    with self._speaking_lock:
                        is_playing = self._is_speaking
                    if is_playing:
                        self._clear_audio_queue()
                        print("[JARVIS] 🔇 Interruption détectée par l'utilisateur")

                if asleep:
                    with self._wake_lock:
                        self._wake_pcm_buf.extend(data)

            else:
                if self._vad_is_speaking:
                    if self._vad_silence_start is None:
                        self._vad_silence_start = time.time()
                    elif time.time() - self._vad_silence_start > SILENCE_DURATION:
                        self._vad_is_speaking = False
                        self._vad_silence_start = None
                        wake_utterance_done = asleep

            # ── Mode VEILLE : wake word sur le micro principal ───────────────
            if asleep:
                if wake_utterance_done:
                    with self._wake_lock:
                        buf = bytes(self._wake_pcm_buf)
                        self._wake_pcm_buf.clear()
                    if buf:
                        threading.Thread(
                            target=self._try_wake_from_pcm,
                            args=(buf,),
                            daemon=True,
                            name="WakeWordSTT",
                        ).start()
                return

            # ── Envoyer l'audio à Gemini (mode ACTIF) ────────────────────────
            if self.awake:
                blob = types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                loop.call_soon_threadsafe(self._safe_put_audio_chunk, blob)

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[JARVIS] 🎤 Mic stream open (VAD active)")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    audio_data = None
                    sc = response.server_content
                    if sc and sc.model_turn:
                        for part in (sc.model_turn.parts or []):
                            inline = getattr(part, "inline_data", None)
                            if inline and inline.data:
                                audio_data = inline.data
                                break
                            
                            # Nouveau SDK GenAI: parfois l'audio est dans executable_code ou juste part.data
                            # On gère le fallback si inline_data n'existe pas
                            elif getattr(part, "data", None):
                                audio_data = part.data
                                break

                    if audio_data:
                        self._enqueue_playback(audio_data)

                    if sc:
                        if sc.output_transcription and sc.output_transcription.text:
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)

                                # ── Détection wake word via transcription Gemini ──
                                # Beaucoup plus rapide et fiable que Google Speech
                                combined_in = " ".join(in_buf)
                                if not self.awake and _matches_phrase(combined_in, WAKE_PHRASES):
                                    threading.Thread(target=self._wake_up, daemon=True).start()

                                # Note: l'interruption est gérée par le VAD dans _listen_audio
                                # On ne vide PAS la queue ici pour éviter de couper JARVIS



                        if sc.turn_complete:
                            # Ne pas couper _is_speaking ici : le thread playback
                            # relâche le micro quand la file audio est vide.

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                self._add_to_history("user", full_in)
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Jarvis: {full_out}")
                                self._add_to_history("jarvis", full_out)
                                
                            # Forward response to pending Telegram command
                            if hasattr(self, "_telegram_request") and self._telegram_request is not None:
                                self._telegram_request["response"] = full_out or "(Command executed successfully)"
                                self._telegram_request["event"].set()
                                self.ui.telegram_messages_count += 1
                                
                            out_buf = []

                            # La mise en veille est gérée uniquement via l'outil sleep_mode appelé par Gemini

                            # On s'appuie désormais uniquement sur l'outil 'save_memory' appelé par Gemini
                            # Cela économise 1 appel d'API complet par phrase et réduit drastiquement les erreurs 429 !

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )

        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        """Maintient la tâche asyncio vivante ; lecture PCM dans _start_playback_thread()."""
        while True:
            await asyncio.sleep(3600)

    # ── Main run loop with reconnection — inspiré d'ADA V2 ───────────────────

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1alpha"}
        )

        retry_delay    = 1
        is_reconnect   = False

        while True:
            try:
                print("[JARVIS] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=10)
                    self._mic_resume_at = 0.0
                    self._clear_audio_queue()
                    self._start_playback_thread()

                    print("[JARVIS] ✅ Connected.")

                    # ── Reconnexion : NE PAS forcer de réponse vocale immédiate ──
                    # Envoyer le contexte de reconnexion FORCE le modèle à répondre
                    # vocalement, ce qui crée une boucle : réponse → mic capte → crash 1011.
                    # On log simplement la reconnexion sans déclencher de réponse.
                    if is_reconnect:
                        context = self._get_reconnection_context()
                        if context:
                            print("[JARVIS] 🔄 Reconnection context ready (silent restore).")

                    # Au démarrage → mode VEILLE (attend le wake word)
                    with self._awake_lock:
                        self._awake = False
                    self.ui.set_state("SLEEPING")
                    self.ui.write_log("SYS: JARVIS en veille — dites 'Ok Jarvis' pour commencer.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._keepalive_session())  # ← garde la session vivante

                    # Reset retry delay on successful connection
                    retry_delay = 1

            except Exception as e:
                err_str = str(e)
                print(f"[JARVIS] ⚠️ {e}")
                traceback.print_exc()

                # ── Offline TTS fallback via pyttsx3 ─────────────────────────
                # Si la connexion est perdue (réseau, quota, WebSocket), JARVIS
                # parle localement pour signaler l'incident.
                if any(k in err_str for k in ("429", "503", "WebSocket", "ConnectionError", "timeout", "1011", "1013")):
                    threading.Thread(
                        target=_speak_offline,
                        args=("Sir, direct connection lost. Activating local communications backup.",),
                        daemon=True
                    ).start()

            self.set_speaking(False)
            self.ui.set_state("THINKING")

            # ── Exponential backoff — inspiré d'ADA V2 ────────────────────────
            print(f"[JARVIS] 🔄 Reconnecting in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 10)  # 1s → 2s → 4s → 8s → max 10s
            is_reconnect = True

    # ── Mark XXXVI Services & Callbacks ───────────────────────────────────────

    def _start_services(self):
        # 1. System Monitor
        try:
            from core.system_monitor import get_monitor
            self._system_monitor = get_monitor()
            self._system_monitor.start()
            self.ui.write_log("SYS: System Monitor initialized.")
        except Exception as e:
            print(f"[JARVIS] ⚠️ Failed to start System Monitor: {e}")

        # 2. Vector Memory Initialization
        try:
            from memory.memory_manager import get_vector_memory
            vm = get_vector_memory()
            if vm and vm.available:
                self.ui.write_log("SYS: Vector Memory loaded.")
            else:
                self.ui.write_log("SYS: Vector Memory offline.")
        except Exception as e:
            print(f"[JARVIS] ⚠️ Failed to initialize Vector Memory: {e}")

        # 3. Presence Detector (disabled by default)
        try:
            from core.presence_detector import PresenceDetector
            self._presence_detector = PresenceDetector()
            self._presence_detector.on_user_arrived(self._on_user_arrived)
            self._presence_detector.on_user_left(self._on_user_left)
            # Hook fatigue and mood callbacks
            self._presence_detector.on_fatigue_detected(self._on_fatigue_detected)
            self._presence_detector.on_mood_changed(self._on_mood_changed)
            # Hook Sentry intruder callback
            self._presence_detector.on_intruder_detected(self._on_intruder_detected_sentry)
            self._presence_detector.start()
            self.ui.write_log("SYS: Presence Detector standby (Disabled).")
        except Exception as e:
            print(f"[JARVIS] ⚠️ Failed to start Presence Detector: {e}")

        # 4. Scheduler
        try:
            from core.scheduler import JarvisScheduler
            self._scheduler = JarvisScheduler()
            self._scheduler.start(on_alert_callback=self._on_scheduler_alert)
            self.ui.write_log("SYS: Scheduler active.")
        except Exception as e:
            print(f"[JARVIS] ⚠️ Failed to start Scheduler: {e}")

        # 5. Telegram Bot
        try:
            from core.telegram_bot import get_telegram_bot
            self._telegram_bot = get_telegram_bot()
            self._telegram_bot.start(
                on_command_callback=self._on_telegram_command,
                get_status_callback=self._telegram_get_status,
                get_memory_callback=self._telegram_get_memory,
                wake_callback=self._telegram_wake
            )
            if self._telegram_bot.is_running():
                self.ui.telegram_active = True
                self.ui.write_log("SYS: Telegram remote bot online.")
            else:
                self.ui.write_log("SYS: Telegram bot configuration missing.")
        except Exception as e:
            print(f"[JARVIS] ⚠️ Failed to start Telegram Bot: {e}")

    def _on_user_arrived(self):
        self.ui.write_log("SYS: User presence detected.")
        if not self.awake:
            self._wake_up()
        if self._scheduler and self.session:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": "System Notification: User has arrived. Please perform the morning briefing."}]},
                    turn_complete=True
                ),
                self._loop
            )

    def _on_user_left(self):
        self.ui.write_log("SYS: User left. Entering standby.")
        if self.awake:
            self._go_to_sleep()

    def _on_fatigue_detected(self, fatigued: bool):
        """Callback: triggered when presence detector detects fatigue."""
        if fatigued and self.awake and self.session and self._loop:
            self.ui.write_log("SYS: ⚠️ Fatigue detected — alerting user.")
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": (
                        "System Alert: Fatigue detected. The user appears tired (eyes closed repeatedly). "
                        "Please say a short, caring message encouraging them to take a break."
                    )}]},
                    turn_complete=True
                ),
                self._loop
            )

    def _on_mood_changed(self, mood: str):
        """Callback: triggered when presence detector detects a mood change."""
        if mood and mood not in ("unknown", "neutral") and self.awake and self.session and self._loop:
            self.ui.write_log(f"SYS: Mood detected — {mood}.")
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": (
                        f"System Alert: Mood analysis indicates the user appears {mood}. "
                        f"Adapt your tone accordingly and briefly acknowledge."
                    )}]},
                    turn_complete=True
                ),
                self._loop
            )

    def _on_intruder_detected_sentry(self, jpeg_bytes: bytes, timestamp: str):
        """
        Callback Sentry : envoi photo sur Telegram + log HUD.
        Called from PresenceDetector._sentry_alert_worker in a daemon thread.
        """
        self.ui.write_log(f"SYS: 🚨 SENTRY — INTRUDER at {timestamp} — Telegram alert sent!")
        self.ui.add_notification(f"🚨 Intruder detected! {timestamp}", "🚨")

        # Envoyer la photo sur Telegram si le bot est actif
        if self._telegram_bot and self._telegram_bot.is_running():
            caption = f"🚨 [JARVIS SENTRY] Intruder detected at {timestamp}\nWorkstation locked."
            self._telegram_bot.send_photo(jpeg_bytes, caption=caption)
        else:
            print(f"[Sentry] ⚠️ Telegram bot offline — photo NOT sent remotely (saved locally).")

        # Annoncer vocalement si JARVIS est éveillé
        if self.awake and self.session and self._loop:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": (
                        "System Alert: Sentry Mode triggered. An intruder has been detected in front of the webcam. "
                        "Photo sent to Telegram. Workstation is being locked. Please confirm."
                    )}]},
                    turn_complete=True
                ),
                self._loop
            )

    def _on_scheduler_alert(self, alert_type: str, message: str, data: dict = None):
        icon = "⚠️"
        if alert_type == "morning":
            icon = "☀️"
        elif alert_type == "evening":
            icon = "🌙"
        elif alert_type == "health":
            icon = "🚨"
        
        self.ui.add_notification(message, icon)
        
        if self._telegram_bot:
            self._telegram_bot.send_notification(f"[{icon} ALERT] {message}")
            
        if self.awake and self.session and self._loop:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": f"System Alert: {message}"}]},
                    turn_complete=True
                ),
                self._loop
            )

    def _on_telegram_command(self, text: str) -> str:
        with self._telegram_lock:
            if not self._loop or not self.session:
                return "JARVIS is currently offline or connecting."
                
            req = {
                "text": text,
                "event": threading.Event(),
                "response": ""
            }
            self._telegram_request = req
            
            if not self.awake:
                self._wake_up()
                
            self.ui.write_log(f"Telegram: {text}")
            
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"role": "user", "parts": [{"text": text}]},
                    turn_complete=True
                ),
                self._loop
            )
            
            success = req["event"].wait(timeout=25.0)
            self._telegram_request = None
            
            if success:
                return req["response"]
            else:
                return "Timeout: JARVIS was unable to respond in time."

    def _telegram_get_status(self) -> str:
        if self._system_monitor:
            return self._system_monitor.get_summary_text()
        return "System Monitor offline."

    def _telegram_get_memory(self) -> str:
        try:
            from memory.memory_manager import load_memory, format_memory_for_prompt
            mem = load_memory()
            total_keys = sum(len(mem.get(cat, {})) for cat in mem)
            summary = f"Total facts stored: {total_keys}\n\n"
            summary += format_memory_for_prompt(mem)
            return summary
        except Exception as e:
            return f"Error: {e}"

    def _telegram_wake(self):
        self._wake_up()
        self.ui.write_log("SYS: Woken up via Telegram.")


def main():
    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)

        # Thread wake word local (actif UNIQUEMENT en mode veille)
        # Utilise Google Speech Recognition — gratuit, zéro tokens Gemini
        # Quand JARVIS est réveillé, ce thread se met en pause automatiquement
        ww_thread = threading.Thread(
            target=jarvis._wake_word_listener,
            daemon=True,
            name="WakeWordListener"
        )
        ww_thread.start()

        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    sys.exit(ui.app.exec_())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        print("\n" + "="*50)
        print("🔴 CRITICAL STARTUP ERROR:")
        traceback.print_exc()
        print("="*50)
        input("\nPress ENTER to close...")
