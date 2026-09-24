"""Tidy one course week into its single 📥 week folder.

The teacher keeps exactly one icon folder per course per week
(00 REAL ALL TURN IN / <course> / 📥 Wk NN — Topic), with each Form's own
'(File responses)' folder inside it, and one '⏰ Late — not graded' folder
there for work turned in more than two weeks late. The earlier shared Form's
script and new Forms can still create extra folders for a week. plan() lists
what would move; apply() moves exactly that previewed plan and checks each
move. An emptied extra folder goes to the Drive trash (restorable) when Drive
lets the dashboard trash it; otherwise it gets '_' in front of its name so the
teacher knows there is nothing in it.
"""
import hashlib
import json
from datetime import datetime, timezone

from . import google, work
from .agenda import norm

ICON = "📥"
LATE_FOLDER = "⏰ Late — not graded"
EMPTY_MARK = "_"
MAX_MOVES = 300
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


def _late_files(token, config, agenda, class_key, week_label):
    """IDs of every file the Student work page marks Late for this week."""
    result = work.lesson_work(token, config, agenda, class_key, week_label)
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
           "late_folder": None, "create_late": None, "late_after": None,
           "moves": [], "empty_after": [], "can_move": False, "can_trash": False,
           "problem": None, "plan_id": ""}
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

    # Work turned in more than two weeks late goes to the week's Late folder.
    closes = work.late_after(agenda, week_label)
    late_ids = set()
    if closes:
        out["late_after"] = closes["label"]
        if (now or datetime.now(timezone.utc)) > closes["at"]:
            late_ids = _late_files(token, config, agenda, class_key, week_label)
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
         sorted(e["id"] for e in out["empty_after"])]).encode("utf-8")).hexdigest()[:24]

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
    if not ok and (out["moves"] or out["create"] or out["empty_after"]):
        out["problem"] = ("The dashboard can't move files yet. In Drive, share '00 REAL ALL TURN IN' with "
                          "%s as an Editor, then click Tidy again." % account)
    return out


def apply(token, write_token, config, agenda, class_key, week_label, plan_id, now=None):
    """Move exactly the previewed plan into the week's one folder."""
    current = plan(token, config, agenda, class_key, week_label, now)
    if current["plan_id"] != plan_id:
        raise Changed()
    if not current["can_move"]:
        return {**current, "moved": 0, "failed": [], "trashed": [], "marked": [], "left_empty": [],
                "applied": False}
    keep_id = current["keep"]["id"] if current["keep"] else None
    if not keep_id:
        root = config.get("HUB_SUBMISSIONS_ROOT_FOLDER", "")
        course = [f for f in google.folder_children(token, root)
                  if norm(f["name"]) == norm(agenda.classes[class_key]["name"])][0]
        keep_id = google.create_folder(write_token, current["create"], course["id"])["id"]
    late_id = current["late_folder"]["id"] if current["late_folder"] else None
    if current["create_late"]:
        late_id = google.create_folder(write_token, current["create_late"], keep_id)["id"]
    moved, failed = 0, []
    for m in sorted(current["moves"], key=lambda m: m["to"] == "late"):
        target = late_id if m["to"] == "late" else keep_id
        try:
            after = google.move_item(write_token, m["id"], target, [m["from_id"]] if m["from_id"] else [])
            if target in (after.get("parents") or []):
                moved += 1
            else:
                failed.append({"name": m["name"], "why": "Drive didn't confirm the move."})
        except google.GoogleError as error:
            failed.append({"name": m["name"], "why": str(error)})
    trashed, marked, left = [], [], []
    for folder in current["empty_after"]:
        if google.folder_children(token, folder["id"]):
            left.append({**folder, "why": "It still has something in it."})
            continue
        if current["can_trash"]:
            try:
                google.trash_item(write_token, folder["id"])
                trashed.append(folder)
                continue
            except google.GoogleError:
                pass
        if folder["name"].startswith(EMPTY_MARK):
            continue
        try:
            google.rename_item(write_token, folder["id"], EMPTY_MARK + folder["name"])
            marked.append({**folder, "name": EMPTY_MARK + folder["name"]})
        except google.GoogleError:
            left.append({**folder, "why": "Only you can delete this empty folder."})
    return {**current, "keep_id": keep_id, "moved": moved, "failed": failed, "trashed": trashed,
            "marked": marked, "left_empty": left, "applied": True}
