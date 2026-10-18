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

from . import feedback as feedback_module
from . import completion
from . import completion_holds
from . import google
from . import ledger
from . import work
from .agenda import build_lesson, form_link, links, norm

DECK = re.compile(r"/presentation/d/([A-Za-z0-9_-]{20,})")
PUBLISHED = re.compile(r"/forms/d/e/([A-Za-z0-9_-]{20,})")
WEEK = re.compile(r"\b(?:week|wk)\s*0*(\d{1,2})\b", re.I)


def school_today():
    """Use the same Eastern school date as the morning helper and receipts."""
    return date.fromisoformat(ledger.eastern_now()[:10])


def _week(text):
    match = WEEK.search(str(text or ""))
    return int(match.group(1)) if match else None


def _forms_in(cell):
    return [f for f in (form_link(url, label) for url, label in links(cell)) if f]


def _titles(token, ids):
    def fetch(file_id):
        try:
            meta = google.drive_file(token, file_id)
            return {"problem": "in trash", "trashed": True} if meta.get("trashed") else meta.get("name", "")
        except google.GoogleError as error:
            return {"problem": "can't be opened by the dashboard (%s)" % error}
    ids = sorted(set(ids))
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(ids, pool.map(fetch, ids)))


def _release_deck(token, deck_id, prompt0="", prompt2="", *, native_teacher=False):
    """Read-only completion coding validation; VT curriculum audit is separate."""
    stage, meta = "metadata", {}
    try:
        meta = google.drive_file(
            token, deck_id, "id,name,mimeType,trashed,modifiedTime,version"
        )
        if meta.get("trashed"):
            raise completion.Unreadable("This linked deck is in trash; it was not restored or used as a current lesson source.")
        stage = "download"
        if native_teacher:
            meta, deck = completion._teacher_template(token, deck_id)
        else:
            raw = google.deck_bytes(token, deck_id, meta.get("mimeType", ""))
            stage = "parse"
            deck = completion.read_deck(raw)
        stage = "parse"
        result = completion.readiness(
            deck,
            revision=str(meta.get("version") or meta.get("modifiedTime") or ""),
        )
        result["title"] = meta.get("name", "")
        result.update(deck_id=deck_id, scope="linked_deck", student_copies_checked=False)
        if native_teacher:
            result["_teacher_source_metadata"] = meta
        return result
    except (google.GoogleError, completion.Unreadable, ValueError) as error:
        status = getattr(error, "status", None)
        problem = ("The exact Agenda deck link could not be opened%s. This failed before file contents were read; "
                   "no file-format problem has been established." % (" (Google HTTP %s)" % status if status else "")) if stage == "metadata" else str(error)
        return {
            "schema_version": "hub-completion-readiness/v1", "workflow": "completion",
            "status": "NOT_READY", "label": "Cannot read lesson",
            "revision": "",
            "critical_aspects": [],
            "inventory_cycle": "",
            "counts": {},
            "issues": ["deck_release_unreadable"],
            "warnings": [],
            "receipt": {},
            "external_writes": 0,
            "problem": problem, "title": meta.get("name", ""), "deck_id": deck_id,
            "scope": "linked_deck", "student_copies_checked": False,
            "readability": {"stage": stage, "http_status": status, "mime": meta.get("mimeType", "")},
        }



_NATIVE_METADATA = ("id", "name", "mimeType", "trashed", "version", "modifiedTime", "parents")


def _ordinary_native_candidate(row):
    forms = row["lesson"]["forms"]
    teachers = [DECK.search(m.get("url", "")) for m in row["lesson"]["materials"]
                if m.get("label") == "Teacher deck"]
    return (len(forms) == 1 and bool(forms[0].get("form_id")) and not forms[0].get("problem")
            and len(teachers) == 1 and teachers[0] is not None
            and not row["week_policy"]["excluded"] and not row["week_policy"]["problem"])


