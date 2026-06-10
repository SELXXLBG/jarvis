# memory/habits_tracker.py — JARVIS Habit & Context Tracker
# Tracks usage patterns, active projects context, and generates proactive suggestions.

import json
import time
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from threading import Lock
import sys


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR      = get_base_dir()
HABITS_PATH   = BASE_DIR / "memory" / "habits.json"
CONTEXT_PATH  = BASE_DIR / "memory" / "session_context.json"
_lock         = Lock()

# ── Persistence ────────────────────────────────────────────────────────────────

def _load_json(path: Path, default: dict) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Habit Tracking ─────────────────────────────────────────────────────────────

def _default_habits() -> dict:
    return {
        "tool_usage":     {},   # {tool_name: count}
        "hourly_usage":   {},   # {"HH": count}
        "daily_usage":    {},   # {"YYYY-MM-DD": count}
        "topics":         {},   # {topic_keyword: count}
        "first_seen":     str(date.today()),
        "total_turns":    0,
    }


def record_interaction(tool_name: str | None, user_text: str) -> None:
    habits = _load_json(HABITS_PATH, _default_habits())
    hour   = datetime.now().strftime("%H")
    today  = str(date.today())

    habits["total_turns"] = habits.get("total_turns", 0) + 1

    # Outil utilisé
    if tool_name:
        habits["tool_usage"][tool_name] = habits["tool_usage"].get(tool_name, 0) + 1

    # Heure d'activité
    habits["hourly_usage"][hour] = habits["hourly_usage"].get(hour, 0) + 1

    # Activité journalière
    habits["daily_usage"][today] = habits["daily_usage"].get(today, 0) + 1

    # Sujets clés détectés
    topic_keywords = {
        "code": ["code", "python", "debug", "erreur", "function", "script", "build"],
        "music": ["music", "spotify", "chanson", "playlist", "joue"],
        "web": ["search", "cherche", "trouve", "web", "site", "article"],
        "system": ["volume", "luminosité", "brightness", "ouvre", "ferme", "lance"],
        "files": ["fichier", "file", "dossier", "folder", "copie", "move"],
        "games": ["steam", "epic", "jeu", "game", "install"],
        "email": ["email", "mail", "envoie", "réponds", "inbox"],
        "calendar": ["réunion", "meeting", "agenda", "rendez-vous", "event"],
    }
    text_lower = user_text.lower()
    for topic, kws in topic_keywords.items():
        if any(kw in text_lower for kw in kws):
            habits["topics"][topic] = habits["topics"].get(topic, 0) + 1

    _save_json(HABITS_PATH, habits)


def get_habits_summary() -> str:
    habits = _load_json(HABITS_PATH, _default_habits())

    tool_usage  = habits.get("tool_usage", {})
    hourly      = habits.get("hourly_usage", {})
    topics      = habits.get("topics", {})
    total_turns = habits.get("total_turns", 0)

    if total_turns == 0:
        return ""

    lines = [f"[USAGE PATTERNS — {total_turns} total interactions since {habits.get('first_seen','?')}]"]

    # Top outils
    if tool_usage:
        top_tools = sorted(tool_usage.items(), key=lambda x: x[1], reverse=True)[:5]
        lines.append("Most used tools: " + ", ".join(f"{t}({n})" for t, n in top_tools))

    # Heure de pic
    if hourly:
        peak_hour = max(hourly, key=hourly.get)
        lines.append(f"Peak activity hour: {peak_hour}:00")

    # Sujets fréquents
    if topics:
        top_topics = sorted(topics.items(), key=lambda x: x[1], reverse=True)[:3]
        lines.append("Frequent topics: " + ", ".join(t for t, _ in top_topics))

    return "\n".join(lines)


# ── Session Context (multi-sessions) ──────────────────────────────────────────

def _default_context() -> dict:
    return {
        "active_projects": {},  # {name: {description, last_updated, status}}
        "pending_tasks":   [],  # [{task, created, priority}]
        "last_session":    None,
        "session_count":   0,
    }


def start_session() -> str:
    ctx = _load_json(CONTEXT_PATH, _default_context())
    last = ctx.get("last_session")
    ctx["last_session"]  = datetime.now().isoformat()
    ctx["session_count"] = ctx.get("session_count", 0) + 1
    _save_json(CONTEXT_PATH, ctx)

    if not last:
        return ""

    # Résumé de contexte pour le prompt
    parts = []
    projects = ctx.get("active_projects", {})
    if projects:
        proj_lines = [f"  - {n}: {v.get('description','')}" for n, v in list(projects.items())[:4]]
        parts.append("Active projects:\n" + "\n".join(proj_lines))

    pending = ctx.get("pending_tasks", [])
    if pending:
        task_lines = [f"  - {t['task']}" for t in pending[:3]]
        parts.append("Pending tasks:\n" + "\n".join(task_lines))

    if not parts:
        return ""

    last_dt = datetime.fromisoformat(last)
    delta   = datetime.now() - last_dt
    hours   = int(delta.total_seconds() / 3600)
    time_str = f"{hours}h ago" if hours < 24 else f"{delta.days}d ago"

    header = f"[RESUMING SESSION — last seen {time_str}, session #{ctx['session_count']}]"
    return header + "\n" + "\n".join(parts)


