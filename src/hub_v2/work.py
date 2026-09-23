"""Student work for one lesson, found from its Google Form responses.

The Form link in the lesson's Agenda row is the only mapping needed. Each
response says who submitted (their school email) and which files they
uploaded. A student's latest response is their current work; earlier ones are
kept as history. Every response is shown: one that can't be matched to the
roster is listed by email, never dropped.
"""
from concurrent.futures import ThreadPoolExecutor

from . import google, roster


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
        found.append({"email": str(response.get("respondentEmail") or "").lower(),
                      "response_id": response.get("responseId", ""),
                      "submitted_at": response.get("lastSubmittedTime") or response.get("createTime") or "",
                      "files": files, "typed": typed[:4]})
    return found


def identify(submission, students, domains, prefix):
    """(student, how) — by school email first, then the uploader name Forms
    adds to each file, then a name typed into the Form."""
    student = roster.match_email(submission["email"], students, domains, prefix)
    if student:
        return student, "email"
    for upload in submission["files"]:
        student = roster.match_name(roster.uploader_name(upload["name"]), students)
        if student:
            return student, "file name"
    for value in submission["typed"]:
        student = roster.match_name(value, students)
        if student:
            return student, "name typed in the Form"
    return None, None


def student_key(student):
    return (student["section"], roster.plain(student["last"]), roster.plain(student["first"]))


def group(found, students, domains=(), prefix="s."):
    """([one row per student who submitted], [roster students with nothing yet])."""
    groups = {}
    for submission in found:
        student, how = identify(submission, students, domains, prefix)
        key = ("roster",) + student_key(student) if student else (
            "unmatched", submission["email"] or submission["response_id"])
        entry = groups.setdefault(key, {"student": student, "how": how, "items": []})
        entry["items"].append(submission)
    rows = []
    for entry in groups.values():
        items = sorted(entry["items"], key=lambda s: s["submitted_at"], reverse=True)
        student, latest = entry["student"], items[0]
        rows.append({"matched": bool(student),
                     "last": student["last"] if student else "",
                     "first": student["first"] if student else "",
                     "section": student["section"] if student else "",
                     "matched_by": entry["how"], "email": latest["email"],
                     "typed": [] if student else latest["typed"],
                     "submitted_at": latest["submitted_at"], "files": latest["files"],
                     "earlier": [{"submitted_at": s["submitted_at"], "files": s["files"]}
                                 for s in items[1:]]})
    rows.sort(key=lambda r: (not r["matched"], r["section"], roster.plain(r["last"]),
                             roster.plain(r["first"]), r["email"]))
    done = {(r["section"], roster.plain(r["last"]), roster.plain(r["first"])) for r in rows if r["matched"]}
    missing = [{"last": s["last"], "first": s["first"], "section": s["section"]}
               for s in students if student_key(s) not in done]
    missing.sort(key=lambda s: (s["section"], roster.plain(s["last"]), roster.plain(s["first"])))
    return rows, missing


def describe_files(token, rows):
    """Add each file's current Drive name and link; flag trashed or unshared files."""
    files = [f for row in rows for f in row["files"] + [x for e in row["earlier"] for x in e["files"]]]
    ids = sorted({f["id"] for f in files})

    def fetch(file_id):
        try:
            return google.drive_file(token, file_id)
        except google.GoogleError as error:
            if error.status in (403, 404):
                return {"problem": "The dashboard can't open this file, but you can."}
            return {"problem": str(error)}

    with ThreadPoolExecutor(max_workers=8) as pool:
        details = dict(zip(ids, pool.map(fetch, ids)))
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


def lesson_work(token, config, agenda, class_key, week_label):
    by_class, problems = roster.load(token, config)
    students = by_class.get(class_key, [])
    domains = {d.strip().lower() for d in config.get("HUB_SCHOOL_EMAIL_DOMAINS", "").split(",") if d.strip()}
    prefix = config.get("HUB_EMAIL_USERNAME_PREFIX", "s.")
    account = google.service_account(config)
    lessons = agenda.lessons(class_key, week_label)
    if not lessons:
        problems.append("No %s row labelled '%s' was found in the Agenda."
                        % (agenda.classes[class_key]["name"], week_label))
    result = []
    for lesson in lessons:
        forms = []
        for form in lesson["forms"]:
            item = {"label": form["label"], "url": form["url"], "form_id": form["form_id"],
                    "problem": form["problem"], "responses": 0, "students": [], "missing": []}
            if form["form_id"]:
                try:
                    found = submissions(google.form_responses(token, form["form_id"]))
                except google.GoogleError as error:
                    item["problem"] = form_problem(error, account)
                else:
                    item["responses"] = len(found)
                    item["students"], item["missing"] = group(found, students, domains, prefix)
                    describe_files(token, item["students"])
            forms.append(item)
        result.append({"row": lesson["row"], "topic": lesson["topic"], "edit_url": lesson["edit_url"],
                       "forms": forms,
                       "problem": None if forms else
                       "This lesson has no Form link in its 'Turn it in' column."})
    return {"class_key": class_key, "week": week_label, "lessons": result,
            "sections": list(roster.SECTIONS[class_key]), "roster_size": len(students),
            "problems": problems}
