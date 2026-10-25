"""Read-only lesson prep projection. Labels come from Agenda, never inference.

This module does no I/O, link-health check, scheduling or release validation.
Unstructured source text remains visible; only explicit tags are grouped.
"""
import re
from urllib.parse import urlsplit

TAG = re.compile(r"^(BUY|PULL|PRINT|SETUP|SAFETY|SCIENCE BACKGROUND|BACKGROUND)\s*[:—–-]\s*(.*)$", re.I)
TRACK = re.compile(r"^(DEMO|INDEPENDENT(?:\s+LAB)?)\b\s*[:—–-]?\s*(.*)$", re.I)
SPLIT = re.compile(r"\n|\s+[•|]\s+|;\s*(?=(?:BUY|PULL|PRINT|SETUP|SAFETY|BACKGROUND|SCIENCE BACKGROUND|DEMO|INDEPENDENT)\b)", re.I)
RESOURCE = re.compile(r"^(?:BUY|PULL|PRINT|SETUP|SAFETY|BACKGROUND|SCIENCE BACKGROUND|DEMO|INDEPENDENT(?:\s+LAB)?|TEACHER PREP)\b", re.I)


def safe_url(value):
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and bool(parts.hostname) and not parts.username
                and not parts.password and not any(c.isspace() or ord(c) < 32 for c in value)
                and parts.port in (None, 443))
    except (ValueError, TypeError):
        return False


def project(lesson, *, biology=False):
    """Keep every prep line, and only explicitly labeled material resources.

    Rich-text URLs are retained separately, since their display labels may not
    occur literally in a plain-text line. Never guess which Form belongs to a
    track from another Form in the same row.
    """
    groups, tracks, resources, issues, seen = [], [], [], [], set()
    active = None

    def add_line(line):
        nonlocal active
        line = line.strip().lstrip("• ").strip()
        if not line:
            return
        track = TRACK.fullmatch(line) if biology else None
        if track:
            name = "Demo" if track[1].upper() == "DEMO" else "Independent Lab"
            active = next((t for t in tracks if t["label"] == name), None)
            if active is None:
                active = {"label": name, "items": [], "links": []}
                tracks.append(active)
            line = track[2].strip()
            if not line:
                return
        match = TAG.fullmatch(line)
        category, body = (match[1].upper(), match[2].strip()) if match else ("Notes", line)
        target = active["items"] if active else groups
        item = {"label": category, "text": body}
        if item not in target and body:
            target.append(item)

    for line in SPLIT.split(lesson.get("prep") or ""):
        add_line(line)

    candidates = list(lesson.get("prep_links") or [])
    candidates += [m for m in lesson.get("materials") or [] if RESOURCE.match(m.get("label") or "")]
    for item in candidates:
        url, label = item.get("url") or "", (item.get("label") or "Prep resource").strip()
        if not safe_url(url):
            warning = "A prep link has an unsupported address; check its original Agenda cell."
            if warning not in issues:
                issues.append(warning)
            continue
        if url in seen:
            continue
        seen.add(url)
        resource = {"label": label, "url": url}
        match = TRACK.match(label) if biology else None
        if match:
            name = "Demo" if match[1].upper() == "DEMO" else "Independent Lab"
            target = next((t for t in tracks if t["label"] == name), None)
            if target is None:
                target = {"label": name, "items": [], "links": []}
                tracks.append(target)
            target["links"].append(resource)
        else:
            resources.append(resource)

    if any(t["label"] == "Independent Lab" for t in tracks):
        forms = lesson.get("forms") or []
        if not forms:
            issues.append("No Turn-in Form is linked in this Agenda row. Independent Lab release is unverified.")
        else:
            issues.extend(dict.fromkeys(f["problem"] for f in forms if f.get("problem")))
            issues.append("Independent Lab Form mapping and release are not verified by these prep notes.")
    if not (groups or tracks or resources or issues):
        return None
    return {"items": groups, "tracks": tracks, "links": resources, "issues": issues,
            "link_status": "not_checked"}
