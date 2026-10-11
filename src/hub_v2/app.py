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
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

from . import agenda as agenda_module
from concurrent.futures import ThreadPoolExecutor

from . import agenda_edit, audit, completion, daily_feedback_packet, feedback, google, ledger, morning, native_feedback, recovery, routine, student_archive, tidy, work

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
          "provider-status.js": "text/javascript; charset=utf-8",
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


def learning_report(config):
    """A private report handoff; school data and report IDs stay out of source code."""
    url = str(config.get('HUB_GROWTH_REPORT_URL') or '')
    teaching = str(config.get('HUB_GROWTH_TEACHING_URL') or '')
    pattern = r'https://docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]{10,200}/edit(?:#gid=\d+)?'
    if not re.fullmatch(pattern, url):
        return {'configured': False}
    as_of = str(config.get('HUB_GROWTH_REPORT_AS_OF') or '')
    try:
        date.fromisoformat(as_of)
    except ValueError:
        as_of = ''
    separate = {
        'analysis_url': 'HUB_BIOLOGY_ANALYSIS_URL',
        'work_index_url': 'HUB_STUDENT_WORK_INDEX_URL',
        'checklist_url': 'HUB_FEEDBACK_CHECKLIST_URL',
    }
    links = {key: value if re.fullmatch(pattern, value) else ''
             for key, setting in separate.items()
             for value in (str(config.get(setting) or ''),)}
    return {'configured': True, 'class_key': 'biology', 'url': url,
            'teaching_url': teaching if re.fullmatch(pattern, teaching) and teaching.split('/edit')[0] == url.split('/edit')[0] else url,
            'as_of': as_of, 'automatic_refresh': False, **links}


def api(sub, query, config):
    if sub == 'api/learning-report':
        return learning_report(config)
    token = google.service_token(config)
    if sub == 'api/feedback/daily':
        if set(query) not in (set(), {'date'}):
            raise ValueError('date')
        return daily_feedback_packet.status(token, config, _one(query, 'date', False) or None)
    if sub == 'api/feedback/today':
        if set(query) not in (set(), {'date'}):
            raise ValueError('date')
        return native_feedback.today_files(token, config, date_et=_one(query, 'date', False) or None)
    if sub == 'api/student-records':
        if set(query) != {'class'}:
            raise ValueError('class')
        return student_archive.archive_listing(token, config, _class_key(_one(query, 'class')))
    fresh = _one(query, "fresh", False) == "1"
    agenda = load_agenda(token, config, fresh)
    sheet = {"title": agenda.title,
             "url": "https://docs.google.com/spreadsheets/d/%s/edit" % agenda.spreadsheet_id}
    if sub == "api/morning/catalog":
        return morning.catalog(agenda, config)
    if sub == "api/morning/reports":
        return {"reports": morning.reports(token, config), "recovery": recovery.records(token, config)}
    if sub == "api/morning/list":
        return morning.make_list(token, config, agenda, _class_key(_one(query, "class")),
                                 _one(query, "week"), _one(query, "form"), lesson_work, _score_rows)
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
        found = lesson_work(token, config, agenda, class_key, week)
        candidates = {f["form_id"]: {"rows": completion.recheck_candidates(f)}
                      for lesson in found.get("lessons", []) for f in lesson["forms"] if f.get("form_id")}
        return {**found, "entry_rechecks": candidates, "agenda": sheet}
    if sub == "api/score":
        class_key = _class_key(_one(query, "class"))
        week, form_id, file_id = _one(query, "week"), _one(query, "form"), _one(query, "file")
        if not 1 <= len(week) <= 100 or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", form_id + file_id):
            raise ValueError("score")
        found = lesson_work(token, config, agenda, class_key, week, fresh=False)
        form = next((f for lesson in found.get("lessons", []) for f in lesson["forms"]
                     if f["form_id"] == form_id), None)
        original_rows = (form or {}).get("students", []) + (form or {}).get("late", [])
        extended_file = any(r.get("matched") and any(f["id"] == file_id for snapshot in
                            [r] + r.get("earlier", []) for f in snapshot.get("files", []))
                            and (r in (form or {}).get("late", []) or r.get("earlier")) for r in original_rows)
        if extended_file:
            # A cached page cannot authorize a later or replacement source.
            found = lesson_work(token, config, agenda, class_key, week, fresh=True)
            forms = [f for lesson in found.get("lessons", []) for f in lesson["forms"]
                     if f["form_id"] == form_id]
            if len(forms) != 1:
                raise ValueError("form")
            form = forms[0]
            context = "%s · %s · %s" % (agenda.classes[class_key]["name"], week,
                                         form.get("label") or "this assignment")
            if (found.get("problem") or found.get("problems") or found.get("assessment_problem")
                    or found.get("other_files") or form.get("problem")):
                return {"code": "coverage_incomplete", "problem":
                        context + ": current submission coverage or lesson matches need review before this late-work recheck."}
            if not any(r.get("matched") and any(f["id"] == file_id for snapshot in
                       [r] + r.get("earlier", []) for f in snapshot.get("files", []))
                       for r in form.get("students", []) + form.get("late", [])):
                raise ValueError("file")
            current_file = any(any(f["id"] == file_id for f in r["files"]) for r in form.get("students", []))
            cutoff = work.when((form.get("window") or {}).get("at"))
            if current_file and (not cutoff or cutoff >= datetime.now(timezone.utc)):
                return completion.score_file(token, agenda, class_key, week, form_id, file_id, found)
            mark = ledger.read(token, config, form_id)
            if not mark.get("configured") or mark.get("problem"):
                return {"code": "receipt_unconfirmed", "problem": context + ": entry history could not be verified."}
            if not morning.closed_recheck(form, mark):
                if not current_file:
                    raise ValueError("file")
                return completion.score_file(token, agenda, class_key, week, form_id, file_id, found)
            ready = completion.assignment_readiness(token, config, agenda, class_key, week, form_id)
            if ready["status"] != "READY":
                return {"code": "deck_not_ready", "label": ready.get("label"), "readiness": ready,
                        "problem": ready["problem"], "action": ready.get("action")}
            return completion.score_file(token, agenda, class_key, week, form_id, file_id, found, include_late=True)
        return completion.score_file(token, agenda, class_key, week, form_id, file_id, found)
    if sub == "api/routine":
        prompt, where = feedback.load_embed_prompt(token, agenda)
        return {**routine.status(token, config),
                "embed_prompt": {"text": prompt, "url": where} if prompt else {"problem": where}}
    if sub == "api/audit":
        return audit.check(token, agenda, _class_key(_one(query, "class")), config)
    if sub == "api/feedback/native":
        class_key, week, form_id = _form_args(query)
        found = lesson_work(token, config, agenda, class_key, week, fresh=True)
        return native_feedback.listing(config, native_feedback.target(class_key, week, form_id, ''), found)
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
    return context, _score_rows(token, agenda, class_key, week, form_id, form, found)


