"""
Enhanced Text-to-Speech with Kokoro local TTS + Gemini Live voice selection.
Provides more consistent, human-sounding voice output.
"""

import os
import json
from pathlib import Path
from typing import Optional

try:
    import sounddevice as sd
    import numpy as np
    _SD_OK = True
except ImportError:
    _SD_OK = False

try:
    import onnxruntime as ort
    _ONNX_OK = True
except ImportError:
    _ONNX_OK = False

BASE_DIR = Path(__file__).resolve().parent.parent
KOKORO_MODEL = BASE_DIR / "models" / "kokoro" / "kokoro-v1.0.onnx"
KOKORO_VOICES = BASE_DIR / "models" / "kokoro" / "voices-v1.0.bin"

# Voix supportées par gemini-2.5-flash-native-audio-latest
GEMINI_VOICES = ["Aoede", "Charon", "Fenrir", "Kore", "Puck"]


class TTSConfig:
    """Configuration for TTS engine."""

    def __init__(self, config_path=None):
        self.config_path = config_path or BASE_DIR / "config" / "tts_config.json"
        self.load()

    def load(self):
        """Load TTS config (voice preference, engine choice, etc)."""
        try:
            if self.config_path.exists():
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.gemini_voice = data.get("gemini_voice", "Aoede")
                    self.tts_engine = data.get("tts_engine", "gemini")  # "gemini" or "kokoro"
                    self.kokoro_voice = data.get("kokoro_voice", "af")  # af/am/bf/bm
                    self.speed = data.get("speed", 1.0)
                    return
        except Exception:
            pass

        self.gemini_voice = "Aoede"  # More natural than Charon
        self.tts_engine = "gemini"
        self.kokoro_voice = "af"
        self.speed = 1.0

    def save(self):
        """Persist TTS config."""
        os.makedirs(self.config_path.parent, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({
                "gemini_voice": self.gemini_voice,
                "tts_engine": self.tts_engine,
                "kokoro_voice": self.kokoro_voice,
                "speed": self.speed,
            }, f, indent=2)


class KokoroTTS:
    """Local Kokoro TTS engine for ultra-human speech."""

    def __init__(self):
        self.available = _ONNX_OK and KOKORO_MODEL.exists() and KOKORO_VOICES.exists()
        if not self.available:
            print("[TTS] Kokoro not available (missing ONNX runtime or model files)")
            return

        try:
            self.session = ort.InferenceSession(str(KOKORO_MODEL))
            print(f"[TTS] Kokoro model loaded: {KOKORO_MODEL}")
        except Exception as e:
            print(f"[TTS] Failed to load Kokoro: {e}")
            self.available = False

    def synthesize(self, text: str, voice: str = "af") -> bytes | None:
        """NOT IMPLEMENTED — Kokoro TTS integration pending."""
        return None  # Falls back to Gemini TTS


class GeminiVoiceConfig:
    """Helper for Gemini Live voice configuration."""

    @staticmethod
    def get_voice_config(voice_name: str):
        """Return proper VoiceConfig for Gemini Live."""
        from google.genai import types

        return types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                voice_name=voice_name
            )
        )

    @staticmethod
    def recommend_voice(context: str = "default") -> str:
        """Recommend a Gemini voice based on context."""
        recommendations = {
            "default":      "Aoede",   # Chaleureuse, naturelle
            "professional": "Charon",  # Neutre, autoritaire
            "friendly":     "Kore",    # Approchable
            "energetic":    "Puck",    # Vif, dynamique
            "warm":         "Fenrir",  # Grave, intime
        }
        return recommendations.get(context, "Aoede")


_TTS_CONFIG: Optional[TTSConfig] = None


def get_tts_config() -> TTSConfig:
    """Get or create global TTS config."""
    global _TTS_CONFIG
    if _TTS_CONFIG is None:
        _TTS_CONFIG = TTSConfig()
    return _TTS_CONFIG


def set_gemini_voice(voice_name: str):
    """Change Gemini Live voice for next session."""
    if voice_name not in GEMINI_VOICES:
        print(f"[TTS] Invalid voice: {voice_name}. Available: {GEMINI_VOICES}")
        return False
    config = get_tts_config()
    config.gemini_voice = voice_name
    config.save()
    print(f"[TTS] Gemini voice changed to: {voice_name}")
    return True


def set_kokoro_voice(voice_id: str):
    """Change Kokoro voice (af/am/bf/bm)."""
    if voice_id not in ["af", "am", "bf", "bm"]:
        print(f"[TTS] Invalid Kokoro voice: {voice_id}. Use: af/am/bf/bm")
        return False
    config = get_tts_config()
    config.kokoro_voice = voice_id
    config.save()
    print(f"[TTS] Kokoro voice changed to: {voice_id}")
    return True


def set_tts_engine(engine: str):
    """Switch TTS engine (gemini/kokoro)."""
    if engine not in ["gemini", "kokoro"]:
        print(f"[TTS] Invalid engine: {engine}")
        return False
    config = get_tts_config()
    config.tts_engine = engine
    config.save()
    print(f"[TTS] Engine changed to: {engine}")
    return True
