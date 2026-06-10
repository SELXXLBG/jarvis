"""
JARVIS Telegram Bot — Module d'accès distant / Remote access module
====================================================================
Permet de contrôler JARVIS à distance via Telegram.
Allows remote control of JARVIS through a Telegram bot.

Requires: python-telegram-bot[ext] >= 20.0, mss
Graceful fallback if not installed.
"""

import asyncio
import io
import json
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, List, Optional

# ── Graceful import guards ───────────────────────────────────────────
_TELEGRAM_AVAILABLE = False
_MSS_AVAILABLE = False

try:
    from telegram import Bot, Update
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
    from telegram.constants import ParseMode
    from telegram.error import (
        Forbidden,
        NetworkError,
        RetryAfter,
        TelegramError,
        TimedOut,
    )
    _TELEGRAM_AVAILABLE = True
except ImportError:
    pass

try:
    import mss
    import mss.tools
    _MSS_AVAILABLE = True
except ImportError:
    pass


# ── Chemins de configuration / Config paths ─────────────────────────
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_API_KEYS_FILE = _CONFIG_DIR / "api_keys.json"


def _log(msg: str) -> None:
    """Print avec préfixe [Telegram] / Prefixed print."""
    print(f"[Telegram] {msg}")


def _load_config() -> dict:
    """Charge la configuration depuis api_keys.json / Load config."""
    try:
        with open(_API_KEYS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        _log(f"⚠ Config file not found: {_API_KEYS_FILE}")
        return {}
    except json.JSONDecodeError as e:
        _log(f"⚠ Invalid JSON in config: {e}")
        return {}
    except Exception as e:
        _log(f"⚠ Error reading config: {e}")
        return {}


class JarvisTelegramBot:
    """
    Bot Telegram pour JARVIS — tourne dans son propre thread daemon.
    Telegram bot for JARVIS — runs in its own daemon thread.

    Usage:
        bot = JarvisTelegramBot()
        bot.start(
            on_command_callback=my_command_handler,
            get_status_callback=my_status_getter,
            get_memory_callback=my_memory_getter,
            wake_callback=my_wake_function,
        )
        # ...
        bot.stop()
    """

    def __init__(self) -> None:
        # ── État interne / Internal state ────────────────────────────
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._app: Optional["Application"] = None  # type: ignore[name-defined]
        self._bot: Optional["Bot"] = None  # type: ignore[name-defined]

        # ── Callbacks (définis au start) / Set at start() ────────────
        self._on_command_callback: Optional[Callable[[str], str]] = None
        self._get_status_callback: Optional[Callable[[], str]] = None
        self._get_memory_callback: Optional[Callable[[], str]] = None
        self._wake_callback: Optional[Callable[[], None]] = None

        # ── Config ───────────────────────────────────────────────────
        self._token: str = ""
        self._allowed_ids: List[int] = []
        self._allow_all: bool = False  # True si whitelist vide

    # ══════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════

    def start(
        self,
        on_command_callback: Callable[[str], str],
        get_status_callback: Callable[[], str],
        get_memory_callback: Callable[[], str],
        wake_callback: Callable[[], None],
    ) -> None:
        """
        Démarre le bot dans un thread daemon / Start the bot in a daemon thread.
        Safe to call multiple times — will do nothing if already running.
        """
        with self._lock:
            if self._running:
                _log("Bot already running, ignoring start().")
                return

        # ── Vérifications préalables / Pre-flight checks ─────────────
        if not _TELEGRAM_AVAILABLE:
            _log("⚠ python-telegram-bot not installed. pip install python-telegram-bot")
            _log("  Telegram bot will NOT start.")
            return

        config = _load_config()
        self._token = config.get("telegram_bot_token", "").strip()
        if not self._token:
            _log("⚠ No 'telegram_bot_token' in config — bot disabled.")
            return

        raw_ids = config.get("telegram_allowed_ids", [])
        self._allowed_ids = [int(uid) for uid in raw_ids if uid]
        if not self._allowed_ids:
            self._allow_all = False
            _log("⚠ WARNING: telegram_allowed_ids is empty — ALL commands REFUSED for security. Add your Telegram ID to api_keys.json.")
        else:
            self._allow_all = False
            _log(f"Whitelist: {self._allowed_ids}")

        # ── Enregistrer les callbacks / Register callbacks ───────────
        self._on_command_callback = on_command_callback
        self._get_status_callback = get_status_callback
        self._get_memory_callback = get_memory_callback
        self._wake_callback = wake_callback

        # ── Lancer le thread / Launch the thread ─────────────────────
        with self._lock:
            self._running = True
        self._thread = threading.Thread(target=self._run_bot, daemon=True, name="TelegramBot")
        self._thread.start()
        _log("✓ Bot thread started.")

    def stop(self) -> None:
        """Arrête le bot proprement / Stop the bot gracefully."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        _log("Stopping bot...")

        # Demander l'arrêt de l'application dans la boucle async
        if self._loop and self._app:
            try:
                future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
                future.result(timeout=10)
            except Exception as e:
                _log(f"⚠ Error during shutdown: {e}")

        # Attendre la fin du thread
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

        self._app = None
        self._bot = None
        self._loop = None
        _log("✓ Bot stopped.")

    def is_running(self) -> bool:
        """Retourne True si le bot tourne / Returns True if bot is running."""
        with self._lock:
            return self._running

    def send_notification(self, message: str) -> None:
        """
        Envoie un message texte à tous les utilisateurs autorisés.
        Send a text message to all whitelisted users.
        """
        if not self.is_running() or not self._loop or not self._bot:
            _log("⚠ Cannot send notification — bot not running.")
            return

        future = asyncio.run_coroutine_threadsafe(self._broadcast_text(message), self._loop)
        future.add_done_callback(
            lambda f: _log(f"Notification error: {f.exception()}") if f.exception() else None
        )

    def send_photo(self, image_bytes: bytes, caption: str = "") -> None:
        """
        Envoie une image à tous les utilisateurs autorisés.
        Send a photo to all whitelisted users.
        """
        if not self.is_running() or not self._loop or not self._bot:
            _log("⚠ Cannot send photo — bot not running.")
            return

        asyncio.run_coroutine_threadsafe(
            self._broadcast_photo(image_bytes, caption), self._loop
        )

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL — Bot lifecycle
    # ══════════════════════════════════════════════════════════════════

    def _run_bot(self) -> None:
        """Fonction exécutée dans le thread daemon / Daemon thread entry point."""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._start_polling())
        except Exception as e:
            _log(f"✖ Fatal error in bot thread: {e}")
            traceback.print_exc()
        finally:
            with self._lock:
                self._running = False
            _log("Bot thread exited.")

    async def _start_polling(self) -> None:
        """Construit l'application et lance le polling / Build app and start polling."""
        builder = Application.builder().token(self._token)
        # Timeouts réseau plus tolérants / More tolerant network timeouts
        builder.read_timeout(30)
        builder.write_timeout(30)
        builder.connect_timeout(15)
        builder.pool_timeout(15)

        self._app = builder.build()
        self._bot = self._app.bot

        # ── Enregistrer les handlers / Register handlers ─────────────
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("screenshot", self._cmd_screenshot))
        self._app.add_handler(CommandHandler("memory", self._cmd_memory))
        self._app.add_handler(CommandHandler("wake", self._cmd_wake))
        # Tous les messages texte / All text messages
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))
        # Erreurs / Errors
        self._app.add_error_handler(self._error_handler)

        _log("✓ Handlers registered. Starting polling...")

        # Initialise et démarre / Initialize and start
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message"],
        )

        _log("✓ Polling started — bot is online!")

        # Boucle infinie tant que running / Loop while running
        try:
            while self.is_running():
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

        # Arrêt propre / Clean stop
        _log("Polling loop ended, shutting down updater...")
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except Exception as e:
            _log(f"⚠ Error during app shutdown: {e}")

    async def _shutdown(self) -> None:
        """Signal d'arrêt interne / Internal shutdown signal."""
        with self._lock:
            self._running = False

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL — Security
    # ══════════════════════════════════════════════════════════════════

    def _is_authorized(self, user_id: int) -> bool:
        """Vérifie si l'utilisateur est autorisé / Check if user is whitelisted."""
        if self._allow_all:
            return True
        return user_id in self._allowed_ids

    def _save_allowed_id_to_config(self, user_id: int) -> None:
        """Sauvegarde un ID utilisateur dans api_keys.json de manière persistante."""
        try:
            config = _load_config()
            allowed_ids = config.get("telegram_allowed_ids", [])
            if not isinstance(allowed_ids, list):
                allowed_ids = []
            if user_id not in allowed_ids:
                allowed_ids.append(user_id)
                config["telegram_allowed_ids"] = allowed_ids
                with open(_API_KEYS_FILE, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=4)
                _log(f"💾 Saved Telegram ID {user_id} to config file.")
        except Exception as e:
            _log(f"⚠️ Failed to save Telegram ID to config: {e}")

    async def _check_auth(self, update: "Update") -> bool:
        """
        Vérifie l'autorisation et répond si refusé.
        Check auth and reply with denial if not authorized.
        Returns True if authorized.
        """
        user = update.effective_user
        if not user:
            return False

        if not self._is_authorized(user.id):
            _log(f"⛔ Access denied for user {user.id} ({user.full_name})")
            try:
                await update.message.reply_text(
                    "⛔ *Access denied*\n"
                    "Your Telegram ID is not authorized to use JARVIS.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            return False

        # Si autorisé, et pas encore dans allowed_ids (ex: cas allow_all = True), on l'ajoute dynamiquement
        if user.id not in self._allowed_ids:
            self._allowed_ids.append(user.id)
            _log(f"➕ Dynamically added authorized user {user.id} ({user.full_name}) to recipients list.")
            self._save_allowed_id_to_config(user.id)

        return True

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL — Command handlers
    # ══════════════════════════════════════════════════════════════════

    async def _cmd_start(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        """Handler pour /start / Handler for /start command."""
        if not await self._check_auth(update):
            return

        welcome = (
            "🤖 *JARVIS Telegram Remote Access*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Welcome. I am JARVIS, your personal AI assistant.\n"
            "You can control me remotely via the following commands:\n\n"
            "📋 *Commands:*\n"
            "  /status — System status overview\n"
            "  /screenshot — Capture current screen\n"
            "  /memory — View memory summary\n"
            "  /wake — Wake JARVIS if sleeping\n"
            "  /help — Show this help message\n\n"
            "💬 *Text messages:*\n"
            "  Any text message will be processed as a JARVIS command.\n\n"
            "🔒 _This session is secured by user ID whitelist._"
        )
        await self._safe_reply(update, welcome, parse_mode=ParseMode.MARKDOWN)
        _log(f"User {update.effective_user.id} started bot.")

    async def _cmd_help(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        """Handler pour /help / Handler for /help command."""
        if not await self._check_auth(update):
            return

        help_text = (
            "📋 *JARVIS — Available Commands*\n\n"
            "/start — Welcome & introduction\n"
            "/status — System status info\n"
            "/screenshot — Take a screenshot\n"
            "/memory — Memory summary\n"
            "/wake — Wake up JARVIS\n"
            "/help — This message\n\n"
            "💬 Or just send any text as a command."
        )
        await self._safe_reply(update, help_text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_status(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        """Handler pour /status / Handler for /status command."""
        if not await self._check_auth(update):
            return

        _log(f"Status requested by user {update.effective_user.id}")
        await self._safe_reply(update, "⏳ Retrieving system status...")

        try:
            if self._get_status_callback:
                # Exécuter dans un thread pour ne pas bloquer / Run in thread to avoid blocking
                status_text = await asyncio.get_running_loop().run_in_executor(
                    None, self._get_status_callback
                )
            else:
                status_text = "⚠ Status callback not configured."

            response = f"📊 *System Status*\n━━━━━━━━━━━━━━━━\n\n{status_text}"
            await self._safe_reply(update, response, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            _log(f"✖ Error getting status: {e}")
            await self._safe_reply(update, f"✖ Error retrieving status: {e}")

    async def _cmd_screenshot(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        """Handler pour /screenshot / Handler for /screenshot command."""
        if not await self._check_auth(update):
            return

        _log(f"Screenshot requested by user {update.effective_user.id}")

        if not _MSS_AVAILABLE:
            await self._safe_reply(update, "⚠ Screenshot unavailable — `mss` library not installed.")
            return

        await self._safe_reply(update, "📸 Capturing screen...")

        try:
            # Capture dans un thread / Capture in a thread
            screenshot_bytes = await asyncio.get_running_loop().run_in_executor(
                None, self._take_screenshot
            )

            if screenshot_bytes:
                await update.message.reply_photo(
                    photo=io.BytesIO(screenshot_bytes),
                    caption="🖥 JARVIS — Screen Capture",
                )
                _log("Screenshot sent successfully.")
            else:
                await self._safe_reply(update, "✖ Failed to capture screenshot.")
        except Exception as e:
            _log(f"✖ Screenshot error: {e}")
            await self._safe_reply(update, f"✖ Screenshot error: {e}")

    async def _cmd_memory(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        """Handler pour /memory / Handler for /memory command."""
        if not await self._check_auth(update):
            return

        _log(f"Memory requested by user {update.effective_user.id}")

        try:
            if self._get_memory_callback:
                memory_text = await asyncio.get_running_loop().run_in_executor(
                    None, self._get_memory_callback
                )
            else:
                memory_text = "⚠ Memory callback not configured."

            response = f"🧠 *JARVIS Memory Summary*\n━━━━━━━━━━━━━━━━━━━━\n\n{memory_text}"
            # Tronquer si trop long / Truncate if too long (Telegram limit: 4096 chars)
            if len(response) > 4000:
                response = response[:3990] + "\n\n… _(truncated)_"
            await self._safe_reply(update, response, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            _log(f"✖ Error getting memory: {e}")
            await self._safe_reply(update, f"✖ Error: {e}")

    async def _cmd_wake(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        """Handler pour /wake / Handler for /wake command."""
        if not await self._check_auth(update):
            return

        _log(f"Wake requested by user {update.effective_user.id}")

        try:
            if self._wake_callback:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._wake_callback
                )
                await self._safe_reply(update, "☀️ JARVIS has been woken up!")
            else:
                await self._safe_reply(update, "⚠ Wake callback not configured.")
        except Exception as e:
            _log(f"✖ Wake error: {e}")
            await self._safe_reply(update, f"✖ Error waking JARVIS: {e}")

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL — Text message handler
    # ══════════════════════════════════════════════════════════════════

    async def _handle_text(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE") -> None:
        """
        Traite tout message texte comme une commande JARVIS.
        Process any text message as a JARVIS command.
        """
        if not await self._check_auth(update):
            return

        user_text = update.message.text.strip()
        if not user_text:
            return

        user = update.effective_user
        _log(f"Command from {user.id} ({user.full_name}): {user_text[:80]}...")

        try:
            if self._on_command_callback:
                # Exécuter dans un executor pour ne pas bloquer la boucle async
                # Run in executor to avoid blocking the async loop
                response = await asyncio.get_running_loop().run_in_executor(
                    None, self._on_command_callback, user_text
                )
            else:
                response = "⚠ Command callback not configured."

            if not response:
                response = "_(No response from JARVIS)_"

            # Découper en morceaux si nécessaire / Split into chunks if needed
            await self._send_long_message(update, response)

        except Exception as e:
            _log(f"✖ Error processing command: {e}")
            traceback.print_exc()
            await self._safe_reply(update, f"✖ Error processing your command:\n`{e}`",
                                   parse_mode=ParseMode.MARKDOWN)

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL — Broadcasting
    # ══════════════════════════════════════════════════════════════════

    async def _broadcast_text(self, message: str) -> None:
        """Envoie un message à tous les utilisateurs autorisés / Broadcast text."""
        if not self._bot:
            _log("⚠ Cannot broadcast — bot not initialized.")
            return

        if not self._allowed_ids:
            _log("⚠ Cannot broadcast — no recipients in whitelist (telegram_allowed_ids). "
                 "Please send a message to the bot first (e.g. /start) to register your Telegram ID.")
            return

        for uid in self._allowed_ids:
            try:
                await self._safe_send_text(uid, message)
            except Exception as e:
                _log(f"⚠ Failed to send notification to {uid}: {e}")

    async def _broadcast_photo(self, image_bytes: bytes, caption: str = "") -> None:
        """Envoie une photo à tous les utilisateurs autorisés / Broadcast photo."""
        if not self._bot:
            _log("⚠ Cannot broadcast photo — bot not initialized.")
            return

        if not self._allowed_ids:
            _log("⚠ Cannot broadcast photo — no recipients in whitelist (telegram_allowed_ids). "
                 "Please send a message to the bot first (e.g. /start) to register your Telegram ID.")
            return

        for uid in self._allowed_ids:
            try:
                await self._bot.send_photo(
                    chat_id=uid,
                    photo=io.BytesIO(image_bytes),
                    caption=caption[:1024] if caption else None,
                )
            except RetryAfter as e:
                _log(f"⚠ Rate limited, waiting {e.retry_after}s...")
                await asyncio.sleep(e.retry_after)
                try:
                    await self._bot.send_photo(
                        chat_id=uid,
                        photo=io.BytesIO(image_bytes),
                        caption=caption[:1024] if caption else None,
                    )
                except Exception as e2:
                    _log(f"⚠ Retry failed for {uid}: {e2}")
            except Forbidden:
                _log(f"⚠ User {uid} has blocked the bot.")
            except Exception as e:
                _log(f"⚠ Failed to send photo to {uid}: {e}")

    # ══════════════════════════════════════════════════════════════════
    # INTERNAL — Utilities
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _take_screenshot() -> Optional[bytes]:
        """
        Capture l'écran principal en PNG / Capture the primary monitor as PNG bytes.
        Runs in a worker thread.
        """
        try:
            with mss.mss() as sct:
                # Monitor 0 = all monitors combined, 1 = primary
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                sct_img = sct.grab(monitor)
                png_bytes = mss.tools.to_png(sct_img.rgb, sct_img.size)
                return png_bytes
        except Exception as e:
            _log(f"✖ Screenshot capture failed: {e}")
            return None

    async def _safe_reply(self, update: "Update", text: str, **kwargs) -> None:
        """
        Réponse sécurisée avec gestion des erreurs réseau et rate-limit.
        Safe reply with network error and rate-limit handling.
        """
        for attempt in range(3):
            try:
                await update.message.reply_text(text, **kwargs)
                return
            except RetryAfter as e:
                _log(f"⚠ Rate limited (attempt {attempt+1}/3), waiting {e.retry_after}s...")
                await asyncio.sleep(e.retry_after)
            except TimedOut:
                _log(f"⚠ Timed out (attempt {attempt+1}/3), retrying...")
                await asyncio.sleep(2 ** attempt)
            except NetworkError as e:
                _log(f"⚠ Network error (attempt {attempt+1}/3): {e}")
                await asyncio.sleep(2 ** attempt)
            except Forbidden:
                _log(f"⚠ User has blocked the bot — cannot reply.")
                return
            except TelegramError as e:
                _log(f"✖ Telegram API error: {e}")
                return
            except Exception as e:
                _log(f"✖ Unexpected reply error: {e}")
                return

        _log("✖ Failed to reply after 3 attempts.")

    async def _safe_send_text(self, chat_id: int, text: str) -> None:
        """
        Envoi sécurisé d'un message texte avec retry.
        Safe text send with retry logic.
        """
        # Découper en morceaux si nécessaire / Split into chunks
        chunks = self._split_message(text)
        for chunk in chunks:
            for attempt in range(3):
                try:
                    await self._bot.send_message(chat_id=chat_id, text=chunk)
                    break
                except RetryAfter as e:
                    _log(f"⚠ Rate limited, waiting {e.retry_after}s...")
                    await asyncio.sleep(e.retry_after)
                except TimedOut:
                    await asyncio.sleep(2 ** attempt)
                except NetworkError:
                    await asyncio.sleep(2 ** attempt)
                except Forbidden:
                    _log(f"⚠ User {chat_id} has blocked the bot.")
                    return
                except Exception as e:
                    _log(f"✖ Send error to {chat_id}: {e}")
                    return

    async def _send_long_message(self, update: "Update", text: str) -> None:
        """
        Envoie un message long découpé en morceaux de 4096 caractères.
        Send a long message split into 4096-char chunks.
        """
        chunks = self._split_message(text)
        for chunk in chunks:
            await self._safe_reply(update, chunk)

    @staticmethod
    def _split_message(text: str, max_len: int = 4096) -> List[str]:
        """
        Découpe un message en morceaux de max_len caractères.
        Split a message into chunks of max_len characters.
        Essaie de couper aux retours à la ligne / Tries to split at newlines.
        """
        if len(text) <= max_len:
            return [text]

        chunks: List[str] = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break

            # Chercher un retour à la ligne pour couper proprement
            # Look for a newline to split cleanly
            split_at = text.rfind("\n", 0, max_len)
            if split_at == -1 or split_at < max_len // 2:
                # Pas de newline convenable, couper au max
                split_at = max_len

            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")

        return chunks

    async def _error_handler(self, update: object, context: "ContextTypes.DEFAULT_TYPE") -> None:
        """
        Gestionnaire d'erreurs global / Global error handler.
        Attrape les erreurs non gérées dans les handlers.
        """
        error = context.error

        if isinstance(error, NetworkError):
            _log(f"⚠ Network error (will auto-retry): {error}")
        elif isinstance(error, TimedOut):
            _log("⚠ Request timed out (will auto-retry)")
        elif isinstance(error, RetryAfter):
            _log(f"⚠ Rate limited — retry after {error.retry_after}s")
        elif isinstance(error, Forbidden):
            _log(f"⚠ Forbidden — user may have blocked the bot: {error}")
        elif isinstance(error, TelegramError):
            _log(f"✖ Telegram API error: {error}")
        else:
            _log(f"✖ Unhandled error: {error}")
            traceback.print_exc()


# ══════════════════════════════════════════════════════════════════════
# Module-level convenience — Singleton pattern
# ══════════════════════════════════════════════════════════════════════

_instance: Optional[JarvisTelegramBot] = None
_instance_lock = threading.Lock()


def get_telegram_bot() -> JarvisTelegramBot:
    """
    Retourne l'instance singleton du bot / Return the singleton bot instance.
    Thread-safe.
    """
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = JarvisTelegramBot()
        return _instance


# ══════════════════════════════════════════════════════════════════════
# Quick test / Test rapide
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("JARVIS Telegram Bot — Manual Test")
    print("=" * 60)

    if not _TELEGRAM_AVAILABLE:
        print("✖ python-telegram-bot not installed!")
        print("  Run: pip install python-telegram-bot")
        exit(1)

    # Callbacks de test / Test callbacks
    def test_command(text: str) -> str:
        return f"[JARVIS Test] You said: {text}"

    def test_status() -> str:
        import platform
        return (
            f"OS: {platform.system()} {platform.release()}\n"
            f"Python: {platform.python_version()}\n"
            f"Status: Online (test mode)"
        )

    def test_memory() -> str:
        return "No memories stored (test mode)."

    def test_wake() -> None:
        print("[Test] Wake callback triggered!")

    bot = get_telegram_bot()
    bot.start(
        on_command_callback=test_command,
        get_status_callback=test_status,
        get_memory_callback=test_memory,
        wake_callback=test_wake,
    )

    if bot.is_running():
        print("\n✓ Bot is running. Press Ctrl+C to stop.\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping...")
            bot.stop()
            print("Done.")
    else:
        print("\n✖ Bot failed to start. Check config and token.")
