"""v2 dashboard: lesson plans and found student work, served at /v2/.

Runs inside the existing Cloud Run service behind the same Cloud IAP sign-in,
and uses the same settings and service account as v1. It reads the Agenda, the
roster workbook, Form responses, Drive file details and (to count completed
boxes) the student decks themselves. Its one write is the Tidy button, which moves a previewed set of files and folders into a week's one
📥 folder when the teacher confirms.
"""
import json
import os
import re
import threading
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

from . import agenda as agenda_module
from concurrent.futures import ThreadPoolExecutor

from . import completion, feedback, google, ledger, tidy, work

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
WORK_CACHE_SECONDS = 600
_cache = {"agenda": None, "at": 0.0}
_cache_lock = threading.Lock()
_work_cache = {}


def lesson_work(token, config, agenda, class_key, week, fresh=True):
    """Student work for a lesson, kept for ten minutes so scoring can reuse
    the list the page is showing instead of re-reading every folder."""
    key = (class_key, week)
    with _cache_lock:
        cached = _work_cache.get(key)
    if not fresh and cached and time.time() - cached[0] < WORK_CACHE_SECONDS:
        return cached[1]
    result = work.lesson_work(token, config, agenda, class_key, week)
    with _cache_lock:
        if len(_work_cache) > 40:
            _work_cache.clear()
        _work_cache[key] = (time.time(), result)
    return result


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
        return {**lesson_work(token, config, agenda, class_key, week), "agenda": sheet}
    if sub == "api/score":
        class_key = _class_key(_one(query, "class"))
        week, form_id, file_id = _one(query, "week"), _one(query, "form"), _one(query, "file")
        if not 1 <= len(week) <= 100 or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", form_id + file_id):
            raise ValueError("score")
        found = lesson_work(token, config, agenda, class_key, week, fresh=False)
        return completion.score_file(token, agenda, class_key, week, form_id, file_id, found)
    if sub in ("api/ledger", "api/feedback"):
        class_key, week, form_id = _form_args(query)
        if sub == "api/ledger":
            return ledger.read(token, config, form_id)
        return feedback_list(token, config, agenda, class_key, week, form_id)
    return None


def _form_args(values):
    """(class key, week label, Form ID) from a query string or a JSON body."""
    def get(name):
        value = values.get(name)
        if isinstance(value, list):
            if len(value) != 1:
                raise ValueError(name)
            value = value[0]
        return value if isinstance(value, str) else ""
    class_key, week, form_id = _class_key(get("class")), get("week"), get("form")
    if not 1 <= len(week) <= 100 or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", form_id):
        raise ValueError("form")
    return class_key, week, form_id


def _form_context(token, config, agenda, class_key, week, form_id):
    """(context, form, found) for one Form on the page."""
    found = lesson_work(token, config, agenda, class_key, week, fresh=False)
    form = next((f for lesson in found["lessons"] for f in lesson["forms"] if f["form_id"] == form_id), None)
    if not form:
        raise ValueError("form")
    context = {"class": agenda.classes[class_key]["name"], "week": week, "assignment": form["label"],
               "form_id": form_id, "date": ledger.eastern_now()[:10],
               # Feedback is saved in the week's one 📥 turn-in folder, beside the work.
               "folder": next((f for f in found.get("folders", []) if f["name"].startswith("📥")), None)}
    return context, form, found


def _form_rows(token, config, agenda, class_key, week, form_id):
    """(context, [(row, score)]) for the roster-matched, on-time students of one Form."""
    context, form, found = _form_context(token, config, agenda, class_key, week, form_id)
    rows = [r for r in form["students"] if r["matched"] and r["files"]]

    def one(row):
        upload = next((f for f in row["files"] if f.get("mime") in (completion.SLIDES, google.PPTX)), row["files"][0])
        return completion.score_file(token, agenda, class_key, week, form_id, upload["id"], found)
    with ThreadPoolExecutor(max_workers=4) as pool:
        scores = list(pool.map(one, rows))
    return context, list(zip(rows, scores))