def _score_rows(token, agenda, class_key, week, form_id, form, found, *, include_late=False):
    candidates = completion.recheck_candidates(form) if include_late else form["students"]
    if any(r.get("selection_problem") for r in candidates):
        return [(r, {"code": "coverage_incomplete", "problem": r["selection_problem"]})
                for r in candidates if r.get("selection_problem")]
    rows = [r for r in candidates if r["matched"] and r["files"]]

    def one(row):
        upload = next((f for f in row["files"] if f.get("mime") in (completion.SLIDES, google.PPTX)), row["files"][0])
        if include_late:
            return completion.score_file(token, agenda, class_key, week, form_id, upload["id"], found, include_late=True)
        return completion.score_file(token, agenda, class_key, week, form_id, upload["id"], found)
    with ThreadPoolExecutor(max_workers=4) as pool:
        scores = list(pool.map(one, rows))
    return list(zip(rows, scores))


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


def native_feedback_post(environ, respond, config):
    refused = _same_origin_json(environ, respond, config)
    if refused:
        return refused
    from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable
    from school_workflow_hub.adapters.hosted_agenda import ConnectionError
    try:
        payload = _json_body(environ, 2000)
        if environ['PATH_INFO'].endswith('/daily'):
            if set(payload) != {'date'}:
                raise ValueError()
            return respond('200 OK', daily_feedback_packet.build(google.service_token(config),
                google.service_token(config, write=True), config, payload['date']))
        if environ['PATH_INFO'].endswith('/print-today'):
            if set(payload) != {'ids'}:
                raise ValueError()
            packet, name = native_feedback.print_today(
                google.service_token(config), config, payload['ids'])
            return respond('200 OK', packet, 'application/pdf',
                           [('Content-Disposition', 'inline; filename="%s"' % name)])
        assertion = environ.get('HTTP_X_GOOG_IAP_JWT_ASSERTION')
        if environ['PATH_INFO'].endswith('/save'):
            if set(payload) != {'id', 'class', 'week', 'form', 'file'}:
                raise ValueError()
            saved = native_feedback.get_saved(config, payload['id']).value
            _form_args(payload)
            scope = {k: payload[k] for k in ('class', 'week', 'form', 'file')}
            if any(scope[k] != saved['scope'][k] for k in ('class', 'form', 'file')):
                raise ValueError()
            token = google.service_token(config)
            agenda = load_agenda(token, config, fresh=True)
            found = lesson_work(token, config, agenda, scope['class'], scope['week'], fresh=True)
            inputs = native_feedback.evidence(assertion, token, config, agenda, scope, found)
            archive_tokens = ({'token': token, 'write_token': google.service_token(config, write=True)}
                              if student_archive.enabled(config) else {})
            return respond('200 OK', native_feedback.save(config, payload['id'], inputs, **archive_tokens))
        if set(payload) != {'class', 'week', 'form', 'file'}:
            raise ValueError()
        cls, week, form_id = _form_args(payload)
        if not re.fullmatch(r'[A-Za-z0-9_-]{20,128}', str(payload.get('file', ''))):
            raise ValueError()
        token = google.service_token(config)
        agenda = load_agenda(token, config, fresh=True)
        found = lesson_work(token, config, agenda, cls, week, fresh=True)
        inputs = native_feedback.evidence(assertion, token, config, agenda, payload, found)
        return respond('200 OK', native_feedback.generate(config, inputs))
    except (native_feedback.Held, daily_feedback_packet.Held) as error:
        return respond('409 Conflict', {'error': str(error)})
    except Conflict:
        return respond('409 Conflict', {'error': 'This feedback is already being prepared. Reopen the saved draft before retrying.'})
    except (StoreUnavailable, ConnectionError):
        return respond('503 Service Unavailable', {'error': 'The private feedback source or storage is unavailable. No grades changed.'})
    except google.GoogleError as error:
        return respond('502 Bad Gateway', {'error': str(error)})
    except (ValueError, KeyError, TypeError):
        return respond('400 Bad Request', {'error': 'That feedback request was not understood.'})
    except Exception:
        return respond('503 Service Unavailable', {'error': 'Feedback could not finish. Reopen the draft before retrying; no grades changed.'})


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
        note = payload.get("note", "")
        if kind not in ledger.KINDS or not isinstance(note, str) or len(note) > 500:
            raise ValueError()
        token = google.service_token(config)
        agenda = load_agenda(token, config, fresh=kind == "ic")
        if kind == "ic":
            policy = work.week_policy(config, agenda, class_key, week)
            if policy.get("problem") or policy.get("excluded"):
                return respond("409 Conflict", {"error": policy.get("problem") or policy["reason"],
                                                "code": "coverage_incomplete" if policy.get("problem") else "excluded_week"})
        context, form, found = _form_context(token, config, agenda, class_key, week, form_id)
        if kind == "ic" and found.get("assessment_problem"):
            return respond("409 Conflict", {"error": found["assessment_problem"], "code": "coverage_incomplete"})
        if kind == "ic":
            ready = completion.assignment_readiness(token, config, agenda, class_key, week, form_id)
            if ready["status"] == "READY":
                ready = completion.submitted_readiness(token, form,
                    require_complete_codes=completion.student_coding_required(agenda, class_key, week, form_id))
            if ready["status"] != "READY":
                return respond("409 Conflict", {"error": ready["problem"], "action": ready.get("action"),
                                                "code": "coverage_incomplete", "readiness": ready})
        ledger.record(token, google.service_token(config, write=True), config, kind, context, note)
        return respond("200 OK", {"recorded": 1, "sheet": ledger.read(token, config, form_id)})
    except (ValueError, KeyError, TypeError, AttributeError):
        return respond("400 Bad Request", {"error": "That request wasn't understood."})
    except google.GoogleError as error:
        return respond("502 Bad Gateway", {"error": str(error)})
    except Exception:
        return respond("500 Internal Server Error", {"error": "Something went wrong on the server. Nothing was recorded."})


