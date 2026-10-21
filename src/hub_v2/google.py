"""Google access for v2: confirm the signed-in teacher, then act as the
dashboard's service account.

Everything reads, except move_item, rename_item, prefix_name, trash_item and
create_folder (the Tidy button), append_rows (the teacher's own "Scores entered" sheet)
and slides_batch_update (the weekly preflight's guarded deck repair), which use a
separate write token. Every call has a short
timeout. Errors carry a short sentence a teacher can act on, never a token or a
response body.
"""
import hashlib
import os
import re
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
FILE_FIELDS = "id,name,mimeType,trashed,createdTime,modifiedTime,parents,properties"
# Tidy puts the student's name and lesson in front of their files' names
# ("Ana Rivera.Energy Flow - <original name>") and tags the file with the
# prefix's length and a short hash, so the Hub keeps reading the original name
# (its turn-in time, username and deck title) wherever it reads names.
PREFIX_TAG = "hubPrefix"
FOLDER = "application/vnd.google-apps.folder"


class GoogleError(Exception):
    """A problem worth showing the teacher, with the HTTP status if there was one."""

    def __init__(self, message, status=None, reason=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.reason = reason
        # Keep the provider's retry guidance as data. Only explicitly scoped
        # read callers may use it; this exception never repeats a write.
        self.retry_after = retry_after


def read_timed_out(error):
    """A typed read-only GET timeout from get_json, never inferred from message text."""
    return isinstance(error, GoogleError) and error.status is None and error.reason == "read_timeout"


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
    from requests.exceptions import Timeout
    for attempt in range(2):
        try:
            response = _session().request(method, url, params=params, json=body, timeout=(5, timeout),
                                          allow_redirects=False, headers={"Authorization": "Bearer " + token})
        except Timeout:
            # This is a transport classification, not an automatic provider retry.
            # Write-capable requests must never acquire the read-only retry tag.
            raise GoogleError("Google didn't answer in time. Try again.",
                              reason="read_timeout" if method == "GET" else None) from None
        except Exception:
            raise GoogleError("Google didn't answer in time. Try again.") from None
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                raise GoogleError("Google sent an unreadable answer.", 200) from None
        if method == "GET" and attempt == 0 and response.status_code == 500:
            # The status is sufficient; do not inspect an untrusted error body
            # to decide whether to repeat this identical, bounded read.
            continue
        raise _response_error(response)


def _response_error(response):
    """Disclose only an actual provider reason; never infer quota from status403."""
    reason = None
    try:
        errors = response.json().get('error', {}).get('errors', [])
        found = {item.get('reason') for item in errors if isinstance(item, dict)
                 and isinstance(item.get('reason'), str)
                 and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{1,80}', item['reason'])}
        if len(found) == 1:
            reason = next(iter(found))
    except (ValueError, TypeError, AttributeError):
        pass
    quota = {'rateLimitExceeded', 'userRateLimitExceeded', 'dailyLimitExceeded', 'downloadQuotaExceeded'}
    permission = {'insufficientFilePermissions', 'permissionDenied', 'accessDenied', 'forbidden', 'notAuthorizedToAccessFile'}
    if response.status_code == 403 and reason in quota:
        message = 'Google is limiting requests (' + reason + '). Wait before a fresh source check.'
    elif response.status_code == 403 and reason not in permission:
        message = 'Google rejected the request (403); its access or request limit needs review.'
    else:
        message = _status_message(response.status_code)
    retry_after = getattr(response, 'headers', {}).get('Retry-After')
    return GoogleError(message, response.status_code, reason, retry_after)


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


def form_info(token, form_id):
    """A Form's student-facing title and its published link (responderUri)."""
    return get_json("https://forms.googleapis.com/v1/forms/%s" % quote(form_id), token,
                    {"fields": "info(title,documentTitle),responderUri"})


def form_responses(token, form_id):
    """Read a complete, bounded Form response list or fail without partial data."""
    responses, cursor, seen = [], None, set()
    for _ in range(20):
        params = {"pageSize": 5000}
        if cursor:
            params["pageToken"] = cursor
        page = get_json("https://forms.googleapis.com/v1/forms/%s/responses" % quote(form_id),
                        token, params)
        values = page.get("responses", []) if isinstance(page, dict) else None
        if (not isinstance(values, list) or len(values) > 5000
                or any(not isinstance(value, dict) for value in values)):
            raise GoogleError("This Form's responses could not be fully verified.",
                              reason="incomplete_response_listing")
        cursor = page.get("nextPageToken")
        if cursor is not None and not isinstance(cursor, str):
            raise GoogleError("This Form's responses could not be fully verified.",
                              reason="incomplete_response_listing")
        responses.extend(values)
        if not cursor:
            return responses
        if cursor in seen:
            raise GoogleError("This Form's response listing repeated a page; its coverage is incomplete.",
                              reason="incomplete_response_listing")
        seen.add(cursor)
    raise GoogleError("This Form's response list exceeded the verification limit; its coverage is incomplete.",
                      reason="incomplete_response_listing")


def _prefix_tag(prefix):
    return "%d:%s" % (len(prefix), hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:10])


def original_name(item):
    """The name a file had before Tidy put the student and lesson in front of
    it; a file renamed by hand since (the tag no longer fits) keeps its name."""
    name = str(item.get("name") or "")
    tag = re.fullmatch(r"(\d{1,4}):([0-9a-f]{10})", str((item.get("properties") or {}).get(PREFIX_TAG, "")))
    if tag and 0 < int(tag.group(1)) < len(name) and _prefix_tag(name[:int(tag.group(1))]) == tag.group(0):
        return name[int(tag.group(1)):]
    return name


def as_read(item):
    """Drive metadata as the Hub reads it: "name" is the original name, and
    "shown_name" what Drive shows when Tidy has renamed the file."""
    if "name" not in item:  # a lookup that didn't ask for names
        return item
    original = original_name(item)
    if original != item["name"]:
        item["shown_name"], item["name"] = item["name"], original
    return item


METADATA_READ_ATTEMPTS = 3
METADATA_READ_BACKOFF = (0.5, 1.5)


def _metadata_read(url, token, params):
    """Repeat only a typed read timeout of an idempotent Drive metadata GET.

    One slow files.get or permissions page among hundreds of private archive
    reads must not abort a whole catalog. Every other error, and a timeout on
    the final attempt, propagates unchanged; nothing here can repeat a write.
    """
    for attempt in range(METADATA_READ_ATTEMPTS):
        try:
            return get_json(url, token, params, timeout=10)
        except GoogleError as error:
            if not read_timed_out(error) or attempt == METADATA_READ_ATTEMPTS - 1:
                raise
            time.sleep(METADATA_READ_BACKOFF[attempt])


def _file_permissions(token, file_id):
    """Complete, bounded access evidence when files.get omits Shared Drive ACLs.

    Keep every permission, including public and group access, so the caller's
    privacy check sees the actual ACL. An incomplete listing is never evidence
    of private access.
    """
    permissions, cursor, seen = [], None, set()
    for _ in range(10):
        params = {"fields": "nextPageToken,permissions(id,type,emailAddress,role,domain,deleted,permissionDetails)",
                  "pageSize": 100, "supportsAllDrives": "true"}
        if cursor:
            params["pageToken"] = cursor
        page = _metadata_read("https://www.googleapis.com/drive/v3/files/" + quote(file_id, safe="") + "/permissions",
                              token, params)
        values = page.get("permissions") if isinstance(page, dict) else None
        if (not isinstance(values, list) or len(values) > 100
                or any(not isinstance(value, dict) for value in values)):
            raise GoogleError("Google's Drive access list could not be fully verified.")
        permissions.extend(values)
        cursor = page.get("nextPageToken")
        if cursor is not None and not isinstance(cursor, str):
            raise GoogleError("Google's Drive access list could not be fully verified.")
        if not cursor:
            if not permissions:
                raise GoogleError("Google returned no Drive access evidence. Private access needs checking.")
            return permissions
        if cursor in seen:
            raise GoogleError("Google's Drive access list could not be fully verified.")
        seen.add(cursor)
    raise GoogleError("Google's Drive access list exceeded the verification limit.")


def drive_file(token, file_id, fields=FILE_FIELDS):
    item = _metadata_read("https://www.googleapis.com/drive/v3/files/" + quote(file_id), token,
                          {"fields": fields, "supportsAllDrives": "true"})
    requested_permissions = fields.strip() == "*" or re.search(r"(?:^|,)\s*permissions(?:\s*[,(/]|\s*$)", fields)
    if requested_permissions and not isinstance(item, dict):
        raise GoogleError("Google sent incomplete Drive file metadata.")
    if requested_permissions and "permissions" not in item:
        # Shared Drive files deliberately omit permissions from files.get.
        # Do not invent missing file metadata while enriching their ACL.
        if (fields.strip() == "*" or re.search(r"(?:^|,)\s*id\s*(?:,|$)", fields)) and not item.get("id"):
            raise GoogleError("Google sent incomplete Drive file metadata.")
        item["permissions"] = _file_permissions(token, file_id)
    return as_read(item)


def native_presentation(token, presentation_id):
    """Official read-only Slides source using the existing drive.readonly scope."""
    return get_json("https://slides.googleapis.com/v1/presentations/" + quote(presentation_id, safe=""),
                    token, timeout=30)


def prefix_name(token, file_id, prefix, original):
    """Rename a student's file to prefix + its original name, tagged so the Hub
    still reads the original. Returns the file as Drive has it afterwards."""
    return get_json("https://www.googleapis.com/drive/v3/files/" + quote(file_id), token,
                    {"fields": "id,name,properties", "supportsAllDrives": "true"}, method="PATCH",
                    body={"name": prefix + original, "properties": {PREFIX_TAG: _prefix_tag(prefix)}})


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
            raise _response_error(response)
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


def printable_pdf_bytes(token, file_id, mime):
    """Download a saved PDF or export a native Google Doc as its print PDF."""
    if mime == "application/pdf":
        url, params = ("https://www.googleapis.com/drive/v3/files/" + quote(file_id),
                       {"alt": "media", "supportsAllDrives": "true"})
    elif mime == "application/vnd.google-apps.document":
        url, params = ("https://www.googleapis.com/drive/v3/files/%s/export" % quote(file_id),
                       {"mimeType": "application/pdf"})
    else:
        raise GoogleError("Only saved feedback documents and PDFs can be printed.")
    try:
        response = _session().get(url, params=params, timeout=(5, 30), stream=True,
                                  allow_redirects=False,
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
                if total > 10_000_000:
                    raise GoogleError("A feedback document is too large to add to the print packet.")
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


def archive_file_id(token, *, shortcut=False):
    """Reserve an ID before an archive create, so uncertain responses cannot duplicate it."""
    data = get_json("https://www.googleapis.com/drive/v3/files/generateIds", token,
                    {"count": 1, "space": "drive", "type": "shortcuts" if shortcut else "files"})
    ids = data.get("ids") or []
    if len(ids) != 1 or not isinstance(ids[0], str):
        raise GoogleError("Drive did not reserve an archive file ID.")
    return ids[0]


def archive_create(token, metadata, pdf_bytes=None):
    """Create one reserved folder/shortcut/PDF/PPTX; never change sharing or sources."""
    fields = "id,name,mimeType,parents,properties,shortcutDetails,createdTime,md5Checksum,size"
    if pdf_bytes is None:
        return get_json("https://www.googleapis.com/drive/v3/files", token,
                        {"fields": fields, "supportsAllDrives": "true"}, method="POST", body=metadata)
    import json
    media_mime = metadata.get("mimeType")
    if media_mime not in {"application/pdf", PPTX}:
        raise GoogleError("This reserved archive upload has an unsupported file type.")
    boundary = "hub-archive-" + hashlib.sha256(pdf_bytes).hexdigest()[:24]
    body = ("--%s\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n%s\r\n"
            "--%s\r\nContent-Type: %s\r\n\r\n" %
            (boundary, json.dumps(metadata), boundary, media_mime)).encode() + pdf_bytes + \
        ("\r\n--%s--\r\n" % boundary).encode()
    try:
        response = _session().post("https://www.googleapis.com/upload/drive/v3/files",
            params={"uploadType": "multipart", "fields": fields, "supportsAllDrives": "true"},
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "multipart/related; boundary=" + boundary},
            data=body, timeout=(5, 30), allow_redirects=False)
        if response.status_code != 200:
            try:
                reasons = {e.get("reason") for e in response.json().get("error", {}).get("errors", [])}
            except (ValueError, TypeError, AttributeError):
                reasons = set()
            if reasons & {"storageQuotaExceeded", "teamDriveFileLimitExceeded"}:
                message = ("Drive storage quota prevents this PDF upload. The saved feedback is still downloadable from the Hub."
                           if media_mime == "application/pdf" else
                           "Drive storage quota prevents this work snapshot upload. The original work is preserved.")
                raise GoogleError(message,
                                  response.status_code)
            raise GoogleError(_status_message(response.status_code), response.status_code)
        return response.json()
    except GoogleError:
        raise
    except Exception:
        raise GoogleError("Drive did not confirm the archive file upload.") from None


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
DOC = "application/vnd.google-apps.document"


def files_named(token, name, mime):
    """Files of this type with exactly this name that the dashboard can see."""
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    page = get_json("https://www.googleapis.com/drive/v3/files", token, {
        "q": "name = '%s' and mimeType = '%s' and trashed = false" % (escaped, mime),
        "fields": "files(id,name,modifiedTime)", "pageSize": 10,
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    return page.get("files", []) or []


def files_named_in(token, name, mime, parent, prefix=False, order=None):
    """Files of this type directly inside a folder, named exactly this (or,
    with prefix, whose name starts with it); newest first with order="createdTime desc"."""
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    params = {
        "q": "name %s '%s' and mimeType = '%s' and '%s' in parents and trashed = false"
             % ("contains" if prefix else "=", escaped, mime, parent.replace("'", "")),
        "fields": "files(id,name,modifiedTime,createdTime)", "pageSize": 20,
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
    if order:
        params["orderBy"] = order
    page = get_json("https://www.googleapis.com/drive/v3/files", token, params)
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


def update_cells(token, spreadsheet_id, updates):
    """Write [(A1 range, text)] as plain text: nothing typed runs as a formula."""
    return get_json("https://sheets.googleapis.com/v4/spreadsheets/%s/values:batchUpdate" % quote(spreadsheet_id),
                    token, method="POST", body={"valueInputOption": "RAW", "data": [
                        {"range": cell_range, "values": [[value]]} for cell_range, value in updates]})


def batch_update(token, spreadsheet_id, requests):
    """Apply Sheets requests in one call: all of them happen, or none do."""
    return get_json("https://sheets.googleapis.com/v4/spreadsheets/%s:batchUpdate" % quote(spreadsheet_id),
                    token, method="POST", body={"requests": requests}, timeout=60)


SHORTCUT = "application/vnd.google-apps.shortcut"
LISTING_PAGES = 20


def folder_listing(token, folder_id, max_pages=LISTING_PAGES):
    """(items, complete) for one Drive folder, trashed items excluded.

    complete is False when the page limit is reached while Drive still offers
    another page: the caller holds the listing rather than treating it as all.
    A shortcut keeps its own ID and names its target in "target"; the target
    itself is not read here.
    """
    items, cursor = [], None
    for _ in range(max_pages):
        params = {"q": "'%s' in parents and trashed = false" % folder_id.replace("'", ""),
                  "fields": "nextPageToken,files(%s,shortcutDetails(targetId,targetMimeType))" % FILE_FIELDS,
                  "pageSize": 1000, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if cursor:
            params["pageToken"] = cursor
        page = get_json("https://www.googleapis.com/drive/v3/files", token, params)
        for item in page.get("files", []) or []:
            if item.get("trashed"):
                continue
            details = item.get("shortcutDetails") or {}
            if item.get("mimeType") == SHORTCUT and details.get("targetId"):
                item["target"] = {"id": details["targetId"], "mimeType": details.get("targetMimeType", "")}
            items.append(as_read(item))
        cursor = page.get("nextPageToken")
        if not cursor:
            return items, True
    return items, False


def folder_children(token, folder_id):
    """Every item directly inside a Drive folder, trashed items excluded."""
    return folder_listing(token, folder_id)[0]


def slides_batch_update(token, presentation_id, requests, required_revision_id):
    """Apply Slides requests only to the exact revision just read: Slides
    refuses the whole batch when the deck has changed since."""
    if not isinstance(required_revision_id, str) or not required_revision_id:
        raise GoogleError("A deck change needs the deck's current revision.")
    return get_json("https://slides.googleapis.com/v1/presentations/%s:batchUpdate" % quote(presentation_id, safe=""),
                    token, method="POST", timeout=30,
                    body={"requests": requests, "writeControl": {"requiredRevisionId": required_revision_id}})
