"""Counts-only receipts for fresh, verified mapped IC comparisons.

The teacher's extension supplies the destination readback. The server proves
the source list and mapping again; a partial comparison never marks the whole
assignment entered. This does not retry IC or clear a helper uncertainty block.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from . import google, ledger, work

KIND = "Morning IC comparison"
SCHEMA = "hub-morning-entry-receipt/v1"
TTL_SECONDS = 3 * 60 * 60
COUNTS = {"filled", "raised", "kept", "left", "not_saved"}
RESULT_FIELDS = {"list_id", "form_id", "class_key", "week", "column", "section",
                 "ic_title", "outcome", "certain", "counts", "at"}
TARGET_FIELDS = {"section", "column", "url", "section_id", "term_id", "year",
                 "school_id", "calendar_id", "structure_id", "assignment_id",
                 "score_group", "task_id", "label_code"}


class Refused(ValueError):
    def __init__(self, code="receipt_unconfirmed"):
        super().__init__(code)
        self.code = code


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value, version=4)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def scope(payload):
    if (not isinstance(payload, dict)
            or set(payload) != {"run_id", "scope", "revision", "list", "targets", "results"}
            or not _uuid(payload["run_id"])):
        raise Refused()
    value = payload["scope"]
    if (not isinstance(value, dict) or set(value) != {"class", "week", "form"}
            or value["class"] not in ("biology", "forensics", "anatomy")
            or not isinstance(value["week"], str) or not 1 <= len(value["week"]) <= 100
            or not isinstance(value["form"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{10,100}", value["form"])):
        raise Refused()
    return value


def _target(value):
    if not isinstance(value, dict) or set(value) != TARGET_FIELDS:
        raise Refused()
    if (not isinstance(value["section"], str) or not value["section"].strip()
            or len(value["section"]) > 100 or value["column"] not in ("notes", "revision")
            or not isinstance(value["label_code"], str)
            or not re.fullmatch(r"[A-Za-z0-9_()-]{1,80}", value["label_code"])):
        raise Refused()
    for key in ("section_id", "term_id", "year", "school_id", "calendar_id", "structure_id",
                "assignment_id", "score_group", "task_id"):
        if not isinstance(value[key], str) or not re.fullmatch(r"\d{1,30}", value[key]):
            raise Refused()
    if not isinstance(value["url"], str):
        raise Refused()
    url = urlsplit(value["url"])
    path = "/campus/sis/control-center/classroom/curriculum/resource/%s/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/%s/objectSectionID/%s/calendarID/%s/structureID/%s)" % (
        value["assignment_id"], value["section_id"], value["assignment_id"], value["calendar_id"], value["structure_id"])
    query = parse_qs(url.query, keep_blank_values=True)
    expected = {"classroomSectionID": value["section_id"], "classroomTermID": value["term_id"],
                "year": value["year"], "schoolID": value["school_id"],
                "calendarID": value["calendar_id"], "structureID": value["structure_id"]}
    if (url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment
            or url.path != path or set(query) - set(expected) - {"appName"}
            or any(query.get(key) != [entry] for key, entry in expected.items())
            or "appName" in query and len(query["appName"]) != 1):
        raise Refused()


def validate(payload, current, *, now=None):
    """Bind the entire current list, including held rows, to every readback."""
    selected = scope(payload)
    now = now or datetime.now(timezone.utc)
    old, fresh = payload["list"], current.get("list")
    if current.get("code") != "ready" or not isinstance(fresh, dict):
        raise Refused("source_changed")
    if (not isinstance(payload["revision"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", payload["revision"])
            or payload["revision"] != current.get("revision")
            or payload["targets"] != current.get("targets")):
        raise Refused("source_changed")
    if not isinstance(old, dict) or set(old) != set(fresh) or not _uuid(old.get("list_id")):
        raise Refused()
    sent = work.when(old.get("sent_at"))
    if (not sent or not isinstance(old["sent_at"], str) or len(old["sent_at"]) > 40
            or not -60 <= (now - sent).total_seconds() <= TTL_SECONDS):
        raise Refused()
    if any(old[key] != fresh[key] for key in fresh if key not in ("list_id", "sent_at")):
        raise Refused("source_changed")
    if (old["class_key"], old["week"], old["form_id"]) != (
            selected["class"], selected["week"], selected["form"]):
        raise Refused()
    needed = {(row["section"], column) for row in fresh["rows"]
              if row["kind"] in ("score", "zero") for column in ("notes", "revision")
              if row[column] is not None}
    targets, results = payload["targets"], payload["results"]
    if (not isinstance(targets, list) or not 1 <= len(targets) <= 12
            or not isinstance(results, list) or len(results) != len(targets)):
        raise Refused()
    pairs, urls = set(), set()
    for target in targets:
        _target(target)
        pair = (target["section"], target["column"])
        if pair not in needed or pair in pairs or target["url"] in urls:
            raise Refused()
        pairs.add(pair); urls.add(target["url"])
    if pairs != needed:
        raise Refused()
    seen = set()
    for result in results:
        if not isinstance(result, dict) or set(result) != RESULT_FIELDS:
            raise Refused()
        pair = (result["section"], result["column"])
        at = work.when(result["at"])
        if (pair not in pairs or pair in seen
                or (result["list_id"], result["form_id"], result["class_key"], result["week"]) != (
                    old["list_id"], old["form_id"], old["class_key"], old["week"])
                or result["certain"] is not True or result["outcome"] not in ("entered", "checked")
                or not isinstance(result["at"], str) or len(result["at"]) > 40
                or not at or not sent <= at <= now or not isinstance(result["ic_title"], str)
                or len(result["ic_title"]) > 200
                or any(ord(char) < 32 or ord(char) == 127 for char in result["ic_title"])):
            raise Refused()
        counts = result["counts"]
        if (not isinstance(counts, dict) or set(counts) != COUNTS
                or any(type(value) is not int or not 0 <= value <= 300 for value in counts.values())
                or counts["not_saved"] != 0
                or sum(counts.values()) > 300
                or result["outcome"] == "checked" and counts["filled"] + counts["raised"] != 0
                or result["outcome"] == "entered" and counts["filled"] + counts["raised"] == 0):
            raise Refused()
        eligible = sum(row["section"] == pair[0] and row["kind"] in ("score", "zero")
                       and row[pair[1]] is not None for row in fresh["rows"])
        if counts["filled"] + counts["raised"] > eligible:
            raise Refused()
        seen.add(pair)
    return {"schema": SCHEMA, "run_id": payload["run_id"], "list_id": old["list_id"],
            "scope": selected, "source_revision": payload["revision"], "targets": targets,
            "results": results, "source_check_rows": sum(row["kind"] == "check" for row in fresh["rows"])}


def record(token, write_token, config, context, receipt, *, whole_assignment):
    """One append with exact readback; an already identical receipt adds nothing."""
    receipt = {**receipt, "whole_assignment_recorded": whole_assignment}
    note = "Morning Chrome helper: verified mapped comparisons; %d source check rows remain. Receipt %s/%s." % (
        receipt["source_check_rows"], receipt["run_id"], receipt["list_id"])
    sheet = ledger.find(token, config)
    tab = ledger._first_tab(token, sheet["id"]) if sheet else None
    if not sheet or not tab:
        raise google.GoogleError("The Scores entered sheet is unavailable.")
    area = "%s!A1:G%d" % (tab, ledger.MAX_ROWS)
    def read():
        return google.sheet_values(token, sheet["id"], area)
    def matching(rows):
        matches = []
        for row in rows:
            if len(row) < 7 or row[1] != KIND or row[5] != receipt["scope"]["form"]:
                continue
            try:
                value = json.loads(row[6])
            except (ValueError, TypeError):
                raise Refused() from None
            if not isinstance(value, dict) or value.get("schema") != SCHEMA:
                raise Refused()
            if value.get("list_id") != receipt["list_id"]:
                continue
            if value != receipt or row[2:6] != [context["class"], context["week"], context["assignment"], context["form_id"]]:
                raise Refused()
            if whole_assignment and [row[0], ledger.KINDS["ic"], *row[2:6], note] not in rows:
                raise Refused()
            matches.append(row)
        return bool(matches)
    rows = read()
    if matching(rows):
        return receipt
    stamp = ledger.eastern_now()
    additions = [] if rows else [ledger.HEADER]
    serialized = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    if len(serialized) > 45000:
        raise Refused()
    additions.append([stamp, KIND, context["class"], context["week"], context["assignment"],
                      context["form_id"], serialized])
    if whole_assignment:
        additions.append([stamp, ledger.KINDS["ic"], context["class"], context["week"],
                          context["assignment"], context["form_id"], note])
    google.append_rows(write_token, sheet["id"], tab + "!A1", additions)
    if not matching(read()):
        raise google.GoogleError("The mapped comparison receipt could not be read back.")
    return receipt


def acknowledgement(receipt):
    counts = {key: sum(result["counts"][key] for result in receipt["results"])
              for key in ("filled", "raised", "kept", "left", "not_saved")}
    return {"schema": "hub-morning-entry-ack/v1", "run_id": receipt["run_id"],
            "scope": receipt["scope"], "list_id": receipt["list_id"],
            "source_revision": receipt["source_revision"], "target_count": len(receipt["targets"]),
            "certain": True, "counts": counts, "source_check_rows": receipt["source_check_rows"],
            "whole_assignment_recorded": receipt["whole_assignment_recorded"],
            "receipt_sha256": hashlib.sha256(json.dumps(receipt, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()}
