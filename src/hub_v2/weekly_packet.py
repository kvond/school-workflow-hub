"""The Monday preparation packet: one private PDF for the checked weeks.

It is built only from a finished weekly preflight (weekly_preflight.status):
a cover page, then each class and meeting date with its reviewed 90-minute
flow (or "flow not yet reviewed"), print list and teacher prep, then the
supply list, then the matched student handouts in order, then a separated
TEACHER-ONLY section (keys and answer files). Nothing is invented: a flow that
was never reviewed says so, and a handout that changed since the check stops
the build.

Storage mirrors daily_feedback_packet: one manifest record in the Hub's private
state reserves one Drive ID, the PDF goes into the same verified private
packet folder (HUB_FEEDBACK_DAILY_PACKETS_FOLDER; no new folder is made), and
it is read back before it is offered. The packet's identity is its kind and a
hash of the source revisions and reviewed records it was built from, so a
rebuild with the same inputs returns the saved packet. Its file carries its
own system tag and a name without "feedback": it is never a student feedback
source, and print_ready_email never selects it.
"""
import hashlib
import re
from datetime import datetime, timezone
from io import BytesIO
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from . import daily_feedback_packet as daily, google, student_archive, weekly_preflight as preflight
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, key
from school_workflow_hub.core.runs import content_hash

KIND = "weekly_lesson_packet"
SCHEMA = "hub-weekly-lesson-packet/v1"
TAG = "hubWeeklyLessonPacket"
PDF = "application/pdf"
PACKET_ID = re.compile(r"weeklylessonpacket_[a-f0-9]{64}")
MAX_BYTES, MAX_PAGES, MAX_FILES = daily.MAX_BYTES, 1500, 120
CLASS_ORDER = [k for k, _ in preflight.agenda_module.CLASSES]


