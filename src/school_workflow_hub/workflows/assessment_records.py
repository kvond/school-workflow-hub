"""Version-bound assessment packets validated against the captured submission.

A packet carries one reviewed assessment of one student's actual writing: the
distributed marker inventory, a judgment and quoted evidence for every required
item, conceptual/explanation levels, and the student report. Nothing here reads
or writes any external system. Every evidence excerpt must be a literal
substring of the freshly captured source text, so a document that changed since
the review can never be recorded as assessed with older evidence, and the
completion arithmetic is the existing Prompt-1 policy, never re-derived here.
"""
import re

from ..core.runs import content_hash
from .completion_policy import (
    CATEGORIES, POLICY_VERSION, AssessedResponse, ReadinessAssessment,
    assess_completion,
)

PACKET_VERSION = "hub-assessment-packet/v1"
ASSESSMENT_VERSION = "hub-assessment-save/v1+" + POLICY_VERSION
CLASSES = {"biology", "forensics", "anatomy"}
JUDGMENTS = {"meaningful_attempt", "blank", "printed_copy", "review"}
CONCEPTUAL_LEVELS = {"Developing", "Almost there", "Proficient", "Evidence unavailable"}
EXPLANATION_LEVELS = {"Names an idea", "Connects ideas", "Explains how or why",
                      "Evidence unavailable"}
STAGES = {"initial", "final"}
PACKET_KEYS = {"schema_version", "class_key", "section_id", "section_name",
               "assignment_key", "identity", "stage", "stage_reference", "source",
               "manifest", "responses", "feedback", "comparison_status",
               "ic_assignment_id"}
IDENTITY_KEYS = {"student_id", "student_id_kind", "learner_key", "display_name",
                 "identity_basis"}
SOURCE_KEYS = {"file_id", "url", "modified_at", "version", "sha256"}
MANIFEST_KEYS = {"inventory_line", "version_label", "notes_ids", "bank_ids",
                 "draft_ids", "optional_ids"}
RESPONSE_KEYS = {"question_id", "category", "judgment", "response_excerpt",
                 "evidence_note", "conceptual_level", "explanation_level",
                 "conceptual_feedback", "prompt", "prompt_basis", "slide_number",
                 "optional"}