def _ordinary_native_proof(token, row):
    """Exact ordinary teacher identity for this read-only audit, never grading."""
    fields = google.FILE_FIELDS + ",version"
    source = completion.lesson_sources(token, row["lesson"], metadata_fields=fields)
    teachers = [DECK.search(m["url"]).group(1) for m in row["lesson"]["materials"]
                if m.get("label") == "Teacher deck"]
    if source["problem"] or source["stale_links"] or len(source["decks"]) != 1:
        raise completion.Unreadable("The ordinary teacher collection is not exact.")
    deck = source["decks"][0]
    meta = deck["metadata"]
    if (deck["id"] != teachers[0] or meta.get("id") != deck["id"]
            or meta.get("mimeType") != completion.SLIDES or meta.get("trashed") is not False
            or not meta.get("version") or not meta.get("modifiedTime")):
        raise completion.Unreadable("The exact ordinary native teacher metadata is incomplete.")
    folders = []
    for folder in source["folders"]:
        value = google.drive_file(token, folder["id"], fields)
        if (value.get("id") != folder["id"] or value.get("mimeType") != google.FOLDER
                or value.get("trashed") is not False or not value.get("version")
                or not value.get("modifiedTime")):
            raise completion.Unreadable("The ordinary teacher folder metadata is incomplete.")
        folders.append(value)
    return {"source": source, "folders": folders}

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