class Held(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def manifest(reply):
    """Everything the packet's content depends on, and nothing that only marks time."""
    return {"kind": KIND, "rules": preflight.RULES, "start": reply["start"], "weeks": reply["weeks_requested"],
            "lessons": [{"id": l["lesson_id"], "status": l["status"], "topic": l["topic"],
                         "dates": l["meeting_dates"], "prep": l.get("teacher_prep") or [],
                         "sources": sorted([n["id"], n.get("revision") or ""] for n in l["sources"]),
                         "reviews": {name: [r["state"], r.get("saved_at"), content_hash(r.get("text", ""))]
                                     for name, r in sorted((l.get("reviews") or {}).items())}}
                        for l in reply["lessons"]],
            "print": [[p["id"], p["text"], p["teacher_only"], p["optional"], p["basis"], p["per_section"],
                       (p.get("file") or {}).get("id"), (p.get("file") or {}).get("revision")]
                      for p in reply["print_items"]],
            "supplies": [[s["item"], s["status"], s["quantity"], s["use_dates"], s["prep_by"], s["lessons"]]
                         for s in reply["supplies"]]}


def _clean(value):
    value = str(value or "")
    for old, new in (("→", "->"), ("←", "<-"), ("✔", "[ordered]"), ("⚠️", "!"), ("⚠", "!"), ("₂", "2"),
                     ("↗", ""), ("🛒", ""), ("≥", ">="), ("≤", "<=")):
        value = value.replace(old, new)

    def fits(ch):
        try:
            ch.encode("cp1252")
            return True
        except UnicodeEncodeError:
            return False
    return " ".join("".join(ch for ch in value if fits(ch)).split())


def _day(value):
    if not value:
        return ""
    day = datetime.strptime(value, "%Y-%m-%d")
    return "%s %d" % (day.strftime("%a %b"), day.day)


def _quantity(item):
    if not item["per_section"]:
        return item["basis"]
    return ", ".join("%s: %s" % (section, count if count is not None else "?")
                     for section, count in item["per_section"].items()) + " (" + item["basis"] + ")"


def _render(reply, digest, generated_at, ranges, teacher_ranges):
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ink, muted, blue, red = map(HexColor, ("#17384B", "#526575", "#087EA4", "#9B2C2C"))
    styles = {
        "title": ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=22, leading=27, textColor=ink),
        "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=blue, spaceBefore=6),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=ink, spaceBefore=8),
        "body": ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=13, textColor=ink),
        "small": ParagraphStyle("s", fontName="Helvetica", fontSize=8, leading=10, textColor=muted),
        "warn": ParagraphStyle("w", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=red),
    }

    def p(value, style="body"):
        return Paragraph(escape(_clean(value)), styles[style])

    story = [p("Monday preparation packet", "title"), Spacer(1, 10)]
    weeks = reply["weeks"]
    story.append(p("Weeks: " + "; ".join("%s (from %s)" % (w["label"], w["monday"]) for w in weeks), "h2"))
    sections = []
    for class_key in CLASS_ORDER:
        lessons = [l for l in reply["lessons"] if l["class_key"] == class_key]
        if lessons:
            sections.append("%s (%s)" % (lessons[0]["class"], ", ".join(lessons[0]["sections"])))
    story += [p("Classes and sections: " + "; ".join(sections)),
              p("Generated: %s (America/New_York)" % generated_at),
              p("Checked: %s" % (reply.get("checked_at") or "")),
              p("Source fingerprint: %s" % digest[:16]),
              p("Lessons: " + ", ".join("%d %s" % (n, s.replace("_", " "))
                                         for s, n in sorted(reply["coverage"]["lesson_status"].items()))),
              Spacer(1, 8),
              p("Access was checked with the Hub's own account; student access is not verified. "
                "The TEACHER-ONLY section at the end holds keys and answers: keep it out of student hands.", "small"),
              PageBreak()]
    prints = {p_["id"]: p_ for p_ in reply["print_items"]}
    for class_key in CLASS_ORDER:
        lessons = [l for l in reply["lessons"] if l["class_key"] == class_key]
        if not lessons:
            continue
        story.append(p(lessons[0]["class"], "h1"))
        for lesson in lessons:
            dates = sorted({d for values in lesson["meeting_dates"].values() for d in values})
            for day in dates or [""]:
                first = not dates or day == dates[0]
                story.append(p("%s - %s - %s" % (_day(day) or lesson["week"], ", ".join(lesson["sections"]),
                                                 lesson["topic"]), "h2"))
                if lesson["status"] != "ready":
                    story.append(p("Lesson status: %s. See the Week ahead list on Today." %
                                   lesson["status"].replace("_", " "), "warn"))
                flow = (lesson.get("reviews") or {}).get("meeting_flow:" + day, {"state": "not_reviewed"})
                if flow["state"] == "verified":
                    story.append(p("90-minute flow (reviewed by %s):" % flow.get("reviewer", "")))
                    story += [p(line) for line in flow["text"].splitlines() if line.strip()]
                elif flow["state"] == "needs_re_review":
                    story.append(p("Flow needs re-review: a source changed after it was reviewed.", "warn"))
                else:
                    story.append(p("Flow not yet reviewed.", "warn"))
                items = [prints[i] for i in lesson["print_items"] if i in prints and not prints[i]["teacher_only"]]
                if not first:
                    # One lesson, one print list and prep: under its first meeting.
                    if items or lesson.get("teacher_prep"):
                        story.append(p("Print list and prep: see %s." % _day(dates[0]), "small"))
                    continue
                if items:
                    story.append(p("Print", "h2"))
                    rows = [["Item", "Quantity (basis)", "File / pages", "Sides"]]
                    for item in items:
                        file = item.get("file") or {}
                        where = ranges.get(file.get("id"))
                        rows.append([p(item["text"] + (" (optional)" if item["optional"] else ""), "small"),
                                     p(_quantity(item), "small"),
                                     p("%s%s" % (file.get("name") or "NO FILE", " - packet pages %d-%d" % where
                                                 if where else " - Word file: print it from the lesson folder"
                                                 if file.get("print_from_folder") else ""), "small"),
                                     p("as the file is set up" if file.get("print_from_folder")
                                       else file.get("sides") or "unknown", "small")])
                    table = Table(rows, colWidths=[150, 150, 150, 70], repeatRows=1)
                    table.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
                                               ("GRID", (0, 0), (-1, -1), 0.4, muted), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
                    story.append(table)
                prep = lesson.get("teacher_prep") or []
                if prep:
                    story.append(p("Teacher prep", "h2"))
                    story += [p("- %s (slide %s)" % (line["text"], line["slide"])) for line in prep]
    story += [PageBreak(), p("Supply list", "h1"),
              p("Statuses come from the Agenda and the decks. Nothing here is verified in hand.", "small")]
    rows = [["Item", "Status", "Quantity", "Lessons", "Use dates", "Prep by"]]
    for supply in reply["supplies"]:
        rows.append([p(supply["item"] + ((" (also: %s)" % ", ".join(supply["also_named"])) if supply["also_named"] else ""), "small"),
                     p(supply["status"], "small"), p(supply["quantity"], "small"),
                     p("; ".join(supply["lessons"]), "small"),
                     p(", ".join(_day(d) for d in supply["use_dates"]) or "not stated", "small"),
                     p(_day(supply["prep_by"]) if supply["prep_by"] else "", "small")])
    if len(rows) == 1:
        rows.append([p("No supplies listed.", "small"), "", "", "", "", ""])
    table = Table(rows, colWidths=[130, 75, 95, 110, 75, 55], repeatRows=1)
    table.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
                               ("GRID", (0, 0), (-1, -1), 0.4, muted), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(table)
    main = BytesIO()
    SimpleDocTemplate(main, pagesize=letter, invariant=1, title="Monday preparation packet",
                      author="School Workflow Hub", leftMargin=40, rightMargin=40, topMargin=40,
                      bottomMargin=40).build(story)
    teacher = [p("TEACHER-ONLY", "title"), Spacer(1, 8),
               p("Keys, answers and teacher-reference material. Do not hand out.", "warn"), Spacer(1, 8)]
    for item in reply["print_items"]:
        if item["teacher_only"]:
            file = item.get("file") or {}
            where = teacher_ranges.get(file.get("id"))
            teacher.append(p("%s - %s - %s%s" % (item["class"], item["lesson"], item["text"],
                                                 " (packet pages %d-%d)" % where if where else " (no file)")))
    separator = BytesIO()
    SimpleDocTemplate(separator, pagesize=letter, invariant=1, title="Teacher-only",
                      leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40).build(teacher)
    return main.getvalue(), separator.getvalue()


