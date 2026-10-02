"""Read-only morning lists and counts-only receipts in the existing private ledger.

The runtime's explicit Form/section/component bindings extend the existing IC
resource reference contract. No destination is inferred from a lesson title.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from . import completion, google, ledger, work

KIND = "Morning helper"
CODES = frozenset(("running", "finished", "no_scores", "mapping_required", "signed_out", "hub_unavailable",
    "ic_unavailable", "panel_unusable", "missed_noon", "interrupted", "disabled", "uncertain_blocked",
    "read_back", "nothing_to_enter", "ic_changed", "ic_unreadable", "typing_stopped", "typed_values_differ",
    "save_unconfirmed", "readback_incomplete", "readback_mismatch", "destination_mapping_changed",
    "students_dont_match", "several_sections", "ic_not_saved", "unsupported_points_scale",
    "unsupported_gradebook", "list_expired", "source_changed", "receipt_unconfirmed", "coverage_incomplete"))


def bindings(config):
    try:
        value = json.loads(config.get("HUB_IC_ASSIGNMENT_MAP", "[]"))
    except (ValueError, TypeError):
        raise ValueError("mapping") from None
    if (not isinstance(value, list) or len(value) > 500
            or any(not isinstance(m, dict) or set(m) != {"class_key", "form_id", "targets"}
                   or not isinstance(m["targets"], list) for m in value)):
        raise ValueError("mapping")
    return value


def catalog(agenda):
    today = ledger.eastern_now()[:10]
    out, seen = [], set()
    for cls in agenda.classes:
        for week in agenda.week_options(cls):
            if not week.get("monday") or week["monday"] > today:
                continue
            for lesson in agenda.lessons(cls, week["label"]):
                for form in lesson["forms"]:
                    fid = form.get("form_id")
                    if fid and (cls, fid) not in seen:
                        seen.add((cls, fid))
                        out.append({"class": cls, "week": week["label"], "form": fid,
                                    "assignment": str(form.get("label") or lesson["topic"])[:200]})
    return {"assignments": out, "roster": "unavailable",
            "roster_note": "Roster sync is status-only; additions and removals need review."}


def person(row):
    return (work.norm(row.get("last", "")), work.norm(row.get("first", "")), row["section"])


def rows(form, scored, now):
    scores = {person(row): score for row, score in scored}
    window = form.get("window")
    started = bool(window and (not window.get("from") or work.when(window["from"]) <= now))
    elsewhere = set()
    for item in form.get("elsewhere", []):
        last, _, first = item.get("who", "").partition(",")
        elsewhere.add(person({"last": last, "first": first, "section": item["section"]}))
    result = {}

    def add(row, kind, notes=None, revision=None, reason=""):
        key = person(row)
        if not str(row.get("last", "")).strip() or not row.get("section"):
            return
        if key in result:
            result[key].update(kind="check", notes=None, revision=None, reason="listed twice on the roster")
            return
        result[key] = {"last": row["last"], "first": row.get("first", ""), "section": row["section"],
                       "kind": kind, "notes": notes, "revision": revision, "reason": reason}

    for row in form["students"]:
        if not row.get("matched"):
            continue
        score = scores.get(person(row))
        if not score or score.get("problem") or score.get("note") or all(score.get(k) is None for k in ("notes_score", "revision_score")):
            add(row, "check", reason="deck needs scoring by hand")
        else:
            add(row, "score", score.get("notes_score"), score.get("revision_score"))
    for row in form.get("missing", []) + [r for r in form.get("late", []) if r.get("matched")]:
        if not started:
            add(row, "none", reason="its two weeks haven't started")
        elif row.get("unsorted") or person(row) in elsewhere:
            add(row, "check", reason="submission needs review")
        else:
            add(row, "zero", 0, 0)
    for row in form.get("excused", []):
        add(row, "excused", reason="new student")
    return list(result.values())


def make_list(token, config, agenda, cls, week, fid, load_work, score_rows):
    found = load_work(token, config, agenda, cls, week, fresh=True)
    forms = [(lesson, form) for lesson in found["lessons"] for form in lesson["forms"] if form["form_id"] == fid]
    if len(forms) != 1:
        raise ValueError("form")
    lesson, form = forms[0]
    if found.get("problems") or form.get("problem"):
        return {"code": "coverage_incomplete"}
    now = datetime.now(timezone.utc)
    mark = ledger.read(token, config, fid)
    if not mark.get("configured") or mark.get("problem"):
        return {"code": "receipt_unconfirmed"}
    window = form.get("window")
    closed = bool(window and work.when(window["at"]) < now)
    if closed and mark.get("entered_at") and work.when(mark["entered_at"]["at"]) >= work.when(window["at"]):
        return {"code": "no_scores"}
    scored = score_rows(token, agenda, cls, week, fid, form, found)
    values = rows(form, scored, now)
    if not any(r["kind"] in ("score", "zero") for r in values):
        return {"code": "no_scores"}
    matched = [m for m in bindings(config) if m.get("class_key") == cls and m.get("form_id") == fid]
    if len(matched) != 1:
        return {"code": "mapping_required"}
    label = " ".join(str(form.get("label") or lesson["topic"]).split())[:200]
    value = {"schema": "hub-ic-list/v1", "list_id": str(uuid.uuid4()), "class_key": cls,
        "class_name": agenda.classes[cls]["name"], "week": week, "form_id": fid, "assignment": label,
        "form_label": label, "status": "closed" if closed else "open", "status_text": "Morning scores",
        "sent_at": now.isoformat(), "rows": values}
    revision = hashlib.sha256(json.dumps([form, scored], sort_keys=True, default=str).encode()).hexdigest()
    return {"code": "ready", "list": value, "revision": revision, "targets": matched[0].get("targets", [])}


def validate_report(value):
    if not isinstance(value, dict) or set(value) != {"day", "run_id", "code", "roster", "items"}:
        raise ValueError("report")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value["day"])) or str(uuid.UUID(value["run_id"])) != value["run_id"]:
        raise ValueError("report")
    if value["code"] not in CODES or value["roster"] != "unavailable" or not isinstance(value["items"], list) or len(value["items"]) > 300:
        raise ValueError("report")
    for item in value["items"]:
        if not isinstance(item, dict) or set(item) != {"assignment", "code", "filled", "raised", "kept", "left", "reasons"}:
            raise ValueError("report")
        if not isinstance(item["assignment"], str) or len(item["assignment"]) > 200 or item["code"] not in CODES:
            raise ValueError("report")
        if any(type(item[k]) is not int or not 0 <= item[k] <= 3600 for k in ("filled", "raised", "kept", "left")):
            raise ValueError("report")
        if set(item["reasons"]) != {"flag", "excused", "check", "name", "missing"} or any(type(n) is not int or not 0 <= n <= 3600 for n in item["reasons"].values()):
            raise ValueError("report")
    return value


def reports(token, config):
    sheet = ledger.find(token, config)
    if not sheet:
        return []
    tab = ledger._first_tab(token, sheet["id"])
    source = google.sheet_values(token, sheet["id"], f"{tab}!A1:G{ledger.MAX_ROWS}") if tab else []
    latest = {}
    for row in source:
        if len(row) < 7 or row[1] != KIND:
            continue
        try:
            value = validate_report(json.loads(row[6]))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        latest.pop(value["run_id"], None)
        latest[value["run_id"]] = value
    return sorted(reversed(list(latest.values())), key=lambda r: r["day"], reverse=True)[:10]


def record(token, write_token, config, report):
    value = validate_report(report)
    # Identical receipt retries append nothing and never execute IC again.
    if any(r == value for r in reports(token, config)):
        return
    sheet = ledger.find(token, config)
    if not sheet:
        raise google.GoogleError("The Scores entered sheet is unavailable.")
    tab = ledger._first_tab(token, sheet["id"])
    if not tab:
        raise google.GoogleError("The Scores entered sheet is unavailable.")
    payload = json.dumps(value, separators=(",", ":"))
    if len(payload) > 45000:
        raise ValueError("report")
    google.append_rows(write_token, sheet["id"], tab + "!A1", [[ledger.eastern_now(), KIND, "", value["day"], "Morning run", value["run_id"], payload]])
    if not any(r == value for r in reports(token, config)):
        raise google.GoogleError("The morning summary could not be read back.")
