"""Private four-page read model and delivery planning for the existing Agenda.

Pure functions; no new database, HTTP endpoint, authentication, model call or
external write. Host adapters authenticate and validate provenance before use.
Opaque references require an authorized resolver; they are not invented URLs.
Do not publish these payloads or log them: learner keys remain private data.
"""
from __future__ import annotations

from collections import Counter
import copy
from dataclasses import asdict, dataclass
from datetime import date
from urllib.parse import urlsplit

from school_workflow_hub.core.models import EnrollmentRecord, SubmissionRevision
from school_workflow_hub.core.runs import deterministic_id
from school_workflow_hub.workflows.completion_policy import _key, assess_completion
from school_workflow_hub.workflows.due_completion import boundaries
from school_workflow_hub.workflows.operations_status import build_operations_snapshot, _timestamp
from school_workflow_hub.workflows.research_agenda import build_research_agenda
from school_workflow_hub.workflows.schoology_gradebook import canonicalize_url

SCHEMA_VERSION = "agenda-workspace/v1"
EFFECTS = ("save_agenda", "send_parent_email", "file_feedback", "record_roster_grade",
           "post_ic_grade", "print_feedback", "collect_research")


def _require_key(value):
    if not _key(value):
        raise ValueError("unverified_workspace_identity")
    return value


def _items(values, item_type, maximum=5000):
    if not isinstance(values, (tuple, list)) or len(values) > maximum or any(
            not isinstance(v, item_type) for v in values):
        raise ValueError("invalid_workspace_items")


@dataclass(frozen=True)
class DailyLesson:
    lesson_key: str
    class_key: str
    assignment_key: str
    teaching_date: str
    title: str
    notes: str
    source_page_reference: str
    source_revision: str
    observed_at: str
    slides_url: str | None = None


@dataclass(frozen=True)
class SubmissionFact:
    revision: SubmissionRevision
    submitted_at: str  # Actual turn-in timestamp; not Drive modified/observed time.


@dataclass(frozen=True)
class Deadline:
    assignment_key: str
    cutoff_at: str  # Host resolves teacher-approved anchor, calendar and timezone.
    policy_reference: str
    learner_key: str | None = None  # A verified individual extension overrides the class cutoff.


def deadline_after_due_date(assignment_key, due_date, *, timezone_name="America/New_York",
                            learner_key=None):
    """Teacher-approved rule: fourteen calendar days after the due date.

    Date-only deadlines use the end of that local date, not 336 UTC hours. This
    leaves the whole fourteenth day available across daylight-saving changes.
    Host must pass the current effective due date, including approved exceptions.
    """
    _require_key(assignment_key)
    if learner_key is not None:
        _require_key(learner_key)
    if not isinstance(due_date, str):
        raise ValueError("invalid_effective_due_date")
    try:
        parsed = date.fromisoformat(due_date)
        if parsed.isoformat() != due_date:
            raise ValueError()
        _, cutoff = boundaries(due_date, timezone_name)
    except (ValueError, TypeError, KeyError):
        raise ValueError("invalid_deadline_date_or_timezone") from None
    return Deadline(assignment_key, cutoff.isoformat(),
                    "deadline-policy:due-plus-14-calendar-days:v1", learner_key)


@dataclass(frozen=True)
class FeedbackDocument:
    class_key: str
    assignment_key: str
    learner_key: str
    submission_revision: str
    folder_reference: str
    document_reference: str
    prompt_version: str
    observed_at: str
    receipt_reference: str
    document_revision: str


@dataclass(frozen=True)
class Outcome:
    class_key: str
    assignment_key: str
    learner_key: str
    submission_revision: str
    kind: str  # parent_email, roster_grade, ic_grade, print_feedback
    state: str  # pending, failed, uncertain, verified
    observed_at: str
    receipt_reference: str | None = None
    verification: str = "unverified"


@dataclass(frozen=True)
class CompletionCase:
    class_key: str
    assignment_key: str
    learner_key: str
    submission_revision: str
    stage: str
    readiness: object
    responses: tuple


