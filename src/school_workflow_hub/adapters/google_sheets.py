"""Bounded read-only Sheets transport; credentials and responses stay in memory.

This reader is separate from the fixture-only planners. Never relabel its real
output as synthetic input. Callers choose approved ranges and private storage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
import time
from typing import Callable, Mapping, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_BYTES = 2 * 1024 * 1024
MAX_CELLS = 5000
FIELDS = "spreadsheetId,properties(title,timeZone),sheets(properties(sheetId,title,index,hidden),data(startRow,startColumn,rowData(values(formattedValue,hyperlink,textFormatRuns(format(link(uri))),userEnteredValue(formulaValue)))))"
METADATA_FIELDS = "spreadsheetId,properties(title,timeZone),sheets(properties(sheetId,title,index,hidden))"


class SheetsReadError(ValueError):
    """A fixed code, never a response body, URL, credential, or cell value."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _column_number(value: str) -> int:
    number = 0
    for char in value:
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def validate_ranges(ranges: Tuple[str, ...]) -> Tuple[str, ...]:
    if not isinstance(ranges, tuple) or len(ranges) > 4:
        raise SheetsReadError("invalid_ranges")
    cells = 0
    for value in ranges:
        if not isinstance(value, str) or len(value) > 200:
            raise SheetsReadError("invalid_range")
        # Explicit quoted sheet and rectangular bounds; no whole sheets/columns.
        match = re.fullmatch(r"'(?:[^'\r\n]|'')+'!([A-Z]{1,3})([1-9][0-9]{0,5}):([A-Z]{1,3})([1-9][0-9]{0,5})", value)
        if not match:
            raise SheetsReadError("invalid_range")
        left, top, right, bottom = match.groups()
        width = _column_number(right) - _column_number(left) + 1
        height = int(bottom) - int(top) + 1
        if width < 1 or height < 1:
            raise SheetsReadError("invalid_range")
        cells += width * height
    if cells > MAX_CELLS or len(set(ranges)) != len(ranges):
        raise SheetsReadError("range_limit")
    return ranges


@dataclass(frozen=True)
class SheetsReader:
    access_token: str = field(repr=False)
    opener: object = field(default=None, repr=False, compare=False)
    sleeper: Callable = field(default=time.sleep, repr=False, compare=False)

    def __post_init__(self):
        if (not isinstance(self.access_token, str) or not self.access_token
                or len(self.access_token) > 8192
                or any(ord(c) < 33 or ord(c) > 126 for c in self.access_token)):
            raise SheetsReadError("invalid_credential")
        if self.opener is None:
            object.__setattr__(self, "opener", build_opener(_NoRedirect()))

    def read(self, spreadsheet_id: str, ranges: Tuple[str, ...] = ()) -> Mapping:
        if not isinstance(spreadsheet_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", spreadsheet_id):
            raise SheetsReadError("invalid_spreadsheet_id")
        validate_ranges(ranges)
        query = [("fields", FIELDS if ranges else METADATA_FIELDS)]
        query.extend(("ranges", value) for value in ranges)
        request = Request("https://sheets.googleapis.com/v4/spreadsheets/" + spreadsheet_id
                          + "?" + urlencode(query), method="GET",
                          headers={"Authorization": "Bearer " + self.access_token,
                                   "Accept": "application/json"})
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=20) as response:
                    body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise SheetsReadError("response_limit")
                try:
                    result = json.loads(body)
                except (ValueError, UnicodeError):
                    raise SheetsReadError("invalid_response") from None
                _validate_response(result, spreadsheet_id)
                return result
            except HTTPError as error:
                status = error.code
                if error.fp is not None:
                    error.close()
                if status in (429, 500, 502, 503, 504) and attempt < 2:
                    self.sleeper(2 ** attempt)
                    continue
                code = {401: "authentication_required", 403: "access_denied", 404: "source_not_found"}.get(status, "source_unavailable")
                raise SheetsReadError(code) from None
            except (URLError, OSError):
                if attempt < 2:
                    self.sleeper(2 ** attempt)
                    continue
                raise SheetsReadError("source_unavailable") from None
        raise SheetsReadError("source_unavailable")


def _validate_response(data, expected_id):
    if (not isinstance(expected_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", expected_id)
            or not isinstance(data, dict) or data.get("spreadsheetId") != expected_id
            or not isinstance(data.get("properties"), dict)
            or not isinstance(data["properties"].get("title"), str)
            or not isinstance(data.get("sheets"), list)):
        raise SheetsReadError("invalid_response")
    ids = set()
    for sheet in data["sheets"]:
        properties = sheet.get("properties", {}) if isinstance(sheet, dict) else {}
        if not isinstance(properties, dict):
            raise SheetsReadError("invalid_response")
        sheet_id = properties.get("sheetId")
        if (type(sheet_id) is not int or sheet_id < 0 or sheet_id in ids
                or not isinstance(properties.get("title"), str)):
            raise SheetsReadError("invalid_response")
        ids.add(sheet_id)


def fingerprint_snapshot(data: Mapping, key: bytes, ranges: Tuple[str, ...] = ()) -> dict:
    """Opaque comparable observation; no raw values, sheet titles or IDs escape.

    Same key + scope are mandatory for comparisons. Does not prove downstream
    links work, or establish student access. No persistence or source mutation.
    """
    if not isinstance(key, bytes) or len(key) != 32:
        raise SheetsReadError("invalid_fingerprint_key")
    validate_ranges(ranges)
    _validate_response(data, data.get("spreadsheetId") if isinstance(data, dict) else None)
    def digest(value):
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hmac.new(key, serialized.encode(), hashlib.sha256).hexdigest()
    scope = digest({"source": data["spreadsheetId"], "ranges": sorted(ranges)})
    # Stable sheet IDs survive renames. Grid responses are observed as supplied;
    # changed order may trigger conservative review but can never retarget a link.
    sheets = sorted(data["sheets"], key=lambda sheet: sheet["properties"]["sheetId"])
    content = {"properties": data["properties"], "sheets": sheets}
    return {"schema_version": "sheets-observation/v1", "scope": scope,
            "fingerprint": digest(content), "tab_count": len(sheets),
            "range_count": len(ranges), "source_reads": 1, "external_writes": 0,
            "link_access_verified": False, "student_access_verified": False}


def compare_fingerprints(previous, current: Mapping) -> dict:
    """Missing baseline is first observation; incompatible scope is never no-op."""
    for item in (current,) if previous is None else (previous, current):
        if (not isinstance(item, dict) or item.get("schema_version") != "sheets-observation/v1"
                or any(not isinstance(item.get(k), str) or not re.fullmatch(r"[0-9a-f]{64}", item[k])
                       for k in ("scope", "fingerprint"))):
            raise SheetsReadError("invalid_observation")
    if previous is None:
        return {"status": "baseline_required", "change_proven": False}
    if previous["scope"] != current["scope"]:
        return {"status": "scope_changed", "change_proven": False}
    changed = previous["fingerprint"] != current["fingerprint"]
    return {"status": "changed" if changed else "unchanged", "change_proven": changed}
