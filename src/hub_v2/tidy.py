"""Tidy one course week into its single 📥 week folder.

The teacher keeps exactly one icon folder per course per week
(00 REAL ALL TURN IN / <course> / 📥 Wk NN — Topic), with each Form's own
'(File responses)' folder inside it, and one '⏰ Late — not graded' folder
there for work turned in more than two weeks late. The earlier shared Form's
script and new Forms can still create extra folders for a week. plan() lists
what would move; apply() moves exactly that previewed plan and checks each
move. A verified empty duplicate gets '~delete ' in front of its name for the
teacher to remove later. The Hub never trashes folders or marks Form upload trees.

Tidy also puts each student's name and lesson in front of their turned-in
files' names ("Ana Rivera.Energy Flow - <original name>"), so a Drive search
by name finds them. The original name stays readable to the Hub (see
google.original_name), so matching, turn-in times and scoring don't change.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from . import google, work
from .agenda import norm

ICON = "📥"
LATE_FOLDER = "⏰ Late — not graded"
EMPTY_MARK = "~delete "
MAX_MOVES = 300
MAX_RENAMES = 300
CAPABILITIES = "id,name,parents,capabilities(canAddChildren,canMoveItemWithinDrive,canTrash,canRename)"


class Changed(Exception):
    """The week's folders changed after the preview; nothing was moved."""


def _is_form_folder(name):
    return str(name or "").endswith("(File responses)")


def _folder_url(folder_id):
    return "https://drive.google.com/drive/folders/" + folder_id


def _ancestors(token, item_id, cache, limit=8):
    chain, current = [], item_id
    for _ in range(limit):
        if current not in cache:
            try:
                cache[current] = google.drive_file(token, current).get("parents") or []
            except google.GoogleError:
                cache[current] = []
        parents = cache[current]
        if not parents:
            break
        current = parents[0]
        chain.append(current)
    return chain


def form_name(label, topic):
    """An assignment's own name: its Form's "📤 Name — Turn in" label, else the lesson topic."""
    named = " ".join(re.sub(r"\s*[—–-]\s*turn\s*in\s*$", "", re.sub(r"^📤\s*", "", str(label or "")),
                            flags=re.I).split())
    generic = re.fullmatch(r"(?:2nd\s+)?turn\s*(?:it\s*)?in", named, re.I)
    return named if named and not generic else " ".join(str(topic).split())


def student_prefix(row, assignment):
    """'Sabrina Del Rosario Davila.Investigative Processes - ': first name (no
    nickname) and last name, then the assignment it counts for."""
    first = re.sub(r"\([^)]*\)", " ", str(row.get("first", "")))
    return "%s.%s - " % (" ".join((first + " " + str(row.get("last", ""))).split()), assignment)


def _renames(result):
    """Each on-roster student's files counted for this week's assignments
    (current, earlier and late versions) that don't start with their name yet."""
    out, seen = [], set()
    for lesson in result["lessons"]:
        for form in lesson["forms"]:
            assignment = form_name(form.get("label"), lesson.get("topic", ""))
            if not assignment:
                continue
            for row in form["students"] + form.get("late", []):
                if not row.get("matched"):
                    continue
                prefix = student_prefix(row, assignment)
                for version in [row] + row.get("earlier", []):
                    for f in version.get("files", []):
                        if (not f.get("id") or f["id"] in seen or f.get("trashed") or f.get("problem")
                                or f.get("shown") or f.get("name", "").startswith(prefix)):
                            continue
                        seen.add(f["id"])
                        out.append({"id": f["id"], "from": f["name"], "prefix": prefix, "to": prefix + f["name"]})
    return out


def _late_files(token, config, agenda, class_key, week_label, result=None):
    """IDs of every file the Student work page marks Late for this week."""
    result = result or work.lesson_work(token, config, agenda, class_key, week_label)
    rows = [row for lesson in result["lessons"] for form in lesson["forms"]
            for row in form["students"] + form["late"]] + result["other_files"]
    versions = [row for row in rows] + [e for row in rows for e in row["earlier"]]
    return {f["id"] for v in versions if v.get("late") for f in v["files"] if f.get("id")}