def build_submission_feed(*, class_key, assignment_keys, roster, submissions,
                          roster_complete, full_history_complete, observed_at,
                          as_of, deadlines=(), feedback=(), turnin_folders=None,
                          prompt_version, ic_contact_references=None, outcomes=(), completion_cases=()):
    """Current membership x assignment, joined with cumulative turn-in history.

    A complete 14-day *change window* is NOT full_history_complete. A host must
    reconcile it with the cumulative ledger or provide a complete listing.
    Missing work can be asserted only from complete class roster AND history.
    """
    _require_key(class_key)
    _require_key(prompt_version)
    _items(roster, EnrollmentRecord)
    _items(submissions, SubmissionFact)
    _items(deadlines, Deadline)
    _items(feedback, FeedbackDocument)
    _items(outcomes, Outcome)
    _items(completion_cases, CompletionCase)
    if (not isinstance(assignment_keys, (tuple, list)) or not assignment_keys
            or len(assignment_keys) > 500 or len(set(assignment_keys)) != len(assignment_keys)
            or type(roster_complete) is not bool or type(full_history_complete) is not bool):
        raise ValueError("invalid_submission_scope")
    for key in assignment_keys:
        _require_key(key)
    now, checked = _timestamp(as_of), _timestamp(observed_at)
    if checked > now:
        raise ValueError("future_submission_snapshot")
    learners = set()
    for member in roster:
        if member.class_stable_id != class_key or member.learner_stable_id in learners:
            raise ValueError("duplicate_or_wrong_class_roster")
        learners.add(_require_key(member.learner_stable_id))
    if len(learners) * len(assignment_keys) > 10000:
        raise ValueError("submission_case_limit")
    cutoff = {}
    for deadline in deadlines:
        key = deadline.assignment_key, deadline.learner_key
        if (deadline.assignment_key not in assignment_keys or key in cutoff
                or (deadline.learner_key is not None and deadline.learner_key not in learners)):
            raise ValueError("invalid_deadline_scope")
        _require_key(deadline.policy_reference)
        cutoff[key] = (_timestamp(deadline.cutoff_at), deadline.policy_reference)
    latest, identities, submitted_times, scope_documents, observations = {}, {}, {}, {}, {}
    for fact in submissions:
        item = fact.revision
        if not isinstance(item, SubmissionRevision):
            raise ValueError("invalid_submission_record")
        for key in (item.stable_id, item.assignment_stable_id, item.learner_stable_id, item.revision_hash):
            _require_key(key)
        scope = item.assignment_stable_id, item.learner_stable_id
        if scope[0] not in assignment_keys or scope[1] not in learners:
            raise ValueError("submission_scope_mismatch")
        if item.stable_id in identities and identities[item.stable_id] != scope:
            raise ValueError("submission_identity_conflict")
        identities[item.stable_id] = scope
        if scope in scope_documents and scope_documents[scope] != item.stable_id:
            # A second file may be a duplicate or contain different work. A
            # later observation cannot choose which document should be scored.
            raise ValueError("multiple_submission_documents_need_review")
        scope_documents[scope] = item.stable_id
        observed, submitted = _timestamp(item.observed_at), _timestamp(fact.submitted_at)
        if not submitted <= observed <= checked:
            raise ValueError("invalid_submission_chronology")
        observation_key = item.stable_id, observed
        if observation_key in observations and observations[observation_key] != fact:
            raise ValueError("ambiguous_submission_observation")
        observations[observation_key] = fact
        submitted_times.setdefault(scope, []).append(submitted)
        if scope in latest:
            previous = latest[scope]
            if observed == _timestamp(previous.observed_at) and item != previous:
                raise ValueError("ambiguous_latest_submission")
            if observed < _timestamp(previous.observed_at):
                continue
        latest[scope] = item
    folders = {} if turnin_folders is None else turnin_folders
    contacts = {} if ic_contact_references is None else ic_contact_references
    if not isinstance(folders, dict) or not isinstance(contacts, dict):
        raise ValueError("invalid_private_resource_mapping")
    for key, reference in folders.items():
        if key not in assignment_keys:
            raise ValueError("turnin_folder_scope_mismatch")
        _require_key(reference)
    for learner, reference in contacts.items():
        if learner not in learners:
            raise ValueError("contact_scope_mismatch")
        _require_key(reference)
    docs = {}
    for doc in feedback:
        scope = doc.assignment_key, doc.learner_key
        if doc.class_key != class_key or scope[0] not in assignment_keys or scope[1] not in learners:
            raise ValueError("feedback_scope_mismatch")
        for key in (doc.submission_revision, doc.folder_reference, doc.document_reference,
                    doc.prompt_version, doc.receipt_reference, doc.document_revision):
            _require_key(key)
        if _timestamp(doc.observed_at) > checked:
            raise ValueError("future_feedback_document")
        if scope in docs:
            raise ValueError("duplicate_feedback_document")
        docs[scope] = doc
    delivered = {}
    for outcome in outcomes:
        scope = outcome.assignment_key, outcome.learner_key
        if (outcome.class_key != class_key or scope[0] not in assignment_keys or scope[1] not in learners
                or outcome.kind not in {"parent_email", "roster_grade", "ic_grade", "print_feedback"}
                or outcome.state not in {"pending", "failed", "uncertain", "verified"}
                or outcome.verification not in {"unverified", "synthetic_test", "live_read", "live_end_to_end"}):
            raise ValueError("invalid_delivery_outcome")
        _require_key(outcome.submission_revision)
        if _timestamp(outcome.observed_at) > checked:
            raise ValueError("future_delivery_outcome")
        if outcome.receipt_reference is not None:
            _require_key(outcome.receipt_reference)
        if outcome.state == "verified" and (not outcome.receipt_reference
                or outcome.verification != "live_end_to_end"):
            raise ValueError("delivery_requires_live_receipt")
        key = scope + (outcome.kind,)
        if key in delivered:
            raise ValueError("duplicate_delivery_outcome")
        delivered[key] = outcome
    completions = {}
    for case in completion_cases:
        scope = case.assignment_key, case.learner_key
        if (case.class_key != class_key or scope[0] not in assignment_keys
                or scope[1] not in learners or scope in completions):
            raise ValueError("completion_case_scope_mismatch")
        _require_key(case.submission_revision)
        completions[scope] = case
    rows = []
    for assignment in sorted(assignment_keys):
        for learner in sorted(learners):
            scope = assignment, learner
            item, doc = latest.get(scope), docs.get(scope)
            case = completions.get(scope)
            completion, completion_state = None, "not_assessed"
            if case is not None:
                if item is None or case.submission_revision != item.revision_hash:
                    completion_state = "prior_revision"
                else:
                    completion = assess_completion(case.readiness, case.responses, stage=case.stage)
                    completion_state = completion["status"]
            state = "submitted" if item else "not_submitted" if (
                roster_complete and full_history_complete) else "unverified"
            deadline_state = "unverified"
            effective_deadline = cutoff.get(scope, cutoff.get((assignment, None)))
            if effective_deadline is not None and state != "unverified":
                boundary, _ = effective_deadline
                # An earlier known on-time submission proves on-time even if
                # later revisions exist. An incomplete history cannot prove late.
                if item:
                    first = min(submitted_times[scope])
                    deadline_state = "submitted_by_deadline" if first <= boundary else (
                        "submitted_after_deadline" if full_history_complete else "unverified")
                else:
                    deadline_state = "past_deadline" if checked > boundary else "within_deadline"
            feedback_state = "missing_work_feedback_needed" if state == "not_submitted" else "unverified"
            link = None
            if item:
                feedback_state = "needs_feedback"
                if doc:
                    if doc.folder_reference != folders.get(assignment):
                        feedback_state = "folder_needs_review"
                    elif doc.submission_revision != item.revision_hash or doc.prompt_version != prompt_version:
                        feedback_state = "stale_feedback"
                    else:
                        feedback_state, link = "available", doc.document_reference
            contact = contacts.get(learner)
            email = "not_needed"
            if deadline_state == "past_deadline":
                email = "ready_for_draft_review" if contact else "contact_needs_verification"
            elif deadline_state == "unverified":
                email = "eligibility_unverified"
            delivery = {}
            for kind in ("parent_email", "roster_grade", "ic_grade", "print_feedback"):
                outcome = delivered.get(scope + (kind,))
                if outcome:
                    same = item is not None and outcome.submission_revision == item.revision_hash
                    delivery[kind] = {"state": outcome.state if same else "prior_revision",
                                      "receipt_reference": outcome.receipt_reference,
                                      "last_checked_at": outcome.observed_at}
                else:
                    delivery[kind] = {"state": "not_verified", "receipt_reference": None,
                                      "last_checked_at": None}
            rows.append({"class_key": class_key, "assignment_key": assignment, "learner_key": learner,
                         "submission_state": state, "submission_revision": item.revision_hash if item else None,
                         "deadline_state": deadline_state, "feedback_state": feedback_state,
                         "deadline_cutoff_at": effective_deadline[0].isoformat() if effective_deadline else None,
                         "deadline_policy_reference": effective_deadline[1] if effective_deadline else None,
                         "feedback_document_reference": link, "parent_email_route": email,
                         "ic_contact_reference": contact if deadline_state == "past_deadline" else None,
                         "completion": completion, "completion_state": completion_state,
                         "delivery": delivery, "last_checked_at": observed_at})
    return {"class_key": class_key, "rows": rows, "roster_complete": roster_complete,
            "full_history_complete": full_history_complete, "last_checked_at": observed_at,
            "deadline_policy": {k[0]: v[1] for k, v in cutoff.items() if k[1] is None},
            "external_writes": 0}


