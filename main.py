import asyncio
import threading
import json
import sys
import traceback
import re
import time
import struct
import math
from pathlib import Path

try:
    import speech_recognition as sr
    _SR_AVAILABLE = True
except ImportError:
    _SR_AVAILABLE = False
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


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "gemini-2.5-flash-native-audio-latest"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# ── VAD (Voice Activity Detection) — inspiré d'ADA V2 ────────────────────────
VAD_THRESHOLD       = 1200   # RMS seuil pour détection de parole (16-bit) — assez haut pour ignorer l'écho
SILENCE_DURATION    = 0.7    # Secondes de silence pour considérer "fin de parole"

# ── Outils non-bloquants (s'exécutent en arrière-plan) ────────────────────────
NON_BLOCKING_TOOLS  = {
    "web_search", "browser_control", "dev_agent", "agent_task",
    "code_helper", "flight_finder", "game_updater", "web_agent",
}


def _get_api_key() -> str:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("gemini_api_key", "")
    except Exception:
        return ""


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )


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
        api_key = _get_api_key()  # peut être vide, memory_manager gère le fallback FreeLLMAPI
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
]


# ── Wake word config ─────────────────────────────────────────────────────────
# Phrases qui réveillent JARVIS (insensible à la casse)
WAKE_PHRASES  = ["ok jarvis", "okay jarvis", "hey jarvis", "oi jarvis", "ok, jarvis"]
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
        if p in t:
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

        # ── Historique de conversation (pour reconnexion intelligente) ────────
        self._chat_history  = []          # list of {"role": "user"|"jarvis", "text": str}
        self._max_history   = 20          # garder les 20 derniers échanges

        self.ui.on_text_command = self._on_text_command

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
        print("[JARVIS] 🟢 Wake word détecté — JARVIS actif")
        self.ui.set_state("LISTENING")
        self.ui.write_log("SYS: ✅ JARVIS — mode actif")
        # Réponse vocale de réveil
        self.speak("Oui, je vous écoute.")

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

    def _wake_word_listener(self):
        """
        Écoute locale du wake word via Google Speech Recognition.
        Actif UNIQUEMENT en mode veille — consomme zéro tokens Gemini.
        Quand JARVIS est réveillé, ce thread fait une pause.
        """
        if not _SR_AVAILABLE:
            print("[WakeWord] ❌ SpeechRecognition non disponible")
            return

        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 1.5

        print("[WakeWord] 🎤 Listener démarré (local, gratuit)")

        while True:
            try:
                # Si JARVIS est réveillé, on fait une pause
                # (le nouveau système Gemini gère l'audio)
                if self.awake:
                    time.sleep(0.5)
                    continue

                # Si muted, on fait aussi une pause
                if self.ui.muted:
                    time.sleep(0.3)
                    continue

                with sr.Microphone(sample_rate=16000) as source:
                    recognizer.adjust_for_ambient_noise(source, duration=0.3)
                    try:
                        audio = recognizer.listen(source, timeout=3, phrase_time_limit=3)
                    except sr.WaitTimeoutError:
                        continue

                try:
                    text = recognizer.recognize_google(audio, language="fr-FR").lower()
                except (sr.UnknownValueError, sr.RequestError):
                    # Essayer en anglais aussi
                    try:
                        text = recognizer.recognize_google(audio, language="en-US").lower()
                    except Exception:
                        continue

                text = re.sub(r"[^\w\s]", "", text).strip()
                if not text:
                    continue

                print(f"[WakeWord] 🗣️ Entendu: '{text}'")

                if _matches_phrase(text, WAKE_PHRASES):
                    self._wake_up()

            except Exception as e:
                err_msg = str(e).lower()
                if "aborted" not in err_msg:
                    print(f"[WakeWord] ⚠️ {e}")
                if "could not find pyaudio" in err_msg:
                    print("[WakeWord] 🛑 Désactivation du WakeWord pour éviter les lags (PyAudio introuvable).")
                    break
                time.sleep(1)

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
        if self.audio_in_queue is None:
            return
        count = 0
        try:
            while not self.audio_in_queue.empty():
                self.audio_in_queue.get_nowait()
                count += 1
            if count > 0:
                print(f"[JARVIS] 🔇 Audio queue vidée ({count} chunks) — interruption utilisateur")
        except Exception:
            pass

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
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription=types.AudioTranscriptionConfig(),
            # input_audio_transcription retiré — dégrade les performances avec un flux audio continu
            # et cause des erreurs 1011 côté serveur après quelques échanges
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
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking

            if self.ui.muted or jarvis_speaking:
                return  # micro coupé ou JARVIS parle → on ignore

            data = indata.tobytes()

            # ── VAD : IA Neuronal (Silero) ou Mathématique (RMS) ─────────────
            is_voice_detected = False

            if self._local_vad.enabled:
                # Inférence IA super légère (ne tourne QUE parce qu'on est pas muet/en train de parler)
                is_voice_detected = self._local_vad.is_speech(data, threshold=0.5)
            else:
                # Fallback mathématique bourrin (RMS) si le modèle IA n'est pas dispo
                count = len(data) // 2
                if count > 0:
                    shorts = struct.unpack(f"<{count}h", data)
                    sum_squares = sum(s * s for s in shorts)
                    rms = int(math.sqrt(sum_squares / count))
                else:
                    rms = 0
                is_voice_detected = (rms > VAD_THRESHOLD)

            if is_voice_detected:
                # Parole détectée
                self._vad_silence_start = None

                if not self._vad_is_speaking:
                    self._vad_is_speaking = True
                    # L'utilisateur commence à parler pendant que JARVIS parle
                    # → interrompre JARVIS (vrai interruption seulement)
                    with self._speaking_lock:
                        is_playing = self._is_speaking
                    if is_playing:
                        self._clear_audio_queue()
                        print("[JARVIS] 🔇 Interruption détectée par l'utilisateur")

            else:
                # Silence
                if self._vad_is_speaking:
                    if self._vad_silence_start is None:
                        self._vad_silence_start = time.time()
                    elif time.time() - self._vad_silence_start > SILENCE_DURATION:
                        self._vad_is_speaking = False
                        self._vad_silence_start = None

            # ── Envoyer l'audio à Gemini ─────────────────────────────────────
            # En mode ACTIF : envoyer l'audio UNIQUEMENT si voix détectée par le VAD
            # En mode VEILLE : NE PAS envoyer (le wake word est géré localement
            #                  par _wake_word_listener → zéro tokens Gemini)
            if self.awake:
                # Utilisation du type types.Blob exact avec le rate explicite
                blob = types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                loop.call_soon_threadsafe(self.out_queue.put_nowait, blob)

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

                    if response.data:
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            self.set_speaking(True)
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
                            self.set_speaking(False)

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                self._add_to_history("user", full_in)
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Jarvis: {full_out}")
                                self._add_to_history("jarvis", full_out)
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
        print("[JARVIS] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        try:
            while True:
                chunk = await self.audio_in_queue.get()
                self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    # ── Main run loop with reconnection — inspiré d'ADA V2 ───────────────────

    async def run(self):
        api_key = _get_api_key()
        if not api_key:
            msg = (
                "⚠️  Clé Gemini API manquante.\n"
                "La voix temps réel (Gemini Live) nécessite une clé Gemini.\n"
                "Ajoutez 'gemini_api_key' dans config/api_keys.json.\n"
                "Les outils texte (recherche, mémoire, code...) fonctionnent avec FreeLLMAPI."
            )
            print(f"[JARVIS] {msg}")
            self.ui.write_log("ERR: Clé Gemini manquante — voix désactivée. Voir console.")
            self.ui.set_state("SLEEPING")
            # Garder l'UI vivante en mode texte seulement
            while True:
                await asyncio.sleep(60)

        client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1beta"}
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

                    # Reset retry delay on successful connection
                    retry_delay = 1

            except Exception as e:
                print(f"[JARVIS] ⚠️ {e}")
                traceback.print_exc()

            self.set_speaking(False)
            self.ui.set_state("THINKING")

            # ── Exponential backoff — inspiré d'ADA V2 ────────────────────────
            print(f"[JARVIS] 🔄 Reconnecting in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 10)  # 1s → 2s → 4s → 8s → max 10s
            is_reconnect = True


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
    ui.root.mainloop()


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
