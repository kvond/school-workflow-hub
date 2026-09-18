"""Third-meeting deadline rule derived from the teacher's own Plan/Notes text.

The teacher does not enter due dates. Writing an actual start ("Started Energy
today") in the day's Plan or Notes makes that dashboard date MEETING 1, and the
final revised submission is due at the end of the THIRD actual class meeting's
day in school time, whether or not the topic is finished. Meetings are counted
from the Agenda's own verified calendar: fixed weekday patterns per class, the
week closure lines, and the per-week A/B Friday designation. Nothing here
guesses: an unparseable week or an unknown Friday makes the result provisional,
and contradictory starts are flagged instead of resolved.
"""
from datetime import date, timedelta
import re

VERSION = "hub-topic-deadline/v1"
# Monday-to-Thursday meeting weekdays per class; Fridays alternate per week.
MEETING_WEEKDAYS = {"biology": (0, 2), "forensics": (0, 2), "anatomy": (1, 3)}
FRIDAY = 4
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MEETINGS_REQUIRED = 3
MAX_CALENDAR_DAYS = 60

_ACTUAL = re.compile(r"\b(?:started|began|kicked\s*off)\b", re.IGNORECASE)
_TODAY_START = re.compile(r"\bstart(?:ing|ed)?\b[^\n.;]{0,40}\btoday\b", re.IGNORECASE)
_INTENTION = re.compile(
    r"\b(?:plan(?:ning)?\s+to|will|going\s+to|hope\s+to|want\s+to|next\s+(?:class|week|time)|tomorrow)\b"
    r"[^\n.;]{0,40}\bstart", re.IGNORECASE)
_EXCEPTION = re.compile(
    r"\b(?:deadline|due)\b[^\n.;]{0,40}?\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)",
    re.IGNORECASE)


def _excerpt(text, match):
    line = next((l for l in text.splitlines() if match.group(0) in l), text)
    return line.strip()[:120]


def _exception_date(token, anchor):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
        return date.fromisoformat(token).isoformat()
    parts = [int(p) for p in token.split("/")]
    month, day = parts[0], parts[1]
    year = parts[2] if len(parts) == 3 else anchor.year
    if year < 100:
        year += 2000
    candidate = date(year, month, day)
    if len(parts) == 2 and candidate < anchor:
        candidate = date(year + 1, month, day)
    return candidate.isoformat()


def detect_declarations(plan, notes, saved_date):
    """Read the teacher's own words for one dashboard date, conservatively.

    An actual start uses a past-tense start verb (or "start ... today"); a
    future intention ("Plan to start Energy") is recorded as an intention and
    never becomes a start. "Today" is the selected dashboard date even when the
    note is written afterward. An explicit deadline exception needs the word
    deadline/due followed by an actual date.
    """
    anchor = date.fromisoformat(saved_date)
    result = {"start": None, "intention": None, "exception": None}
    for field, text in (("plan", plan or ""), ("notes", notes or "")):
        if not isinstance(text, str):
            continue
        actual = _ACTUAL.search(text) or _TODAY_START.search(text)
        intention = _INTENTION.search(text)
        if actual and not result["start"]:
            result["start"] = {"date": saved_date, "field": field,
                               "excerpt": _excerpt(text, actual)}
        elif intention and not result["intention"]:
            result["intention"] = {"field": field, "excerpt": _excerpt(text, intention)}
        exception = _EXCEPTION.search(text)
        if exception and not result["exception"]:
            try:
                result["exception"] = {"date": _exception_date(exception.group(1), anchor),
                                       "field": field,
                                       "excerpt": _excerpt(text, exception)}
            except ValueError:
                pass
    return result


