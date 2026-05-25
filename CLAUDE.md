# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

JARVIS MARK XXXV is a Windows-native, real-time voice-driven AI assistant. It streams audio to the Gemini Live API via WebSocket, executes tool calls against 20 action modules, and routes non-voice LLM requests through FreeLLMAPI to avoid Gemini quota consumption.

**Requirements**: Windows 10/11, Python 3.11–3.13, microphone, Gemini API key.

## Setup & Running

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Configure API keys (copy example and fill in)
cp config/api_keys.example.json config/api_keys.json

# Run
python main.py

# Or via Windows launcher
.LANCER_JARVIS.bat

# Debug wrapper (shows full stack traces)
python starter.py
```

No build step, no test suite. The app is a long-running async process.

## Architecture

### Entry point & initialization (`main.py`)

`JarvisLive` is the central class. On startup it:
1. Loads `config/api_keys.json`
2. Calls `llm_patcher.patch()` to intercept Google SDK calls
3. Creates the Tkinter UI (`JarvisUI` from `ui.py`)
4. Loads the system prompt from `core/prompt.txt`
5. Opens a Gemini Live WebSocket session
6. Spawns audio threads and enters the wake-word loop

### Smart LLM routing (`core/llm_patcher.py`)

All Google SDK calls are intercepted and re-routed:

| Request type | Model | Route |
|---|---|---|
| Real-time voice/audio | `gemini-2.5-flash-native-audio-latest` | Direct Gemini (WebSocket) |
| Code / complex reasoning | `gpt-4o` | FreeLLMAPI proxy |
| Everything else | `gemini-2.5-flash-lite` | FreeLLMAPI proxy |

`analyze_request_complexity()` detects keywords like `"code"`, `"python"`, `"debug"`, `"algorithm"` to decide routing. This is what makes everyday usage quota-free.

### Voice pipeline

- **Input**: `sounddevice` → 16 kHz PCM chunks → Gemini Live WebSocket
- **VAD**: Silero v4 ONNX (`models/silero_vad.onnx`), RMS threshold 1200, 0.7 s silence = end of utterance
- **Output**: 24 kHz PCM from Gemini → `sounddevice` playback
- **Codec**: 16-bit PCM, mono, 1024 bytes/chunk

### Tool execution (`actions/`)

20 tool modules are declared in `TOOL_DECLARATIONS` in `main.py`. Tools flagged in `NON_BLOCKING_TOOLS` run in background threads; others block the loop. The `agent_task` tool invokes the planner/executor pipeline (`agent/`) for multi-step goals.

Key tool routing rules from `core/prompt.txt`:
- `computer_settings` → ANY single-command system control (volume, brightness, windows)
- `web_search` → quick factual lookups
- `web_agent` → complex multi-page research (hidden Playwright browser)
- `browser_control` → ONLY when user asks to control their visible browser
- `game_updater` → DIRECT for any Steam/Epic request, never via `agent_task`
- `spotify_control` → Spotify only, never route Spotify requests to YouTube

### Memory system (`memory/`)

Persistent JSON at `memory/long_term.json`. Categories: `identity`, `preferences`, `projects`, `relationships`, `wishes`, `notes`. `memory_manager.py` extracts facts from each conversation turn. 429 quota errors trigger a 1-hour cooldown.

### UI (`ui.py`)

Tkinter with a cyberpunk theme (cyan `#00e5ff` on dark blue `#000508`). UI states: `INITIALISING`, `LISTENING`, `SPEAKING`, `THINKING`, `MUTED`, `ONLINE`. Keyboard input bar lets users type commands. F4 = mute toggle.

## Key Files

| File | Role |
|---|---|
| `main.py` | Core orchestration, tool dispatch, audio streaming |
| `core/prompt.txt` | System prompt — defines personality, tool selection rules, sleep triggers |
| `core/llm_patcher.py` | FreeLLMAPI proxy; edit here to change model routing logic |
| `core/vad_local.py` | Silero VAD ONNX inference |
| `agent/planner.py` | Decomposes goals into tool sequences (max 5 steps) |
| `agent/executor.py` | Runs step chains; can generate and exec Python code |
| `memory/memory_manager.py` | Fact extraction and retrieval |
| `actions/*.py` | One file per tool |
| `config/api_keys.json` | Credentials (gitignored) |

## Configuration

`config/api_keys.json` (gitignored):
```json
{
    "gemini_api_key": "...",
    "freellmapi_key": "..."
}
```

`memory/long_term.json` and `*.db` files in `config/` are also gitignored (runtime state).
