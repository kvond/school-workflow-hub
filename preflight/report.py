"""Render a run as a short page: exceptions first, then the print packet, supplies and conversions.

Nothing that passed is listed. The page is meant to be read in two minutes on Thursday.
"""

from __future__ import annotations

import html
import re
from collections import defaultdict

from preflight.models import Severity
from preflight.runner import Result

HEAD = {
    Severity.BLOCKER: "Fix before class",
    Severity.FIX: "Fix this week",
    Severity.CHECK: "Worth a look",
}


def _counts(result: Result) -> str:
    parts = []
    for sev in (Severity.BLOCKER, Severity.FIX, Severity.CHECK):
        n = len(result.by_severity(sev))
        if n:
            parts.append(f"{n} {HEAD[sev].lower()}")
    return ", ".join(parts) or "nothing to fix"


def markdown(result: Result, max_per_course: int = 12) -> str:
    ctx = result.ctx
    w = ctx.week
    lines = [f"# Preflight: {ctx.label} ({w.monday:%b %-d}–{w.dates[-1]:%b %-d})", ""]
    sides = f"{w.friday_side} Friday" if w.friday_side else "Friday side unknown"
    closed = "; ".join(f"{d:%a %-m/%-d} {r}" for d, r in w.closures.items())
    lines.append(f"{sides}{'; closed ' + closed if closed else ''}. {_counts(result)}.")
    lines.append("")

    for sev in (Severity.BLOCKER, Severity.FIX, Severity.CHECK):
        items = result.by_severity(sev)
        if not items:
            continue
        lines.append(f"## {HEAD[sev]}")
        by_course = defaultdict(list)
        for f in items:
            by_course[f.course].append(f)
        for course, fs in by_course.items():
            lines.append(f"**{course}**")
            for f in fs[:max_per_course]:
                tail = f" — {f.fix}" if f.fix else ""
                link = f" [open]({f.where})" if f.where.startswith("http") else ""
                lines.append(f"- {f.message}{tail}{link}")
            if len(fs) > max_per_course:
                lines.append(f"- …and {len(fs) - max_per_course} more of the same kind (full list in preflight.json).")
            lines.append("")

    if result.prints:
        lines += ["## Monday print packet", "", "| Course | First needed | Item | Copies |", "|---|---|---|---|"]
        for p in result.prints:
            item = f"[{p.item}]({p.link})" if p.link else p.item
            lines.append(f"| {p.course} | {p.day} | {item} | {p.copies} |")
        lines.append("")

    if result.supplies:
        lines.append("## Supplies")
        names = {"buy": "Buy", "on-order": "On order — confirm it arrived", "pull": "Pull from the lab"}
        for status in ("buy", "on-order", "pull"):
            group = [s for s in result.supplies if s.status == status]
            if group:
                items = "; ".join(f"{s.item} ({s.course}{', by ' + s.needed_by if s.needed_by else ''})" for s in group)
                lines.append(f"- **{names[status]}:** {items}")
        lines.append("")

    convert = [c for c in result.conversions if not c.keep_on_paper]
    if convert:
        lines.append("## Paper you may not need")
        for c in convert:
            lines.append(f"- {c.course} — {c.item}: {c.suggestion}")
        lines.append("")
        for title, spec in result.slide_specs.items():
            lines += [f"<details><summary>Slide text for {title}</summary>", "", "```", spec, "```", "</details>", ""]

    if result.not_checked:
        lines.append("_Not checked: " + "; ".join(result.not_checked) + "._")
    return "\n".join(lines).rstrip() + "\n"


def to_html(result: Result) -> str:
    """Minimal HTML for Drive's Google Docs import (headings, lists and a table convert cleanly)."""
    md = markdown(result)
    out = ["<html><body>"]
    in_list = in_table = False
    for raw in md.splitlines():
        line = html.escape(raw)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', line)
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        if raw.startswith("|"):
            if raw.startswith("|---"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not in_table:
                out.append("<table border='1'>")
                in_table = True
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if raw.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if raw.startswith("# "):
            out.append(f"<h1>{line[2:]}</h1>")
        elif raw.startswith("## "):
            out.append(f"<h2>{line[3:]}</h2>")
        elif raw.strip():
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    if in_table:
        out.append("</table>")
    out.append("</body></html>")
    return "\n".join(out)


def to_json(result: Result) -> dict:
    return {
        "week": result.ctx.label,
        "findings": [
            {"severity": f.severity.name, "course": f.course, "area": f.area, "message": f.message,
             "fix": f.fix, "where": f.where, "day": f.day}
            for f in result.findings
        ],
        "print_packet": [vars(p) for p in result.prints],
        "supplies": [vars(s) for s in result.supplies],
        "conversions": [vars(c) for c in result.conversions],
        "not_checked": result.not_checked,
    }
