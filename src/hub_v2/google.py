"""Google access for v2: confirm the signed-in teacher, then read as the
dashboard's service account.

Everything here is read-only and every call has a short timeout. Errors carry a
short sentence a teacher can act on, never a token or a response body.
"""
import os
import threading
import time
from urllib.parse import quote

IAP_CERTS = "https://www.gstatic.com/iap/verify/public_key"
SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/forms.responses.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
)
SHEET_FIELDS = ("sheets(properties(sheetId,title),data(startRow,startColumn,rowData(values("
                "formattedValue,hyperlink,textFormatRuns(startIndex,format(link(uri))),"
                "userEnteredValue(formulaValue)))))")
FILE_FIELDS = "id,name,mimeType,trashed,modifiedTime"


class GoogleError(Exception):
    """A problem worth showing the teacher, with the HTTP status if there was one."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class SignInRequired(Exception):
    pass


_local = threading.local()
_token = {"value": None, "expires": 0.0}
_token_lock = threading.Lock()


def _session():
    if not hasattr(_local, "session"):
        import requests
        session = requests.Session()
        session.trust_env = False
        _local.session = session
    return _local.session


def _auth_request():
    """google-auth transport with a bounded timeout and no redirects."""
    from google.auth.transport.requests import Request
    transport = Request(session=_session())

    def bounded(*args, **kwargs):
        kwargs["timeout"] = 10
        kwargs["allow_redirects"] = False
        return transport(*args, **kwargs)
    return bounded


def verify_teacher(assertion, config=None):
    """Return the teacher's email from Cloud IAP's signed header, or refuse."""
    config = os.environ if config is None else config
    audience = config.get("HUB_IAP_AUDIENCE", "")
    allowed = {e.strip().lower() for e in config.get("HUB_ALLOWED_EMAILS", "").split(",") if e.strip()}
    if not isinstance(assertion, str) or not 1 <= len(assertion) <= 16000 or not audience or not allowed:
        raise SignInRequired()
    try:
        from google.oauth2 import id_token
        claims = id_token.verify_token(assertion, _auth_request(), audience=audience,
                                       certs_url=IAP_CERTS)
    except Exception:
        raise SignInRequired() from None
    email = str(claims.get("email", "")).lower()
    if claims.get("iss") != "https://cloud.google.com/iap" or claims.get("aud") != audience \
            or email not in allowed:
        raise SignInRequired()
    return email


def service_token(config=None):
    """A short-lived token for the dashboard's own service account.

    Cloud Run's metadata token ignores requested scopes, so the scoped token is
    minted by the account impersonating itself (no key file involved).
    """
    config = os.environ if config is None else config
    with _token_lock:
        if _token["value"] and _token["expires"] > time.time() + 60:
            return _token["value"]
        expected = config.get("HUB_READER_SERVICE_ACCOUNT", "")
        try:
            from google.auth import compute_engine, impersonated_credentials
            request = _auth_request()
            runtime = compute_engine.Credentials()
            runtime.refresh(request)
            if not expected or runtime.service_account_email != expected:
                raise ValueError("unexpected runtime identity")
            scoped = impersonated_credentials.Credentials(
                source_credentials=runtime, target_principal=expected,
                target_scopes=list(SCOPES), lifetime=900)
            scoped.refresh(request)
        except Exception:
            raise GoogleError("The dashboard couldn't get its Google access token. "
                              "Check the service account setting in Cloud Run.") from None
        _token["value"], _token["expires"] = scoped.token, time.time() + 840
        return scoped.token


def service_account(config=None):
    config = os.environ if config is None else config
    return config.get("HUB_READER_SERVICE_ACCOUNT", "")


def get_json(url, token, params=None, timeout=20):
    try:
        response = _session().get(url, params=params, timeout=(5, timeout), allow_redirects=False,
                                  headers={"Authorization": "Bearer " + token})
    except Exception:
        raise GoogleError("Google didn't answer in time. Try again.") from None
    if response.status_code == 200:
        try:
            return response.json()
        except ValueError:
            raise GoogleError("Google sent an unreadable answer.", 200) from None
    raise GoogleError(_status_message(response.status_code), response.status_code)


def _status_message(status):
    if status in (401, 403):
        return "The dashboard's account doesn't have access."
    if status == 404:
        return "Not found, or not shared with the dashboard's account."
    if status == 429:
        return "Google is rate-limiting requests. Wait a minute and try again."
    if status == 400:
        return "Google rejected the request (a tab or range may have been renamed)."
    return "Google returned an error (%s)." % status


def sheet_tabs(token, spreadsheet_id):
    data = get_json("https://sheets.googleapis.com/v4/spreadsheets/" + quote(spreadsheet_id), token,
                    {"fields": "properties(title),sheets(properties(sheetId,title))"})
    return data.get("properties", {}).get("title", ""), {
        s["properties"]["title"]: s["properties"]["sheetId"] for s in data.get("sheets", [])}


def sheet_grid(token, spreadsheet_id, ranges):
    """{tab title: {(row, col): cell}} with 1-based rows and columns."""
    data = get_json("https://sheets.googleapis.com/v4/spreadsheets/" + quote(spreadsheet_id), token,
                    [("ranges", r) for r in ranges] + [("includeGridData", "true"),
                                                      ("fields", SHEET_FIELDS)], timeout=30)
    grids = {}
    for sheet in data.get("sheets", []):
        cells = grids.setdefault(sheet["properties"]["title"], {})
        for block in sheet.get("data", []):
            for r, row in enumerate(block.get("rowData", []) or [], block.get("startRow", 0) + 1):
                for c, cell in enumerate(row.get("values", []) or [], block.get("startColumn", 0) + 1):
                    if cell:
                        cells[r, c] = cell
    return grids


def form_responses(token, form_id):
    responses, cursor = [], None
    for _ in range(20):
        params = {"pageSize": 5000}
        if cursor:
            params["pageToken"] = cursor
        page = get_json("https://forms.googleapis.com/v1/forms/%s/responses" % quote(form_id),
                        token, params)
        responses.extend(page.get("responses", []) or [])
        cursor = page.get("nextPageToken")
        if not cursor:
            return responses
    return responses


def drive_file(token, file_id):
    return get_json("https://www.googleapis.com/drive/v3/files/" + quote(file_id), token,
                    {"fields": FILE_FIELDS, "supportsAllDrives": "true"}, timeout=10)
