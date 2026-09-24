"""Start feedback (use Teacher Claude): the prompt to paste into the teacher's
own Claude, so feedback costs nothing per run here and no model is called.

The instructions are the teacher's current Prompt 2 from the Agenda's
"Feedback Prompts" tab, the same every time. What changes is the list: the
students whose latest on-time deck has at least one revised answer and who
haven't had feedback for this assignment yet (from the "Scores entered" sheet).
Students are named by first name and last initial only. Teacher Claude writes
one Google Doc with exactly two pages per student, so a double-sided print
never puts one student's feedback on the back of another's.
"""
import threading
import time

from . import google
from .agenda import find_tab, norm, text

PROMPT_CACHE_SECONDS = 60
_cache = {}
_lock = threading.Lock()


def load_prompt(token, agenda):
    """(Prompt 2 text, link to its cell) from the Agenda, or (None, reason)."""
    with _lock:
        cached = _cache.get(agenda.spreadsheet_id)
        if cached and time.time() - cached[0] < PROMPT_CACHE_SECONDS:
            return cached[1]
    try:
        _, tabs = google.sheet_tabs(token, agenda.spreadsheet_id)
        tab = find_tab(tabs, "Feedback Prompts")
        if not tab:
            return None, "The Agenda has no “Feedback Prompts” tab, so there's no feedback prompt to use."
        cells = google.sheet_grid(token, agenda.spreadsheet_id, ["'%s'!A1:B20" % tab.replace("'", "''")]).get(tab, {})
    except google.GoogleError as error:
        return None, "The feedback prompt couldn't be read from the Agenda (%s)." % error
    found = None
    for row in range(1, 21):
        value = text(cells.get((row, 2))).strip()
        label = norm(text(cells.get((row, 1))))
        if norm(value).startswith("prompt 2") or ("feedback" in label and len(value) > 200):
            found = (value, "https://docs.google.com/spreadsheets/d/%s/edit#gid=%s&range=B%d"
                     % (agenda.spreadsheet_id, tabs[tab], row))
            break
    result = found or (None, "No “Prompt 2” cell was found on the Agenda's Feedback Prompts tab.")
    with _lock:
        _cache[agenda.spreadsheet_id] = (time.time(), result)
    return result


def short_names(students):
    """First name and last initial, with more of the last name only where two would match."""
    def name(student, letters):
        last = student["last"].strip()
        return ("%s %s." % (student["first"].split("(")[0].strip(), last[:letters])).strip()
    letters = {id(s): 1 for s in students}
    for _ in range(12):
        seen = {}
        for s in students:
            seen.setdefault(name(s, letters[id(s)]).lower(), []).append(s)
        clashes = [s for group in seen.values() if len(group) > 1 for s in group]
        if not clashes:
            break
        for s in clashes:
            letters[id(s)] += 1
    return {id(s): name(s, letters[id(s)]) for s in students}


def build(context, students, prompt):
    """The text to paste into Teacher Claude."""
    names = short_names(students)
    lines = [
        "Start feedback — %(class)s · %(week)s · %(assignment)s" % context,
        "",
        "HOW TO WORK THIS TIME",
        "- Open each student's deck from the links below with your Google Drive access. “Latest” is the deck "
        "to give feedback on; “Earlier” (when listed) is an earlier submission to compare it with for "
        "“How my thinking changed”. If you can't open a link, tell me which ones and I'll upload those decks.",
        "- Students are named by first name and last initial only. Use exactly that name as the heading of their "
        "feedback and don't add or guess any other name.",
        "- Completion scores are already recorded, so write only the feedback for the student (Part 2 of the "
        "instructions below). Write it for the student to read.",
        "",
        "MAKE ONE GOOGLE DOC FOR PRINTING",
        "- Name it “Feedback — %(class)s %(week)s — %(assignment)s — %(date)s”." % context,
        ("- Save it in this week's turn-in folder, “%s”: %s. If you can't create or save a Google Doc there, "
         "give me the finished document as a .docx file and I'll put it in that folder myself."
         % (context["folder"]["name"], context["folder"]["url"])) if context.get("folder") else
        "- Save it in my Google Drive and tell me where it is.",
        "- Give every student exactly two pages, in the order listed: start each student at the top of a new page "
        "with their name as the heading, and keep their feedback to two pages at most.",
        "- If a student's feedback fits on one page, add a page break after it so their second page is blank. "
        "Then add a page break so the next student starts on a new sheet. This way a double-sided print gives "
        "each student one sheet with only their own feedback.",
        "- Never put two students on the same page.",
        "- When you're done, reply with the Doc's link and the names you included.",
        "",
        "STUDENTS THIS TIME (%d)" % len(students),
    ]
    for number, s in enumerate(students, 1):
        parts = ["%d. %s — %s" % (number, names[id(s)], s["section"]),
                 "Latest: %s (%s)" % (s["url"], s["when"])]
        for earlier in s["earlier"][-1:]:  # their first submission, to compare with
            parts.append("Earlier: %s (%s)" % (earlier["url"], earlier["when"]))
        parts.append("Revised answers done: %d of %d" % (s["draft_done"], s["draft_required"]))
        lines.append(" — ".join(parts))
    lines += ["", "FEEDBACK INSTRUCTIONS (Prompt 2, from the Agenda)", prompt.strip()]
    return "\n".join(lines)