def _pages(raw):
    from pypdf import PdfReader
    return len(PdfReader(BytesIO(raw), strict=False).pages)


def _assemble(token, reply, digest, generated_at):
    """(pdf, pages, file hashes): handouts are read fresh and must match the check."""
    from pypdf import PdfReader, PdfWriter
    students, teachers, seen = [], [], set()
    for item in reply["print_items"]:
        file = item.get("file") or {}
        if not file.get("id") or not file.get("pages") or file["id"] in seen:
            continue
        seen.add(file["id"])
        (teachers if item["teacher_only"] else students).append(file)
    if len(students) + len(teachers) > MAX_FILES:
        raise Held("This packet has too many handouts. Choose fewer weeks.")
    downloads, total = {}, 0
    for file in students + teachers:
        meta = google.drive_file(token, file["id"], preflight.NODE_FIELDS)
        if meta.get("trashed") or preflight.revision(meta) != file.get("revision"):
            raise Held("“%s” changed since the week was checked. Run the check again before building the packet."
                       % file.get("name", "A handout"))
        raw = google.printable_pdf_bytes(token, file["id"], file["mimeType"])
        total += len(raw)
        if total > MAX_BYTES:
            raise Held("The handouts exceed the packet size limit. Choose fewer weeks.")
        downloads[file["id"]] = raw
    counts = {ident: _pages(raw) for ident, raw in downloads.items()}
    # The page ranges are printed inside the pages they count: render until
    # the main section and the separator keep the page counts assumed.
    guess = (1, 1)
    for _ in range(5):
        start, ranges, teacher_ranges = guess[0] + 1, {}, {}
        for file in students:
            ranges[file["id"]] = (start, start + counts[file["id"]] - 1)
            start += counts[file["id"]]
        start += guess[1]
        for file in teachers:
            teacher_ranges[file["id"]] = (start, start + counts[file["id"]] - 1)
            start += counts[file["id"]]
        main, separator = _render(reply, digest, generated_at, ranges, teacher_ranges)
        actual = (_pages(main), _pages(separator))
        if actual == guess:
            break
        guess = actual
    else:
        raise Held("The packet's page numbers didn't settle. Nothing was saved.")
    writer = PdfWriter()
    for part in [main] + [downloads[f["id"]] for f in students] + [separator] + [downloads[f["id"]] for f in teachers]:
        writer.append(PdfReader(BytesIO(part), strict=False), import_outline=False)
    writer.add_metadata({"/Title": "Monday preparation packet " + reply["start"],
                         "/Subject": "Weekly lesson packet; teacher-only section at the end"})
    output = BytesIO()
    writer.write(output)
    pdf = output.getvalue()
    pages = _pages(pdf)
    if len(pdf) > MAX_BYTES or pages > MAX_PAGES:
        raise Held("This weekly packet exceeds the private packet size limit. Choose fewer weeks.")
    return pdf, pages, {ident: hashlib.sha256(raw).hexdigest() for ident, raw in downloads.items()}