def plan_feedback_print(documents, *, class_key, assignment_key, expected_learners,
                        folder_reference, prompt_version, teacher_request_key=None,
                        printer_reference=None, submission_revisions=None):
    """Build a content-bound batch proposal, never a spooler job or approval.

    Caller selects the current documents using submission/prompt revisions.
    A real executor must enforce authorization, freshness, durable idempotency,
    privacy-safe printer routing and spooler receipts before sending anything.
    """
    _items(documents, FeedbackDocument)
    for value in (class_key, assignment_key, folder_reference, prompt_version):
        _require_key(value)
    if not isinstance(expected_learners, (tuple, list)) or len(set(expected_learners)) != len(expected_learners):
        raise ValueError("invalid_print_membership")
    for value in expected_learners:
        _require_key(value)
    reasons = []
    if submission_revisions is None:
        reasons.append("current_submission_revisions_unverified")
    elif (not isinstance(submission_revisions, dict)
          or set(submission_revisions) != set(expected_learners)):
        raise ValueError("invalid_print_revision_scope")
    else:
        for revision in submission_revisions.values():
            _require_key(revision)
    if teacher_request_key is None:
        reasons.append("teacher_print_direction_required")
    else:
        _require_key(teacher_request_key)
    if printer_reference is None:
        reasons.append("printer_mapping_unverified")
    else:
        _require_key(printer_reference)
    membership = []
    for doc in documents:
        if (doc.class_key != class_key or doc.assignment_key != assignment_key
                or doc.folder_reference != folder_reference or doc.prompt_version != prompt_version):
            raise ValueError("print_document_scope_or_version_mismatch")
        for value in (doc.learner_key, doc.submission_revision, doc.document_reference,
                      doc.receipt_reference, doc.document_revision):
            _require_key(value)
        if submission_revisions is not None and submission_revisions.get(doc.learner_key) != doc.submission_revision:
            reasons.append("print_feedback_revision_stale")
        _timestamp(doc.observed_at)
        membership.append(doc.learner_key)
    if not documents or sorted(membership) != sorted(expected_learners):
        reasons.append("print_batch_incomplete_or_duplicate")
    refs = [d.document_reference for d in documents]
    if len(set(refs)) != len(refs):
        reasons.append("duplicate_print_document")
    payload = {"class_key": class_key, "assignment_key": assignment_key,
               "teacher_request_key": teacher_request_key, "printer_reference": printer_reference,
               "documents": sorted(({k: v for k, v in asdict(d).items()
                                      if k not in {"observed_at", "receipt_reference"}} for d in documents),
                                   key=lambda d: d["learner_key"])}
    return {"batch_key": deterministic_id("feedback-print", payload),
            "status": "blocked" if reasons else "prepared_not_sent", "issue_codes": reasons,
            "document_count": len(documents), "printer_reference": printer_reference,
            "execution_supported": False, "external_writes": 0}