def plan(token, config, agenda, class_key, week_label, now=None):
    """What Tidy would do for one course week; changes nothing."""
    class_name = agenda.classes[class_key]["name"]
    number = work.week_number(week_label)
    account = google.service_account(config)
    out = {"class_key": class_key, "week": week_label, "keep": None, "create": None,
           "late_folder": None, "create_late": None,
           "moves": [], "empty_after": [], "can_move": False, "can_trash": False,
           "renames": [], "can_rename": False, "problem": None, "plan_id": ""}
    root = config.get("HUB_SUBMISSIONS_ROOT_FOLDER", "")
    if not root or number is None:
        out["problem"] = "No turn-in root folder is configured."
        return out
    courses = [f for f in google.folder_children(token, root)
               if f.get("mimeType") == google.FOLDER and norm(f["name"]) == norm(class_name)]
    if len(courses) != 1:
        out["problem"] = "No single '%s' folder was found in the turn-in root." % class_name
        return out
    course = courses[0]
    week_folders = [f for f in google.folder_children(token, course["id"])
                    if f.get("mimeType") == google.FOLDER and work.folder_week(f["name"]) == number]
    icons = sorted((f for f in week_folders if f["name"].startswith(ICON)),
                   key=lambda f: f.get("createdTime", ""))
    loose_form_folders = [f for f in week_folders if _is_form_folder(f["name"])]
    others = [f for f in week_folders if f not in icons and f not in loose_form_folders]

    # The lesson Forms' own upload folders, wherever the dashboard can see them.
    parents_cache = {}
    form_folders = {}
    for lesson in agenda.lessons(class_key, week_label):
        for form in lesson["forms"]:
            if not form["form_id"]:
                continue
            try:
                name = google.drive_file(token, form["form_id"]).get("name", "")
            except google.GoogleError:
                name = ""
            for f in google.folders_named(token, name + " (File responses)") if name else []:
                form_folders[f["id"]] = f
            try:
                found = work.submissions(google.form_responses(token, form["form_id"]))
            except google.GoogleError:
                found = []
            for upload in [f for s in found for f in s["files"]][:10]:
                for question in _ancestors(token, upload["id"], parents_cache, limit=1):
                    meta = google.drive_file(token, question)
                    if _is_form_folder(meta.get("name")):
                        top = (meta.get("parents") or [None])[0]
                        top_meta = google.drive_file(token, top) if top else {}
                        target = top_meta if _is_form_folder(top_meta.get("name")) else meta
                        if target.get("id"):
                            form_folders[target["id"]] = target

    keep = next((i for i in icons if any(course_parent == i["id"] for ff in form_folders.values()
                                         for course_parent in ff.get("parents") or [])), None) \
        or (icons[0] if icons else None)
    if keep:
        out["keep"] = {"id": keep["id"], "name": keep["name"], "url": _folder_url(keep["id"])}
    else:
        topic = next((l["topic"] for l in agenda.lessons(class_key, week_label) if l["topic"]), "")
        out["create"] = "%s %s%s" % (ICON, week_label, " — " + topic if topic else "")

    # Work turned in after an assignment's two weeks goes to the week's Late
    # folder. No assignment can have closed before two weeks past the week's Monday.
    monday = work.week_monday(agenda, week_label)
    late_ids = set()
    result = work.lesson_work(token, config, agenda, class_key, week_label)
    if monday and (now or datetime.now(timezone.utc)).date() > monday + timedelta(days=work.LATE_AFTER_DAYS):
        late_ids = _late_files(token, config, agenda, class_key, week_label, result)
    out["renames"] = _renames(result)[:MAX_RENAMES]
    late_folder = None
    if keep:
        late_folder = next((f for f in google.folder_children(token, keep["id"])
                            if f.get("mimeType") == google.FOLDER and f["name"] == LATE_FOLDER), None)
    if late_folder:
        out["late_folder"] = {"id": late_folder["id"], "name": late_folder["name"],
                              "url": _folder_url(late_folder["id"])}

    extra = [f for f in icons + others if not keep or f["id"] != keep["id"]]
    extra_ids = {f["id"] for f in extra}
    moves = []
    for folder in extra:
        children = google.folder_children(token, folder["id"])
        if not children and folder["name"].startswith(EMPTY_MARK):
            continue  # already emptied and marked
        for child in children:
            moves.append({"id": child["id"], "name": child["name"], "from_id": folder["id"],
                          "from_name": folder["name"], "folder": child.get("mimeType") == google.FOLDER,
                          "to": "late" if child["id"] in late_ids else "week"})
        out["empty_after"].append({"id": folder["id"], "name": folder["name"], "url": _folder_url(folder["id"])})
    placed = {m["id"] for m in moves}
    for f in loose_form_folders:
        moves.append({"id": f["id"], "name": f["name"], "from_id": course["id"], "from_name": course["name"],
                      "folder": True, "to": "week"})
        placed.add(f["id"])
    for fid, f in form_folders.items():
        parent = (f.get("parents") or [None])[0]
        if fid in placed or parent in extra_ids or (keep and parent == keep["id"]):
            continue
        chain = _ancestors(token, fid, parents_cache)
        if keep and keep["id"] in chain:
            continue
        moves.append({"id": fid, "name": f.get("name", ""), "from_id": parent, "from_name": "", "folder": True,
                      "to": "week"})
        placed.add(fid)

    # Earlier shared Form uploads for this week that its script left elsewhere.
    shared, note = work.shared_submissions(token, config, class_name, number)
    if note:
        out["problem"] = note
    inside = extra_ids | {m["id"] for m in moves if m["folder"]} | ({keep["id"]} if keep else set())
    for submission in shared:
        for upload in submission["files"]:
            if upload["id"] in placed:
                continue
            chain = _ancestors(token, upload["id"], parents_cache)
            if not chain or set(chain) & inside:
                continue
            try:
                name = google.drive_file(token, upload["id"]).get("name") or upload["name"]
            except google.GoogleError:
                name = upload["name"]
            moves.append({"id": upload["id"], "name": name, "from_id": chain[0], "from_name": "",
                          "folder": False, "to": "late" if upload["id"] in late_ids else "week"})
            placed.add(upload["id"])

    # Late files anywhere else (inside the week's folder, or inside a folder
    # moving in above) move into the Late folder, after the folders move.
    for file_id in sorted(late_ids - placed):
        chain = _ancestors(token, file_id, parents_cache)
        if not chain or (late_folder and late_folder["id"] in chain):
            continue
        try:
            name = google.drive_file(token, file_id).get("name") or file_id
        except google.GoogleError:
            continue
        moves.append({"id": file_id, "name": name, "from_id": chain[0], "from_name": "", "folder": False,
                      "to": "late"})
        placed.add(file_id)
    if any(m["to"] == "late" for m in moves) and not late_folder:
        out["create_late"] = LATE_FOLDER

    out["moves"] = moves[:MAX_MOVES]
    out["plan_id"] = hashlib.sha256(json.dumps(
        [out["keep"] and out["keep"]["id"], out["create"], out["create_late"],
         sorted((m["id"], m["from_id"], m["to"]) for m in out["moves"]),
         sorted(e["id"] for e in out["empty_after"]),
         sorted((r["id"], r["to"]) for r in out["renames"])]).encode("utf-8")).hexdigest()[:24]

    # Can the dashboard's account move things here? (It needs Editor access.)
    try:
        target = google.drive_file(token, keep["id"] if keep else course["id"], CAPABILITIES)
        ok = bool((target.get("capabilities") or {}).get("canAddChildren"))
        if ok and out["moves"]:
            first = google.drive_file(token, out["moves"][0]["id"], CAPABILITIES)
            ok = bool((first.get("capabilities") or {}).get("canMoveItemWithinDrive"))
        if out["empty_after"]:
            empty = google.drive_file(token, out["empty_after"][0]["id"], CAPABILITIES)
            out["can_trash"] = bool((empty.get("capabilities") or {}).get("canTrash"))
            ok = ok and bool(out["can_trash"] or (empty.get("capabilities") or {}).get("canRename"))
    except google.GoogleError:
        ok = False
    out["can_move"] = ok
    if out["renames"]:
        try:
            first = google.drive_file(token, out["renames"][0]["id"], CAPABILITIES)
            out["can_rename"] = bool((first.get("capabilities") or {}).get("canRename"))
        except google.GoogleError:
            out["can_rename"] = False
    if (not ok and (out["moves"] or out["create"] or out["empty_after"])) or (out["renames"] and not out["can_rename"]):
        out["problem"] = ("The dashboard can't move or rename files yet. In Drive, share '00 REAL ALL TURN IN' with "
                          "%s as an Editor, then click Tidy again." % account)
    return out


