"""v2 dashboard: lesson plans and found student work, served at /v2/.

Runs inside the existing Cloud Run service behind the same Cloud IAP sign-in,
and uses the same settings and service account as v1. It reads the Agenda, the
roster workbook, Form responses and Drive file details. Its one write is the
Tidy button, which moves a previewed set of files and folders into a week's one
📥 folder when the teacher confirms.
"""
import json
import os
import threading
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

from . import agenda as agenda_module
from . import google, tidy, work

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
          "style.css": "text/css; charset=utf-8"}
SECURITY_HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
     "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
     "form-action 'none'; frame-ancestors 'none'"),
]
CACHE_SECONDS = 60
_cache = {"agenda": None, "at": 0.0}
_cache_lock = threading.Lock()


def load_agenda(token, config, fresh=False):
    """The parsed Agenda, re-read at most once a minute unless asked."""
    spreadsheet_id = config.get("HUB_AGENDA_SPREADSHEET_ID", "")
    if not spreadsheet_id:
        raise google.GoogleError("No Agenda spreadsheet is configured (HUB_AGENDA_SPREADSHEET_ID).")
    with _cache_lock:
        cached = _cache["agenda"]
        if (not fresh and cached is not None and cached.spreadsheet_id == spreadsheet_id
                and time.time() - _cache["at"] < CACHE_SECONDS):
            return cached
    try:
        loaded = agenda_module.load(token, spreadsheet_id)
    except google.GoogleError as error:
        if error.status in (403, 404):
            raise google.GoogleError(
                "The dashboard can't open the Agenda spreadsheet. Share it with %s (Viewer)."
                % google.service_account(config), error.status) from None
        raise google.GoogleError("The Agenda couldn't be read: %s" % error, error.status) from None
    with _cache_lock:
        _cache["agenda"], _cache["at"] = loaded, time.time()
    return loaded


def _one(query, name, required=True):
    values = query.get(name, [])
    if len(values) > 1 or (required and not values):
        raise ValueError(name)
    return values[0] if values else ""


def _class_key(value):
    if value not in dict(agenda_module.CLASSES):
        raise ValueError("class")
    return value


def api(sub, query, config):
    token = google.service_token(config)
    fresh = _one(query, "fresh", False) == "1"
    agenda = load_agenda(token, config, fresh)
    sheet = {"title": agenda.title,
             "url": "https://docs.google.com/spreadsheets/d/%s/edit" % agenda.spreadsheet_id}
    if sub == "api/day":
        day = date.fromisoformat(_one(query, "date"))
        return {**agenda.day(day), "agenda": sheet}
    if sub == "api/weeks":
        class_key = _class_key(_one(query, "class"))
        today = _one(query, "today", False)
        current = agenda.week_for(date.fromisoformat(today)) if today else None
        return {"class_key": class_key, "weeks": agenda.week_options(class_key),
                "current": current["label"] if current else None,
                "sections": list(work.roster.SECTIONS[class_key]), "agenda": sheet}
    if sub == "api/tidy":
        class_key = _class_key(_one(query, "class"))
        week = _one(query, "week")
        if not 1 <= len(week) <= 100:
            raise ValueError("week")
        return tidy.plan(token, config, agenda, class_key, week)
    if sub == "api/work":
        class_key = _class_key(_one(query, "class"))
        week = _one(query, "week")
        if not 1 <= len(week) <= 100:
            raise ValueError("week")
        return {**work.lesson_work(token, config, agenda, class_key, week), "agenda": sheet}
    return None


def application(environ, start_response, config=None):
    config = os.environ if config is None else config
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")

    def respond(status, body, content_type="application/json; charset=utf-8", extra=()):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode("utf-8")
        start_response(status, SECURITY_HEADERS + [("Content-Type", content_type),
                                                   ("Content-Length", str(len(body)))] + list(extra))
        return [b"" if method == "HEAD" else body]

    if method == "POST" and path == "/v2/api/tidy":
        return tidy_post(environ, respond, config)
    if method not in ("GET", "HEAD"):
        return respond("405 Method Not Allowed", {"error": "This page only reads."},
                       extra=[("Allow", "GET, HEAD")])
    if path == "/v2":
        return respond("302 Found", b"", "text/plain", [("Location", "/v2/")])
    sub = path[len("/v2/"):] if path.startswith("/v2/") else None
    if sub in ("", "index.html", "app.js", "style.css"):
        name = sub or "index.html"
        try:
            return respond("200 OK", (STATIC / name).read_bytes(), ASSETS[name])
        except OSError:
            return respond("503 Service Unavailable", {"error": "Page files are missing."})
    if sub is None or not sub.startswith("api/"):
        return respond("404 Not Found", {"error": "Not found."})
    try:
        google.verify_teacher(environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION"), config)
    except google.SignInRequired:
        return respond("401 Unauthorized", {"error": "Please sign in again: reload this page."})
    try:
        query = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=10)
        body = api(sub, query, config)
    except ValueError:
        return respond("400 Bad Request", {"error": "That request wasn't understood."})
    except google.GoogleError as error:
        return respond("502 Bad Gateway", {"error": str(error)})
    except Exception:
        return respond("500 Internal Server Error",
                       {"error": "Something went wrong on the server. Try Reload."})
    if body is None:
        return respond("404 Not Found", {"error": "Not found."})
    return respond("200 OK", body)


def tidy_post(environ, respond, config):
    """Move a previewed Tidy plan. Signed-in teacher, same-origin JSON only."""
    try:
        google.verify_teacher(environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION"), config)
    except google.SignInRequired:
        return respond("401 Unauthorized", {"error": "Please sign in again: reload this page."})
    origins = {v.strip() for v in config.get("HUB_STATE_PROBE_ORIGINS", "").split(",")
               if v.strip().startswith("https://")}
    if (environ.get("HTTP_ORIGIN") not in origins
            or environ.get("CONTENT_TYPE", "").split(";")[0].strip() != "application/json"):
        return respond("403 Forbidden", {"error": "Tidy can only be run from the dashboard page."})
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if not 1 <= length <= 2000:
            raise ValueError()
        payload = json.loads(environ["wsgi.input"].read(length))
        class_key = _class_key(payload.get("class"))
        week, plan_id = str(payload.get("week", "")), str(payload.get("plan_id", ""))
        if not 1 <= len(week) <= 100 or not 1 <= len(plan_id) <= 64:
            raise ValueError()
        token = google.service_token(config)
        write_token = google.service_token(config, write=True)
        agenda = load_agenda(token, config)
        return respond("200 OK", tidy.apply(token, write_token, config, agenda, class_key, week, plan_id))
    except tidy.Changed:
        return respond("409 Conflict", {"error": "Something changed since the preview. Review it again."})
    except (ValueError, KeyError, TypeError, AttributeError):
        return respond("400 Bad Request", {"error": "That request wasn't understood."})
    except google.GoogleError as error:
        return respond("502 Bad Gateway", {"error": str(error)})
    except Exception:
        return respond("500 Internal Server Error", {"error": "Something went wrong on the server. Nothing more was moved."})