def _text(value, limit, *, required=False, multiline=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError("invalid_assessment_packet")
    banned = "\r" if multiline else "\r\n"
    if any(c in banned or (ord(c) < 32 and c != "\n") for c in value):
        raise ValueError("invalid_assessment_packet")
    return value


def _question_id(value):
    # The teacher's Concept Bank terms are marker ids too ("Trophic level"),
    # so interior spaces are part of the id vocabulary.
    return (isinstance(value, str) and value == value.strip()
            and bool(re.fullmatch(r"[A-Za-z0-9 _-]{1,80}", value)))


def _ids(value):
    if (not isinstance(value, list) or len(value) > 200
            or any(not _question_id(q) for q in value)
            or len(set(value)) != len(value)):
        raise ValueError("invalid_assessment_manifest")
    return tuple(value)


def validate_packet(packet, source_text):
    """One reviewed packet against the freshly captured source text.

    Raises ValueError with a fixed code; never echoes student writing. The
    returned packet is the validated original, not a repaired one.
    """
    if not isinstance(packet, dict) or set(packet) != PACKET_KEYS:
        raise ValueError("invalid_assessment_packet")
    if packet["schema_version"] != PACKET_VERSION or packet["class_key"] not in CLASSES:
        raise ValueError("invalid_assessment_packet")
    if not isinstance(source_text, str) or not source_text:
        raise ValueError("invalid_assessment_source")
    _text(packet["section_id"], 40, required=True)
    _text(packet["section_name"], 120, required=True)
    _text(packet["assignment_key"], 160, required=True)
    _text(packet["stage_reference"], 400, required=True)
    _text(packet["feedback"], 12000, required=True, multiline=True)
    _text(packet["comparison_status"], 300, required=True)
    _text(packet["ic_assignment_id"], 200)
    if packet["stage"] not in STAGES:
        raise ValueError("invalid_assessment_stage")

    identity = packet["identity"]
    if not isinstance(identity, dict) or set(identity) != IDENTITY_KEYS:
        raise ValueError("invalid_assessment_identity")
    _text(identity["display_name"], 120, required=True)
    _text(identity["identity_basis"], 400, required=True)
    _text(identity["student_id_kind"], 60, required=True)
    if identity["student_id"] and not re.fullmatch(r"[0-9]{2,12}", identity["student_id"]):
        raise ValueError("invalid_assessment_identity")
    if identity["learner_key"] and not re.fullmatch(r"roster:[a-f0-9]{8,64}",
                                                    identity["learner_key"]):
        raise ValueError("invalid_assessment_identity")
    if not identity["student_id"] and not identity["learner_key"]:
        raise ValueError("assessment_identity_unresolved")

    source = packet["source"]
    if (not isinstance(source, dict) or set(source) != SOURCE_KEYS
            or not isinstance(source["file_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", source["file_id"])
            or not isinstance(source["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", source["sha256"])):
        raise ValueError("invalid_assessment_source")
    _text(source["modified_at"], 60, required=True)
    _text(source["version"], 40)
    url = source["url"]
    if (not isinstance(url, str) or len(url) > 300 or source["file_id"] not in url
            or not url.startswith(("https://docs.google.com/", "https://drive.google.com/"))):
        raise ValueError("invalid_assessment_source")

    manifest = packet["manifest"]
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise ValueError("invalid_assessment_manifest")
    inventory = _text(manifest["inventory_line"], 300, required=True)
    version_label = _text(manifest["version_label"], 160, required=True)
    if not inventory.startswith("[[MARKER-INVENTORY:"):
        raise ValueError("invalid_assessment_manifest")
    # The inventory line and version label bind the packet to the distributed
    # version actually present in this capture, not a remembered one.
    if inventory not in source_text or version_label not in source_text:
        raise ValueError("assessment_manifest_not_in_source")
    required = {"NOTES": _ids(manifest["notes_ids"]), "BANK": _ids(manifest["bank_ids"]),
                "DRAFT": _ids(manifest["draft_ids"])}
    optional_ids = set(_ids(manifest["optional_ids"]))
    all_required = {q for ids in required.values() for q in ids}
    if optional_ids & all_required:
        raise ValueError("invalid_assessment_manifest")

    responses = packet["responses"]
    if not isinstance(responses, list) or not 1 <= len(responses) <= 400:
        raise ValueError("invalid_assessment_responses")
    seen = set()
    for response in responses:
        if not isinstance(response, dict) or set(response) != RESPONSE_KEYS:
            raise ValueError("invalid_assessment_response")
        if (response["category"] not in CATEGORIES
                or response["judgment"] not in JUDGMENTS
                or type(response["optional"]) is not bool
                or response["conceptual_level"] not in CONCEPTUAL_LEVELS
                or response["explanation_level"] not in EXPLANATION_LEVELS):
            raise ValueError("invalid_assessment_response")
        qid = response["question_id"]
        if not _question_id(qid):
            raise ValueError("invalid_assessment_response")
        if (response["category"], qid) in seen:
            raise ValueError("duplicate_assessment_response")
        seen.add((response["category"], qid))
        expected_pool = optional_ids if response["optional"] else set(
            required[response["category"]])
        if qid not in expected_pool:
            raise ValueError("assessment_response_outside_manifest")
        _text(response["evidence_note"], 400)
        _text(response["conceptual_feedback"], 1200, multiline=True)
        _text(response["prompt"], 1200, multiline=True)
        _text(response["prompt_basis"], 200)
        if response["slide_number"] and not re.fullmatch(r"[0-9]{1,3}",
                                                         response["slide_number"]):
            raise ValueError("invalid_assessment_response")
        excerpt = response["response_excerpt"]
        if response["judgment"] == "blank":
            if excerpt != "":
                raise ValueError("assessment_blank_with_excerpt")
        else:
            # A judged response must quote the student's actual words from this
            # exact capture; a paraphrase or stale quote fails the save.
            _text(excerpt, 6000, required=True, multiline=True)
            if excerpt not in source_text:
                raise ValueError("assessment_evidence_not_in_source")
    return packet


def completion(packet):
    """The existing Prompt-1 arithmetic over this packet's judgments."""
    manifest = packet["manifest"]
    required = {"NOTES": tuple(manifest["notes_ids"]), "BANK": tuple(manifest["bank_ids"]),
                "DRAFT": tuple(manifest["draft_ids"])}
    evidence = "sha256:" + packet["source"]["sha256"][:32]
    readiness = ReadinessAssessment(
        inventory_line=manifest["inventory_line"], inventory_verified=True,
        required_ids=required, observed_ids=required,
        lesson_sequence_verified=True, conflict_required=False,
        conflict_after_bank_verified=False, evidence_reference=evidence)
    assessed = tuple(
        AssessedResponse(category=r["category"], question_id=r["question_id"],
                         judgment=r["judgment"],
                         evidence_reference=evidence + ":" + r["question_id"].replace(" ", "_"),
                         optional=r["optional"])
        for r in packet["responses"])
    result = assess_completion(readiness, assessed, stage=packet["stage"])
    if result["status"] != "proposed":
        raise ValueError("assessment_not_ready")
    return result


def record_identity(packet):
    identity = packet["identity"]
    return ("ic:" + identity["student_id"]) if identity["student_id"] else identity["learner_key"]


def record_id(packet):
    """Deterministic: an identical reviewed assessment of the same submission
    version is the same record, so exact reruns cannot duplicate it, while a
    genuinely re-reviewed assessment (different judgments or feedback) becomes
    a new record that supersedes the earlier one instead of colliding."""
    return "asmt-" + content_hash({
        "assignment": packet["assignment_key"], "identity": record_identity(packet),
        "sha256": packet["source"]["sha256"], "stage": packet["stage"],
        "policy": POLICY_VERSION,
        "assessment": content_hash({"responses": packet["responses"],
                                    "feedback": packet["feedback"]})})[:24]


MANIFEST_SECTIONS = {"NOTES_IDS": "notes_ids", "DRAFT_IDS": "draft_ids",
                     "OPTIONAL_IDS": "optional_ids", "BANK_IDS": "bank_ids"}


def derive_manifest(source_text):
    """The distributed requirement read from the deck's own manifest header.

    The header is the teacher's distributed contract: the marker-inventory tag,
    a VERSION label, per-category counts, and one id per line under each
    section. A deck without the header (an older or self-modified version)
    raises assessment_manifest_missing and stays a manual-reconciliation case;
    a header whose lists disagree with its own counts is never guessed at.
    """
    if not isinstance(source_text, str) or not source_text:
        raise ValueError("invalid_assessment_source")
    inventory = re.search(r"\[\[MARKER-INVENTORY:[A-Za-z0-9_-]{1,40}\]\]", source_text)
    if not inventory:
        raise ValueError("assessment_manifest_missing")
    lines = [line.strip() for line in
             source_text[inventory.start():inventory.start() + 4000].splitlines()]
    version = next((line for line in lines if line.startswith("VERSION=")), "")
    counts_line = next((line for line in lines if re.match(r"NOTES=\d+", line)), "")
    counts = dict(re.findall(r"([A-Z_]+)=(\d+)", counts_line))
    sections = {name: [] for name in MANIFEST_SECTIONS.values()}
    current = None
    for line in lines:
        if not line:
            current = None
            continue
        if line.endswith("=") and line[:-1] in MANIFEST_SECTIONS:
            current = MANIFEST_SECTIONS[line[:-1]]
            continue
        if "=" in line or ":" in line:
            current = None
            continue
        if current and _question_id(line):
            sections[current].append(line)
    expected = {"notes_ids": counts.get("NOTES"), "draft_ids": counts.get("DRAFT"),
                "optional_ids": counts.get("OPTIONAL"),
                "bank_ids": counts.get("BANK_TERMS")}
    for key, want in expected.items():
        ids = sections[key]
        if want is None or len(ids) != int(want) or len(set(ids)) != len(ids):
            raise ValueError("assessment_manifest_unparsed")
    if not version or len(version) > 160:
        raise ValueError("assessment_manifest_unparsed")
    return {"inventory_line": inventory.group(0), "version_label": version,
            "notes_ids": sections["notes_ids"], "bank_ids": sections["bank_ids"],
            "draft_ids": sections["draft_ids"],
            "optional_ids": sections["optional_ids"]}


def _flag(value):
    return "TRUE" if value else "FALSE"


def _ordered_responses(packet):
    manifest = packet["manifest"]
    order = {}
    for category, ids in (("NOTES", manifest["notes_ids"]), ("BANK", manifest["bank_ids"]),
                          ("DRAFT", manifest["draft_ids"])):
        for index, qid in enumerate(ids):
            order[(category, qid)] = (CATEGORIES.index(category), 0, index)
    for index, qid in enumerate(manifest["optional_ids"]):
        for category in CATEGORIES:
            order.setdefault((category, qid), (len(CATEGORIES), 1, index))
    return sorted(packet["responses"],
                  key=lambda r: order[(r["category"], r["question_id"])])


def build_rows(packet, result, capture, *, now, prior_score_exact="", prior_hold_reason=""):
    """Workbook rows for one validated packet and its completion result.

    capture is the fresh server-side source capture; its metadata, not the
    packet's, is recorded so the row names the version that was re-verified.
    """
    if capture["sha256"] != packet["source"]["sha256"]:
        raise ValueError("assessment_capture_mismatch")
    rid = record_id(packet)
    identity = packet["identity"]
    scores, counts = result["scores"], result["counts"]
    content, revision = scores["content_notes"], scores["revision"]
    submission = {
        "record_id": rid, "assignment_key": packet["assignment_key"],
        "section_id": packet["section_id"], "section_name": packet["section_name"],
        "student_id": identity["student_id"], "student_id_kind": identity["student_id_kind"],
        "learner_key": identity["learner_key"], "display_name": identity["display_name"],
        "identity_basis": identity["identity_basis"],
        "source_url": packet["source"]["url"], "source_file_id": packet["source"]["file_id"],
        "source_revision": capture["file"]["version"],
        "source_sha256": capture["sha256"],
        "source_snapshot_date": capture["captured_at"][:10],
        "status": "assessed", "assessment_stage": packet["stage"],
        "assessment_version": ASSESSMENT_VERSION,
        "reconciliation_basis": packet["stage_reference"],
        "content_score": content["value"], "content_score_exact": content["exact_value"] or "",
        "content_points_possible": str(content["out_of"]),
        "revision_score": revision["value"],
        "revision_score_exact": revision["exact_value"] or "",
        "notes_completed": str(counts["NOTES"]["completed"]),
        "notes_required": str(counts["NOTES"]["required"]),
        "bank_completed": str(counts["BANK"]["completed"]),
        "bank_required": str(counts["BANK"]["required"]),
        "drafts_completed": str(counts["DRAFT"]["completed"]),
        "drafts_required": str(counts["DRAFT"]["required"]),
        "hold_reason": "", "prior_hold_reason": prior_hold_reason,
        "prior_readiness": "", "prior_score_exact": prior_score_exact,
        "feedback": packet["feedback"], "checked_at": now, "checked_date": now[:10],
        "ic_assignment_id": packet["ic_assignment_id"],
        "ic_posting_status": "unposted", "superseded_by": "",
    }
    answers = []
    for response in _ordered_responses(packet):
        due = (not response["optional"]
               and (packet["stage"] == "final" or response["category"] == "NOTES"))
        answers.append({
            "record_id": rid, "question_id": response["question_id"],
            "stage": response["category"], "response": response["response_excerpt"],
            "conceptual_level": response["conceptual_level"],
            "explanation_level": response["explanation_level"],
            "meaningful_complete": _flag(response["judgment"] == "meaningful_attempt"),
            "prompt": response["prompt"], "prompt_basis": response["prompt_basis"],
            "evidence_note": response["evidence_note"],
            "response_shape_id": "", "slide_number": response["slide_number"],
            "source_url": packet["source"]["url"], "source_sha256": capture["sha256"],
            "conceptual_feedback": response["conceptual_feedback"],
            "assessment_date": now[:10],
            "comparison_status": packet["comparison_status"],
            "counts_toward_current_score": _flag(due),
        })
    return {"record_id": rid, "submission": submission, "answers": answers}


def aligned_cells(headers, row, *, numeric=()):
    """One appendCells row aligned to the live header order.

    Unknown live headers stay empty; a row value with no live column is an
    error, never silently dropped. Numeric fields write real numbers so the
    strict reader's effectiveValue path sees them.
    """
    named = [h for h in headers if h]
    missing = set(row) - set(named)
    if missing:
        raise ValueError("assessment_column_missing")
    cells = []
    for header in headers:
        value = row.get(header, "") if header else ""
        if header in numeric:
            if value is None:
                cells.append({"userEnteredValue": {"stringValue": ""}})
            else:
                cells.append({"userEnteredValue": {"numberValue": float(value)}})
        else:
            if not isinstance(value, str):
                raise ValueError("assessment_cell_invalid")
            cells.append({"userEnteredValue": {"stringValue": value}})
    return {"values": cells}


def summary_row(summary, *, now):
    if not isinstance(summary, dict) or set(summary) != {"class_key", "section_id",
                                                         "section_name", "summary",
                                                         "coverage"}:
        raise ValueError("invalid_class_summary")
    if summary["class_key"] not in CLASSES:
        raise ValueError("invalid_class_summary")
    _text(summary["section_id"], 40, required=True)
    _text(summary["section_name"], 120, required=True)
    _text(summary["summary"], 1600, required=True, multiline=True)
    _text(summary["coverage"], 400, required=True)
    return {"class_key": summary["class_key"], "section_id": summary["section_id"],
            "section_name": summary["section_name"], "summary": summary["summary"],
            "coverage": summary["coverage"], "checked_at": now}
