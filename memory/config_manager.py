import json
import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


import core.profile_loader

BASE_DIR    = get_base_dir()

def get_active_config_file() -> Path:
    paths = core.profile_loader.get_profile_paths()
    return paths["api_keys_path"]

def ensure_config_dir() -> None:
    config_file = get_active_config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)


def config_exists() -> bool:
    return get_active_config_file().exists()


def save_api_keys(gemini_api_key: str) -> None:
    ensure_config_dir()
    config_file = get_active_config_file()

    data: dict = {}
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    data["gemini_api_key"] = gemini_api_key.strip()

    config_file.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )


def load_api_keys() -> dict:
    return core.profile_loader.load_api_keys()


def get_gemini_key() -> str | None:
    return load_api_keys().get("gemini_api_key")


def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)