import os
import json
import platform
import subprocess
from datetime import datetime
from pathlib import Path

def get_local_ai_status():
    """Checks for local AI capabilities."""
    status = []
    
    # Check VAD
    vad_path = Path(__file__).resolve().parent.parent / "models" / "silero_vad.onnx"
    status.append(f"VAD (Silero): {'✅ Local' if vad_path.exists() else '❌ Cloud Fallback'}")
    
    # Check OCR
    try:
        import easyocr
        status.append("OCR (EasyOCR): ✅ Local")
    except:
        status.append("OCR (EasyOCR): ❌ Cloud Fallback")
        
    # Check Ollama
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=1)
        if r.status_code == 200:
            status.append("LLM (Ollama): ✅ Ready")
        else:
            status.append("LLM (Ollama): ⚠️ Installed but not running")
    except:
        status.append("LLM (Ollama): ❌ Not found")
        
    return status

def get_system_overview():
    """Returns a brief overview of the system status."""
    system = platform.system()
    
    overview = f"System: {system} {platform.release()}\n"
    overview += f"Current Time: {datetime.now().strftime('%H:%M')}\n"
    
    # Local AI Status
    ai_status = get_local_ai_status()
    overview += "\nLocal AI Assets:\n"
    for s in ai_status:
        overview += f"  - {s}\n"
    
    # Check for active windows if on Windows
    if system == "Windows":
        try:
            cmd = 'tasklist /FI "STATUS eq running" /FO CSV'
            output = subprocess.check_output(cmd, shell=True).decode('utf-8', errors='ignore')
            lines = output.strip().split('\n')[1:10]
            apps = [line.split(',')[0].strip('"') for line in lines]
            overview += f"\nRunning Context: {', '.join(set(apps))}\n"
        except:
            pass
            
    return overview

def daily_briefing(parameters, player=None, speak=None):
    """
    Provides a daily briefing: weather, system status, and any important notes.
    """
    if player:
        player.write_log("[Proactive] Generating Daily Briefing...")
        
    now = datetime.now()
    time_str = now.strftime("%I:%M %p")
    date_str = now.strftime("%A, %B %d")
    
    briefing = f"Good { 'morning' if now.hour < 12 else 'afternoon' if now.hour < 18 else 'evening' }, sir. "
    briefing += f"It is currently {time_str} on this fine {date_str}.\n\n"
    
    # System Overview
    briefing += "--- System Status ---\n"
    briefing += get_system_overview()
    
    if speak:
        speak(f"Good day, sir. Systems are nominal. It is {time_str}. Shall I proceed with the full briefing?")
        
    return briefing

def proactive_check(parameters, player=None, speak=None):
    """
    A tool for JARVIS to proactively check things or offer suggestions.
    """
    action = parameters.get("action", "briefing")
    
    if action == "briefing":
        return daily_briefing(parameters, player, speak)
    
    return "Proactive check complete, sir."
