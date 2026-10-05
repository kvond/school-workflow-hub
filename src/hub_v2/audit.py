"""Link check: does every lesson row's Turn-in Form and deck belong to it?

For each lesson row of a class tab, read the real Drive titles of its Turn-in
Forms (the visible "Turn it in" links, the hidden _turnInURL and any "2nd turn
it in" Form) and of its decks, and flag what doesn't line up:

- a Form or deck whose title names a different week than its row;
- a hidden _turnInURL that points to a different Form than the visible link;
- a Form link the dashboard can't read (published, short or not shared);
- a lesson with a topic but no Turn-in Form.

A Form linked from two weeks' rows is listed once ("shared-form"), since a
lesson can span weeks. A deck the dashboard can't open is "deck-unreadable",
which the page counts rather than lists: its week can't be checked, but
nothing else depends on it.

Read only. Weeks named in brackets ("[CLOSED …]", "[EMPTY …]") and "No class"
weeks are skipped.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from school_workflow_hub.workflows.deck_release import pptx_slide_texts, validate_release
from . import feedback as feedback_module
from . import google
from .agenda import build_lesson, form_link, links, norm

DECK = re.compile(r"/presentation/d/([A-Za-z0-9_-]{20,})")
PUBLISHED = re.compile(r"/forms/d/e/([A-Za-z0-9_-]{20,})")
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


def _release_deck(token, deck_id, prompt0, prompt2):
    """Read-only validation of the actual exported final-deck revision."""
    try:
        meta = google.drive_file(
            token, deck_id, "id,name,mimeType,modifiedTime,version"
        )
        raw = google.deck_bytes(token, deck_id, meta.get("mimeType", ""))
        result = validate_release(
            pptx_slide_texts(raw),
            revision=str(meta.get("version") or meta.get("modifiedTime") or ""),
            prompt0=prompt0 or "",
            prompt2=prompt2 or "",
        )
        result["title"] = meta.get("name", "")
        return result
    except (google.GoogleError, ValueError) as error:
        return {
            "schema_version": "hub-deck-release/v1",
            "status": "NOT_READY",
            "revision": "",
            "critical_aspects": [],
            "inventory_cycle": "",
            "counts": {},
            "issues": ["deck_release_unreadable"],
            "warnings": [],
            "receipt": {},
            "external_writes": 0,
            "problem": str(error),
        }


def _published(token, form_ids):
    """{form id: (student-facing title, published id)} from the Forms API, so a
    published link (the kind Schoology uses) can be matched to its Form."""
    def fetch(form_id):
        try:
            info = google.form_info(token, form_id)
        except google.GoogleError:
            return ("", "")
        match = PUBLISHED.search(info.get("responderUri", "") or "")
        return ((info.get("info") or {}).get("title", ""), match.group(1) if match else "")
    ids = sorted(set(form_ids))
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
        if not topic or topic.startswith("[") or norm(topic).startswith("noclass"):
            continue
        cell = lambda key: cells.get((row, cols[key])) if cols.get(key) else None
        decks = []
        for material in lesson["materials"]:
            match = DECK.search(material["url"])
            if match and match.group(1) not in {d["id"] for d in decks}:
                decks.append({"label": material["label"], "id": match.group(1)})
        release_deck = next((d["id"] for d in decks if d["label"] == "Teacher deck"),
                            decks[0]["id"] if decks else "")
        rows.append({"row": row, "week": label, "topic": topic, "visible": _forms_in(cell("turnin")),
                     "hidden": _forms_in(cell("turnin_url")), "second": _forms_in(cell("turnin2")),
                     "decks": decks, "release_deck": release_deck})
    ids = [f["form_id"] for r in rows for f in r["visible"] + r["hidden"] + r["second"] if f["form_id"]]
    ids += [d["id"] for r in rows for d in r["decks"]]
    titles = _titles(token, ids) if ids else {}
    form_ids = [f["form_id"] for r in rows for f in r["visible"] + r["hidden"] + r["second"] if f["form_id"]]
    published = _published(token, form_ids) if form_ids else {}

    # Release checks are heavier than title checks, so run them only for the
    # current and next two planned lessons in this class. Older lessons remain
    # available in the ordinary link audit without re-exporting every deck.
    prompt0, _ = feedback_module.load_embed_prompt(token, agenda)
    prompt2, _ = feedback_module.load_prompt(token, agenda)
    current = agenda.week_for(date.today())
    active_rows = []
    if current:
        labels = [r["week"] for r in rows]
        try:
            start = labels.index(current["label"])
            active_rows = rows[start:start + 3]
        except ValueError:
            active_rows = []
    release_ids = [r["release_deck"] for r in active_rows if r["release_deck"]]
    releases = {}
    if release_ids:
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = pool.map(
                lambda deck_id: _release_deck(token, deck_id, prompt0, prompt2),
                release_ids,
            )
            releases = dict(zip(release_ids, results))

    flags = []

    def flag(r, kind, message):
        flags.append({"week": r["week"], "row": r["row"], "topic": r["topic"], "kind": kind, "message": message})

    used = {}
    for r in rows:
        number = _week(r["week"])
        forms = r["visible"] + [f for f in r["second"] if f["form_id"] not in {v["form_id"] for v in r["visible"]}]
        if not forms and not r["hidden"]:
            flag(r, "no-form", "No Turn-in Form.")
        seen = {f["form_id"] for f in forms if f["form_id"]}
        for f in forms + [f for f in r["hidden"] if not f["form_id"] or f["form_id"] not in seen]:
            if not f["form_id"]:
                flag(r, "unreadable", "The Hub can't check the “%s” link (%s)."
                     % (f["label"] or "Turn in", "a short forms.gle link" if "forms.gle" in f["url"] else "a published link"))
                continue
            title = titles.get(f["form_id"])
            if isinstance(title, dict):
                flag(r, "unreadable", "The Hub can't open the “%s” Form (deleted, or not shared with it)."
                     % (f["label"] or "Turn in"))
                continue
            named = _week(title)
            if number is not None and named is not None and named != number:
                flag(r, "other-week", "Turn-in Form is for Week %d: “%s”." % (named, title))
            if f in forms:
                used.setdefault(f["form_id"], set()).add(norm(r["week"]))
        shown = {f["form_id"] for f in r["visible"] if f["form_id"]}
        for f in r["hidden"]:
            if f["form_id"] and shown and f["form_id"] not in shown:
                title = titles.get(f["form_id"])
                flag(r, "hidden-differs", "The hidden _turnInURL column links a different Form: “%s”."
                     % (title if isinstance(title, str) else "one the Hub can't open"))
        for d in r["decks"]:
            title = titles.get(d["id"])
            if isinstance(title, dict):
                flag(r, "deck-unreadable", "The Hub can't open the %s." % d["label"])
                continue
            named = _week(title)
            if number is not None and named is not None and named != number:
                flag(r, "deck-week", "%s is for Week %d: “%s”." % (d["label"], named, title))
    for form_id, weeks in used.items():
        if len(weeks) > 1:
            linked = [r for r in rows if any(f["form_id"] == form_id for f in r["visible"] + r["second"])]
            title = titles.get(form_id)
            flags.append({"week": ", ".join(dict.fromkeys(r["week"] for r in linked)), "row": linked[0]["row"],
                          "topic": linked[0]["topic"], "kind": "shared-form",
                          "message": "One Form for these weeks: “%s”." % (title if isinstance(title, str) else form_id)})
    def form_row(f, where):
        title = titles.get(f["form_id"]) if f["form_id"] else None
        student_title, pub = published.get(f["form_id"], ("", "")) if f["form_id"] else ("", "")
        if not pub:
            match = PUBLISHED.search(f["url"])
            pub = match.group(1) if match else ""
        return {"where": where, "label": f["label"], "form_id": f["form_id"] or "", "published_id": pub,
                "title": title if isinstance(title, str) else "", "student_title": student_title}
    summary = [{"week": r["week"], "topic": r["topic"],
                "release": releases.get(r["release_deck"]) if r["release_deck"] else None,
                "forms": [form_row(f, "Turn it in") for f in r["visible"]]
                + [form_row(f, "2nd turn it in") for f in r["second"]]
                + [form_row(f, "_turnInURL") for f in r["hidden"]],
                "decks": [{"label": d["label"], "id": d["id"],
                           "title": titles.get(d["id"]) if isinstance(titles.get(d["id"]), str) else ""}
                          for d in r["decks"]]}
               for r in rows]
    return {"class": entry["name"], "rows": summary, "flags": flags, "problem": None}