def _check_file(token, folder, record):
    try:
        meta = student_archive._private(google.drive_file(token, record["drive_file_id"], daily.FIELDS),
                                        parent=folder["id"])
    except student_archive.Held as error:
        raise Held("The saved weekly packet needs checking: " + str(error)) from None
    props = meta.get("properties") or {}
    if (meta.get("mimeType") != PDF or props.get(TAG) != record["id"] or props.get("manifestHash") != record["version"]
            or meta.get("md5Checksum") != record["pdf_md5"] or int(meta.get("size", -1)) != record["size"]):
        raise Held("The saved weekly packet doesn't match its manifest. Preserve it for review.")
    return meta


def _reply(record, cached):
    return {"schema": SCHEMA, "kind": KIND, "status": "ready", "id": record["id"], "version": record["version"],
            "start": record["start"], "weeks": record["weeks"], "name": record["name"], "pages": record["pages"],
            "compiled_at": record["compiled_at"], "pdf_url": "/v2/api/weekly-preflight/packet.pdf?id=" + record["id"],
            "cached": cached, "student_feedback": False, "email_eligible": False}


def _index_id(folder_id, start, weeks):
    return key("weeklylessonpackets", [folder_id, start, weeks])


def build(token, write_token, config, start, weeks, *, port=None):
    """Build (or return the saved) packet for the latest finished check."""
    if not token or not write_token:
        raise Held("Saving the packet needs the Hub's authorized Drive writer.")
    port = port if port is not None else configured_store(config)
    reply = preflight.status(config, config.get("HUB_AGENDA_SPREADSHEET_ID", ""), start, weeks, port=port)
    if reply["status"] in ("not_checked", "incomplete"):
        raise Held("Run the week check to completion first; an unfinished check can't become a packet.")
    try:
        folder = daily._location(token, config, write=True)
    except daily.Held as error:
        raise Held(str(error).replace("daily feedback documents", "weekly packet")) from None
    version = content_hash(manifest(reply))
    ident = key("weeklylessonpacket", [folder["id"], reply["start"], reply["weeks_requested"], version])
    old = port.read([ident]).documents[ident]
    if old and old.value.get("status") == "verified":
        _check_file(token, folder, old.value)
        return _reply(old.value, True)
    generated = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M")
    pdf, pages, hashes = _assemble(token, reply, version, generated)
    if old:
        record = old.value
        if record.get("schema") != SCHEMA or record.get("version") != version:
            raise Held("The weekly packet record could not be confirmed.")
    else:
        record = {"schema": SCHEMA, "kind": KIND, "id": ident, "status": "reserved", "version": version,
                  "start": reply["start"], "weeks": reply["weeks_requested"],
                  "name": "%s - Weekly lesson packet - %s.pdf" % (reply["start"], version[:10]),
                  "drive_file_id": google.archive_file_id(write_token), "folder_id": folder["id"],
                  "pdf_sha256": hashlib.sha256(pdf).hexdigest(), "pdf_md5": hashlib.md5(pdf).hexdigest(),
                  "size": len(pdf), "pages": pages, "handout_sha256": hashes, "compiled_at": now()}
        try:
            old = preflight._put(port, ident, record)
        except Conflict:
            raise Held("This packet is already being prepared. Reload before trying again.") from None
    try:
        meta = _check_file(token, folder, record)
    except google.GoogleError as error:
        if error.status != 404 or record["status"] not in ("reserved", "creating"):
            raise Held("An earlier packet upload was not confirmed. No new upload was attempted.") from None
        if record["status"] == "creating":
            raise Held("An earlier packet upload is unconfirmed; check its reserved file. No new upload was attempted.")
        if record["pdf_sha256"] != hashlib.sha256(pdf).hexdigest():
            record = {**record, "pdf_sha256": hashlib.sha256(pdf).hexdigest(), "pdf_md5": hashlib.md5(pdf).hexdigest(),
                      "size": len(pdf), "pages": pages, "handout_sha256": hashes}
        record = {**record, "status": "creating"}
        old = preflight._put(port, ident, record, old)
        problem = None
        try:
            google.archive_create(write_token, {"id": record["drive_file_id"], "name": record["name"], "mimeType": PDF,
                                                "parents": [folder["id"]],
                                                "properties": {TAG: ident, "manifestHash": version,
                                                               "weekStart": record["start"]}}, pdf)
        except google.GoogleError as failure:
            problem = str(failure)
        try:
            meta = _check_file(token, folder, record)
        except google.GoogleError:
            raise Held(problem or "Drive did not confirm the packet upload. Check its reserved file before retrying.") from None
    record = {**record, "status": "verified", "drive_created_at": meta.get("createdTime")}
    preflight._put(port, ident, record, old)
    index = _index_id(folder["id"], record["start"], record["weeks"])
    previous = port.read([index]).documents[index]
    preflight._put(port, index, {"schema": SCHEMA, "id": ident, "version": version, "observed_at": now()}, previous)
    return _reply(record, False)


