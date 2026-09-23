"""Student work for one lesson: its Google Form responses plus everything in
the lesson's turn-in folders.

The Form link(s) in the lesson's Agenda row name the assignments. Each response
says who submitted (their school email) and which files they uploaded. The
lesson's turn-in folders in 00 ALL TURN IN / <class> (any folder named for the
lesson's week, and each Form's own '(File responses)' folder) also hold files
that arrived another way, such as uploads moved from an earlier Form; those
are matched to students by file name and shown as found in the folder.

Files are sorted to an assignment by where they sit, or, when one folder holds
several assignments, by the deck they were copied from. A student's current
work is their latest usable Form upload (not trashed, not named for a different
week), else their latest usable folder file; everything else stays visible as
an earlier version. Nothing is dropped: a file that can't be matched to the
roster or to an assignment is still listed.
"""
import os
import re
from concurrent.futures import ThreadPoolExecutor

from . import google, roster
from .agenda import norm

STUDENT_WORK = ("application/vnd.google-apps.presentation", "application/vnd.google-apps.document",
                "application/pdf", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.ms-powerpoint", "image/jpeg", "image/png", "image/heic")
NOT_STUDENT_WORK = re.compile(r"growth report|feedback|class summary|template|answer key", re.I)
MAX_FOLDERS = 60


def week_number(label):
    match = re.search(r"(\d+)", str(label or ""))
    return int(match.group(1)) if match else None


def named_week(file_name, lesson_week):
    """The week a file's name says it's for, when that differs from the lesson's."""
    weeks = [int(n) for n in re.findall(r"\b(?:week|wk)\s*0*(\d{1,2})\b", str(file_name or ""), re.I)]
    if lesson_week is None or not weeks or lesson_week in weeks:
        return None
    return weeks[0]


def submissions(responses):
    found = []
    for response in responses:
        files, typed = [], []
        for answer in (response.get("answers") or {}).values():
            for upload in ((answer.get("fileUploadAnswers") or {}).get("answers") or []):
                if upload.get("fileId"):
                    files.append({"id": upload["fileId"], "name": upload.get("fileName", ""),
                                  "mime": upload.get("mimeType", "")})
            for value in ((answer.get("textAnswers") or {}).get("answers") or []):
                value = str(value.get("value", "")).strip()
                if value and len(value) <= 120:
                    typed.append(value)
        found.append({"source": "form", "email": str(response.get("respondentEmail") or "").lower(),
                      "key": response.get("responseId", ""),
                      "submitted_at": response.get("lastSubmittedTime") or response.get("createTime") or "",
                      "files": files, "typed": typed[:4]})
    return found


def folder_submission(item):
    created = item.get("createdTime", "")
    return {"source": "folder", "email": "", "key": item["id"],
            "submitted_at": name_time(item.get("name"), created) or created,
            "files": [{"id": item["id"], "name": item.get("name", ""), "mime": item.get("mimeType", "")}],
            "typed": []}


def identify(submission, students, domains, prefix):
    """(student, how): school email, then the username or uploader name in a
    file's name, then a name typed into the Form."""
    student = roster.match_email(submission["email"], students, domains, prefix)
    if student:
        return student, "email"
    for upload in submission["files"]:
        student = roster.match_username(roster.file_username(upload["name"], prefix), students, prefix) \
            if prefix else None
        student = student or roster.match_name(roster.uploader_name(upload["name"]), students)
        if student:
            return student, "file name"
    for value in submission["typed"]:
        student = roster.match_name(value, students)
        if student:
            return student, "name typed in the Form"
    return None, None


def student_key(student):
    return (student["section"], roster.plain(student["last"]), roster.plain(student["first"]))


def _usable(submission):
    return not submission["files"] or any(
        not f.get("trashed") and not f.get("other_week") for f in submission["files"])


def group(found, students, domains=(), prefix="s.", lesson_week=None, with_missing=True):
    """([one row per student], [roster students with nothing yet])."""
    for submission in found:
        for f in submission["files"]:
            f["other_week"] = named_week(f.get("name"), lesson_week)
            f["from_folder"] = submission["source"] == "folder"
    groups = {}
    for submission in found:
        student, how = identify(submission, students, domains, prefix)
        key = ("roster",) + student_key(student) if student else (
            "unmatched", submission["email"] or submission["key"])
        entry = groups.setdefault(key, {"student": student, "how": how, "items": []})
        entry["items"].append(submission)
    rows = []
    for entry in groups.values():
        items = sorted(entry["items"], key=lambda s: (_usable(s), s["source"] == "form", s["submitted_at"]),
                       reverse=True)
        student, current = entry["student"], items[0]
        rows.append({"matched": bool(student),
                     "last": student["last"] if student else "",
                     "first": student["first"] if student else "",
                     "section": student["section"] if student else "",
                     "matched_by": entry["how"], "email": current["email"],
                     "typed": [] if student else current["typed"],
                     "source": current["source"], "submitted_at": current["submitted_at"],
                     "files": current["files"],
                     "earlier": [{"submitted_at": s["submitted_at"], "source": s["source"], "files": s["files"]}
                                 for s in sorted(items[1:], key=lambda s: s["submitted_at"], reverse=True)]})
    rows.sort(key=lambda r: (not r["matched"], r["section"], roster.plain(r["last"]),
                             roster.plain(r["first"]), r["email"], r["files"][0]["name"] if r["files"] else ""))
    if not with_missing:
        return rows, []
    done = {(r["section"], roster.plain(r["last"]), roster.plain(r["first"])) for r in rows if r["matched"]}
    missing = [{"last": s["last"], "first": s["first"], "section": s["section"]}
               for s in students if student_key(s) not in done]
    missing.sort(key=lambda s: (s["section"], roster.plain(s["last"]), roster.plain(s["first"])))
    return rows, missing


def folder_week(name):
    """The week number in a folder name: '📥 Wk 03 — …', 'A&P — Wk 03 — …', '… Week 03 …'."""
    match = re.search(r"\b(?:wk|week)\s*0*(\d+)\b", str(name or ""), re.I)
    return int(match.group(1)) if match else None


def lesson_folders(token, config, class_name, week_label, form_names, response_folders):
    """(roots, note): the lesson's turn-in folders inside 00 ALL TURN IN / <class>.

    A folder belongs to the lesson when its name carries the lesson's week
    number, or it is one of the lesson's Forms' own '(File responses)' upload
    folders (found by the Form's title, or by where its responses were saved).
    """
    root = config.get("HUB_SUBMISSIONS_ROOT_FOLDER", "")
    roots = {folder_id: "" for folder_id in response_folders}
    if not root:
        return roots, "No turn-in root folder is configured, so only Form uploads were read."
    number = week_number(week_label)
    wanted = {name + " (File responses)" for name in form_names if name}
    try:
        classes = [f for f in google.folder_children(token, root)
                   if f.get("mimeType") == google.FOLDER and norm(f["name"]) == norm(class_name)]
        if len(classes) != 1:
            return roots, "No single '%s' folder in the turn-in root, so only Form uploads were read." % class_name
        for f in google.folder_children(token, classes[0]["id"]):
            if f.get("mimeType") == google.FOLDER and (
                    (number is not None and folder_week(f["name"]) == number) or f["name"] in wanted):
                roots[f["id"]] = f["name"]
    except google.GoogleError as error:
        return roots, "The %s turn-in folder couldn't be read (%s)." % (class_name, error)
    if not roots:
        return roots, "No turn-in folder for %s %s was found in %s." % (class_name, week_label, class_name)
    return roots, None


def list_tree(token, roots):
    """(items, parents): everything inside the given folders, subfolders included."""
    items, parents, queue, seen = [], {}, list(roots), set()
    while queue and len(seen) < MAX_FOLDERS:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        for item in google.folder_children(token, current):
            if item["id"] in parents:
                continue
            parents[item["id"]] = current
            items.append(item)
            if item.get("mimeType") == google.FOLDER:
                queue.append(item["id"])
    return items, parents


def name_time(name, created):
    """The submission time earlier routing wrote into copies' names
    ('s.first.last — 09-16 11.29 — …', Eastern time), as UTC ISO."""
    match = re.search(r"[—-]\s*(\d{2})-(\d{2})\s+(\d{2})\.(\d{2})\s*[—-]", str(name or ""))
    if not match or not re.match(r"\d{4}", str(created or "")):
        return None
    from datetime import datetime, timedelta
    year = int(created[:4])
    month, day, hour, minute = (int(g) for g in match.groups())
    try:
        local = datetime(year, month, day, hour, minute)
    except ValueError:
        return None
    # US Eastern: daylight time from the 2nd Sunday of March to the 1st Sunday of November.
    march = datetime(year, 3, 8) + timedelta(days=(6 - datetime(year, 3, 8).weekday()) % 7)
    november = datetime(year, 11, 1) + timedelta(days=(6 - datetime(year, 11, 1).weekday()) % 7)
    offset = 4 if march <= local < november else 5
    return (local + timedelta(hours=offset)).strftime("%Y-%m-%dT%H:%M:00Z")


def deck_stem(name):
    """The deck a copy was made from: 's.x — 09-16 11.29 — Copy of ▶ LIVE — Deck - Name.pptx' → 'deck'."""
    text = re.sub(r"^\s*s\.[a-z0-9.\-]+\s+[—-]\s+\d{2}-\d{2}\s+\d{2}\.\d{2}\s+[—-]\s*", "", str(name or ""), flags=re.I)
    text = re.sub(r"\.(pptx?|pdf|docx?|key)$", "", text, flags=re.I)
    if " - " in text:
        text = text.rsplit(" - ", 1)[0]
    text = re.sub(r"^(?:\s*(?:copy of|new|live|▶|[—-])\s*)+", "", text, flags=re.I)
    return norm(text)


def _matches(stem, signature):
    """How strongly a file's deck stem matches a set of known deck names: the
    shared length when one contains the other, or a long shared beginning
    (students sometimes rename the end of their copy)."""
    best = 0
    for known in signature:
        if not known or not stem:
            continue
        if known in stem or (len(stem) >= 6 and stem in known):
            best = max(best, min(len(known), len(stem)))
            continue
        shared = len(os.path.commonprefix([known, stem]))
        if shared >= 16:
            best = max(best, shared)
    return best


def describe_files(token, submissions_list, known):
    """Fill in each file's Drive name, type, trash state and link, reusing
    metadata already read from the folder listing."""
    files = [f for s in submissions_list for f in s["files"]]
    missing = sorted({f["id"] for f in files if f["id"] not in known})

    def fetch(file_id):
        try:
            return google.drive_file(token, file_id)
        except google.GoogleError as error:
            if error.status in (403, 404):
                return {"problem": "The dashboard can't open this file, but you can."}
            return {"problem": str(error)}

    with ThreadPoolExecutor(max_workers=8) as pool:
        details = {**known, **dict(zip(missing, pool.map(fetch, missing)))}
    for f in files:
        info = details.get(f["id"], {})
        f["name"] = info.get("name") or f["name"] or "Uploaded file"
        f["mime"] = info.get("mimeType") or f["mime"]
        f["trashed"] = bool(info.get("trashed"))
        f["problem"] = info.get("problem")
        f["url"] = ("https://docs.google.com/presentation/d/%s/edit" % f["id"]
                    if f["mime"] == "application/vnd.google-apps.presentation"
                    else "https://drive.google.com/open?id=%s" % f["id"])


def form_problem(error, account):
    if error.status in (401, 403):
        return ("The dashboard can't read this Form's responses. Open the Form, choose "
                "⋮ → Add collaborators, and add %s as an editor." % account)
    if error.status == 404:
        return "This Form wasn't found. It may be deleted, or not shared with %s." % account
    return "This Form's responses couldn't be read: %s" % error


def _ancestors(item_id, parents):
    chain = []
    while item_id in parents and len(chain) < 20:
        item_id = parents[item_id]
        chain.append(item_id)
    return chain


def _metadata(token, file_ids):
    def fetch(file_id):
        try:
            return google.drive_file(token, file_id)
        except google.GoogleError as error:
            if error.status in (403, 404):
                return {"problem": "The dashboard can't open this file, but you can."}
            return {"problem": str(error)}
    ids = sorted(set(file_ids))
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(ids, pool.map(fetch, ids)))


