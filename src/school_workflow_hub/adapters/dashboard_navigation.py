"""Bounded row-2 daily-link discovery from the live Agenda Dashboard.

Uses the existing read-only SheetsReader interface. Returned links are private
source evidence, not verified destinations. Never follows links or edits cells.
"""
import copy
import re

from school_workflow_hub.adapters.agenda_rows import cell_links


def _column(index):
    result = ""
    value = index + 1
    while value:
        value, rem = divmod(value - 1, 26)
        result = chr(65 + rem) + result
    return result


def _resolve_fragment(value, source_id):
    if isinstance(value, str) and value.startswith("#gid="):
        if not re.fullmatch(r"#gid=[0-9]+(?:&range=[A-Za-z0-9:%!_$.-]+)?", value):
            raise ValueError("unverified_dashboard_fragment")
        return "https://docs.google.com/spreadsheets/d/" + source_id + "/edit" + value
    return value


def read_dashboard_navigation(reader, *, spreadsheet_id, dashboard_title,
                              first_column=0, last_column=26):
    """Metadata first, then one bounded rectangle on the user-identified row 2.

    last_column is zero-based exclusive. If more columns are needed, caller
    deliberately reads a subsequent small segment; do not call this a complete
    year inventory. Cell labels like Mon14 are never converted to calendar dates
    or assignment identities. The selected daily page still needs a real read.
    """
    if (not isinstance(spreadsheet_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", spreadsheet_id)
            or not isinstance(dashboard_title, str) or not dashboard_title
            or len(dashboard_title) > 100 or any(ord(c) < 32 for c in dashboard_title)
            or type(first_column) is not int or type(last_column) is not int
            or not 0 <= first_column < last_column <= 1000
            or last_column - first_column > 100):
        raise ValueError("invalid_dashboard_navigation_scope")
    empty = {"status": "blocked", "entries": [], "external_writes": 0,
             "link_access_verified": False, "daily_lessons_read": False}
    try:
        metadata = reader.read(spreadsheet_id)
        matches = [s["properties"] for s in metadata["sheets"]
                   if s["properties"]["title"] == dashboard_title]
        if metadata["spreadsheetId"] != spreadsheet_id or len(matches) != 1:
            return dict(empty, issue_codes=["dashboard_metadata_mismatch"])
        sheet_id = matches[0]["sheetId"]
        if type(sheet_id) is not int or sheet_id < 0:
            return dict(empty, issue_codes=["dashboard_metadata_mismatch"])
        selected_range = "'{}'!{}2:{}2".format(dashboard_title.replace("'", "''"),
                                                _column(first_column), _column(last_column - 1))
        data = reader.read(spreadsheet_id, (selected_range,))
        matches = [s for s in data["sheets"] if s["properties"]["sheetId"] == sheet_id
                   and s["properties"]["title"] == dashboard_title]
        if data["spreadsheetId"] != spreadsheet_id or len(matches) != 1:
            return dict(empty, issue_codes=["dashboard_changed_during_read"])
        grids = matches[0].get("data")
        if not isinstance(grids, list) or not grids:
            return dict(empty, issue_codes=["dashboard_row_data_unavailable"])
        # The request is one rectangle. Missing grid evidence is different from
        # a returned empty row, and even empty out-of-scope grids must fail.
        if len(grids) != 1:
            return dict(empty, issue_codes=["dashboard_grid_scope_mismatch"])
        grid = grids[0]
        row_start, column_start = grid.get("startRow", 0), grid.get("startColumn", 0)
        rows = grid.get("rowData", [])
        if (type(row_start) is not int or row_start != 1
                or type(column_start) is not int or column_start != first_column
                or not isinstance(rows, list) or len(rows) > 1):
            return dict(empty, issue_codes=["dashboard_grid_scope_mismatch"])
        entries, seen, issues = [], set(), []
        for grid in grids:
            for ri, row in enumerate(grid.get("rowData", []), grid.get("startRow", 0)):
                for ci, original in enumerate(row.get("values", []), grid.get("startColumn", 0)):
                    if ri != 1 or not first_column <= ci < last_column or ci in seen:
                        return dict(empty, issue_codes=["dashboard_grid_scope_mismatch"])
                    seen.add(ci)
                    cell = copy.deepcopy(original)
                    label = cell.get("formattedValue", "")
                    if not isinstance(label, str) or len(label) > 1000:
                        return dict(empty, issue_codes=["invalid_dashboard_label"])
                    try:
                        if cell.get("hyperlink"):
                            cell["hyperlink"] = _resolve_fragment(cell["hyperlink"], spreadsheet_id)
                        for run in cell.get("textFormatRuns", []):
                            link = run.get("format", {}).get("link", {})
                            if link.get("uri"):
                                link["uri"] = _resolve_fragment(link["uri"], spreadsheet_id)
                        formula = cell.get("userEnteredValue", {}).get("formulaValue", "")
                        # Resolve only a literal internal first argument, never
                        # execute or infer a computed spreadsheet formula.
                        match = re.match(r'(\s*=\s*HYPERLINK\(\s*")(#gid=[^"]+)(")', formula, re.I)
                        if match:
                            resolved = _resolve_fragment(match.group(2), spreadsheet_id)
                            cell["userEnteredValue"]["formulaValue"] = (
                                match.group(1) + resolved + formula[match.end(2):])
                        links = cell_links(cell)
                    except (ValueError, TypeError, AttributeError):
                        issues.append("dashboard_link_requires_review")
                        continue
                    if not label.strip() and not links:
                        continue
                    state = "candidate" if len(links) == 1 else "unlinked" if not links else "ambiguous"
                    if state == "ambiguous":
                        issues.append("dashboard_cell_has_multiple_targets")
                    entries.append({"cell": _column(ci) + "2", "label": label,
                                    "destinations": list(links), "status": state})
        return dict(empty, status="needs_review" if issues else "observed", entries=entries,
                    issue_codes=sorted(set(issues)), inspected_range=selected_range,
                    coverage="requested_row_segment_only")
    except Exception:
        # Never include exception strings: transports can echo tokens or URLs.
        return dict(empty, issue_codes=["dashboard_read_failed"])
