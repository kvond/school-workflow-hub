"""Student work for one lesson: its Google Form responses plus everything in
that week's turn-in folder.

The Form link in the lesson's Agenda row names the assignment. Each response
says who submitted (their school email) and which files they uploaded. The
week's folder (00 ALL TURN IN / <class> / 📥 Wk NN — ...) also holds files that
reached it another way, such as uploads moved from an earlier Form; those are
matched to students by file name and shown as found in the folder.

A student's current work is their latest usable Form submission (not trashed,
not named for a different week), else their latest usable folder file. Every
other file stays visible as an earlier version. Nothing is dropped: a file that
can't be matched to the roster is listed by email or file name.
"""
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
    return {"source": "folder", "email": "", "key": item["id"], "submitted_at": item.get("createdTime", ""),
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


def turnin_folder(token, config, class_name, week_label):
    """({'id','name','url','items','parents'}, note). Walks 00 ALL TURN IN →
    the class folder → the '📥 Wk NN' folder, and lists everything inside."""
    root = config.get("HUB_SUBMISSIONS_ROOT_FOLDER", "")
    if not root:
        return None, "No turn-in root folder is configured, so only Form responses were read."
    number = week_number(week_label)

    def folder_week(name):
        match = re.search(r"\bwk\s*0*(\d+)", name, re.I)
        return int(match.group(1)) if match else None

    try:
        classes = [f for f in google.folder_children(token, root)
                   if f.get("mimeType") == google.FOLDER and norm(f["name"]) == norm(class_name)]
        if len(classes) != 1:
            return None, "No single '%s' folder in the turn-in root, so only Form responses were read." % class_name
        weeks = [f for f in google.folder_children(token, classes[0]["id"])
                 if f.get("mimeType") == google.FOLDER and number is not None
                 and folder_week(f["name"]) == number]
        if len(weeks) != 1:
            return None, ("No '📥 %s' folder in %s, so only Form responses were read." % (week_label, class_name)
                          if not weeks else "More than one '%s' folder in %s; only Form responses were read."
                          % (week_label, class_name))
        folder = weeks[0]
        items, parents, queue, seen = [], {}, [folder["id"]], 0
        while queue and seen < MAX_FOLDERS:
            current = queue.pop(0)
            seen += 1
            for item in google.folder_children(token, current):
                parents[item["id"]] = current
                if item.get("mimeType") == google.FOLDER:
                    queue.append(item["id"])
                items.append(item)
    except google.GoogleError as error:
        return None, "The turn-in folder couldn't be read (%s), so only Form responses were read." % error
    note = None if not queue else "The turn-in folder has more subfolders than were read."
    return {"id": folder["id"], "name": folder["name"], "items": items, "parents": parents,
            "url": "https://drive.google.com/drive/folders/" + folder["id"]}, note


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
    folder, folder_note = turnin_folder(token, config, class_name, week_label) if lessons else (None, None)
    items = folder["items"] if folder else []
    parents = folder["parents"] if folder else {}
    known = {i["id"]: i for i in items}

    # Read every Form in the week first, so no Form's uploads are mistaken
    # for loose folder files.
    forms = [read_form(token, form, account, items, parents)
             for lesson in lessons for form in lesson["forms"]]
    referenced = {f["id"] for form in forms for s in form["found"] for f in s["files"]}
    readable = [form for form in forms if form["form_id"]]
    other = []
    for entry in items:
        if (entry["id"] in referenced or entry.get("mimeType") not in STUDENT_WORK
                or NOT_STUDENT_WORK.search(entry.get("name", ""))):
            continue
        chain = set(_ancestors(entry["id"], parents))
        owners = [form for form in forms if form["folders"] & chain]
        if len(owners) != 1 and len(readable) == 1:
            owners = readable
        (owners[0]["found"] if len(owners) == 1 else other).append(folder_submission(entry))

    result, index = [], 0
    for lesson in lessons:
        out_forms = []
        for form in forms[index:index + len(lesson["forms"])]:
            describe_files(token, form["found"], known)
            rows, missing = group(form["found"], students, domains, prefix, number)
            out_forms.append({"label": form["label"], "url": form["url"], "form_id": form["form_id"],
                              "problem": form["problem"], "responses": form["responses"],
                              "folder_files": sum(1 for s in form["found"] if s["source"] == "folder"),
                              "students": rows, "missing": missing})
        index += len(lesson["forms"])
        result.append({"row": lesson["row"], "topic": lesson["topic"], "edit_url": lesson["edit_url"],
                       "forms": out_forms,
                       "problem": None if lesson["forms"] else
                       "This lesson has no Form link in its 'Turn it in' column."})
    describe_files(token, other, known)
    other_rows, _ = group(other, students, domains, prefix, number, with_missing=False)
    return {"class_key": class_key, "week": week_label, "lessons": result, "other_files": other_rows,
            "folder": {"name": folder["name"], "url": folder["url"], "files": sum(
                1 for i in items if i.get("mimeType") != google.FOLDER)} if folder else None,
            "folder_note": folder_note,
            "sections": list(roster.SECTIONS[class_key]), "roster_size": len(students),
            "problems": problems}


def read_form(token, form, account, items, parents):
    """One Form's responses, and the folders in the week folder that hold its uploads."""
    item = {"label": form["label"], "url": form["url"], "form_id": form["form_id"],
            "problem": form["problem"], "responses": 0, "found": [], "folders": set()}
    if not form["form_id"]:
        return item
    try:
        item["found"] = submissions(google.form_responses(token, form["form_id"]))
    except google.GoogleError as error:
        item["problem"] = form_problem(error, account)
        return item
    item["responses"] = len(item["found"])
    try:
        name = google.drive_file(token, form["form_id"]).get("name", "")
    except google.GoogleError:
        name = ""
    item["folders"] = {i["id"] for i in items if i.get("mimeType") == google.FOLDER
                       and name and i["name"] == name + " (File responses)"}
    item["folders"] |= {parents[f["id"]] for s in item["found"] for f in s["files"] if f["id"] in parents}
    return item