def read_form(token, form, account):
    """One Form's responses and its Drive title (which names its upload folder)."""
    item = {"label": form["label"], "url": form["url"], "form_id": form["form_id"],
            "problem": form["problem"], "responses": 0, "found": [], "name": "", "folders": set()}
    if not form["form_id"]:
        return item
    try:
        item["found"] = submissions(google.form_responses(token, form["form_id"]))
    except google.GoogleError as error:
        item["problem"] = form_problem(error, account)
        return item
    item["responses"] = len(item["found"])
    try:
        item["name"] = google.drive_file(token, form["form_id"]).get("name", "")
    except google.GoogleError:
        pass
    return item


def _words(text):
    return {w for w in re.findall(r"[a-z]{4,}", str(text or "").lower())} - {"turn", "copy", "live", "week"}


def pair_decks(forms, decks):
    """Give each unclaimed lesson deck to the Form it most plausibly belongs to:
    the only Form with no uploads yet, or the pairing whose names share the most
    words. Ambiguous pairings are left alone."""
    import itertools
    empty = [f for f in forms if f["form_id"] and not f["signature"]]
    unclaimed = [d for d in decks if not any(_matches(d, f["signature"]) for f in forms)]
    if not empty or not unclaimed:
        return
    if len(empty) == 1 and len(unclaimed) == 1:
        empty[0]["signature"].add(unclaimed[0])
        return
    if len(empty) != len(unclaimed) or len(empty) > 4:
        return
    scored = []
    for order in itertools.permutations(unclaimed):
        scored.append((sum(len(_words(f["label"] + " " + f["name"]) & _words(d))
                           for f, d in zip(empty, order)), order))
    scored.sort(key=lambda s: s[0], reverse=True)
    if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        for form, deck in zip(empty, scored[0][1]):
            form["signature"].add(deck)


