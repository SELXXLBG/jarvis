# actions/calendar_control.py — JARVIS Calendar Control
# Supports: Outlook (win32com) + Google Calendar (API)

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_calendar_cfg() -> dict:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("calendar", {})
    except Exception:
        return {}


def _parse_dt(s: str) -> datetime:
    """Parse flexible date/time string."""
    s = s.strip()
    now = datetime.now()
    if s.lower() in ("today", "aujourd'hui"):
        return now.replace(hour=9, minute=0, second=0, microsecond=0)
    if s.lower() in ("tomorrow", "demain"):
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M",
                "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return now + timedelta(hours=1)


# ── Outlook Calendar ───────────────────────────────────────────────────────────

def _outlook_list(days: int = 7) -> str:
    try:
        import win32com.client
        from pywintypes import TimeType  # noqa
        outlook  = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        calendar = outlook.GetDefaultFolder(9)  # 9 = olFolderCalendar
        items    = calendar.Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")

        start = datetime.now()
        end   = start + timedelta(days=days)
        items.Restrict(
            f"[Start] >= '{start.strftime('%m/%d/%Y')}' AND [Start] <= '{end.strftime('%m/%d/%Y')}'"
        )

        results = []
        for item in items:
            try:
                s = str(item.Start)[:16]
                e = str(item.End)[:16]
                results.append(f"• {s} → {e}  |  {item.Subject}  |  {item.Location or ''}")
            except Exception:
                continue
            if len(results) >= 20:
                break

        if not results:
            return f"No events in the next {days} days."
        return f"📅 Next {days} days ({len(results)} events):\n" + "\n".join(results)
    except ImportError:
        return "Outlook not available."
    except Exception as e:
        return f"Outlook calendar error: {e}"


def _outlook_create(subject: str, start_str: str, end_str: str = "",
                    location: str = "", body: str = "", reminder_min: int = 15) -> str:
    try:
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application")
        appt = outlook.CreateItem(1)  # 1 = olAppointmentItem

        start_dt = _parse_dt(start_str)
        if end_str:
            end_dt = _parse_dt(end_str)
        else:
            end_dt = start_dt + timedelta(hours=1)

        appt.Subject          = subject
        appt.Start            = start_dt.strftime("%m/%d/%Y %H:%M")
        appt.End              = end_dt.strftime("%m/%d/%Y %H:%M")
        appt.Location         = location
        appt.Body             = body
        appt.ReminderMinutesBeforeStart = reminder_min
        appt.Save()

        return (f"✅ Event created: '{subject}'\n"
                f"   Start: {start_dt.strftime('%A %d %b %Y at %H:%M')}\n"
                f"   End:   {end_dt.strftime('%H:%M')}"
                + (f"\n   Location: {location}" if location else ""))
    except ImportError:
        return "Outlook not available."
    except Exception as e:
        return f"Outlook create error: {e}"


def _outlook_delete(keyword: str) -> str:
    try:
        import win32com.client
        outlook  = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        calendar = outlook.GetDefaultFolder(9)
        items    = calendar.Items
        items.Sort("[Start]")
        now = datetime.now()
        items.Restrict(f"[Start] >= '{now.strftime('%m/%d/%Y')}'")

        deleted = []
        for item in items:
            try:
                if keyword.lower() in item.Subject.lower():
                    deleted.append(item.Subject)
                    item.Delete()
            except Exception:
                continue

        if not deleted:
            return f"No upcoming event found matching '{keyword}'."
        return f"🗑️ Deleted {len(deleted)} event(s): " + ", ".join(deleted)
    except Exception as e:
        return f"Outlook delete error: {e}"


# ── Google Calendar ────────────────────────────────────────────────────────────

def _gcal_service():
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        SCOPES    = ["https://www.googleapis.com/auth/calendar"]
        TOKEN_PATH = BASE_DIR / "config" / "gcal_token.json"
        CREDS_PATH = BASE_DIR / "config" / "gcal_credentials.json"

        creds = None
        if TOKEN_PATH.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif CREDS_PATH.exists():
                flow  = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                return None
            TOKEN_PATH.write_text(creds.to_json())

        return build("calendar", "v3", credentials=creds)
    except Exception:
        return None


def _gcal_list(days: int = 7) -> str:
    svc = _gcal_service()
    if not svc:
        return "Google Calendar not configured. Add gcal_credentials.json to config/."

    try:
        now    = datetime.utcnow().isoformat() + "Z"
        end_dt = (datetime.utcnow() + timedelta(days=days)).isoformat() + "Z"
        result = svc.events().list(
            calendarId="primary", timeMin=now, timeMax=end_dt,
            maxResults=20, singleEvents=True, orderBy="startTime"
        ).execute()
        events = result.get("items", [])
        if not events:
            return f"No events in the next {days} days."

        lines = []
        for ev in events:
            start = ev["start"].get("dateTime", ev["start"].get("date", ""))[:16]
            end   = ev["end"].get("dateTime", ev["end"].get("date", ""))[:16]
            lines.append(f"• {start} → {end}  |  {ev.get('summary','(no title)')}")
        return f"📅 Google Calendar — next {days} days:\n" + "\n".join(lines)
    except Exception as e:
        return f"Google Calendar list error: {e}"


def _gcal_create(subject: str, start_str: str, end_str: str = "",
                 location: str = "", description: str = "") -> str:
    svc = _gcal_service()
    if not svc:
        return "Google Calendar not configured."

    try:
        start_dt = _parse_dt(start_str)
        end_dt   = _parse_dt(end_str) if end_str else start_dt + timedelta(hours=1)

        event = {
            "summary":     subject,
            "location":    location,
            "description": description,
            "start":       {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:00"),
                            "timeZone": "Europe/Paris"},
            "end":         {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:00"),
                            "timeZone": "Europe/Paris"},
            "reminders":   {"useDefault": False,
                            "overrides": [{"method": "popup", "minutes": 15}]},
        }
        created = svc.events().insert(calendarId="primary", body=event).execute()
        return (f"✅ Google Calendar event created: '{subject}'\n"
                f"   {start_dt.strftime('%A %d %b %Y at %H:%M')} → {end_dt.strftime('%H:%M')}")
    except Exception as e:
        return f"Google Calendar create error: {e}"


# ── Dispatcher principal ───────────────────────────────────────────────────────

def calendar_control(parameters: dict, player=None) -> str:
    action   = parameters.get("action", "list")
    provider = parameters.get("provider", "auto").lower()
    subject  = parameters.get("subject", parameters.get("title", ""))
    start    = parameters.get("start", parameters.get("date", ""))
    end      = parameters.get("end", "")
    location = parameters.get("location", "")
    body     = parameters.get("body", parameters.get("description", ""))
    days     = int(parameters.get("days", 7))
    keyword  = parameters.get("keyword", subject)

    cfg = _get_calendar_cfg()
    if provider == "auto":
        provider = cfg.get("provider", "outlook")

    if provider == "google":
        if action in ("list", "agenda", "today", "week"):
            return _gcal_list(days)
        elif action in ("create", "add", "schedule"):
            return _gcal_create(subject, start, end, location, body)
        else:
            return f"Unknown Google Calendar action '{action}'. Use: list, create."
    else:  # outlook (default)
        if action in ("list", "agenda", "today", "week"):
            return _outlook_list(days)
        elif action in ("create", "add", "schedule"):
            return _outlook_create(subject, start, end, location, body)
        elif action in ("delete", "cancel", "remove"):
            return _outlook_delete(keyword)
        else:
            return f"Unknown action '{action}'. Use: list, create, delete."
