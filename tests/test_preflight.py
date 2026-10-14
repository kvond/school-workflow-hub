from __future__ import annotations

import datetime as dt

import pytest

from preflight import report
from preflight.baseline import Baseline
from preflight.cli import next_week
from preflight.context import build_context
from preflight.models import Severity
from preflight.runner import run
from preflight.sources import agenda as ag
from preflight.sources.gdrive import SnapshotDrive, parse_google_url, url_mode
from preflight.sources.schoology import SnapshotSchoology
from tests import builders as b


def run_snapshot(tmp_path, config=None, baseline=None, **kw):
    root = b.make_snapshot(tmp_path / "snap", **kw)
    cfg = config or b.make_config()
    wb = ag.load_xlsx(root / "agenda.xlsx")
    ctx = build_context(cfg, wb, SnapshotDrive(root / "drive"), SnapshotSchoology(root / "schoology.json"), 8)
    return run(ctx, baseline)


def messages(result, area=None, sev=None):
    return [f.message for f in result.findings
            if (area is None or f.area == area) and (sev is None or f.severity == sev)]


# ---------------------------------------------------------------- a clean week stays quiet

def test_clean_week_has_no_blockers_or_fixes(tmp_path):
    result = run_snapshot(tmp_path)
    serious = [f for f in result.findings if f.severity >= Severity.FIX]
    assert serious == [], [f.message for f in serious]


# ---------------------------------------------------------------- calendar

def test_friday_side_contradiction_and_missing_day(tmp_path):
    row = b.week8_row(**{"Fri — plan": "10/23 — B Friday — A&P has it. Forensics does not meet.",
                         "Wed — plan": ""})
    result = run_snapshot(tmp_path, row=row)
    blockers = messages(result, "calendar", Severity.BLOCKER)
    assert any("Wed 10/21" in m and "no lesson" in m for m in blockers)
    assert any("Fri 10/23" in m and "no lesson" in m for m in blockers)
    assert any("'B Friday'" in m for m in blockers)


def test_plan_shifted_for_a_closure_that_is_not_happening(tmp_path):
    row = b.week8_row(**{"Wed — plan": "10/21\nUnit exam (moved from Fri 10/23 — in-service)"})
    result = run_snapshot(tmp_path, row=row)
    assert any("treats Fri 10/23 as a closure" in m for m in messages(result, "calendar"))


def test_lesson_on_a_closed_day(tmp_path):
    weeks_e = "Oct 19 – Oct 23, 2026\n⚠ Wed 10/21 Teacher In-Service — Schools Closed\nFri 10/23 = A Friday"
    result = run_snapshot(tmp_path, weeks_e=weeks_e)
    assert any("Wed 10/21 is closed" in m for m in messages(result, "calendar", Severity.BLOCKER))


# ---------------------------------------------------------------- links

def test_dead_deck_and_folder_fallback(tmp_path):
    other = "O" * 33
    row = b.week8_row(_teacherURL=f"https://docs.google.com/presentation/d/{other}/edit",
                      _studentURL=f"https://docs.google.com/presentation/d/{other}/copy",
                      _folderURL="https://drive.google.com/drive/folders/" + "Z" * 33)
    result = run_snapshot(tmp_path, row=row)
    blockers = messages(result, "links", Severity.BLOCKER)
    assert any("teacher deck link does not open" in m for m in blockers)
    assert any("week folder link does not open" in m and "Week 08 - History of Forensics" in m for m in blockers)
    # The deck in the folder found by name is still checked.
    assert result.ctx.courses[0].lesson_decks()


def test_reused_form_and_same_deck_two_weeks(tmp_path):
    nine = b.week8_row(Wk="Wk 09", Topic="Mock Crime Scene")
    result = run_snapshot(
        tmp_path, extra_rows=[nine],
        files_extra=[],
    )
    assert any("link the same deck" in m for m in messages(result, "links"))


def test_form_titled_for_another_week(tmp_path):
    form = "Q" * 44
    row = b.week8_row(_turnInURL=f"https://docs.google.com/forms/d/{form}/viewform")
    result = run_snapshot(tmp_path, row=row, files_extra=[
        {"id": form, "name": "Forensics — Wk 09 — Turn In", "mimeType": "application/vnd.google-apps.form",
         "parents": []}])
    assert any("turn-in form is 'Forensics — Wk 09" in m for m in messages(result, "links"))


