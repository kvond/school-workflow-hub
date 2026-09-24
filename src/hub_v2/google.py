"""Google access for v2: confirm the signed-in teacher, then act as the
dashboard's service account.

Everything reads, except move_item, rename_item, trash_item and create_folder
(the Tidy button) and append_rows (the teacher's own "Scores entered" sheet),
which use a separate write token. Every call has a short
timeout. Errors carry a short sentence a teacher can act on, never a token or a
response body.
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
FILE_FIELDS = "id,name,mimeType,trashed,createdTime,modifiedTime,parents"
FOLDER = "application/vnd.google-apps.folder"


class GoogleError(Exception):
    """A problem worth showing the teacher, with the HTTP status if there was one."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class SignInRequired(Exception):
    pass


_local = threading.local()
WRITE_SCOPES = SCOPES + ("https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets")
_tokens = {False: {"value": None, "expires": 0.0}, True: {"value": None, "expires": 0.0}}
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


def service_token(config=None, write=False):
    """A short-lived token for the dashboard's own service account.

    Cloud Run's metadata token ignores requested scopes, so the scoped token is
    minted by the account impersonating itself (no key file involved). Only the
    Tidy button asks for the write token; everything else reads.
    """
    config = os.environ if config is None else config
    cache = _tokens[bool(write)]
    with _token_lock:
        if cache["value"] and cache["expires"] > time.time() + 60:
            return cache["value"]
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
                target_scopes=list(WRITE_SCOPES if write else SCOPES), lifetime=900)
            scoped.refresh(request)
        except Exception:
            raise GoogleError("The dashboard couldn't get its Google access token. "
                              "Check the service account setting in Cloud Run.") from None
        cache["value"], cache["expires"] = scoped.token, time.time() + 840
        return scoped.token


def service_account(config=None):
    config = os.environ if config is None else config
    return config.get("HUB_READER_SERVICE_ACCOUNT", "")


def get_json(url, token, params=None, timeout=20, method="GET", body=None):
    try:
        response = _session().request(method, url, params=params, json=body, timeout=(5, timeout),
                                      allow_redirects=False, headers={"Authorization": "Bearer " + token})
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


def drive_file(token, file_id, fields=FILE_FIELDS):
    return get_json("https://www.googleapis.com/drive/v3/files/" + quote(file_id), token,
                    {"fields": fields, "supportsAllDrives": "true"}, timeout=10)


PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
MAX_DECK_BYTES = 60_000_000


def deck_bytes(token, file_id, mime):
    """A Google Slides deck exported as .pptx, or an uploaded .pptx, as bytes."""
    if mime == "application/vnd.google-apps.presentation":
        url, params = ("https://www.googleapis.com/drive/v3/files/%s/export" % quote(file_id),
                       {"mimeType": PPTX})
    elif mime == PPTX:
        url, params = ("https://www.googleapis.com/drive/v3/files/" + quote(file_id),
                       {"alt": "media", "supportsAllDrives": "true"})
    else:
        raise GoogleError("Only Google Slides and PowerPoint files can be scored.")
    try:
        response = _session().get(url, params=params, timeout=(5, 60), stream=True, allow_redirects=False,
                                  headers={"Authorization": "Bearer " + token})
    except Exception:
        raise GoogleError("Google didn't answer in time. Try again.") from None
    with response:
        if response.status_code != 200:
            raise GoogleError(_status_message(response.status_code), response.status_code)
        chunks, total = [], 0
        try:
            for chunk in response.iter_content(1 << 20):
                total += len(chunk)
                if total > MAX_DECK_BYTES:
                    raise GoogleError("This deck is too large to read.")
                chunks.append(chunk)
        except GoogleError:
            raise
        except Exception:
            raise GoogleError("Google didn't answer in time. Try again.") from None
    return b"".join(chunks)


def move_item(token, item_id, new_parent, old_parents):
    """Move a file or folder into new_parent; returns its parents afterwards."""
    return get_json("https://www.googleapis.com/drive/v3/files/" + quote(item_id), token, {
        "addParents": new_parent, "removeParents": ",".join(p for p in old_parents if p != new_parent),
        "fields": "id,parents", "supportsAllDrives": "true"}, method="PATCH", body={})


def create_folder(token, name, parent):
    return get_json("https://www.googleapis.com/drive/v3/files", token,
                    {"fields": "id,name,parents", "supportsAllDrives": "true"}, method="POST",
                    body={"name": name, "mimeType": FOLDER, "parents": [parent]})


def rename_item(token, item_id, name):
    return get_json("https://www.googleapis.com/drive/v3/files/" + quote(item_id), token,
                    {"fields": "id,name", "supportsAllDrives": "true"}, method="PATCH", body={"name": name})


def trash_item(token, item_id):
    return get_json("https://www.googleapis.com/drive/v3/files/" + quote(item_id), token,
                    {"fields": "id,trashed", "supportsAllDrives": "true"}, method="PATCH",
                    body={"trashed": True})


def folders_named(token, name):
    """Folders with exactly this name that the dashboard can see, with their parents."""
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    page = get_json("https://www.googleapis.com/drive/v3/files", token, {
        "q": "name = '%s' and mimeType = '%s' and trashed = false" % (escaped, FOLDER),
        "fields": "files(id,name,parents)", "pageSize": 20,
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    return page.get("files", []) or []


SHEET = "application/vnd.google-apps.spreadsheet"


def files_named(token, name, mime):
    """Files of this type with exactly this name that the dashboard can see."""
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    page = get_json("https://www.googleapis.com/drive/v3/files", token, {
        "q": "name = '%s' and mimeType = '%s' and trashed = false" % (escaped, mime),
        "fields": "files(id,name,modifiedTime)", "pageSize": 10,
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    return page.get("files", []) or []


def files_named_in(token, name, mime, parent, prefix=False):
    """Files of this type directly inside a folder, named exactly this (or,
    with prefix, whose name starts with it)."""
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    page = get_json("https://www.googleapis.com/drive/v3/files", token, {
        "q": "name %s '%s' and mimeType = '%s' and '%s' in parents and trashed = false"
             % ("contains" if prefix else "=", escaped, mime, parent.replace("'", "")),
        "fields": "files(id,name,modifiedTime,createdTime)", "pageSize": 20,
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    return page.get("files", []) or []


def sheet_values(token, spreadsheet_id, cell_range):
    data = get_json("https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s"
                    % (quote(spreadsheet_id), quote(cell_range, safe="")), token, timeout=20)
    return data.get("values", []) or []


def append_rows(token, spreadsheet_id, cell_range, rows):
    return get_json("https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s:append"
                    % (quote(spreadsheet_id), quote(cell_range, safe="")), token,
                    {"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}, method="POST",
                    body={"values": rows})


def folder_children(token, folder_id):
    """Every item directly inside a Drive folder, trashed items excluded."""
    items, cursor = [], None
    for _ in range(20):
        params = {"q": "'%s' in parents and trashed = false" % folder_id.replace("'", ""),
                  "fields": "nextPageToken,files(%s)" % FILE_FIELDS, "pageSize": 1000,
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if cursor:
            params["pageToken"] = cursor
        page = get_json("https://www.googleapis.com/drive/v3/files", token, params)
        items.extend(page.get("files", []) or [])
        cursor = page.get("nextPageToken")
        if not cursor:
            break
    return items
