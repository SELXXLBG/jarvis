# actions/email_control.py — JARVIS Email Control
# Supports: Gmail (IMAP/SMTP) + Outlook (win32com)

import imaplib
import smtplib
import email
import json
import re
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from pathlib import Path
from datetime import datetime


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_email_config() -> dict:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("email", {})
    except Exception:
        return {}


def _decode_str(s) -> str:
    if s is None:
        return ""
    parts = decode_header(s)
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return " ".join(result)


def _get_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    pass
    else:
        try:
            return msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )
        except Exception:
            pass
    return ""


# ── Gmail via IMAP/SMTP ────────────────────────────────────────────────────────

def _gmail_read(cfg: dict, count: int = 5, folder: str = "INBOX", unread_only: bool = True) -> str:
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(cfg["address"], cfg["app_password"])
        imap.select(folder)

        criteria = "(UNSEEN)" if unread_only else "ALL"
        _, data = imap.search(None, criteria)
        ids = data[0].split()
        if not ids:
            return "No emails found."

        ids = ids[-count:][::-1]  # les plus récents en premier
        results = []
        for uid in ids:
            _, msg_data = imap.fetch(uid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode_str(msg.get("Subject", "(no subject)"))
            sender  = _decode_str(msg.get("From", "unknown"))
            date    = msg.get("Date", "")[:16]
            body    = _get_body(msg)[:200].replace("\n", " ").strip()
            results.append(f"• [{date}] From: {sender}\n  Subject: {subject}\n  Preview: {body}")

        imap.logout()
        return f"📧 {len(results)} email(s):\n\n" + "\n\n".join(results)

    except Exception as e:
        return f"Gmail read error: {e}"


def _gmail_send(cfg: dict, to: str, subject: str, body: str) -> str:
    try:
        msg = MIMEMultipart()
        msg["From"]    = cfg["address"]
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(cfg["address"], cfg["app_password"])
            server.sendmail(cfg["address"], to, msg.as_string())

        return f"✅ Email sent to {to} — Subject: {subject}"
    except Exception as e:
        return f"Gmail send error: {e}"


def _gmail_search(cfg: dict, query: str, count: int = 5) -> str:
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(cfg["address"], cfg["app_password"])
        imap.select("INBOX")

        _, data = imap.search(None, f'(SUBJECT "{query}")')
        ids = data[0].split()
        if not ids:
            _, data = imap.search(None, f'(TEXT "{query}")')
            ids = data[0].split()
        if not ids:
            return f"No emails found matching '{query}'."

        ids = ids[-count:][::-1]
        results = []
        for uid in ids:
            _, msg_data = imap.fetch(uid, "(RFC822)")
            msg     = email.message_from_bytes(msg_data[0][1])
            subject = _decode_str(msg.get("Subject", "(no subject)"))
            sender  = _decode_str(msg.get("From", "unknown"))
            date    = msg.get("Date", "")[:16]
            results.append(f"• [{date}] {sender} — {subject}")

        imap.logout()
        return f"🔍 Found {len(results)} email(s) for '{query}':\n" + "\n".join(results)
    except Exception as e:
        return f"Gmail search error: {e}"


# ── Outlook via win32com ───────────────────────────────────────────────────────

def _outlook_read(count: int = 5, unread_only: bool = True) -> str:
    try:
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        inbox   = outlook.GetDefaultFolder(6)  # 6 = olFolderInbox
        msgs    = inbox.Items
        msgs.Sort("[ReceivedTime]", True)

        results = []
        fetched = 0
        for msg in msgs:
            if fetched >= count:
                break
            try:
                if unread_only and msg.UnRead is False:
                    continue
                sender  = msg.SenderName
                subject = msg.Subject
                date    = str(msg.ReceivedTime)[:16]
                body    = (msg.Body or "")[:200].replace("\n", " ").strip()
                results.append(f"• [{date}] From: {sender}\n  Subject: {subject}\n  Preview: {body}")
                fetched += 1
            except Exception:
                continue

        if not results:
            return "No unread emails in Outlook inbox." if unread_only else "No emails found."
        return f"📧 {len(results)} Outlook email(s):\n\n" + "\n\n".join(results)
    except ImportError:
        return "Outlook not available (win32com not installed)."
    except Exception as e:
        return f"Outlook read error: {e}"


def _outlook_send(to: str, subject: str, body: str) -> str:
    try:
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail         = outlook.CreateItem(0)
        mail.To      = to
        mail.Subject = subject
        mail.Body    = body
        mail.Send()
        return f"✅ Outlook email sent to {to} — Subject: {subject}"
    except ImportError:
        return "Outlook not available (win32com not installed)."
    except Exception as e:
        return f"Outlook send error: {e}"


# ── Dispatcher principal ───────────────────────────────────────────────────────

def email_control(parameters: dict, player=None) -> str:
    action  = parameters.get("action", "read")
    provider = parameters.get("provider", "auto").lower()
    count   = int(parameters.get("count", 5))
    to      = parameters.get("to", "")
    subject = parameters.get("subject", "")
    body    = parameters.get("body", "")
    query   = parameters.get("query", "")
    unread_only = parameters.get("unread_only", True)

    cfg = _get_email_config()

    # Auto-detect provider
    if provider == "auto":
        if cfg.get("provider") == "gmail" or cfg.get("app_password"):
            provider = "gmail"
        else:
            provider = "outlook"

    if provider == "gmail":
        if not cfg.get("address") or not cfg.get("app_password"):
            return (
                "Gmail not configured. Add to config/api_keys.json:\n"
                '  "email": {"provider": "gmail", "address": "you@gmail.com", "app_password": "xxxx xxxx xxxx xxxx"}\n'
                "Generate an app password at myaccount.google.com/apppasswords"
            )
        if action in ("read", "inbox"):
            return _gmail_read(cfg, count, unread_only=bool(unread_only))
        elif action == "send":
            return _gmail_send(cfg, to, subject, body)
        elif action == "search":
            return _gmail_search(cfg, query, count)
        else:
            return f"Unknown action '{action}'. Use: read, send, search."

    else:  # outlook
        if action in ("read", "inbox"):
            return _outlook_read(count, unread_only=bool(unread_only))
        elif action == "send":
            return _outlook_send(to, subject, body)
        else:
            return f"Unknown action '{action}'. Use: read, send."