def test_folder_hygiene(tmp_path):
    result = run_snapshot(tmp_path, files_extra=[
        {"id": "A" * 33, "name": "ZZ WRONG — old deck.pptx", "mimeType": "x", "parents": [b.FOLDER_ID]},
        {"id": "B" * 33, "name": "Week 08 — History of Forensics (VT).pptx", "mimeType": "x",
         "parents": [b.FOLDER_ID]},
    ])
    msgs = messages(result, "links", Severity.CHECK)
    assert any("Leftover file" in m for m in msgs)
    assert any("Two versions" in m for m in msgs)


# ---------------------------------------------------------------- markers

def test_inventory_mismatch_and_typographic_label(tmp_path):
    deck = list(b.GOOD_DECK)
    deck[8] = "Extra one [[NOTES:F08:X1]]\nExtra three [[NOTES:F08:X3]]\nBad [[NOTES:F08:Q–2]]"
    result = run_snapshot(tmp_path, deck_slides=deck)
    msgs = messages(result, "markers", Severity.FIX)
    assert any("inventory does not match" in m and "X2" in m and "X3" in m for m in msgs)
    assert any("typographic character" in m for m in msgs)


def test_revision_prompt_lost(tmp_path):
    deck = list(b.GOOD_DECK)
    deck[7] = deck[7][0]  # same slide, notes gone
    deck[2] = (deck[2], "teacher notes elsewhere")
    result = run_snapshot(tmp_path, deck_slides=deck)
    assert any("lost the speaker-note revision prompt" in m for m in messages(result, "markers"))


# ---------------------------------------------------------------- VT structure

def test_rule_given_away_and_order(tmp_path):
    deck = list(b.GOOD_DECK)
    filled = "Critical aspect: contact leaves a trace\nFinish this sentence as a rule: when two things touch, a trace is left."
    deck.insert(5, filled)
    deck[4], deck[6] = deck[6], deck[4]  # pattern break now before the rule
    result = run_snapshot(tmp_path, deck_slides=deck)
    msgs = messages(result, "vt")
    assert any("already filled in" in m for m in msgs)
    assert any("comes before" in m for m in msgs)


def test_missing_steps_and_loose_labels(tmp_path):
    deck = ["CRITICAL ASPECT: LOCARD'S PRINCIPLE\nEvery contact leaves a trace. [[NOTES:F08:A]]",
            "CRITICAL ASPECT — CONTRAST\nBuilding up vs tearing down"]
    result = run_snapshot(tmp_path, deck_slides=deck)
    msgs = messages(result, "vt", Severity.FIX)
    assert any("not in the exact 'Critical aspect: <name>' form" in m for m in msgs)
    assert any("missing Build a Rule, Pattern Break" in m for m in msgs)
    assert any("no Concept Bank" in m for m in msgs)


def test_editing_notes_but_not_markers(tmp_path):
    deck = list(b.GOOD_DECK)
    deck[3] = ("Critical aspect: contact leaves a trace\nCASE A — gloves\nCASE B — none\n"
               "[added — students usually say gloves stop everything]", "this should compare two scenes instead")
    result = run_snapshot(tmp_path, deck_slides=deck)
    msgs = messages(result, "vt")
    assert any("editing note visible" in m and "[added" in m for m in msgs)
    assert any("unfinished edit request" in m for m in msgs)
    assert not any("[DRAFT:" in m for m in msgs)


def test_deck_dated_for_another_week(tmp_path):
    deck = list(b.GOOD_DECK) + ["MEETING 4 — Tue 10/13: revise\nExam Thu 10/15. No class Fri 10/16."]
    result = run_snapshot(tmp_path, deck_slides=deck)
    assert any("is dated 10/13, 10/15, 10/16" in m for m in messages(result, "vt"))


def test_baseline_catches_a_deleted_step(tmp_path):
    base = Baseline(str(tmp_path / "baseline.json"))
    run_snapshot(tmp_path / "a", baseline=base)
    deck = [s for i, s in enumerate(b.GOOD_DECK) if i != 5]  # pattern break deleted
    result = run_snapshot(tmp_path / "b", baseline=base, deck_slides=deck)
    assert any("lost since the last preflight" in m and "Pattern break" in m for m in messages(result, "vt"))


# ---------------------------------------------------------------- flow

