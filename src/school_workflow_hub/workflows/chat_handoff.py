"""Build the block the teacher pastes into a free chat account, and the schema
its answer must come back in.

No model is called here and no external request is made. This module composes
text and refuses to compose it when a precondition fails, which is the whole of
its job. The teacher carries the block to the assistant; the returned records
come back through `validate_return`.
"""
import re

SCHEMA_VERSION = "hub-chat-handoff/v1"

# Part 3 compares students, so it takes the class at once. Parts 1 and 2 batch.
TASK_LIMITS = {"feedback": 20, "summary": None, "research": 20}

# The compiled records the feedback prompt already asks for in prose. Naming them
# is what lets the class summary and the year graphs read a return instead of a
# teacher retyping it.
RETURN_FIELDS = (
    "learner_alias", "class_stable_id", "assignment_stable_id", "concept_id",
    "assessed_on", "stage", "prompt_version", "rubric_version",
    "source_revision", "evidence_quote", "evidence_reference",
    "understanding_level", "explanation_descriptor", "comparison_evidence",
    "notes_count", "bank_count", "draft_count",
    "later_completion", "earlier_evidence", "teacher_discussion_idea",
)

STAGES = frozenset({"initial", "final"})
from .understanding_scale import ALL_LEVELS, UNAVAILABLE

UNDERSTANDING = frozenset({*ALL_LEVELS, UNAVAILABLE})

# A first name and an initial, nothing more. Anything longer is a real name and
# must not leave the teacher's side.
ALIAS = re.compile(r"^[^\s]+(?: [A-Za-z]\.?)?$")

# A chat account cannot reach Drive, the Agenda sheet or the hosted dashboard:
# the dashboard sits behind an identity proxy and the school Google account does
# not extend to the chat accounts. An instruction that asks the assistant to read
# one of those, or to verify an account, a browser profile or a schedule, makes it
# refuse the whole task rather than do the work. So every block states that it is
# self-contained, and no line below may name a system to open or a check to run.
SELF_CONTAINED = (
    "HOW TO USE THIS BLOCK",
    "Everything you need is in this block and in the work pasted after it. Do not",
    "open Drive, the Agenda sheet, the dashboard or any other system: you cannot",
    "reach them, and nothing here requires them.",
    "Do not verify accounts, browser profiles, schedules or permissions. Those are",
    "the teacher's checks, they are already done, and you cannot see them.",
    "The prompt below is the one in force. Do not change it. If it looks wrong, say",
    "so in one line and return the records you can.",
)

TASK_RULES = {
    "feedback": (
        "Work only from the student writing pasted after this block.",
        "Use the aliases given above. Never ask for, infer or write a real name.",
        "If a student's work cannot be assessed, return that student with",
        "understanding_level \"Evidence unavailable\" rather than leaving them out.",
        "Do not write to, send from, or print from any school system.",
    ),
    "summary": (
        "Work from the whole class at once. This stage compares students, so it is",
        "not batched.",
        "Report what the assessed evidence shows. Do not infer a cause you cannot",
        "see in the evidence, and do not rank students.",
        "Where a count has a zero denominator, say N/A rather than 0: they mean",
        "different things and are stored differently.",
        "Do not write to, send from, or print from any school system.",
    ),
    "research": (
        "Code only the records pasted after this block. Collection is disabled: do",
        "not look for further records.",
        "Where a record supports no code in the codebook given, return it uncoded",
        "with a one-line reason. An uncoded record is a finding about the codebook,",
        "not a failure, so do not force a fit.",
        "Aliases only. A coded entry is never a finding about a named student.",
    ),
}


def _check_aliases(aliases):
    if not aliases:
        raise ValueError("empty_batch")
    for alias in aliases:
        if not ALIAS.match(alias.strip()):
            raise ValueError("alias_is_not_first_name_and_initial")
    if len(set(aliases)) != len(aliases):
        raise ValueError("duplicate_alias_in_batch")


