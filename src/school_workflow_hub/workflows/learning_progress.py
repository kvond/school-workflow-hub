"""Evidence-bound learning observations in the existing private state database.

No source/model calls. The host supplies reviewed observations from actual
writing and an approved database connection. This does not configure live storage.
"""
from collections import defaultdict
from datetime import date
import json
from ..core.database import transaction
from .completion_policy import _key

from .understanding_scale import ALL_LEVELS

# Series are already separated by rubric_version, so older three-level points
# never share a line with the four-level critical-aspect scale.
UNDERSTANDING = ALL_LEVELS
EXPLANATION = ("Names an idea", "Connects ideas", "Explains how or why")
SCOPE = ("class_key", "learner_key", "concept_scope", "stage", "rubric_version")


def validate_observation(row):
    if not isinstance(row, dict):
        raise ValueError("invalid_learning_observation")
    for key in ("observation_id", "class_key", "learner_key", "assignment_key",
                "concept_scope", "rubric_version", "prompt_version", "submission_revision",
                "evidence_reference", "assessment_reference"):
        if not _key(row.get(key)):
            raise ValueError("invalid_learning_identity")
    if (row.get("stage") not in {"initial", "final"}
            or row.get("reviewed") is not True
            or row.get("provenance") not in {"source_read", "synthetic"}
            or row.get("understanding") not in (*UNDERSTANDING, None)
            or row.get("explanation") not in (*EXPLANATION, None)
            or not isinstance(row.get("quote"), str)
            or not isinstance(row.get("source_text"), str)
            or row["quote"] not in row["source_text"]
            or not isinstance(row.get("concept_label"), str)
            or not row["concept_label"].strip()
            or len(json.dumps(row)) > 50000):
        raise ValueError("unverified_learning_evidence")
    if (row.get("understanding") or row.get("explanation")) and not row["quote"].strip():
        raise ValueError("blank_learning_evidence")
    try:
        if date.fromisoformat(row["date"]).isoformat() != row["date"] or date.fromisoformat(row["date"]) > date.today():
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("invalid_learning_date") from None
    return row


def record_observation(connection, row):
    """Immutable/idempotent insert into the shared state DB, preserving history."""
    validate_observation(row)
    encoded = json.dumps(row, sort_keys=True, ensure_ascii=False)
    with transaction(connection):
        prior = connection.execute("SELECT payload_json FROM learning_observations WHERE observation_id=?",
                                   (row["observation_id"],)).fetchone()
        if prior:
            if prior[0] != encoded:
                raise ValueError("learning_observation_conflict")
            return False
        connection.execute("INSERT INTO learning_observations VALUES (?,?,?,?,?,?)",
                           (row["observation_id"], row["class_key"], row["learner_key"],
                            row["date"], row["provenance"], encoded))
        return True


def read_observations(connection, *, class_key, start_date, end_date, provenance):
    if not _key(class_key) or provenance not in {"source_read", "synthetic"}:
        raise ValueError("invalid_learning_scope")
    for value in (start_date, end_date):
        date.fromisoformat(value)
    if start_date > end_date:
        raise ValueError("invalid_learning_window")
    return [json.loads(r[0]) for r in connection.execute(
        "SELECT payload_json FROM learning_observations WHERE class_key=? AND observed_date>=? AND observed_date<=? "
        "AND provenance=? ORDER BY observed_date,observation_id",
        (class_key, start_date, end_date, provenance))]


def build_learning_progress(rows, *, class_key, provenance):
    """Separate comparable concepts/stages/rubrics; never average ordinal levels."""
    if not isinstance(rows, (list, tuple)) or len(rows) > 10000:
        raise ValueError("learning_history_limit")
    unique, groups, distributions = {}, defaultdict(list), {}
    for source in rows:
        row = validate_observation(source)
        if row["class_key"] != class_key or row["provenance"] != provenance:
            raise ValueError("learning_history_scope_mismatch")
        old = unique.get(row["observation_id"])
        if old is not None:
            if old != row:
                raise ValueError("learning_observation_conflict")
            continue
        unique[row["observation_id"]] = row
        groups[tuple(row[k] for k in SCOPE)].append(row)
    series = []
    for key, observations in sorted(groups.items()):
        observations.sort(key=lambda r: (r["date"], r["observation_id"]))
        # Multiple assessments on the same day need an explicit selection upstream;
        # do not arbitrarily connect them or count one learner twice.
        if len({r["date"] for r in observations}) != len(observations):
            raise ValueError("ambiguous_learning_date")
        points = [{k: r.get(k) for k in ("observation_id", "date", "assignment_key",
                   "understanding", "explanation", "quote", "evidence_reference", "submission_revision")}
                  for r in observations]
        series.append({**dict(zip(SCOPE, key)), "concept_label": observations[-1]["concept_label"],
                       "points": points, "trend_available": len(points) > 1})
        for r in observations:
            dkey = (r["date"], r["concept_scope"], r["stage"], r["rubric_version"])
            bucket = distributions.setdefault(dkey, {"date": r["date"], "concept_scope": r["concept_scope"],
                "concept_label": r["concept_label"], "stage": r["stage"], "rubric_version": r["rubric_version"],
                "sample_size": 0, "understanding": dict.fromkeys((*UNDERSTANDING, "Unavailable"), 0),
                "explanation": dict.fromkeys((*EXPLANATION, "Unavailable"), 0)})
            bucket["sample_size"] += 1
            for dimension in ("understanding", "explanation"):
                bucket[dimension][r.get(dimension) or "Unavailable"] += 1
    return {"schema_version": "learning-progress/v1", "class_key": class_key,
            "provenance": provenance, "series": series,
            "distributions": [distributions[k] for k in sorted(distributions)],
            "coverage": "observed_work_only", "external_writes": 0}
