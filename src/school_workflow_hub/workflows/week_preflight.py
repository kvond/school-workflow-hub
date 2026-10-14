"""Deterministic weekly teaching preflight over one normalized week snapshot.

The caller (a host reader or the teacher's browser session) supplies what the
weekly folder, Agenda, Schoology, the lesson decks/materials and the Turn-in
Forms currently contain. This module reads nothing itself, writes nothing,
makes no model call and never edits a deck. It returns only:

* actionable exceptions (each with the system to fix and the smallest action),
* the Monday print/lab packet,
* the supply/grocery list,
* prepared digital alternatives for paper worksheets whose learning work can
  move to slides, text boxes or a linked response without being removed.

Variation Theory structure is checked against the teacher's intended critical
aspects and the whole-to-whole phase order; when deck text is supplied the
existing ``deck_release.validate_release`` contract is reused rather than
re-implemented.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date
import math
import re

from school_workflow_hub.core.runs import content_hash, deterministic_id
from school_workflow_hub.workflows.curriculum import PEDAGOGICAL_ARCHITECTURE
from school_workflow_hub.workflows.deck_release import _aspect_key, validate_release

SCHEMA = "week-preflight-input/v1"
RESULT_SCHEMA = "week-preflight/v1"
SYSTEMS = ("folder", "agenda", "schoology")
PHASES = tuple(phase.value for phase in PEDAGOGICAL_ARCHITECTURE)
TEXT_RESPONSES = frozenset(("short_text", "long_text", "calculation", "choice"))
PAPER_RESPONSES = frozenset(("drawing", "diagram_label", "table", "manipulative", "graph"))
PASSIVE_MODES = frozenset(("direct", "listen", "lecture", "video"))
STUDENT_FACING_KINDS = frozenset(("worksheet", "handout", "reading", "lab_sheet"))
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# Flow tolerances, in minutes, for one block. Kept as named constants so the
# teacher can see and change the rule rather than discover it in a branch.
UNDER_TIME_SLACK = 10
LONG_PASSIVE_MINUTES = 20
SPARE_COPY_RATE = 0.10
MIN_SPARE_COPIES = 2


class PreflightInputError(ValueError):
    """The snapshot itself is unusable; nothing was checked."""


def _norm(value) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(value or "")).casefold().split())


def _text(value) -> str:
    return str(value or "").strip()


def _require(condition, code):
    if not condition:
        raise PreflightInputError(code)


def _validate(snapshot):
    _require(isinstance(snapshot, dict), "snapshot_not_object")
    _require(snapshot.get("schema_version") == SCHEMA, "snapshot_schema_unsupported")
    _require(_text(snapshot.get("week_id")), "week_id_required")
    try:
        date.fromisoformat(snapshot.get("week_start", ""))
    except (TypeError, ValueError):
        raise PreflightInputError("week_start_invalid") from None
    minutes = snapshot.get("class_minutes", 90)
    _require(type(minutes) is int and 30 <= minutes <= 180, "class_minutes_invalid")
    lessons = snapshot.get("lessons")
    _require(isinstance(lessons, list) and lessons, "lessons_required")
    ids = [_text(lesson.get("lesson_id")) for lesson in lessons if isinstance(lesson, dict)]
    _require(len(ids) == len(lessons) and all(ids), "lesson_id_required")
    _require(len(set(ids)) == len(ids), "lesson_id_duplicated")
    sections = snapshot.get("sections")
    _require(isinstance(sections, list) and sections, "sections_required")
    for section in sections:
        _require(isinstance(section, dict) and _text(section.get("section_id"))
                 and _text(section.get("course")), "section_identity_required")
        _require(type(section.get("enrollment")) is int and section["enrollment"] >= 0,
                 "section_enrollment_invalid")
    systems = snapshot.get("systems")
    _require(isinstance(systems, dict) and all(isinstance(systems.get(name), list)
                                               for name in SYSTEMS), "systems_incomplete")


class _Exceptions:
    def __init__(self):
        self.items = []

    def add(self, lesson_id, system, code, action, severity="fix"):
        entry = {"lesson_id": lesson_id, "system": system, "code": code,
                 "severity": severity, "action": action}
        if entry not in self.items:
            self.items.append(entry)


def _by_lesson(rows):
    grouped = OrderedDict()
    for row in rows:
        if isinstance(row, dict) and _text(row.get("lesson_id")):
            grouped.setdefault(row["lesson_id"], []).append(row)
    return grouped


def _lesson_days(lesson_id, agenda_rows, week_start):
    days = set()
    for row in agenda_rows.get(lesson_id, []):
        try:
            days.add(date.fromisoformat(row.get("date", "")))
        except (TypeError, ValueError):
            continue
    return sorted(days) or [week_start]


def _course_sections(sections, course):
    return [section for section in sections if section["course"] == course]


# --- cross-system synchronization -------------------------------------------

def _check_sync(lesson, sections, folder, agenda, schoology, out):
    lid = lesson["lesson_id"]
    title = lesson.get("title", "")
    deck = lesson.get("deck") or {}
    editable = _text(deck.get("editable_id"))
    student = _text(deck.get("student_copy_id"))
    form_id = _text((lesson.get("turn_in") or {}).get("form_id"))

    if not editable:
        out.add(lid, "deck", "editable_deck_missing", "Record the editable deck for this lesson.")
    if not student:
        out.add(lid, "deck", "student_copy_missing",
                "Make the student-facing copy of the deck from the editable version.")
    elif student == editable:
        out.add(lid, "deck", "student_copy_is_editable",
                "Students would get the editable deck; make a separate student copy.")

    folder_rows = folder.get(lid, [])
    if not folder_rows:
        out.add(lid, "folder", "missing_in_folder", "Add this lesson to the weekly folder.")
    for row in folder_rows:
        if _norm(row.get("title")) != _norm(title):
            out.add(lid, "folder", "title_mismatch",
                    "Rename the folder copy to “%s” (now “%s”)." % (title, _text(row.get("title"))))
        if editable and _text(row.get("editable_deck_id")) != editable:
            out.add(lid, "folder", "editable_deck_mismatch",
                    "The weekly folder holds a different editable deck than the lesson record.")
        if student and _text(row.get("student_deck_id")) != student:
            out.add(lid, "folder", "student_deck_mismatch",
                    "The weekly folder holds a different student copy than the lesson record.")
        held = set(row.get("material_ids") or [])
        for material in lesson.get("materials") or []:
            if material.get("material_id") not in held:
                out.add(lid, "folder", "material_missing_in_folder",
                        "Put “%s” in the weekly folder." % _text(material.get("title")))

    course_sections = {s["section_id"] for s in _course_sections(sections, lesson.get("course"))}
    for system, rows in (("agenda", agenda.get(lid, [])), ("schoology", schoology.get(lid, []))):
        present = {_text(row.get("section_id")) for row in rows}
        for section_id in sorted(course_sections - present):
            out.add(lid, system, "missing_for_section",
                    "Add this lesson to %s for section %s." % (system.capitalize(), section_id))
        for row in rows:
            where = "%s %s" % (system.capitalize(), _text(row.get("section_id")))
            if _norm(row.get("title")) != _norm(title):
                out.add(lid, system, "title_mismatch",
                        "%s says “%s”; use “%s”." % (where, _text(row.get("title")), title))
            link = _text(row.get("deck_link_id"))
            if not link:
                out.add(lid, system, "deck_link_missing", "%s has no deck link." % where)
            elif editable and link == editable and student != editable:
                out.add(lid, system, "student_link_points_to_editable",
                        "%s links the editable deck; link the student copy." % where)
            elif student and link != student:
                out.add(lid, system, "deck_link_mismatch",
                        "%s links a different deck than this week's student copy." % where)
            row_form = _text(row.get("form_id"))
            if form_id and row_form != form_id:
                out.add(lid, system, "turn_in_link_mismatch",
                        "%s links %s Turn-in Form; link this lesson's Form."
                        % (where, "no" if not row_form else "a different"))
            if system == "schoology":
                if row.get("published") is not True:
                    out.add(lid, system, "not_published", "%s is not published." % where)
                posted = set(row.get("material_ids") or [])
                for material in lesson.get("materials") or []:
                    if (material.get("kind") in STUDENT_FACING_KINDS
                            and material.get("material_id") not in posted):
                        out.add(lid, system, "material_not_posted",
                                "Post “%s” to %s." % (_text(material.get("title")), where))


def _check_student_work(lesson, out):
    lid = lesson["lesson_id"]
    turn_in = lesson.get("turn_in")
    if not isinstance(turn_in, dict) or not _text(turn_in.get("form_id")):
        if lesson.get("collects_work", True):
            out.add(lid, "forms", "turn_in_missing", "Create or bind this lesson's Turn-in Form.")
        return
    if turn_in.get("accepting_responses") is not True:
        out.add(lid, "forms", "turn_in_closed", "Open the Turn-in Form to responses.")
    deck_revision = _text((lesson.get("deck") or {}).get("revision"))
    bound = _text(turn_in.get("deck_revision"))
    if deck_revision and bound and bound != deck_revision:
        out.add(lid, "forms", "turn_in_stale_deck",
                "The Turn-in Form was built for an earlier deck revision; recheck its questions.")


# --- intellectual structure --------------------------------------------------

def _deck_aspects(lesson):
    deck = lesson.get("deck") or {}
    slides = deck.get("slides")
    if not slides:
        return None, None
    release = validate_release(tuple(_text(slide) for slide in slides),
                               revision=_text(deck.get("revision")),
                               prompt0=_text(deck.get("prompt0")),
                               prompt2=_text(deck.get("prompt2")))
    return [entry["aspect"] for entry in release["critical_aspects"]], release


def _check_variation(lessons, out):
    introduced = set()
    for lesson in sorted(lessons, key=lambda item: (item.get("sequence", 0), item["lesson_id"])):
        lid = lesson["lesson_id"]
        intended = [_aspect_key(aspect) for aspect in lesson.get("intended_critical_aspects") or []]
        if not intended:
            out.add(lid, "plan", "critical_aspects_undeclared",
                    "Name the intended critical aspects so edits can be checked against them.")
        actual, release = _deck_aspects(lesson)
        if actual is not None:
            for aspect in intended:
                if aspect not in actual:
                    out.add(lid, "deck", "critical_aspect_dropped",
                            "The deck no longer teaches the intended critical aspect “%s”." % aspect)
            for aspect in actual:
                if aspect not in intended:
                    out.add(lid, "deck", "critical_aspect_unplanned",
                            "The deck adds “%s”, which the plan does not intend; keep or remove it."
                            % aspect, severity="decide")
            for issue in release["issues"]:
                if issue.startswith("critical_aspect_phase_missing:"):
                    _, aspect, phase = issue.split(":", 2)
                    out.add(lid, "deck", "variation_pattern_missing",
                            "“%s” has no %s step; restore it so the aspect is varied against "
                            "an invariant background." % (aspect, phase.replace("_", " ")))
                elif issue.startswith("critical_aspect_block_missing:"):
                    continue  # already reported as a dropped aspect when intended
                elif issue == "agenda_prompt_contract_unavailable":
                    continue  # prompt binding belongs to the release check, not preflight
                else:
                    out.add(lid, "deck", "deck_release_" + issue,
                            "The deck fails the release contract (%s)." % issue.replace("_", " "))
        for prerequisite in lesson.get("builds_on") or []:
            if _aspect_key(prerequisite) not in introduced:
                out.add(lid, "sequence", "prerequisite_not_introduced",
                        "This lesson builds on “%s”, which no earlier lesson this week or "
                        "the prior-weeks list introduces." % _aspect_key(prerequisite))
        introduced.update(actual if actual is not None else intended)


def _seed_prior_aspects(snapshot, lessons):
    prior = [_aspect_key(a) for a in snapshot.get("prior_critical_aspects") or []]
    if not prior:
        return lessons
    seed = {"lesson_id": "~prior", "sequence": -1, "intended_critical_aspects": prior}
    return [seed] + lessons


def _check_flow(lesson, class_minutes, out):
    lid = lesson["lesson_id"]
    flow = lesson.get("flow")
    if not isinstance(flow, list) or not flow:
        out.add(lid, "plan", "flow_unplanned",
                "Lay out the %d-minute block (segments, minutes, phase)." % class_minutes)
        return
    total = 0
    phases = []
    for index, segment in enumerate(flow, 1):
        minutes = segment.get("minutes")
        name = _text(segment.get("segment")) or "segment %d" % index
        if type(minutes) is not int or minutes <= 0:
            out.add(lid, "plan", "flow_segment_untimed", "Give “%s” a time." % name)
            continue
        total += minutes
        if segment.get("mode") in PASSIVE_MODES and minutes > LONG_PASSIVE_MINUTES:
            out.add(lid, "plan", "flow_long_passive",
                    "“%s” is %d passive minutes; break it with a student task." % (name, minutes))
        if segment.get("phase") in PHASES:
            phases.append(segment["phase"])
    if total > class_minutes:
        out.add(lid, "plan", "flow_over_time",
                "The plan runs %d minutes in a %d-minute block; cut or move %d minutes."
                % (total, class_minutes, total - class_minutes))
    elif total < class_minutes - UNDER_TIME_SLACK:
        out.add(lid, "plan", "flow_under_time",
                "The plan fills %d of %d minutes; add the missing work or a planned extension."
                % (total, class_minutes))
    seen = [phase for phase in PHASES if phase in phases]
    for phase in PHASES:
        if phase not in phases:
            out.add(lid, "plan", "flow_phase_missing",
                    "No segment carries the “%s” phase." % phase)
    first_positions = [phases.index(phase) for phase in seen]
    if first_positions != sorted(first_positions):
        out.add(lid, "plan", "flow_phase_order",
                "Phases run out of whole → explore → variation → relationships → whole order.")


# --- print packet, conversions, supplies ------------------------------------

def _copies(per, sections, group_size):
    if per == "student":
        enrolled = sum(section["enrollment"] for section in sections)
        return enrolled + max(MIN_SPARE_COPIES, math.ceil(enrolled * SPARE_COPY_RATE))
    if per == "group":
        size = group_size if type(group_size) is int and group_size > 0 else 4
        return sum(math.ceil(section["enrollment"] / size) for section in sections)
    if per == "class":
        return len(sections)
    if per == "teacher":
        return 1
    return None


def _day_label(day):
    return "%s %s/%s" % (WEEKDAYS[day.weekday()], day.month, day.day)


def _print_and_convert(lesson, sections, first_day, out, packet, conversions):
    lid = lesson["lesson_id"]
    course_sections = _course_sections(sections, lesson.get("course"))
    for material in lesson.get("materials") or []:
        title = _text(material.get("title")) or _text(material.get("material_id"))
        items = material.get("items") or []
        responses = {item.get("response") for item in items if isinstance(item, dict)}
        if (material.get("print") and material.get("kind") == "worksheet" and items
                and responses <= TEXT_RESPONSES):
            form_id = _text((lesson.get("turn_in") or {}).get("form_id"))
            target = "linked_response" if form_id else "slide_text_boxes"
            conversions.append({
                "lesson_id": lid, "material_id": material.get("material_id"), "title": title,
                "target": target,
                "items": [{"prompt": _text(item.get("prompt")), "response": item.get("response")}
                          for item in items],
                "saves_copies": _copies(material.get("per", "student"), course_sections,
                                        material.get("group_size")) or 0,
            })
        if not material.get("print"):
            continue
        if not _text(material.get("file_revision")):
            out.add(lid, "folder", "print_source_missing",
                    "“%s” needs printing but has no final file revision to print from." % title)
        copies = _copies(material.get("per", "student"), course_sections, material.get("group_size"))
        if copies is None:
            out.add(lid, "plan", "print_quantity_unknown",
                    "Say whether “%s” is per student, group, class or teacher." % title)
            continue
        pages = material.get("pages") if type(material.get("pages")) is int else 1
        packet.append({
            "needed": first_day.isoformat(), "day": _day_label(first_day), "lesson_id": lid,
            "material_id": material.get("material_id"), "title": title, "copies": copies,
            "pages": pages, "double_sided": bool(material.get("double_sided")),
            "color": bool(material.get("color")), "lab": material.get("kind") == "lab_sheet",
            "paper_reason": sorted(responses & PAPER_RESPONSES),
        })


def _supplies(lesson, sections, first_day, out, totals):
    lid = lesson["lesson_id"]
    course_sections = _course_sections(sections, lesson.get("course"))
    for supply in lesson.get("supplies") or []:
        item = _text(supply.get("item"))
        quantity = supply.get("quantity")
        if not item:
            continue
        if not isinstance(quantity, (int, float)) or isinstance(quantity, bool) or quantity <= 0:
            out.add(lid, "plan", "supply_quantity_missing", "Give a quantity for “%s”." % item)
            continue
        multiplier = _copies(supply.get("per", "class"), course_sections, supply.get("group_size"))
        if supply.get("per", "class") == "student":
            multiplier = sum(section["enrollment"] for section in course_sections)
        if multiplier is None:
            out.add(lid, "plan", "supply_basis_unknown",
                    "Say whether “%s” is per student, group, class or teacher." % item)
            continue
        unit = _text(supply.get("unit"))
        key = (_norm(item), unit.casefold())
        entry = totals.setdefault(key, {
            "item": item, "unit": unit, "quantity": 0, "needed": first_day.isoformat(),
            "day": _day_label(first_day), "grocery": bool(supply.get("grocery")),
            "on_hand": supply.get("on_hand"), "lessons": [],
        })
        entry["quantity"] += quantity * multiplier
        entry["grocery"] = entry["grocery"] or bool(supply.get("grocery"))
        if first_day.isoformat() < entry["needed"]:
            entry["needed"], entry["day"] = first_day.isoformat(), _day_label(first_day)
        if lid not in entry["lessons"]:
            entry["lessons"].append(lid)


def run_preflight(snapshot):
    """Return exceptions plus the ready-to-use packet, conversions and list."""
    _validate(snapshot)
    week_start = date.fromisoformat(snapshot["week_start"])
    class_minutes = snapshot.get("class_minutes", 90)
    sections = snapshot["sections"]
    lessons = snapshot["lessons"]
    systems = {name: _by_lesson(snapshot["systems"][name]) for name in SYSTEMS}
    out = _Exceptions()
    known = {lesson["lesson_id"] for lesson in lessons}
    for name in SYSTEMS:
        for lid in systems[name]:
            if lid not in known:
                out.add(lid, name, "orphan_entry",
                        "%s has an entry for a lesson that is not in this week's plan; "
                        "remove it or add the lesson." % name.capitalize())

    packet, conversions, totals = [], [], OrderedDict()
    for lesson in lessons:
        first_day = _lesson_days(lesson["lesson_id"], systems["agenda"], week_start)[0]
        _check_sync(lesson, sections, systems["folder"], systems["agenda"],
                    systems["schoology"], out)
        _check_student_work(lesson, out)
        _check_flow(lesson, class_minutes, out)
        _print_and_convert(lesson, sections, first_day, out, packet, conversions)
        _supplies(lesson, sections, first_day, out, totals)
    _check_variation(_seed_prior_aspects(snapshot, lessons), out)

    packet.sort(key=lambda row: (row["needed"], row["lesson_id"], row["title"]))
    supplies = []
    for entry in totals.values():
        on_hand = entry.pop("on_hand")
        need = entry["quantity"]
        if isinstance(on_hand, (int, float)) and not isinstance(on_hand, bool):
            need = max(0, need - on_hand)
        entry["to_get"] = math.ceil(need) if need != int(need) else int(need)
        entry["quantity"] = math.ceil(entry["quantity"])
        supplies.append(entry)
    supplies.sort(key=lambda row: (not row["grocery"], row["needed"], _norm(row["item"])))
    exceptions = sorted(out.items, key=lambda e: (e["severity"] != "fix", e["lesson_id"],
                                                  e["system"], e["code"], e["action"]))
    body = {
        "schema_version": RESULT_SCHEMA,
        "week_id": snapshot["week_id"],
        "status": "READY" if not exceptions else "NEEDS_ATTENTION",
        "exceptions": exceptions,
        "print_packet": packet,
        "conversions": conversions,
        "supplies": [row for row in supplies if row["to_get"] > 0],
        "external_writes": 0,
        "live_model_calls": 0,
    }
    digest = content_hash(body)
    body["content_hash"] = digest
    body["stable_id"] = deterministic_id("week-preflight", {"week": snapshot["week_id"],
                                                            "content_hash": digest})
    return body


def render_text(result, lesson_titles=None):
    """Compact teacher view: exceptions, then what to print and buy."""
    titles = lesson_titles or {}
    lines = ["Week %s preflight: %s" % (result["week_id"],
             "ready to run" if result["status"] == "READY"
             else "%d to fix" % len(result["exceptions"]))]
    if result["exceptions"]:
        lines.append("")
        lines.append("Fix before Monday")
        for item in result["exceptions"]:
            mark = "?" if item["severity"] == "decide" else "-"
            lines.append("%s [%s · %s] %s" % (mark, titles.get(item["lesson_id"], item["lesson_id"]),
                                              item["system"], item["action"]))
    if result["print_packet"]:
        lines.append("")
        lines.append("Monday print/lab packet")
        for row in result["print_packet"]:
            flags = [flag for flag, on in (("2-sided", row["double_sided"]), ("color", row["color"]),
                                           ("lab", row["lab"])) if on]
            lines.append("- %s · %s ×%d (%dp%s)" % (row["day"], row["title"], row["copies"],
                         row["pages"], ", " + ", ".join(flags) if flags else ""))
    if result["supplies"]:
        lines.append("")
        lines.append("Supply / grocery list")
        for row in result["supplies"]:
            lines.append("- %s%s %s%s (by %s)" % ("[grocery] " if row["grocery"] else "",
                         row["to_get"], row["unit"] + " " if row["unit"] else "", row["item"],
                         row["day"]))
    if result["conversions"]:
        lines.append("")
        lines.append("Ready digital alternatives (same prompts; paper copy stays in the packet "
                     "until you choose)")
        for row in result["conversions"]:
            lines.append("- %s → %s (%d prompts, saves %d copies)" % (
                row["title"], row["target"].replace("_", " "), len(row["items"]),
                row["saves_copies"]))
    return "\n".join(lines) + "\n"
