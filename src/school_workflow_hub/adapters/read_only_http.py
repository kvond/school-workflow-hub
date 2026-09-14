"""Shared bounded GET transport for the two implemented source endpoints."""
import json
import re
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

MAX_BYTES = 2 * 1024 * 1024


class SourceReadError(ValueError):
    """Fixed public error code; never includes provider content or credentials."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_token(token):
    if (not isinstance(token, str) or not token or len(token) > 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in token)):
        raise SourceReadError("invalid_credential")


def read_json(url, token, *, opener=None, sleeper=time.sleep):
    validate_token(token)
    try:
        parsed = urlsplit(url)
    except (ValueError, TypeError):
        raise SourceReadError("unsupported_read_endpoint") from None
    valid_path = ((parsed.netloc == "sheets.googleapis.com" and
                   re.fullmatch(r"/v4/spreadsheets/[A-Za-z0-9_-]{20,128}", parsed.path))
                  or (parsed.netloc == "api.todoist.com" and parsed.path == "/api/v1/tasks"))
    if parsed.scheme != "https" or parsed.fragment or not valid_path:
        raise SourceReadError("unsupported_read_endpoint")
    request = Request(url, method="GET", headers={"Authorization": "Bearer " + token,
                                                 "Accept": "application/json"})
    opener = opener if opener is not None else build_opener(_NoRedirect())
    for attempt in range(3):
        try:
            with opener.open(request, timeout=20) as response:
                body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise SourceReadError("response_limit")
            try:
                return json.loads(body)
            except (ValueError, UnicodeError):
                raise SourceReadError("invalid_response") from None
        except HTTPError as error:
            status = error.code
            if error.fp is not None:
                error.close()
            if status in (429, 500, 502, 503, 504) and attempt < 2:
                sleeper(2 ** attempt)
                continue
            code = {401: "authentication_required", 403: "access_denied", 404: "source_not_found"}.get(status, "source_unavailable")
            raise SourceReadError(code) from None
        except (URLError, OSError, HTTPException):
            if attempt < 2:
                sleeper(2 ** attempt)
                continue
            raise SourceReadError("source_unavailable") from None
    raise SourceReadError("source_unavailable")