def retired_answers_post(environ, respond, config):
    """Reject stale clients without reading or writing the retired archive."""
    refused = _same_origin_json(environ, respond, config)
    if refused:
        return refused
    return respond("410 Gone", {"error": "The redundant Biology answers archive has been retired."})


def agenda_post(environ, respond, config):
    """Save a lesson's edited plain-text cells to the Agenda. Signed-in teacher,
    same-origin JSON only; a cell changed in the Agenda meanwhile is a 409."""
    refused = _same_origin_json(environ, respond, config)
    if refused:
        return refused
    try:
        payload = _json_body(environ, 250000)
        class_key, week, row = _class_key(payload.get("class")), payload.get("week"), payload.get("row")
        changes = payload.get("changes")
        if (not isinstance(week, str) or not 1 <= len(week) <= 100 or not isinstance(row, int)
                or isinstance(row, bool) or not 1 <= row <= agenda_module.LAST_ROW or not isinstance(changes, dict)):
            raise ValueError()
        for change in changes.values():
            if not isinstance(change, dict) or not all(
                    isinstance(change.get(k), str) and len(change[k]) <= agenda_edit.MAX_TEXT for k in ("from", "to")):
                raise ValueError()
        token = google.service_token(config)
        agenda = load_agenda(token, config, fresh=True)
        result = agenda_edit.save(token, google.service_token(config, write=True), config, agenda, class_key,
                                  week, row, changes)
        with _cache_lock:  # the next read shows what was saved
            _cache["agenda"] = None
            _work_cache.clear()
        return respond("200 OK", result)
    except agenda_edit.Changed as changed:
        return respond("409 Conflict", {"error": str(changed), "current": changed.current})
    except ValueError as error:
        told = (str(error) if isinstance(error, agenda_edit.Refused) or "Agenda" in str(error)
                else "That request wasn't understood.")
        return respond("400 Bad Request", {"error": told})
    except (KeyError, TypeError, AttributeError):
        return respond("400 Bad Request", {"error": "That request wasn't understood."})
    except google.GoogleError as error:
        return respond("502 Bad Gateway", {"error": str(error)})
    except Exception:
        return respond("500 Internal Server Error",
                       {"error": "Something went wrong on the server. Reload to see what the Agenda says now."})