def _check_prompt_in_sync(prompts, task):
    if task not in prompts.get("prompts", {}):
        raise ValueError("unknown_task")
    matches = prompts.get("bundled_matches")
    if matches is not None and matches.get(task) is False:
        raise ValueError("prompt_out_of_sync_with_agenda")


def build_handoff(task, prompts, context, batch, destination):
    """Compose one paste block. Raises rather than emitting a doubtful block."""
    if task not in TASK_LIMITS:
        raise ValueError("unknown_task")
    _check_prompt_in_sync(prompts, task)
    aliases = list(batch.get("aliases", ()))
    _check_aliases(aliases)
    limit = TASK_LIMITS[task]
    if limit is not None and len(aliases) > limit:
        raise ValueError("batch_over_limit")
    if context.get("stage") not in STAGES:
        raise ValueError("unknown_stage")
    for required in ("class_display_name", "lesson_date", "assignment_title", "folder_reference"):
        if not str(context.get(required, "")).strip():
            raise ValueError("missing_context_" + required)

    header = [
        "CONTEXT — supplied by the hub, do not edit",
        "Class: {}".format(context["class_display_name"]),
        "Lesson date: {}".format(context["lesson_date"]),
        "Assignment: {}".format(context["assignment_title"]),
        "Checkpoint: {}".format(context["stage"]),
        "Prompt version: {} (sha256 {})".format(
            prompts.get("version_label", "unknown"),
            prompts.get("sha256", {}).get(task, "unknown")),
        "Batch: {} of {}, {} student{}".format(
            batch.get("index", 1), batch.get("total", 1),
            len(aliases), "" if len(aliases) == 1 else "s"),
        "Submissions folder: {}".format(context["folder_reference"]),
        "Students in this batch: " + ", ".join(aliases),
        "",
        *SELF_CONTAINED,
        *TASK_RULES[task],
        "",
        "RETURN — end your reply with one fenced block per student, exactly these fields:",
        "  " + ", ".join(RETURN_FIELDS),
        "Use the alias above as learner_alias. Leave a field empty rather than guessing.",
        "",
        "PROMPT",
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "task": task,
        "destination": {"account": destination["account"], "profile": destination["profile"]},
        "batch": {"index": batch.get("index", 1), "total": batch.get("total", 1),
                  "aliases": tuple(aliases)},
        "prompt_version": prompts.get("version_label"),
        "prompt_sha256": prompts.get("sha256", {}).get(task),
        "return_fields": RETURN_FIELDS,
        "self_contained": True,
        "block": "\n".join(header) + "\n" + prompts["prompts"][task],
        "external_writes": 0,
        "live_model_calls": 0,
    }


def validate_return(handoff, records):
    """Accept the records this batch asked for. Partial is fine; wrong is not."""
    if handoff.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unknown_handoff")
    expected = set(handoff["batch"]["aliases"])
    seen, accepted = set(), []
    for record in records:
        if set(record) - set(RETURN_FIELDS):
            raise ValueError("unexpected_field_in_return")
        alias = record.get("learner_alias", "")
        if alias not in expected:
            raise ValueError("alias_not_in_batch")
        if alias in seen:
            raise ValueError("duplicate_record_for_alias")
        if record.get("prompt_version") != handoff["prompt_version"]:
            raise ValueError("return_composed_against_another_prompt_version")
        level = record.get("understanding_level")
        if level and level not in UNDERSTANDING:
            raise ValueError("unknown_understanding_level")
        seen.add(alias)
        accepted.append(record)
    return {"schema_version": SCHEMA_VERSION, "task": handoff["task"],
            "accepted": tuple(accepted), "returned": len(accepted),
            "expected": len(expected), "still_waiting": tuple(sorted(expected - seen)),
            "complete": not (expected - seen), "external_writes": 0, "live_model_calls": 0}