def parse_week_calendar(week_start, closures_text):
    """One week's closures cell -> closed dates and the Friday class set.

    Closure lines look like "⚠ Mon 09/07 Labor Day"; the Friday line looks like
    "Fri 09/11 = A Friday → Bio + Forensics". A ⚠ line that cannot be verified
    against the week's own dates marks the week unparsed rather than guessed.
    """
    closed, friday_classes, parsed = set(), None, True
    for line in (closures_text or "").splitlines():
        line = line.strip()
        if not line or ("–" in line and re.search(r"\b\d{4}\b", line)):
            continue  # the week range header
        closure = re.match(r"⚠\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})/(\d{1,2})", line)
        friday = re.match(r"(⚠\s*)?Fri\s+(\d{1,2})/(\d{1,2})\s*=\s*[AB]\s*Friday\s*(?:→|->|-)\s*(.+)",
                          line, re.IGNORECASE)
        if closure:
            offset = WEEKDAYS.index(closure.group(1))
            day = week_start + timedelta(days=offset)
            if (day.month, day.day) != (int(closure.group(2)), int(closure.group(3))):
                parsed = False
                continue
            closed.add(day)
        elif friday:
            day = week_start + timedelta(days=FRIDAY)
            if (day.month, day.day) != (int(friday.group(2)), int(friday.group(3))):
                parsed = False
                continue
            names = friday.group(4).lower()
            friday_classes = {key for key, token in
                              (("biology", "bio"), ("forensics", "forensic"), ("anatomy", "a&p"))
                              if token in names}
            if "anatomy" in names:
                friday_classes.add("anatomy")
        elif line.startswith("⚠"):
            parsed = False
    return {"closed": closed, "friday_classes": friday_classes, "parsed": parsed}


def _meets(class_key, day, week):
    """True/False when the calendar answers; None when it cannot."""
    if week is None or not week["parsed"]:
        return None
    if day in week["closed"]:
        return False
    if day.weekday() in MEETING_WEEKDAYS[class_key]:
        return True
    if day.weekday() == FRIDAY:
        if week["friday_classes"] is None:
            return None
        return class_key in week["friday_classes"]
    return False


def derive_deadline(class_key, start_date, weeks, *, exception_date=None):
    """Count MEETING 1 = the declared start, then the next actual meetings.

    weeks maps each week's Monday (ISO date string) to parse_week_calendar
    output. Continuing or not finishing the topic never moves the deadline;
    only an explicit teacher exception overrides it. Unknown calendar stretches
    make the result provisional instead of guessed.
    """
    if class_key not in MEETING_WEEKDAYS:
        raise ValueError("invalid_deadline_class")
    start = date.fromisoformat(start_date)
    meetings, flags = [start.isoformat()], []
    calendar = {date.fromisoformat(k): v for k, v in weeks.items()}

    def week_of(day):
        return calendar.get(day - timedelta(days=day.weekday()))

    start_week = week_of(start)
    if start_week is not None and start_week["parsed"] and start in start_week["closed"]:
        flags.append("start_on_closure_needs_review")
    day, provisional = start, False
    while len(meetings) < MEETINGS_REQUIRED:
        day = day + timedelta(days=1)
        if (day - start).days > MAX_CALENDAR_DAYS:
            provisional = True
            flags.append("calendar_horizon_exceeded")
            break
        answer = _meets(class_key, day, week_of(day))
        if answer is None:
            provisional = True
            flags.append("calendar_unverified_from_" + day.isoformat())
            break
        if answer:
            meetings.append(day.isoformat())
    result = {"schema_version": VERSION, "class_key": class_key, "start_date": start_date,
              "meetings": meetings, "meetings_required": MEETINGS_REQUIRED,
              "provisional": provisional, "flags": flags,
              "ic_alignment": "calculated_not_verified_in_ic"}
    if exception_date:
        date.fromisoformat(exception_date)
        result.update(due_date=exception_date, basis="teacher_exception")
    elif len(meetings) == MEETINGS_REQUIRED and not provisional:
        result.update(due_date=meetings[-1], basis="third_meeting_rule")
    else:
        result.update(due_date=None, basis="calendar_incomplete")
    return result