def move_post(environ, respond, config):
    """Move a lesson to another week; the lessons between slide over (see
    agenda_edit.move). Signed-in teacher, same-origin JSON only."""
    refused = _same_origin_json(environ, respond, config)
    if refused:
        return refused
    try:
        payload = _json_body(environ, 2000)
        class_key, week, row, to_week = (_class_key(payload.get("class")), payload.get("week"),
                                         payload.get("row"), payload.get("to_week"))
        if (not all(isinstance(w, str) and 1 <= len(w) <= 100 for w in (week, to_week))
                or not isinstance(row, int) or isinstance(row, bool) or not 1 <= row <= agenda_module.LAST_ROW):
            raise ValueError()
        token = google.service_token(config)
        agenda = load_agenda(token, config, fresh=True)
        result = agenda_edit.move(token, google.service_token(config, write=True), config, agenda, class_key,
                                  week, row, to_week)
        with _cache_lock:  # the next read shows the move
            _cache["agenda"] = None
            _work_cache.clear()
        return respond("200 OK", result)
    except agenda_edit.Changed as changed:
        return respond("409 Conflict", {"error": str(changed), "current": changed.current})
    except ValueError as error:
        told = (str(error) if isinstance(error, agenda_edit.Refused) or "Agenda" in str(error)
                else "That request wasn't understood.")
        return respond("400 Bad Request", {"error": told})
    except (KeyError, TypeError, AttributeError):
        return respond("400 Bad Request", {"error": "That request wasn't understood."})
    except google.GoogleError as error:
        return respond("502 Bad Gateway", {"error": str(error)})
    except Exception:
        return respond("500 Internal Server Error",
                       {"error": "Something went wrong on the server. Reload to see what the Agenda says now."})


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


def morning_post(environ, respond, config):
    refused = _same_origin_json(environ, respond, config)
    if refused:
        return refused
    try:
        payload = morning.validate_report(_json_body(environ, 100000))
        token = google.service_token(config)
        morning.record(token, google.service_token(config, write=True), config, payload)
        return respond("200 OK", {"recorded": True})
    except (ValueError, TypeError, KeyError, AttributeError):
        return respond("400 Bad Request", {"error": "Invalid morning report."})
    except google.GoogleError:
        return respond("502 Bad Gateway", {"error": "Morning summary not confirmed."})


