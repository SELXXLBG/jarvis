# JARVIS Voice Control Guide

## Available Voices

JARVIS now supports **7 premium Gemini Live voices**, each with unique characteristics:

| Voice | Style | Best For |
|-------|-------|----------|
| **Breeze** | Warm, friendly, natural | Default; all-purpose conversations |
| **Sage** | Calm, authoritative, professional | Technical explanations, formal contexts |
| **Cove** | Approachable, engaging, friendly | Casual chats, friendly tone |
| **Orbit** | Upbeat, energetic, dynamic | Excitement, enthusiasm, energy |
| **Juniper** | Warm, intimate, personal | Intimate conversations, warmth |
| **Aoife** | Clear, expressive, theatrical | Storytelling, dramatic moments |
| **Ember** | Strong, confident, assertive | Bold statements, confidence |

## Changing Your Voice

### Method 1: Direct Command
Simply ask JARVIS to change their voice:
- "Change your voice to Breeze"
- "Use Sage for the next response"
- "Let's try Orbit, I like energetic voices"
- "Switch to Juniper"

JARVIS will change their voice on the next response.

### Method 2: CLI (for development)
```python
from core.tts_enhanced import set_gemini_voice

set_gemini_voice("Orbit")  # Change to Orbit
```

## Configuration

The current voice setting is saved in `config/tts_config.json`:
```json
{
  "gemini_voice": "Breeze",
  "tts_engine": "gemini",
  "kokoro_voice": "af",
  "speed": 1.0
}
```

Your voice preference persists across sessions — JARVIS remembers your choice!

## Why Better Voices Matter

The original Charon voice was:
- ❌ Monotone and robotic
- ❌ Variable between responses
- ❌ Difficult to understand in background noise
- ❌ Emotionally flat

**New voices are:**
- ✅ Natural and expressive
- ✅ Consistent across all responses
- ✅ Clear and intelligible
- ✅ Emotionally warm and engaging

## Technical Details

- **Engine**: Google Gemini 2.5 Flash native-audio
- **Codec**: 24kHz PCM, opus-encoded streaming
- **Latency**: ~200-500ms (real-time audio streaming)
- **Quality**: Studio-grade voice models trained on professional voice actors

## Future: Kokoro Local TTS

[In development] Kokoro local TTS will offer:
- Ultra-realistic human speech synthesis
- Offline operation (no API calls)
- Complete voice control (pitch, speed, emotion)
- Support for 4 voice profiles

(Requires ONNX Runtime installation)

## Tips

1. **For focus work**: Use "Sage" — calm and professional
2. **For creative tasks**: Use "Orbit" — energetic and inspiring
3. **For late night**: Use "Juniper" — warm and soothing
4. **For storytelling**: Use "Aoife" — expressive and clear
5. **For everyday use**: Use "Breeze" (default) — balanced and warm

---

*Made with ❤️ for better human-computer interaction*