def apply(token, write_token, config, agenda, class_key, week_label, plan_id, now=None):
    """Move exactly the previewed plan into the week's one folder."""
    current = plan(token, config, agenda, class_key, week_label, now)
    if current["plan_id"] != plan_id:
        raise Changed()
    renamed, rename_failed = _rename(write_token, current)
    if not current["can_move"]:
        return {**current, "moved": 0, "failed": [], "trashed": [], "marked": [], "left_empty": [],
                "renamed": renamed, "rename_failed": rename_failed, "applied": bool(renamed)}
    keep_id = current["keep"]["id"] if current["keep"] else None
    if not keep_id:
        root = config.get("HUB_SUBMISSIONS_ROOT_FOLDER", "")
        course = [f for f in google.folder_children(token, root)
                  if norm(f["name"]) == norm(agenda.classes[class_key]["name"])][0]
        keep_id = google.create_folder(write_token, current["create"], course["id"])["id"]
    late_id = current["late_folder"]["id"] if current["late_folder"] else None
    if current["create_late"]:
        try:
            late_id = google.create_folder(write_token, current["create_late"], keep_id)["id"]
        except google.GoogleError:
            late_id = None  # its late files stay put and are listed below
    moved, moved_items, failed = 0, [], []
    for m in sorted(current["moves"], key=lambda m: m["to"] == "late"):
        if m["to"] == "late" and not late_id:
            failed.append({"name": m["name"], "why": "Drive wouldn't let the dashboard make the Late folder. Make a "
                           "folder named “%s” inside this week's 📥 folder, then click Tidy again."
                           % LATE_FOLDER})
            continue
        target = late_id if m["to"] == "late" else keep_id
        try:
            after = google.move_item(write_token, m["id"], target, [m["from_id"]] if m["from_id"] else [])
            if target in (after.get("parents") or []):
                moved += 1
                source_name = m.get("from_name") or ""
                if not source_name and m.get("from_id"):
                    try:
                        source_name = google.drive_file(token, m["from_id"]).get("name") or ""
                    except google.GoogleError:
                        pass
                moved_items.append({"name": after.get("name") or m["name"],
                                    "from_name": source_name,
                                    "to": m["to"],
                                    "to_name": (current["late_folder"].get("name") if m["to"] == "late" and current.get("late_folder")
                                                else current.get("create_late") if m["to"] == "late"
                                                else current["keep"].get("name") if current.get("keep")
                                                else current.get("create") or "")})
            else:
                failed.append({"name": m["name"], "why": "Drive didn't confirm the move."})
        except google.GoogleError as error:
            failed.append({"name": m["name"], "why": str(error)})
    trashed, marked, left = [], [], []
    for folder in current["empty_after"]:
        if google.folder_children(token, folder["id"]):
            left.append({**folder, "why": "It still has something in it."})
            continue
        if _is_form_folder(folder['name']):
            left.append({**folder, 'why': 'A Form upload folder must remain in place, even when empty.'})
            continue
        if folder["name"].startswith(EMPTY_MARK):
            continue
        try:
            name = EMPTY_MARK + folder['name']
            google.rename_item(write_token, folder['id'], name)
            after = google.drive_file(token, folder['id'])
            if after.get('name') == name and not google.folder_children(token, folder['id']):
                marked.append({**folder, 'name': name})
            else:
                left.append({**folder, 'why': 'The empty-folder label was not confirmed.'})
        except google.GoogleError:
            left.append({**folder, "why": "Only you can delete this empty folder."})
    return {**current, "keep_id": keep_id, "moved": moved, "moved_items": moved_items, "failed": failed, "trashed": trashed,
            "marked": marked, "left_empty": left, "renamed": renamed, "rename_failed": rename_failed,
            "applied": True}


def _rename(write_token, current):
    """Put each previewed student's name and lesson in front of their file's
    name, and check Drive's answer. Returns (renamed count, failures)."""
    renamed, failed = 0, []
    if not current["can_rename"]:
        return 0, [{"name": r["from"], "why": "The dashboard can't rename it."} for r in current["renames"]]
    for r in current["renames"]:
        try:
            after = google.prefix_name(write_token, r["id"], r["prefix"], r["from"])
            if after.get("name") == r["to"]:
                renamed += 1
            else:
                failed.append({"name": r["from"], "why": "Drive didn't confirm the new name."})
        except google.GoogleError as error:
            failed.append({"name": r["from"], "why": str(error)})
    return renamed, failed