def recovery_post(environ, respond, config):
    refused = _same_origin_json(environ, respond, config)
    if refused:
        return refused
    try:
        payload = recovery.validate(_json_body(environ, 100000))
        actor = google.verify_teacher(environ.get('HTTP_X_GOOG_IAP_JWT_ASSERTION'), config)
        token = google.service_token(config)
        saved = recovery.record(token, google.service_token(config, write=True), config, payload, actor)
        return respond('200 OK', {'recorded': True, 'record': saved})
    except (ValueError, TypeError, KeyError, AttributeError):
        return respond('400 Bad Request', {'error': 'Invalid recovery record.'})
    except google.GoogleError:
        return respond('502 Bad Gateway', {'error': 'Recovery record not confirmed; the block remains.'})


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

    if method == "POST" and path == "/v2/api/morning/recovery":
        return recovery_post(environ, respond, config)
    if method == "POST" and path == "/v2/api/morning/reports":
        return morning_post(environ, respond, config)
    if method == "POST" and path == "/v2/api/tidy":
        return tidy_post(environ, respond, config)
    if method == 'POST' and path == '/v2/api/student-records/archive':
        return student_records_post(environ, respond, config)
    if method == "POST" and path == "/v2/api/record":
        return record_post(environ, respond, config)
    if method == 'POST' and path in ('/v2/api/feedback/draft', '/v2/api/feedback/save',
                                     '/v2/api/feedback/print-today', '/v2/api/feedback/daily'):
        return native_feedback_post(environ, respond, config)
    if method == "POST" and path in ("/v2/api/answers", "/v2/api/answers/sweep"):
        return retired_answers_post(environ, respond, config)
    if method == "POST" and path == "/v2/api/agenda":
        return agenda_post(environ, respond, config)
    if method == "POST" and path == "/v2/api/agenda/move":
        return move_post(environ, respond, config)
    if method not in ("GET", "HEAD"):
        return respond("405 Method Not Allowed", {"error": "This page only reads."},
                       extra=[("Allow", "GET, HEAD")])
    if path == "/v2":
        return respond("302 Found", b"", "text/plain", [("Location", "/v2/")])
    sub = path[len("/v2/"):] if path.startswith("/v2/") else None
    if sub == "" or sub in ASSETS:
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
        if sub == 'api/feedback/daily/pdf':
            if set(query) != {'id'}:
                raise ValueError()
            packet, name = daily_feedback_packet.pdf(google.service_token(config), config, _one(query, 'id'))
            return respond('200 OK', packet, 'application/pdf',
                [('Content-Disposition', 'inline; filename="%s"' % name.encode('ascii', 'replace').decode())])
        if sub == 'api/feedback/pdf':
            if set(query) != {'id'}:
                raise ValueError()
            saved = native_feedback.get_saved(config, _one(query, 'id')).value
            if not saved['saved_at']:
                raise native_feedback.Held('Save this private draft before printing it.')
            from .feedback_pdf import render
            return respond('200 OK', render(saved), 'application/pdf',
                           [('Content-Disposition', 'inline; filename="student-feedback-draft.pdf"')])
        body = api(sub, query, config)
    except (native_feedback.Held, daily_feedback_packet.Held) as error:
        return respond('409 Conflict', {'error': str(error)})
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


def student_records_post(environ, respond, config):
    """File verified Form receipts; source files and grades are never mutated."""
    refused = _same_origin_json(environ, respond, config)
    if refused:
        return refused
    try:
        payload = _json_body(environ, 2000)
        if set(payload) != {'class', 'week'}:
            raise ValueError()
        class_key, week = _class_key(payload['class']), payload['week']
        if not isinstance(week, str) or not 1 <= len(week) <= 100:
            raise ValueError()
        token = google.service_token(config)
        agenda = load_agenda(token, config, fresh=True)
        if week not in {w['label'] for w in agenda.week_options(class_key)}:
            raise ValueError()
        found = lesson_work(token, config, agenda, class_key, week, fresh=True)
        return respond('200 OK', student_archive.archive_work(
            token, google.service_token(config, write=True), config, class_key, found))
    except (ValueError, KeyError, TypeError):
        return respond('400 Bad Request', {'error': 'That filing request was not understood.'})
    except google.GoogleError as error:
        return respond('502 Bad Gateway', {'error': str(error)})
    except Exception:
        return respond('503 Service Unavailable', {'error': 'Student filing is not confirmed. Existing work is preserved.'})


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
        result = tidy.apply(token, write_token, config, agenda, class_key, week, plan_id)
        if student_archive.enabled(config):
            try:
                result['student_records'] = student_archive.archive_work(
                    token, write_token, config, class_key,
                    lesson_work(token, config, agenda, class_key, week, fresh=True))
            except Exception as error:
                result['student_records'] = {'status': 'held', 'filed': 0,
                    'problems': [str(error) if isinstance(error, google.GoogleError) else
                                 'Student filing did not finish. The preceding tidy result is preserved.']}
        return respond("200 OK", result)
    except tidy.Changed:
        return respond("409 Conflict", {"error": "Something changed since the preview. Review it again."})
    except (ValueError, KeyError, TypeError, AttributeError):
        return respond("400 Bad Request", {"error": "That request wasn't understood."})
    except google.GoogleError as error:
        return respond("502 Bad Gateway", {"error": str(error)})
    except Exception:
        return respond("500 Internal Server Error", {"error": "Something went wrong on the server. Nothing more was moved."})