def plan_completion_delivery(readiness, responses, *, stage, class_key, assignment_key,
                             learner_key, submission_revision, roster_current_reference=None,
                             roster_grade_readback=None, ic_assignment_reference=None):
    """Grade proposal -> updated roster -> matching roster readback -> IC proposal.

    Host must authenticate freshness/scope of references. A bare roster receipt
    is insufficient: the receipt must bind this exact proposal's identity.
    Execution remains unavailable even after all planning prerequisites pass.
    """
    for value in (class_key, assignment_key, learner_key, submission_revision):
        _require_key(value)
    for reference in (roster_current_reference, ic_assignment_reference):
        if reference is not None:
            _require_key(reference)
    completion = assess_completion(readiness, responses, stage=stage)
    proposal = deterministic_id("completion-delivery", {
        "class_key": class_key, "assignment_key": assignment_key, "learner_key": learner_key,
        "submission_revision": submission_revision, "stage": stage,
        "roster_current_reference": roster_current_reference,
        "ic_assignment_reference": ic_assignment_reference,
        "policy_version": completion["policy_version"], "completion": completion})
    valid = completion["status"] == "proposed"
    roster_ready = valid and roster_current_reference is not None
    roster_verified = False
    if roster_grade_readback is not None:
        if (not isinstance(roster_grade_readback, dict) or set(roster_grade_readback) != {
                "proposal_key", "receipt_reference", "verification"}):
            raise ValueError("invalid_roster_grade_readback")
        _require_key(roster_grade_readback["receipt_reference"])
        roster_verified = (roster_grade_readback["proposal_key"] == proposal
                           and roster_grade_readback["verification"] == "live_end_to_end")
    return {"proposal_key": proposal, "completion": completion,
            "roster_step": "prepared_not_written" if roster_ready else "blocked",
            "ic_step": "prepared_not_posted" if (roster_ready and roster_verified
                       and ic_assignment_reference is not None) else "blocked",
            "history_policy": "append_new_revision_preserve_prior",
            "execution_supported": False, "external_writes": 0}


