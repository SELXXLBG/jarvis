# core/profile_loader.py
import os
import json
import sys
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
PROFILES_DIR = BASE_DIR / "profiles"

def get_active_profile_name() -> str:
    # 1. Check for manual environment override
    env_profile = os.getenv("JARVIS_PROFILE")
    if env_profile:
        return env_profile

    # 2. Check for local .env file
    dotenv_path = BASE_DIR / ".env"
    if dotenv_path.exists():
        try:
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("JARVIS_PROFILE="):
                        return line.split("=", 1)[1].strip()
        except Exception:
            pass

    # 3. Detect via OS username
    try:
        os_user = os.getlogin().lower()
        if (PROFILES_DIR / os_user).is_dir():
            return os_user
    except Exception:
        pass

    # 4. Final fallback
    return "default"

def get_profile_paths():
    profile_name = get_active_profile_name()
    profile_dir = PROFILES_DIR / profile_name
    
    # Fallback to default if profile directory doesn't exist
    if not profile_dir.is_dir():
        profile_dir = PROFILES_DIR / "default"
        # If default doesn't exist, fallback to template
        if not profile_dir.is_dir():
            profile_dir = PROFILES_DIR / "template"
        
    prompt_path = profile_dir / "prompt.txt"
    if not prompt_path.exists():
        prompt_path = BASE_DIR / "core" / "prompt.txt"
        
    api_keys_path = profile_dir / "api_keys.json"
    if not api_keys_path.exists():
        api_keys_path = BASE_DIR / "config" / "api_keys.json"
        
    return {
        "profile_name": profile_name,
        "profile_dir": profile_dir,
        "prompt_path": prompt_path,
        "api_keys_path": api_keys_path
    }

def load_api_keys() -> dict:
    paths = get_profile_paths()
    api_keys_path = paths["api_keys_path"]
    if not api_keys_path.exists():
        return {}
    try:
        with open(api_keys_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Failed to load api keys from {api_keys_path}: {e}")
        return {}

def get_system_prompt() -> str:
    paths = get_profile_paths()
    prompt_path = paths["prompt_path"]
    try:
        return prompt_path.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )
