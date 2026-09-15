"""Supportive feedback for a verified absent submission, never an inferred grade."""
from datetime import datetime
import re
from .completion_policy import _key

MESSAGE = ("I would really like to tell you how your explanations are improving, "
           "but I do not have submitted work from this lesson to read yet. "
           "Your ideas matter, and I need to see them to give you useful feedback.")
COMMITMENT = (
    "For the next assignment, I agree to show my thinking and submit my work.",
    "One step I will take: ________________________________________",
    "When I will do it: __________________________________________",
    "Help I need or something I want my teacher to know: ___________",
    "____________________________________________________________",
    "Student signature: ____________________  Date: ______________",
)

def build_missing_work_feedback(*, class_key, assignment_key, learner_key, display_name,
                                checked_at, evidence_reference, missingness_verified=False,
                                roster_identity_verified=False):
    for value in (class_key, assignment_key, learner_key, evidence_reference):
        if not _key(value):
            raise ValueError("invalid_missing_feedback_identity")
    if (missingness_verified is not True or roster_identity_verified is not True
            or not isinstance(display_name,str)
            or not re.fullmatch(r"[A-Za-zÀ-ÿ'-]+ [A-Za-zÀ-ÿ]\.", display_name)):
        raise ValueError("missing_feedback_not_verified")
    stamp=datetime.fromisoformat(checked_at.replace("Z","+00:00"))
    if stamp.utcoffset() is None:
        raise ValueError("missing_feedback_check_time_required")
    return {"report_version":"missing-work/v1", "status":"prepared_for_review",
        "class_key":class_key,"assignment_key":assignment_key,"learner_key":learner_key,
        "display_name":display_name,"checked_at":checked_at,
        "evidence_reference":evidence_reference,"understanding":None,"explanation":None,
        "message":MESSAGE,"contract_heading":"My commitment for next time",
        "contract_lines":list(COMMITMENT),"grade_change":False,"external_writes":0}