def test_deck_too_long_for_its_days(tmp_path):
    boxes = [f"Task {i} [[NOTES:F08:T{i}]]\n[[NOTES:F08:U{i}]]" for i in range(60)]
    row = b.week8_row(**{"Wed — plan": "", "Fri — plan": ""})
    result = run_snapshot(tmp_path, deck_slides=boxes, row=row)
    assert any("minutes of student work" in m for m in messages(result, "flow", Severity.FIX))


def test_crammed_day(tmp_path):
    row = b.week8_row(**{"Mon — plan": "10/19\nReview + History webquest + Mock trial + Timeline + Quiz"})
    result = run_snapshot(tmp_path, row=row)
    assert any("5 separate activities" in m for m in messages(result, "flow"))


# ---------------------------------------------------------------- print, supplies

def test_print_packet_and_supplies(tmp_path):
    deck = list(b.GOOD_DECK) + [
        "TEACHER REFERENCE — not projected\nTO PRINT\nTrace evidence worksheet · 1 per student\n"
        "Evidence cards · 1 per group\nCRITICAL ASPECTS\nBUY AHEAD\n• Masking tape and a lint roller — demo",
        "SECOND VOTE\nCircle one:\nGUILTY\nNOT GUILTY",
    ]
    result = run_snapshot(tmp_path, deck_slides=deck)
    items = {p.item: p.copies for p in result.prints}
    assert items["Trace evidence worksheet"] == "24"
    assert items["Evidence cards"] == "6 (per group)"
    assert any(k.startswith("Paper for slide") for k in items)
    sup = {(s.item.lower(), s.status) for s in result.supplies}
    assert ("carrots", "buy") in sup and ("trays", "pull") in sup
    assert ("yeast", "on-order") in sup and ("tape", "buy") in sup
    assert ("lint roller", "buy") in sup


# ---------------------------------------------------------------- Schoology

def test_schoology_drift(tmp_path):
    other = "S" * 33
    cfg = b.make_config(sections=["111"])
    sch = {"111": [
        {"kind": "assignment", "title": "Wk 08 History deck", "due": "2026-10-19 08:00:00",
         "urls": [f"https://docs.google.com/presentation/d/{other}/copy"], "published": False},
    ]}
    result = run_snapshot(tmp_path, config=cfg, schoology=sch, files_extra=[
        {"id": other, "name": "Copy of History of Forensics (old)",
         "mimeType": "application/vnd.google-apps.presentation", "parents": []}])
    msgs = messages(result, "sync")
    assert any("not the deck in the Agenda" in m for m in msgs)
    assert any("unpublished" in m for m in msgs)
    assert any("no student deck" in m for m in msgs)
    assert any("no turn-in form" in m for m in msgs)


def test_lms_wording(tmp_path):
    deck = list(b.GOOD_DECK) + ["Upload your timeline to Google Classroom."]
    result = run_snapshot(tmp_path, deck_slides=deck)
    assert any("Google Classroom" in m for m in messages(result, "sync"))


# ---------------------------------------------------------------- report & helpers

def test_report_lists_only_exceptions(tmp_path):
    result = run_snapshot(tmp_path)
    md = report.markdown(result)
    assert "Fix before class" not in md
    assert "## Supplies" in md and "Carrots" in md
    data = report.to_json(result)
    assert data["week"] == "Wk 08"


def test_url_helpers():
    assert parse_google_url("https://docs.google.com/presentation/d/" + "a" * 30 + "/copy") == ("presentation", "a" * 30)
    assert parse_google_url("https://drive.google.com/drive/folders/" + "b" * 30) == ("folder", "b" * 30)
    assert parse_google_url("https://example.com") is None
    assert url_mode("https://docs.google.com/presentation/d/x/copy") == "copy"


def test_next_week_picks_the_coming_monday(tmp_path):
    root = b.make_snapshot(tmp_path / "s")
    weeks = ag.parse_weeks(ag.load_xlsx(root / "agenda.xlsx").tab(b.WEEKS_TAB))
    assert next_week(weeks, dt.date(2026, 10, 15)) == 8
    assert next_week(weeks, dt.date(2026, 10, 17)) == 8
    assert weeks[7].closures == {dt.date(2026, 10, 16): "In-Service — Schools Closed"}
    assert weeks[8].friday_side == "A"


@pytest.mark.parametrize("label,n", [("Wk 08", 8), ("W08b", 8), ("Week 8", 8), ("Wk 39 — last", 39), ("x", None)])
def test_week_number(label, n):
    assert ag.week_number(label) == n