def saved(config, start, weeks, *, port=None):
    """The last saved packet for these weeks, from private state only."""
    folder_id = str(config.get(daily.FOLDER_CONFIG) or "")
    if not daily.enabled(config):
        return {"status": "unconfigured"}
    port = port if port is not None else configured_store(config)
    index = port.read([_index_id(folder_id, start, weeks)]).documents[_index_id(folder_id, start, weeks)]
    if not index:
        return {"status": "not_built"}
    record = port.read([index.value["id"]]).documents[index.value["id"]]
    if not record or record.value.get("status") != "verified":
        return {"status": "not_built"}
    return _reply(record.value, True)


def pdf(token, config, ident, *, port=None):
    if not isinstance(ident, str) or not PACKET_ID.fullmatch(ident):
        raise Held("Choose one saved weekly packet.")
    try:
        folder = daily._location(token, config)
    except daily.Held as error:
        raise Held(str(error)) from None
    port = port if port is not None else configured_store(config)
    stored = port.read([ident]).documents[ident]
    if (not stored or stored.value.get("status") != "verified" or stored.value.get("folder_id") != folder["id"]
            or stored.value.get("kind") != KIND):
        raise Held("This weekly packet is not a confirmed private snapshot.")
    record = stored.value
    _check_file(token, folder, record)
    raw = google.printable_pdf_bytes(token, record["drive_file_id"], PDF)
    if hashlib.sha256(raw).hexdigest() != record["pdf_sha256"]:
        raise Held("The saved weekly packet changed. Its manifest is preserved for review.")
    return raw, record["name"]