def lesson_work(token, config, agenda, class_key, week_label):
    by_class, problems = roster.load(token, config)
    students = by_class.get(class_key, [])
    domains = {d.strip().lower() for d in config.get("HUB_SCHOOL_EMAIL_DOMAINS", "").split(",") if d.strip()}
    prefix = config.get("HUB_EMAIL_USERNAME_PREFIX", "s.")
    account = google.service_account(config)
    class_name = agenda.classes[class_key]["name"]
    lessons = agenda.lessons(class_key, week_label)
    number = week_number(week_label)
    if not lessons:
        problems.append("No %s row labelled '%s' was found in the Agenda." % (class_name, week_label))
        return {"class_key": class_key, "week": week_label, "lessons": [], "other_files": [],
                "folders": [], "folder_files": 0, "folder_note": None,
                "sections": list(roster.SECTIONS[class_key]), "roster_size": len(students), "problems": problems}

    forms = [read_form(token, form, account) for lesson in lessons for form in lesson["forms"]]
    response_ids = [f["id"] for form in forms for s in form["found"] for f in s["files"]]
    known = _metadata(token, response_ids)
    upload_folders = _metadata(token, [p for info in known.values() for p in info.get("parents") or []])
    response_folders = set(upload_folders)
    for info in upload_folders.values():
        if info.get("name", "").endswith("(File responses)"):
            response_folders.update(info.get("parents") or [])

    roots, folder_note = lesson_folders(token, config, class_name, week_label,
                                        [f["name"] for f in forms], response_folders)
    try:
        items, parents = list_tree(token, roots)
    except google.GoogleError as error:
        items, parents = [], {}
        folder_note = "The turn-in folders couldn't be read (%s), so only Form uploads were read." % error
    for fid, info in list(known.items()) + list(upload_folders.items()):
        if fid not in parents and info.get("parents"):
            parents[fid] = info["parents"][0]
    names = {i["id"]: i["name"] for i in items if i.get("mimeType") == google.FOLDER}
    names.update({fid: name for fid, name in roots.items() if name})
    names.update({fid: info.get("name", "") for fid, info in upload_folders.items()})
    known = {**{i["id"]: i for i in items}, **known}

    # Which folders hold each Form's uploads: its titled folder, and wherever its
    # responses were saved (plus the Form folder around that question folder).
    for form in forms:
        mine = {fid for fid, name in names.items() if form["name"] and name == form["name"] + " (File responses)"}
        questions = {p for s in form["found"] for f in s["files"] for p in known.get(f["id"], {}).get("parents") or []}
        mine |= questions
        mine |= {p for q in questions if upload_folders.get(q, {}).get("name", "").endswith("(File responses)")
                 for p in upload_folders[q].get("parents") or []}
        form["folders"] = mine

    referenced = set(response_ids)
    readable = [form for form in forms if form["form_id"]]
    pending, other = [], []
    for entry in items:
        if (entry["id"] in referenced or entry.get("mimeType") not in STUDENT_WORK
                or NOT_STUDENT_WORK.search(entry.get("name", ""))):
            continue
        chain = set(_ancestors(entry["id"], parents))
        owners = [form for form in forms if form["folders"] & chain]
        if len(owners) == 1:
            owners[0]["found"].append(folder_submission(entry))
        elif len(readable) == 1:
            readable[0]["found"].append(folder_submission(entry))
        else:
            pending.append(entry)

    if pending:
        # Several Forms and files in shared folders: sort by the deck each file
        # was copied from, learned from each Form's own files and the lesson's deck links.
        for form in forms:
            form["signature"] = {stem for s in form["found"] for f in s["files"]
                                 for stem in [deck_stem(known.get(f["id"], {}).get("name") or f["name"])]
                                 if len(stem) >= 8}
        index = 0
        for lesson in lessons:
            lesson_forms = forms[index:index + len(lesson["forms"])]
            index += len(lesson["forms"])
            deck_ids = {m.group(1) for material in lesson["materials"]
                        for m in [re.search(r"/presentation/d/([A-Za-z0-9_-]{20,})", material["url"])] if m}
            decks = [deck_stem(info.get("name", "")) for info in _metadata(token, deck_ids).values()]
            pair_decks(lesson_forms, [d for d in decks if len(d) >= 8])
        for entry in pending:
            stem = deck_stem(entry.get("name"))
            scores = sorted(((_matches(stem, form["signature"]), i) for i, form in enumerate(forms)), reverse=True)
            if scores and scores[0][0] and (len(scores) == 1 or scores[0][0] > scores[1][0]):
                forms[scores[0][1]]["found"].append(folder_submission(entry))
            else:
                other.append(folder_submission(entry))

    describe_files(token, other, known)
    other_rows, _ = group(other, students, domains, prefix, number, with_missing=False)
    unsorted = {(r["section"], roster.plain(r["last"]), roster.plain(r["first"])) for r in other_rows if r["matched"]}
    result, index = [], 0
    for lesson in lessons:
        out_forms = []
        for form in forms[index:index + len(lesson["forms"])]:
            describe_files(token, form["found"], known)
            rows, missing = group(form["found"], students, domains, prefix, number)
            for m in missing:
                m["unsorted"] = (m["section"], roster.plain(m["last"]), roster.plain(m["first"])) in unsorted
            out_forms.append({"label": form["label"], "url": form["url"], "form_id": form["form_id"],
                              "problem": form["problem"], "responses": form["responses"],
                              "folder_files": sum(1 for s in form["found"] if s["source"] == "folder"),
                              "students": rows, "missing": missing})
        index += len(lesson["forms"])
        result.append({"row": lesson["row"], "topic": lesson["topic"], "edit_url": lesson["edit_url"],
                       "forms": out_forms,
                       "problem": None if lesson["forms"] else
                       "This lesson has no Form link in its 'Turn it in' column."})
    folder_list = [{"name": name or names.get(fid, "") or "Form upload folder",
                    "url": "https://drive.google.com/drive/folders/" + fid}
                   for fid, name in roots.items() if not _ancestors(fid, parents) or not
                   set(_ancestors(fid, parents)) & set(roots)]
    return {"class_key": class_key, "week": week_label, "lessons": result, "other_files": other_rows,
            "folders": folder_list,
            "folder_files": sum(1 for i in items if i.get("mimeType") != google.FOLDER),
            "folder_note": folder_note,
            "sections": list(roster.SECTIONS[class_key]), "roster_size": len(students),
            "problems": problems}
