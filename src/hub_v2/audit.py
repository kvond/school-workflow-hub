"""Link check: does every lesson row's Turn-in Form and deck belong to it?

For each lesson row of a class tab, read the real Drive titles of its Turn-in
Forms (the visible "Turn it in" links, the hidden _turnInURL and any "2nd turn
it in" Form) and of its decks, and flag what doesn't line up:

- a Form or deck whose title names a different week than its row;
- a hidden _turnInURL that points to a different Form than the visible link;
- one Form linked from rows of two different weeks;
- a Form link the dashboard can't read (published, short or not shared);
- a lesson with a topic but no Turn-in Form.

Read only. Weeks named in brackets ("[CLOSED …]", "[EMPTY …]") are skipped.
"""
import re
from concurrent.futures import ThreadPoolExecutor

from . import google
from .agenda import build_lesson, form_link, links, norm

DECK = re.compile(r"/presentation/d/([A-Za-z0-9_-]{20,})")
WEEK = re.compile(r"\b(?:week|wk)\s*0*(\d{1,2})\b", re.I)


def _week(text):
    match = WEEK.search(str(text or ""))
    return int(match.group(1)) if match else None


def _forms_in(cell):
    return [f for f in (form_link(url, label) for url, label in links(cell)) if f]


def _titles(token, ids):
    def fetch(file_id):
        try:
            return google.drive_file(token, file_id).get("name", "")
        except google.GoogleError as error:
            return {"problem": "can't be opened by the dashboard (%s)" % error}
    ids = sorted(set(ids))
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(ids, pool.map(fetch, ids)))


def check(token, agenda, class_key):
    """{"class", "rows": [...], "flags": [{week, row, kind, message}]} for one class tab."""
    entry = agenda.classes[class_key]
    layout, cells = entry["layout"], entry["cells"]
    if not layout:
        return {"class": entry["name"], "rows": [], "flags": [], "problem": entry["problem"]}
    cols = layout["columns"]
    rows = []
    for row, label in layout["rows"]:
        lesson = build_lesson(cells, layout, row, "")
        topic = lesson["topic"]
        if not topic or topic.startswith("["):
            continue
        cell = lambda key: cells.get((row, cols[key])) if cols.get(key) else None
        decks = []
        for material in lesson["materials"]:
            match = DECK.search(material["url"])
            if match and match.group(1) not in {d["id"] for d in decks}:
                decks.append({"label": material["label"], "id": match.group(1)})
        rows.append({"row": row, "week": label, "topic": topic, "visible": _forms_in(cell("turnin")),
                     "hidden": _forms_in(cell("turnin_url")), "second": _forms_in(cell("turnin2")), "decks": decks})
    ids = [f["form_id"] for r in rows for f in r["visible"] + r["hidden"] + r["second"] if f["form_id"]]
    ids += [d["id"] for r in rows for d in r["decks"]]
    titles = _titles(token, ids) if ids else {}

    flags = []

    def flag(r, kind, message):
        flags.append({"week": r["week"], "row": r["row"], "topic": r["topic"], "kind": kind, "message": message})

    used = {}
    for r in rows:
        number = _week(r["week"])
        forms = r["visible"] + [f for f in r["second"] if f["form_id"] not in {v["form_id"] for v in r["visible"]}]
        if not forms and not r["hidden"]:
            flag(r, "no-form", "No Turn-in Form is linked.")
        seen = {f["form_id"] for f in forms if f["form_id"]}
        for f in forms + [f for f in r["hidden"] if not f["form_id"] or f["form_id"] not in seen]:
            if not f["form_id"]:
                flag(r, "unreadable", "The “%s” link is a %s link the dashboard can't read."
                     % (f["label"] or "Turn in", "short forms.gle" if "forms.gle" in f["url"] else "published"))
                continue
            title = titles.get(f["form_id"])
            if isinstance(title, dict):
                flag(r, "unreadable", "The “%s” Form %s." % (f["label"] or "Turn in", title["problem"]))
                continue
            named = _week(title)
            if number is not None and named is not None and named != number:
                flag(r, "other-week", "The “%s” link opens a Form titled “%s”, which names Week %d."
                     % (f["label"] or "Turn in", title, named))
            if f in forms:
                used.setdefault(f["form_id"], set()).add(norm(r["week"]))
        shown = {f["form_id"] for f in r["visible"] if f["form_id"]}
        for f in r["hidden"]:
            if f["form_id"] and shown and f["form_id"] not in shown:
                title = titles.get(f["form_id"])
                flag(r, "hidden-differs", "The hidden _turnInURL opens a different Form (“%s”) than the "
                     "“Turn it in” link." % (title if isinstance(title, str) else "unknown"))
        for d in r["decks"]:
            title = titles.get(d["id"])
            if isinstance(title, dict):
                flag(r, "unreadable", "The %s deck %s." % (d["label"], title["problem"]))
                continue
            named = _week(title)
            if number is not None and named is not None and named != number:
                flag(r, "deck-week", "The %s deck is titled “%s”, which names Week %d."
                     % (d["label"], title, named))
    for form_id, weeks in used.items():
        if len(weeks) > 1:
            for r in rows:
                if any(f["form_id"] == form_id for f in r["visible"] + r["second"]):
                    title = titles.get(form_id)
                    flag(r, "shared-form", "The Form “%s” is also linked in another week's row."
                         % (title if isinstance(title, str) else form_id))
    summary = [{"week": r["week"], "topic": r["topic"],
                "forms": [titles.get(f["form_id"]) if isinstance(titles.get(f["form_id"]), str) else f["label"]
                          for f in r["visible"] + r["second"]],
                "decks": [titles.get(d["id"]) if isinstance(titles.get(d["id"]), str) else d["label"] for d in r["decks"]]}
               for r in rows]
    return {"class": entry["name"], "rows": summary, "flags": flags, "problem": None}