def feedback_list(token, config, agenda, class_key, week, form_id):
    """Students with revised answers who turned in since the last feedback for this assignment."""
    context, scored = _form_rows(token, config, agenda, class_key, week, form_id)
    sheet = ledger.read(token, config, form_id)
    last = work.when(sheet["feedback_at"]["at"]) if sheet.get("feedback_at") else None
    students, done_before, no_revision = [], 0, 0
    for row, score in scored:
        if score.get("problem"):
            continue
        if not score["counts"]["DRAFT"]["done"]:
            no_revision += 1
            continue
        turned_in = work.when(row["submitted_at"])
        if last and turned_in and turned_in <= last:
            done_before += 1
            continue
        latest = next(f for f in row["files"] if f["id"] == score["file_id"])
        students.append({"first": row["first"], "last": row["last"], "section": row["section"],
                         "file_id": score["file_id"], "url": latest.get("url", ""),
                         "when": work.school_day(turned_in).strftime("%b %d") if turned_in else "",
                         "earlier": [{"url": e["files"][0].get("url", ""),
                                      "when": work.school_day(work.when(e["submitted_at"])).strftime("%b %d")
                                      if work.when(e["submitted_at"]) else ""}
                                     for e in row["earlier"] if e["files"] and not e.get("late")],
                         "draft_done": score["counts"]["DRAFT"]["done"],
                         "draft_required": score["counts"]["DRAFT"]["required"]})
    prompt, where = feedback.load_prompt(token, agenda)  # (text, cell link) or (None, why not)
    names = feedback.short_names(students)
    return {"students": [{"name": names[id(s)], "section": s["section"], "file_id": s["file_id"],
                          "draft_done": s["draft_done"], "draft_required": s["draft_required"]} for s in students],
            "prompt": feedback.build(context, students, prompt) if prompt and students else None,
            "problem": None if prompt else where, "prompt_url": where if prompt else None,
            "done_before": done_before, "no_revision": no_revision, "sheet": sheet}


def record_post(environ, respond, config):
    """Note in the "Scores entered" sheet that this assignment was entered in
    IC, or had its feedback made: one row for the class and assignment."""
    refused = _same_origin_json(environ, respond, config)
    if refused:
        return refused
    try:
        payload = _json_body(environ, 2000)
        class_key, week, form_id = _form_args(payload)
        kind = str(payload.get("kind", ""))
        if kind not in ledger.KINDS:
            raise ValueError()
        token = google.service_token(config)
        agenda = load_agenda(token, config)
        context, _, _ = _form_context(token, config, agenda, class_key, week, form_id)
        ledger.record(token, google.service_token(config, write=True), config, kind, context)
        return respond("200 OK", {"recorded": 1, "sheet": ledger.read(token, config, form_id)})
    except (ValueError, KeyError, TypeError, AttributeError):
        return respond("400 Bad Request", {"error": "That request wasn't understood."})
    except google.GoogleError as error:
        return respond("502 Bad Gateway", {"error": str(error)})
    except Exception:
        return respond("500 Internal Server Error", {"error": "Something went wrong on the server. Nothing was recorded."})


def _same_origin_json(environ, respond, config):
    """A response refusing the request, or None when it is the signed-in
    teacher posting JSON from the dashboard page itself."""
    try:
        google.verify_teacher(environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION"), config)
    except google.SignInRequired:
        return respond("401 Unauthorized", {"error": "Please sign in again: reload this page."})
    origins = {v.strip() for v in config.get("HUB_STATE_PROBE_ORIGINS", "").split(",")
               if v.strip().startswith("https://")}
    if (environ.get("HTTP_ORIGIN") not in origins
            or environ.get("CONTENT_TYPE", "").split(";")[0].strip() != "application/json"):
        return respond("403 Forbidden", {"error": "This can only be done from the dashboard page."})
    return None


def _json_body(environ, limit):
    length = int(environ.get("CONTENT_LENGTH") or 0)
    if not 1 <= length <= limit:
        raise ValueError()
    payload = json.loads(environ["wsgi.input"].read(length))
    if not isinstance(payload, dict):
        raise ValueError()
    return payload


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
    if method == "POST" and path == "/v2/api/record":
        return record_post(environ, respond, config)
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
    refused = _same_origin_json(environ, respond, config)
    if refused:
        return refused
    try:
        payload = _json_body(environ, 2000)
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