def check(token, agenda, class_key, config=None):
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
                     "decks": decks, "release_deck": release_deck,
                     "lesson": lesson,
                     "week_policy": work.week_policy(config or {}, agenda, class_key, label)})
    ids = [f["form_id"] for r in rows for f in r["visible"] + r["hidden"] + r["second"] if f["form_id"]]
    ids += [d["id"] for r in rows for d in r["decks"]]
    titles = _titles(token, ids) if ids else {}
    form_ids = [f["form_id"] for r in rows for f in r["visible"] + r["hidden"] + r["second"] if f["form_id"]]
    published = _published(token, form_ids) if form_ids else {}

    # Release checks are heavier than title checks, so run them only for the
    # current and next two planned lessons in this class. Older lessons remain
    # available in the ordinary link audit without re-exporting every deck.
    current = agenda.week_for(school_today())
    active_rows = []
    if current:
        labels = [r["week"] for r in rows]
        try:
            start = labels.index(current["label"])
            active_rows = rows[start:start + 3]
        except ValueError:
            active_rows = []
    for r in active_rows:
        units = []
        ordinary = None
        has_binding = False
        for form in r["lesson"]["forms"]:
            fid = form.get("form_id")
            if not fid or any(u["form_id"] == fid for u in units):
                continue
            binding = None
            try:
                binding = work.form_teacher_sources(config or {}, agenda, class_key, r["week"], fid)
                source = completion.lesson_sources(token, r["lesson"], binding=binding) if binding else None
            except (ValueError, TypeError, KeyError, completion.Unreadable) as error:
                source = {"decks": [], "stale_links": [], "folders": [], "problem": str(error)}
            has_binding = has_binding or binding is not None or source is not None
            units.append({"form_id": fid, "form": form, "binding": binding, "source": source})
        if not has_binding:
            # Keep the ordinary, unregistered Agenda audit contract unchanged.
            ordinary = completion.lesson_sources(token, r["lesson"])
            units = [{"form_id": None, "binding": None, "source": ordinary}]
        else:
            for unit in units:
                if unit["source"] is None:
                    if ordinary is None:
                        ordinary = completion.lesson_sources(token, r["lesson"])
                    unit["source"] = ordinary
        if not has_binding and _ordinary_native_candidate(r):
            fid = r["lesson"]["forms"][0]["form_id"]
            if not completion_holds.check(config or {}, agenda, class_key, r["week"], fid)["held"]:
                try:
                    proof = _ordinary_native_proof(token, r)
                    units[0].update(source=proof["source"], ordinary_native_proof=proof)
                    ordinary = proof["source"]
                except (google.GoogleError, completion.Unreadable, KeyError, ValueError):
                    pass  # Without exact proof retain the ordinary export/error contract.
        for unit in units:
            unit["native_read_mode"] = "bound" if unit["binding"] else (
                "ordinary_exact" if unit.get("ordinary_native_proof") else "export")
        r["release_units"] = units
        r["form_collections"] = has_binding
        # This union is only for literal Agenda-link diagnostics. Readiness is
        # calculated and bookended for each exact Form below, never from a union.
        verified = [u["source"] for u in units if not u["source"]["problem"]]
        ids = {d["id"] for source in verified for d in source["decks"]}
        r["source_resolution"] = ordinary if not has_binding else {
            "decks": list({d["id"]: d for source in verified for d in source["decks"]}.values()),
            "folders": list({f["id"]: f for source in verified for f in source["folders"]}.values()),
            "stale_links": [d for d in (ordinary or {}).get("stale_links", []) if d["id"] not in ids],
            "problem": None}
    # Bound, exact ordinary and unproven export reads are separate operands:
    # one Form or row cannot borrow another scope's native-read eligibility.
    release_keys = list(dict.fromkeys((d["id"], u["native_read_mode"]) for r in active_rows
                    for u in r["release_units"] if not u["source"]["problem"] for d in u["source"]["decks"]))
    releases = {}
    if release_keys:
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = pool.map(lambda key: _release_deck(token, key[0], native_teacher=True)
                                          if key[1] != "export" else _release_deck(token, key[0]), release_keys)
            releases = dict(zip(release_keys, results))

    for r in active_rows:
        form_results = []
        for unit in r["release_units"]:
            binding, source, fid = unit["binding"], unit["source"], unit["form_id"]
            checks = [releases[(d["id"], unit["native_read_mode"])] for d in source["decks"]
                      if (d["id"], unit["native_read_mode"]) in releases] if not source["problem"] else []
            if binding and not source["problem"]:
                templates = [(v["deck_id"], v["_teacher_source_metadata"], None)
                             for v in checks if "_teacher_source_metadata" in v]
                if len(templates) == len(binding["teacher_ids"]):
                    try:
                        completion._registered_teacher_bookend(token, binding, source, templates)
                    except (google.GoogleError, completion.Unreadable) as error:
                        source = {**source, "problem": str(error)}
                elif not any(v["status"] != "READY" for v in checks):
                    source = {**source, "problem": "Not every exact registered teacher's consumed metadata could be verified."}
            if unit.get("ordinary_native_proof"):
                try:
                    after = _ordinary_native_proof(token, r)
                    if after != unit["ordinary_native_proof"]:
                        raise completion.Unreadable("The exact ordinary teacher collection changed while its contents were read.")
                    consumed = checks[0].get("_teacher_source_metadata") if len(checks) == 1 else None
                    expected = source["decks"][0]["metadata"]
                    if not consumed and all(v.get("status") == "READY" for v in checks):
                        raise completion.Unreadable("The consumed ordinary teacher metadata could not be verified.")
                    if consumed and any(consumed.get(k) != expected.get(k) for k in _NATIVE_METADATA):
                        raise completion.Unreadable("The consumed ordinary teacher differs from its verified source metadata.")
                except (google.GoogleError, completion.Unreadable, KeyError, ValueError) as error:
                    source = {**source, "problem": str(error)}
            # Internal consumed metadata never appears in the UI response.
            checks = [{k: v for k, v in result.items() if k != "_teacher_source_metadata"} for result in checks]
            if source["problem"]:
                result = {"status": "NOT_READY", "label": "Cannot read lesson", "issues": ["lesson_folder_unverified"],
                          "problem": source["problem"], "counts": {}, "readability": {"stage": "folder"}}
            else:
                result = dict(next((v for v in checks if v["status"] != "READY"), checks[0] if checks else {}))
            if not result:
                result = {"status": "NOT_READY", "label": "Cannot read lesson", "issues": ["deck_release_unreadable"],
                          "problem": "No readable teacher source was verified for this exact collection.",
                          "counts": {}, "readability": {"stage": "source"}}
            result.update(source_resolution=source, linked_decks=checks)
            form = unit.get("form", {})
            title = published.get(fid, ("", ""))[0] or titles.get(fid) or form.get("label") or r["topic"]
            title = title if isinstance(title, str) else form.get("label") or r["topic"]
            if re.match(r"https?://", title):
                title = "Turn-in Form"
            context = "%s · %s · %s" % (entry["name"], r["week"], title if fid else r["topic"])
            result["context"] = context
            if fid:
                result.update(form_id=fid, form_title=title, class_key=class_key, week=r["week"], scope="form_collection")
            hold_ids = [fid] if fid else list(dict.fromkeys(f["form_id"] for f in
                r["visible"] + r["second"] + r["hidden"] if f["form_id"]))
            active_holds = [h for h in (completion_holds.check(config or {}, agenda, class_key, r["week"], ident)
                            for ident in hold_ids) if h["held"]]
            if active_holds:
                result.update(linked_source_status=result["status"], status="NOT_READY", scope="assignment",
                              label=active_holds[0].get("label", "Needs check"),
                              teacher_holds=active_holds, problem=context + ": " + active_holds[0]["problem"],
                              action=context + ": " + active_holds[0]["action"])
                result["issues"] = ["teacher_readiness_hold"] + result["issues"]
            elif result["status"] == "READY":
                result["action"] = ("The linked deck's completion codes are ready; this does not verify distributed student copies. "
                                    "If their copies lack codes, run the embed-coding program on those copies, preserving answers, then rerun grading.")
            elif result.get("readability"):
                result["action"] = "%s: check the exact source link and access, then recheck. %s" % (context, result["problem"])
            elif result.get("action"):
                result["action"] = context + ": " + result["action"]
            else:
                result["action"] = ("%s: %s Run the embed-coding program on this existing deck, preserving content; "
                                    "then recheck and rerun grading. Unmarked copies are held without scores." % (context, result["problem"]))
            if binding and not source["problem"]:
                linked = {d["id"] for d in r["decks"]}
                folders = {m.group(1) for item in r["lesson"]["materials"] if item.get("label") == "Lesson folder"
                           if (m := re.search(r"/folders/([A-Za-z0-9_-]+)", item.get("url", "")))}
                absent = [d["id"] for d in source["decks"] if d["id"] not in linked]
                absent_folders = [f["id"] for f in source["folders"] if f["id"] not in folders]
                if absent or absent_folders:
                    result["agenda_link_discrepancy"] = {
                        "source_ids": absent, "folder_ids": absent_folders,
                        "reason": "Some verified registered sources are absent from the Hub's current Agenda lesson links. "
                                  "This link-display discrepancy is separate from this Form's source readiness."}
            form_results.append(result)
        if r["form_collections"]:
            failed = [v for v in form_results if v["status"] != "READY"]
            r["release_result"] = {
                "status": "NOT_READY" if failed else "READY", "label": "Needs check" if failed else "Ready",
                "scope": "form_collections", "student_copies_checked": False,
                "form_releases": form_results,
                "issues": list(dict.fromkeys(issue for v in failed for issue in v.get("issues", []))),
                "problem": " ".join(v["context"] + ": " + (v.get("problem") or v["label"]) for v in failed) or None}
        else:
            r["release_result"] = form_results[0] if form_results else None

    flags = []

    def flag(r, kind, message):
        flags.append({"week": r["week"], "row": r["row"], "topic": r["topic"], "kind": kind, "message": message})

    used = {}
    for r in rows:
        number = _week(r["week"])
        forms = r["visible"] + [f for f in r["second"] if f["form_id"] not in {v["form_id"] for v in r["visible"]}]
        if not forms and not r["hidden"] and not r["week_policy"]["excluded"]:
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
            stale = {s["id"]: s for s in r.get("source_resolution", {}).get("stale_links", [])}
            if d["id"] in stale or (isinstance(title, dict) and title.get("trashed")):
                flag(r, "stale-deck-link", "%s points to an old deck %s; it is not a required current source." %
                     (d["label"], stale.get(d["id"], {}).get("reason", "in trash")))
                continue
            if isinstance(title, dict):
                flag(r, "deck-unreadable", "The Hub can't open the %s." % d["label"])
                continue
            named = _week(title)
            if (number is not None and named is not None and named != number
                    and not r.get("source_resolution", {}).get("folders")):
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
    summary = [{"week": r["week"], "topic": r["topic"], "week_policy": r["week_policy"],
                "release": r.get("release_result"),
                "forms": [form_row(f, "Turn it in") for f in r["visible"]]
                + [form_row(f, "2nd turn it in") for f in r["second"]]
                + [form_row(f, "_turnInURL") for f in r["hidden"]],
                "decks": [{"label": d["label"], "id": d["id"],
                           "title": titles.get(d["id"]) if isinstance(titles.get(d["id"]), str) else ""}
                          for d in r["decks"]]}
               for r in rows]
    exclusions = [{"week": r["week"], "row": r["row"], "topic": r["topic"],
                   "school_year": r["week_policy"]["school_year"], "reason": r["week_policy"]["reason"]}
                  for r in rows if r["week_policy"]["excluded"]]
    problems = list(dict.fromkeys(r["week_policy"]["problem"] for r in rows if r["week_policy"]["problem"]))
    return {"class": entry["name"], "rows": summary, "flags": flags, "exclusions": exclusions,
            "problem": " ".join(problems) or None}
