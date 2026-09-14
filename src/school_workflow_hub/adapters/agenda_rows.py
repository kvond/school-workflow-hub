"""Explicit Sheets grid -> shared AssignmentRecord, in memory only.

No inferred stable identities, formula evaluation, network, dates written to
platforms, or submission to fixture-only planners. Caller approves the layout.
"""
from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Tuple

from school_workflow_hub.core.models import AssignmentRecord
from school_workflow_hub.core.workflow import WorkflowIssue
from school_workflow_hub.workflows.schoology_gradebook import canonicalize_url, UnsafeLink


@dataclass(frozen=True)
class AgendaLayout:
    sheet_id: int
    first_row: int  # zero-based inclusive, after known headers
    last_row: int   # zero-based exclusive, bounded explicit read
    assignment_column: int
    class_column: int
    title_column: int
    due_column: int
    deck_column: int
    date_format: str = "%Y-%m-%d"

    def __post_init__(self):
        columns = self.columns()
        if (type(self.sheet_id) is not int or self.sheet_id < 0
                or type(self.first_row) is not int or type(self.last_row) is not int
                or self.first_row < 0 or not 0 < self.last_row - self.first_row <= 1000
                or any(type(c) is not int or not 0 <= c < 1000 for c in columns)
                or len(set(columns)) != len(columns)
                or self.date_format not in ("%Y-%m-%d", "%m/%d/%Y")):
            raise ValueError("invalid_agenda_layout")

    def columns(self):
        return (self.assignment_column, self.class_column, self.title_column,
                self.due_column, self.deck_column)


@dataclass(frozen=True)
class AgendaRow:
    assignment: AssignmentRecord = field(repr=False)
    deck_reference: str = field(repr=False)
    row_index: int
    source_revision: str = field(repr=False)


@dataclass(frozen=True)
class AgendaRows:
    rows: Tuple[AgendaRow, ...] = field(repr=False)
    issues: Tuple[WorkflowIssue, ...]
    complete: bool
    external_writes: int = 0


def _text(cell):
    if not isinstance(cell, dict) or not isinstance(cell.get("formattedValue", ""), str):
        raise ValueError("invalid_agenda_cell")
    value = cell.get("formattedValue", "").strip()
    if len(value) > 4000:
        raise ValueError("agenda_cell_limit")
    return value


def cell_links(cell):
    """All distinct direct links; ambiguity is never silently resolved.

    Accept a literal HYPERLINK first argument only. Computed formulas, multiple
    rich-text destinations and unsafe URLs require caller review.
    """
    candidates = []
    if cell.get("hyperlink"):
        candidates.append(cell["hyperlink"])
    for run in cell.get("textFormatRuns", []):
        uri = run.get("format", {}).get("link", {}).get("uri")
        if uri:
            candidates.append(uri)
    formula = cell.get("userEnteredValue", {}).get("formulaValue")
    if formula:
        match = re.fullmatch(r'\s*=\s*HYPERLINK\(\s*"((?:[^"]|"")+)"\s*[,;].*\)\s*', formula, re.I)
        if not match:
            raise ValueError("computed_agenda_link_requires_review")
        candidates.append(match.group(1).replace('""', '"'))
    text = _text(cell)
    if text.startswith("https://"):
        candidates.append(text)
    try:
        return tuple(sorted({canonicalize_url(value) for value in candidates}))
    except (ValueError, TypeError):
        raise ValueError("unsafe_agenda_link") from None


def normalize_agenda_rows(data, layout: AgendaLayout, *, source_revision: str,
                          coverage: Tuple[int, int, int, int]) -> AgendaRows:
    """coverage=(first_row,last_row,first_column,last_column), exclusive ends.

    Coverage must describe the complete rectangle actually requested from the
    source. Sparse trailing empty cells are valid; incomplete scope blocks all
    rows. This pure mapper cannot establish reader provenance on its own.
    """
    issues = []
    if not isinstance(source_revision, str) or not re.fullmatch(r"[0-9a-f]{64}", source_revision):
        raise ValueError("invalid_agenda_revision")
    if (not isinstance(coverage, tuple) or len(coverage) != 4
            or any(type(n) is not int or n < 0 for n in coverage)):
        raise ValueError("invalid_agenda_coverage")
    top, bottom, left, right = coverage
    if (top > layout.first_row or bottom < layout.last_row
            or left > min(layout.columns()) or right <= max(layout.columns())
            or (bottom - top) * (right - left) > 5000):
        return AgendaRows((), (WorkflowIssue("incomplete_agenda_coverage", "error"),), False)
    try:
        sheets = [s for s in data["sheets"] if s["properties"]["sheetId"] == layout.sheet_id]
        if len(sheets) != 1 or "data" not in sheets[0] or not isinstance(sheets[0]["data"], list):
            raise ValueError("agenda_sheet_missing_or_duplicate")
        cells = {}
        for grid in sheets[0].get("data", []):
            row_start, col_start = grid.get("startRow", 0), grid.get("startColumn", 0)
            if type(row_start) is not int or type(col_start) is not int or min(row_start, col_start) < 0:
                raise ValueError("invalid_agenda_grid")
            for ri, row in enumerate(grid.get("rowData", []), row_start):
                for ci, cell in enumerate(row.get("values", []), col_start):
                    if not (top <= ri < bottom and left <= ci < right):
                        raise ValueError("agenda_grid_outside_coverage")
                    if (ri, ci) in cells:
                        raise ValueError("overlapping_agenda_grid")
                    cells[ri, ci] = cell
        result = []
        for ri in range(layout.first_row, layout.last_row):
            selected = [cells.get((ri, c), {}) for c in layout.columns()]
            if not any(_text(c) or c.get("hyperlink") or c.get("textFormatRuns") or c.get("userEnteredValue") for c in selected):
                continue
            try:
                assignment, class_id, title, due = (_text(c) for c in selected[:4])
                for identity in (assignment, class_id):
                    if not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", identity) or identity == "UNVERIFIED":
                        raise ValueError("missing_stable_agenda_identity")
                if not title:
                    raise ValueError("missing_agenda_title")
                try:
                    parsed_due = datetime.strptime(due, layout.date_format).date().isoformat()
                except ValueError:
                    raise ValueError("invalid_agenda_date") from None
                links = cell_links(selected[4])
                if len(links) != 1:
                    raise ValueError("missing_or_ambiguous_agenda_link")
                # deck_file_id is unknown unless verified separately; never use
                # a URL, row position or title as an external identity.
                record = AssignmentRecord(assignment, class_id, title, parsed_due)
                result.append(AgendaRow(record, links[0], ri, source_revision))
            except ValueError as error:
                issues.append(WorkflowIssue(str(error), "error"))
        counts = {}
        for row in result:
            counts[row.assignment.stable_id] = counts.get(row.assignment.stable_id, 0) + 1
        if any(count > 1 for count in counts.values()):
            issues.append(WorkflowIssue("duplicate_agenda_identity", "error"))
            result = [row for row in result if counts[row.assignment.stable_id] == 1]
        return AgendaRows(tuple(result), tuple(issues), not issues)
    except (KeyError, TypeError, AttributeError, ValueError):
        return AgendaRows((), (WorkflowIssue("invalid_agenda_grid", "error"),), False)
