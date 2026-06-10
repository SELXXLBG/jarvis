"""
memory_manager.py — MARK XXV Hafıza Sistemi
============================================
Düzeltmeler:
  - _MEMORY_EVERY_N_TURNS: 3 → 1 (her turda kontrol)
  - Stage 1 YES/NO check daha geniş kriterlere sahip
  - Extraction prompt daha kapsamlı ve agresif
  - Projeleri, favori şeyleri, arkadaşları daha iyi yakalar
"""

import json
import re
import time
from datetime import datetime
from threading import Lock
from pathlib import Path
import sys

# Cooldown mémoire : si quota 429 atteint, pause pendant MEMORY_COOLDOWN_S
MEMORY_COOLDOWN_S  = 3600   # 1 heure
_memory_quota_until: float = 0.0   # timestamp Unix jusqu'auquel on ne réessaie pas
_cooldown_logged:    bool  = False  # évite le spam console


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
MEMORY_PATH      = BASE_DIR / "memory" / "long_term.json"
_lock            = Lock()
MAX_VALUE_LENGTH = 400

_vector_mem = None

def get_vector_memory():
    global _vector_mem
    if _vector_mem is None:
        try:
            from memory.vector_memory import VectorMemory
            _vector_mem = VectorMemory()
        except Exception as e:
            print(f"[Memory] ⚠️ Failed to initialize VectorMemory: {e}")
    return _vector_mem


def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":         {}
    }


def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return _empty_memory()

    with _lock:
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _empty_memory()
                for key in base:
                    if key not in data:
                        data[key] = {}
                return data
            return _empty_memory()
        except Exception as e:
            print(f"[Memory] ⚠️ Load error: {e}")
            return _empty_memory()