def update_project(name: str, description: str, status: str = "active") -> None:
    ctx = _load_json(CONTEXT_PATH, _default_context())
    ctx.setdefault("active_projects", {})[name] = {
        "description":  description,
        "last_updated": str(date.today()),
        "status":       status,
    }
    _save_json(CONTEXT_PATH, ctx)


def add_pending_task(task: str, priority: str = "normal") -> None:
    ctx = _load_json(CONTEXT_PATH, _default_context())
    pending = ctx.setdefault("pending_tasks", [])
    # Évite les doublons
    if not any(t["task"].lower() == task.lower() for t in pending):
        pending.append({"task": task, "created": str(date.today()), "priority": priority})
        pending.sort(key=lambda x: {"high": 0, "normal": 1, "low": 2}.get(x["priority"], 1))
        pending = pending[:10]  # max 10 tâches
        ctx["pending_tasks"] = pending
        _save_json(CONTEXT_PATH, ctx)


def complete_task(keyword: str) -> bool:
    ctx = _load_json(CONTEXT_PATH, _default_context())
    before = len(ctx.get("pending_tasks", []))
    ctx["pending_tasks"] = [
        t for t in ctx.get("pending_tasks", [])
        if keyword.lower() not in t["task"].lower()
    ]
    if len(ctx["pending_tasks"]) < before:
        _save_json(CONTEXT_PATH, ctx)
        return True
    return False


def get_context_for_prompt() -> str:
    session_ctx = start_session()
    habits_ctx  = get_habits_summary()
    parts = [p for p in [session_ctx, habits_ctx] if p]
    return "\n\n".join(parts)


# ── Proactive Suggestions ─────────────────────────────────────────────────────

def get_proactive_suggestions() -> list[str]:
    habits = _load_json(HABITS_PATH, _default_habits())
    ctx    = _load_json(CONTEXT_PATH, _default_context())
    now    = datetime.now()
    suggestions = []

    # Suggestion basée sur l'heure de pic
    hourly = habits.get("hourly_usage", {})
    if hourly:
        peak = max(hourly, key=hourly.get)
        if abs(int(peak) - now.hour) <= 1:
            suggestions.append(f"You're usually most active around {peak}:00 — shall I prepare anything?")

    # Tâches en attente depuis plus de 2 jours
    for task in ctx.get("pending_tasks", [])[:2]:
        created = task.get("created", str(date.today()))
        try:
            delta = (date.today() - date.fromisoformat(created)).days
            if delta >= 2:
                suggestions.append(f"Reminder: '{task['task']}' has been pending for {delta} day(s).")
        except Exception:
            pass

    # Projet non touché depuis longtemps
    for name, proj in list(ctx.get("active_projects", {}).items())[:2]:
        last_upd = proj.get("last_updated", str(date.today()))
        try:
            delta = (date.today() - date.fromisoformat(last_upd)).days
            if delta >= 3:
                suggestions.append(f"Project '{name}' hasn't been touched in {delta} days. Continue?")
        except Exception:
            pass

    return suggestions[:3]


# ── Daily Briefing ────────────────────────────────────────────────────────────

def build_daily_briefing_prompt(memory: dict) -> str:
    habits  = _load_json(HABITS_PATH, _default_habits())
    ctx     = _load_json(CONTEXT_PATH, _default_context())
    today   = datetime.now().strftime("%A %d %B %Y")
    hour    = datetime.now().hour

    greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")

    lines = [
        f"{greeting}, sir. Today is {today}.",
        "",
        "Here's your briefing:",
    ]

    # Projets actifs
    projects = ctx.get("active_projects", {})
    if projects:
        lines.append("\nActive projects:")
        for name, proj in list(projects.items())[:3]:
            lines.append(f"  • {name}: {proj.get('description', '')}")

    # Tâches en attente
    pending = ctx.get("pending_tasks", [])
    if pending:
        lines.append("\nPending tasks:")
        for t in pending[:3]:
            lines.append(f"  • {t['task']}")

    # Stats d'utilisation
    today_turns = habits.get("daily_usage", {}).get(str(date.today()), 0)
    total_turns = habits.get("total_turns", 0)
    if total_turns > 0:
        lines.append(f"\nToday: {today_turns} interactions. Total: {total_turns}.")

    # Suggestions proactives
    suggestions = get_proactive_suggestions()
    if suggestions:
        lines.append("\nProactive notes:")
        for s in suggestions:
            lines.append(f"  • {s}")

    return "\n".join(lines)