def build_teacher_workspace(*, lessons, submission_feeds, generated_at, operations=(),
                            connection_state="not_connected", research_receipts=(),
                            research_access=False, instructional_summary_reference=None):
    """Wrap unchanged agenda-operations/v1 with the four page data contracts.

    research_access is an already-authorized server decision, never a browser
    checkbox. Research records are opaque references to separately approved
    records, not copied grades, names, contact lists or automatic agency codes.
    """
    now = _timestamp(generated_at)
    _items(lessons, DailyLesson, 500)
    _items(submission_feeds, dict, 100)
    if type(research_access) is not bool:
        raise ValueError("invalid_research_access")
    if instructional_summary_reference is not None:
        _require_key(instructional_summary_reference)
    ops = build_operations_snapshot(operations, generated_at=generated_at,
                                    connection_state=connection_state)
    cards, seen = [], set()
    for lesson in lessons:
        for value in (lesson.lesson_key, lesson.class_key, lesson.assignment_key,
                      lesson.source_page_reference, lesson.source_revision):
            _require_key(value)
        if lesson.lesson_key in seen:
            raise ValueError("duplicate_daily_lesson")
        seen.add(lesson.lesson_key)
        date.fromisoformat(lesson.teaching_date)
        if (_timestamp(lesson.observed_at) > now or not isinstance(lesson.title, str)
                or not lesson.title.strip() or len(lesson.title) > 500
                or not isinstance(lesson.notes, str) or len(lesson.notes) > 20000):
            raise ValueError("invalid_daily_lesson")
        if lesson.slides_url is not None:
            link = canonicalize_url(lesson.slides_url)
            parsed = urlsplit(link)
            if parsed.netloc != "docs.google.com" or not parsed.path.startswith("/presentation/d/"):
                raise ValueError("lesson_link_is_not_google_slides")
        cards.append(dict(asdict(lesson), open_slides=lesson.slides_url,
                          link_access="not_tested", save_supported=False))
    rows, row_keys = [], set()
    for feed in submission_feeds:
        checked = _timestamp(feed.get("last_checked_at"))
        if (checked > now or not isinstance(feed.get("rows"), list)
                or len(feed["rows"]) > 10000
                or type(feed.get("roster_complete")) is not bool
                or type(feed.get("full_history_complete")) is not bool
                or type(feed.get("external_writes")) is not int or feed["external_writes"] != 0):
            raise ValueError("invalid_submission_feed")
        _require_key(feed.get("class_key"))
        for row in feed["rows"]:
            if (not isinstance(row, dict) or _timestamp(row.get("last_checked_at")) != checked
                    or row.get("submission_state") not in {"submitted", "not_submitted", "unverified"}):
                raise ValueError("invalid_submission_row")
            if row["submission_state"] == "not_submitted" and not (
                    feed["roster_complete"] and feed["full_history_complete"]):
                raise ValueError("missing_work_requires_complete_evidence")
            key = tuple(row[k] for k in ("class_key", "assignment_key", "learner_key"))
            for value in key:
                _require_key(value)
            if key in row_keys or key[0] != feed["class_key"]:
                raise ValueError("duplicate_or_wrong_class_submission_row")
            row_keys.add(key)
            rows.append(row)
    counts = {field: dict(Counter(row[field] for row in rows)) for field in (
        "submission_state", "deadline_state", "feedback_state", "parent_email_route", "completion_state")}
    deliveries = {kind: dict(Counter(row["delivery"][kind]["state"] for row in rows))
                  for kind in ("parent_email", "roster_grade", "ic_grade", "print_feedback")}
    research = []
    if research_access:
        if not isinstance(research_receipts, (tuple, list)) or len(research_receipts) > 500:
            raise ValueError("invalid_research_receipts")
        for record in research_receipts:
            if (not isinstance(record, dict) or set(record) != {
                    "record_reference", "instruction_version", "approval_reference", "observed_at"}
                    or _timestamp(record["observed_at"]) > now):
                raise ValueError("invalid_research_receipt")
            for field in ("record_reference", "instruction_version", "approval_reference"):
                _require_key(record[field])
            research.append(dict(record))
    return {"schema_version": SCHEMA_VERSION, "generated_at": generated_at,
            "operations": ops, "privacy": "authenticated_teacher_only",
            "pages": {
                "teaching_agenda": {"lessons": sorted(cards, key=lambda r: (r["teaching_date"], r["class_key"], r["lesson_key"])),
                                    "comparison_target": "infinite_campus", "two_way_editing_supported": False},
                "submissions_feedback": {"classes": copy.deepcopy(submission_feeds)},
                "teacher_summary": {"case_counts": counts, "delivery_outcomes": deliveries,
                                    "scheduled_lesson_count": len(cards),
                                    "lesson_completion_state": "requires_teaching_receipts",
                                    "instructional_summary_reference": instructional_summary_reference,
                                    "instructional_summary_state": "available_reference" if instructional_summary_reference else "not_generated",
                                    "counts_unit": "learner_assignment_cases", "learner_ranking": False},
                "research_data": {"status": "authorized_records_only" if research_access else "restricted",
                                  "records": research, "collection_supported": False,
                                  "agenda": build_research_agenda()}},
            "actions": {kind: {"supported": False, "reason": "executor_not_connected_and_verified"}
                        for kind in EFFECTS}, "external_writes": 0}
