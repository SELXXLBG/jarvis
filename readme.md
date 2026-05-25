# 🤖 JARVIS — MARK XXXV

> A real-time, voice-driven AI assistant for Windows — powered by the Gemini Live API and enriched by [FreeLLMAPI](https://github.com/M-Gonzalo/FreeLLMAPI) for free AI model access.

---

## ✨ Overview

**MARK XXXV** is an advanced voice-driven AI assistant designed to turn your computer into an interactive intelligent system.
Speak naturally — it listens, understands context, responds with a human-like voice, and executes tasks across your system automatically.
Designed for speed, autonomy, and real-world usability.

---

## 🚀 Capabilities

### Core
- **Real-time voice interaction** — Natural conversation with instant response in any language
- **Smart AI routing** — Uses FreeLLMAPI for everyday tasks (`gemini-2.5-flash-lite`, `gpt-4o`), and Gemini Live for voice
- **System control** — Launch apps, manage files, execute terminal commands
- **Autonomous task execution** — Plans and completes complex multi-step workflows
- **Visual awareness** — Full screen analysis and webcam understanding
- **Persistent memory** — Learns your name, preferences, projects and remembers them across sessions
- **Mute button** — Click or press F4 to instantly silence the microphone
- **Keyboard input** — Type commands directly from the UI without speaking

---

## 🆕 What's New in XXXV

- 🔀 **Smart model routing** — Routes simple requests to fast models, complex ones (code, reasoning) to powerful models, all through FreeLLMAPI
- 🎮 Steam & Epic Games integration — install, update, schedule, auto-shutdown
- 🔇 Mute button (F4 / click) — no more Jarvis picking up side conversations
- ⌨️ Keyboard input on UI — type commands without speaking
- 🧠 Smarter memory — saves favorites, projects, relationships, plans automatically
- 🌐 Incognito browser support
- 🔊 Error reporting — tool failures spoken aloud
- 🔁 Status indicator — LISTENING / SPEAKING / THINKING / MUTED states on UI
- ⚡ Faster response — removed unnecessary round-trips before tool calls

---

## ⚡ Quick Start

### 1. Clone the repository
```bash
git clone https://github.com/YOUR_USERNAME/jarvis-mark-xxxv.git
cd jarvis-mark-xxxv
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
playwright install
```

### 3. Set up FreeLLMAPI (optional but recommended)
FreeLLMAPI lets JARVIS use powerful AI models **for free** without consuming your Gemini quota.

```bash
git clone https://github.com/M-Gonzalo/FreeLLMAPI.git
cd FreeLLMAPI
npm install
npm run build
node server/dist/index.js
```
Get your free key at: https://freellmapi.net

### 4. Configure API keys
Copy the example config and fill in your keys:
```bash
cp config/api_keys.example.json config/api_keys.json
```

Edit `config/api_keys.json`:
```json
{
    "gemini_api_key": "YOUR_GEMINI_API_KEY",
    "freellmapi_key": "YOUR_FREELLMAPI_KEY"
}
```

Get your free Gemini key: https://aistudio.google.com/apikey

### 5. Launch JARVIS
```bash
python main.py
```
Or double-click `LANCER_JARVIS.bat`

---

## 🧠 Smart AI Routing

JARVIS uses an intelligent routing system to maximize performance while minimizing API costs:

| Request Type | Model Used | Via |
|---|---|---|
| Simple chat / questions | `gemini-2.5-flash-lite` | FreeLLMAPI |
| Code / complex reasoning | `gpt-4o` | FreeLLMAPI |
| Real-time voice / audio | `gemini-2.5-flash-native-audio-latest` | Google Gemini Live |

> All text-based requests go through FreeLLMAPI — **zero Gemini quota consumed** for everyday use!

---

## 📋 Requirements

- Windows 10 / 11
- Python 3.11, 3.12 or 3.13
- Microphone
- Free [Gemini API key](https://aistudio.google.com/apikey)
- (Optional) [FreeLLMAPI](https://freellmapi.net) key for free unlimited text generation

---

## ⚠️ License

Personal and non-commercial use only.
Licensed under **Creative Commons BY-NC 4.0**.

⭐ Star the repository to support the project.

---

## 💬 Contact

- YouTube: [@FatihMakes](https://www.youtube.com/@FatihMakes)
- Instagram: [@fatihmakes](https://www.instagram.com/fatihmakes/)