def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        return
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        MEMORY_PATH.write_text(
            json.dumps(memory, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue

        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            if isinstance(value, dict) and "value" in value:
                new_val = _truncate_value(str(value["value"]))
            else:
                new_val = _truncate_value(str(value))

            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True

    return changed


def update_memory(memory_update: dict) -> dict:
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()

    memory = load_memory()
    if _recursive_update(memory, memory_update):
        save_memory(memory)
        print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")
        
        # Sync to Vector Memory
        vm = get_vector_memory()
        if vm and vm.available:
            for category, entries in memory_update.items():
                if isinstance(entries, dict):
                    for key, entry in entries.items():
                        if isinstance(entry, dict) and "value" in entry:
                            val = str(entry["value"])
                        else:
                            val = str(entry)
                        if val.strip():
                            vm.store_fact(category, key, val)
    return memory


# Mots-clés et patterns qui indiquent qu'un tour mérite d'être analysé pour la mémoire
# Détection LOCALE — zéro appel API, zéro token consommé
_MEMORY_KEYWORDS = [
    # Identité & Traits
    "je m'appelle", "mon prénom", "je suis", "j'ai", "mon âge", "ans",
    "j'habite", "ma ville", "mon pays", "mon travail", "mon métier",
    "i am", "my name", "i'm", "i live", "my job", "i work",
    "je me sens", "je suis fatigué", "je suis content", "je suis triste",
    # Préférences & Dégoûts
    "j'aime", "je préfère", "mon favori", "ma favorite", "je déteste",
    "i like", "i love", "i hate", "my favorite", "i prefer",
    "ne supporte pas", "me plaît", "adoré", "passion",
    # Projets & Études
    "je travaille sur", "je construis", "je développe", "mon projet",
    "je code", "je crée", "i'm building", "i'm working on", "my project",
    "j'apprends", "mes études", "mon examen", "mes révisions",
    # Relations & Personnes
    "mon ami", "ma famille", "mon frère", "ma sœur", "mes parents",
    "mon chef", "mon patron", "mon collègue", "ma copine", "mon copain",
    "my friend", "my family", "my brother", "my sister",
    # Plans & Souhaits
    "je veux", "je voudrais", "je vais", "mon rêve", "mon plan",
    "j'aimerais bien", "je compte faire", "mon objectif",
    "i want", "i would like", "i plan", "my goal", "my dream",
    # Habitudes & Notes
    "d'habitude", "tous les jours", "chaque matin", "souvent",
    "n'oublie pas", "rappelle-toi", "mémorise", "garde en note"
]

# Patterns Regex pour une détection locale plus fine (0 token)
_MEMORY_PATTERNS = [
    r"je (suis|m'appelle|habite|travaille|vais|veux|cherche|aime|déteste|préfère)",
    r"mon (nom|prénom|âge|job|travail|projet|ami|frère|sœur|père|mère|chien|chat|favori)",
    r"(ma|mes) (ville|passion|études|vacances|amis|préférences)",
    r"(i|i'm) (am|living|working|building|developing|want|like|love|hate)",
    r"(my|mine) (name|job|work|project|friend|family|favorite|dream|goal)",
    r"rappelle-toi que", "garde en mémoire", "mémorise"
]

def should_extract_memory(user_text: str, jarvis_text: str, api_key: str) -> bool:
    """
    Stage 1 : Détection LOCALE par mots-clés et Regex.
    ZERO appel API, ZERO token consommé.
    """
    combined = (user_text + " " + jarvis_text).lower()
    
    # Check simple keywords
    for kw in _MEMORY_KEYWORDS:
        if kw in combined:
            return True
            
    # Check regex patterns
    import re
    for pattern in _MEMORY_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return True
            
    return False


def _extract_memory_local(combined: str) -> dict:
    """Attempts to extract memory using a local LLM via Ollama API."""
    import urllib.request
    import json

    # Try several popular small models
    models = ["llama3", "mistral", "phi3", "gemma"]
    
    prompt = (
        "Extract memorable facts from this conversation. Return ONLY valid JSON.\n"
        'Format: {"category": {"key": {"value": "text"}}}\n'
        f"Conversation:\n{combined}\n\nJSON:"
    )

    for model in models:
        try:
            url = "http://localhost:11434/api/generate"
            data = json.dumps({
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json"
            }).encode("utf-8")
            
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            
            with urllib.request.urlopen(req, timeout=10) as response:
                res = json.loads(response.read().decode("utf-8"))
                raw = res.get("response", "{}")
                return json.loads(raw)
        except:
            continue
            
    return {}


def extract_memory(user_text: str, jarvis_text: str, api_key: str) -> dict:
    """
    Stage 2 : Extraction détaillée.
    Tente d'abord une extraction LOCALE via Ollama pour économiser les tokens.
    Fallback sur Google Gemini si Ollama n'est pas dispo ou échoue.
    """
    global _memory_quota_until, _cooldown_logged

    combined = f"User: {user_text[:500]}\nJarvis: {jarvis_text[:300]}"

    # --- TENTATIVE LOCALE (Ollama) ---
    try:
        local_data = _extract_memory_local(combined)
        if local_data and local_data != {}:
            print(f"[Memory] 🏠 Extraction LOCALE réussie (Ollama)")
            return local_data
    except Exception:
        pass

    # --- FALLBACK CLOUD (Gemini) ---
    if time.time() < _memory_quota_until:
        return {}

    try:
        from google import genai

        client  = genai.Client(api_key=api_key)
        
        prompt = (
            "You are JARVIS's memory extraction module. Extract EVERY memorable detail about the user from this conversation. "
            "Think like a loyal sidekick who wants to know everything about their master to serve them better.\n\n"
            "Include:\n"
            "  - Explicit facts (name, age, job).\n"
            "  - Implicit preferences (topics they enjoy, their mood, how they like to be addressed).\n"
            "  - Ongoing tasks or projects they mention.\n"
            "  - People they talk about and their relationship to them.\n"
            "  - Habits, routines, or schedules hinted at.\n"
            "  - Specific tools or websites they frequently use.\n\n"
            "Category guide:\n"
            "  identity      → name, age, birthday, city, country, job, school, nationality, language, mood/personality traits\n"
            "  preferences   → favorites, dislikes, style, UI preferences, conversational tone preferred\n"
            "  projects      → projects being built, coding tasks, gaming goals, learning objectives\n"
            "  relationships → friends, family, partner, colleagues, pets, even enemies/rivals\n"
            "  wishes        → travel, purchases, career goals, bucket list, immediate needs\n"
            "  notes         → routines, habits, passwords (if hinted), specific URLs, any other context\n\n"
            "Return ONLY valid JSON. Use {} if truly nothing is worth saving.\n"
            "Use concise English values regardless of conversation language.\n\n"
            'Format: {"identity":{"traits":{"value":"curious"}}, "preferences":{"theme":{"value":"dark mode"}}}\n\n'
            f"Conversation:\n{combined}\n\nJSON:"
        )

        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=prompt,
        )
        raw = response.text.strip()

        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        if not raw or raw == "{}":
            return {}

        return json.loads(raw)

    except json.JSONDecodeError:
        return {}
    except Exception as e:
        err = str(e)
        if "429" in err:
            _memory_quota_until = time.time() + MEMORY_COOLDOWN_S
            _cooldown_logged    = False
            print(f"[Memory] 💤 Quota dépassé — extraction suspendue 1h")
        else:
            print(f"[Memory] ⚠️ Extract failed: {e}")
        return {}


def format_memory_for_prompt(memory: dict | None) -> str:
    if not memory:
        return ""

    lines = []

    identity  = memory.get("identity", {})
    id_fields = ["name", "age", "birthday", "city", "job", "language", "school", "nationality"]
    for field in id_fields:
        entry = identity.get(field)
        if entry:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in id_fields:
            continue
        val = entry.get("value") if isinstance(entry, dict) else entry
        if val:
            lines.append(f"{key.replace('_', ' ').title()}: {val}")

    prefs = memory.get("preferences", {})
    if prefs:
        lines.append("")
        lines.append("Preferences:")
        for key, entry in list(prefs.items())[:15]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    projects = memory.get("projects", {})
    if projects:
        lines.append("")
        lines.append("Active Projects / Goals:")
        for key, entry in list(projects.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    rels = memory.get("relationships", {})
    if rels:
        lines.append("")
        lines.append("People in their life:")
        for key, entry in list(rels.items())[:10]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    wishes = memory.get("wishes", {})
    if wishes:
        lines.append("")
        lines.append("Wishes / Plans / Wants:")
        for key, entry in list(wishes.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key.replace('_', ' ').title()}: {val}")

    notes = memory.get("notes", {})
    if notes:
        lines.append("")
        lines.append("Other notes:")
        for key, entry in list(notes.items())[:8]:
            val = entry.get("value") if isinstance(entry, dict) else entry
            if val:
                lines.append(f"  - {key}: {val}")

    if not lines:
        return ""

    header = "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]\n"
    result = header + "\n".join(lines)
    if len(result) > 2000:
        result = result[:1997] + "…"

    return result + "\n"


def remember(key: str, value: str, category: str = "notes") -> str:
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    memory = load_memory()
    cat    = memory.get(category, {})
    if key in cat:
        del cat[key]
        memory[category] = cat
        save_memory(memory)
        
        # Delete from Vector Memory
        vm = get_vector_memory()
        if vm and vm.available:
            vm.delete_fact(category, key)
            
        return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"

# Alias — eski import'larla uyumluluk için
forget_memory = forget