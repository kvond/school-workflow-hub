"""Request-scoped, read-only Drive budget for interactive submission refresh."""
import json
import re
import time

from .assessment_save import PPTX, _pptx_text
from .hosted_agenda import ConnectionError
from .submission_inventory import DriveReader

NATIVE = "application/vnd.google-apps.presentation"
MAX_SECONDS = 180
MAX_REQUESTS = 256
MAX_EXPORTS = 80
MAX_TOTAL_BYTES = 64_000_000
MAX_FILE_BYTES = 20_000_000
CHUNK_BYTES = 64 * 1024
FILE_ID = re.compile(r"[A-Za-z0-9_-]{20,128}")


class BudgetedDrive(DriveReader):
    """One refresh budget, including metadata, listings and deck content.

    Bodies are streamed and bounded before buffering or parsing. No request is
    retried, no redirect is followed, and no data is cached or persisted.
    """

    def __init__(self, token):
        self.started_at = time.monotonic()
        self.deadline = self.started_at + MAX_SECONDS
        self.request_count = 0
        self.export_count = 0
        self.bytes_read = 0
        self.exhausted = False
        self.form_cache = {}
        super().__init__(token)

    def _exhaust(self):
        self.exhausted = True
        raise ConnectionError("submission_refresh_budget_exhausted")

    def check(self):
        """Caller may use this before/after adjacent Form or Agenda reads."""
        if self.exhausted or time.monotonic() >= self.deadline:
            self._exhaust()

    @property
    def stats(self):
        return {"requests": self.request_count, "exports": self.export_count,
                "bytes_read": self.bytes_read, "exhausted": self.exhausted,
                "elapsed_seconds": round(max(0, time.monotonic() - self.started_at), 3)}

    def _body(self, response):
        self.check()
        capacity = min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - self.bytes_read)
        if capacity <= 0:
            self._exhaust()
        try:
            advertised = int(response.headers.get("Content-Length", ""))
        except (TypeError, ValueError):
            advertised = None
        if advertised is not None and advertised > capacity:
            self._exhaust()
        body = bytearray()
        for chunk in response.iter_content(chunk_size=min(CHUNK_BYTES, capacity)):
            self.check()
            if not chunk:
                continue
            # Never retain a chunk exceeding either request limit. The stream
            # closes immediately; no remainder is downloaded for diagnostics.
            if (len(body) + len(chunk) > MAX_FILE_BYTES
                    or self.bytes_read + len(chunk) > MAX_TOTAL_BYTES):
                self._exhaust()
            body.extend(chunk)
            self.bytes_read += len(chunk)
        self.check()
        return bytes(body)

    def _read(self, path, params, *, deck=False, form=False):
        self.check()
        if self.request_count >= MAX_REQUESTS or (deck and self.export_count >= MAX_EXPORTS):
            self._exhaust()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self._exhaust()
        self.request_count += 1
        self.export_count += int(deck)
        response = None
        unavailable = ("submission_form_unavailable" if form else
                       "assessment_source_unavailable" if deck else "submission_source_unavailable")
        try:
            response = self.session.get(
                ("https://forms.googleapis.com/v1/" if form else "https://www.googleapis.com/drive/v3/") + path, params=params,
                timeout=(min(5.0, remaining / 2), min(30.0, remaining / 2)),
                allow_redirects=False, stream=True)
            self.check()
            if response.status_code in (401, 403, 404):
                if form:
                    raise ConnectionError("submission_form_access_required" if response.status_code != 404
                                          else "submission_form_not_found")
                if deck:
                    raise ConnectionError("assessment_source_access_required")
                if response.status_code == 403:
                    raw = self._body(response)
                    try:
                        error = json.loads(raw).get("error", {})
                        reasons = {r.get("reason", "") for key in ("details", "errors")
                                   for r in error.get(key, []) if isinstance(r, dict)}
                    except (ValueError, AttributeError, TypeError):
                        reasons = set()
                    if reasons & {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions"}:
                        raise ConnectionError("submission_reader_scope_missing")
                raise ConnectionError("submission_folder_access_required")
            if response.status_code != 200:
                raise ConnectionError(unavailable)
            return self._body(response)
        except ConnectionError:
            raise
        except Exception:
            # A timeout after the whole request budget expires is a budget
            # failure; ordinary transport errors keep a stable source code.
            self.check()
            raise ConnectionError(unavailable) from None
        finally:
            if response is not None:
                response.close()

    def get(self, path, params):
        if not re.fullmatch(r"files(?:/[A-Za-z0-9_-]{20,128})?", path):
            raise ConnectionError("invalid_submission_scope")
        raw = self._read(path, params)
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ConnectionError("submission_source_unavailable") from None
        if not isinstance(result, dict):
            raise ConnectionError("submission_source_unavailable")
        return result

    def form(self, form_id, *, fresh=False):
        """Read exact upload-folder IDs using the existing Drive read-only scope.

        forms.get accepts drive.readonly. No new token scope, public URL fetch,
        title search or upload-parent expansion is needed for this source read.
        """
        self.check()
        if not isinstance(form_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", form_id):
            raise ConnectionError("assignment_form_mapping_required")
        if fresh or form_id not in self.form_cache:
            raw = self._read("forms/" + form_id, {"fields":
                "formId,responderUri,revisionId,items(questionItem(question(fileUploadQuestion(folderId))))"}, form=True)
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise ConnectionError("submission_form_structure_invalid") from None
            if not isinstance(value, dict) or value.get("formId") != form_id:
                raise ConnectionError("submission_form_structure_invalid")
            self.form_cache[form_id] = value
        return self.form_cache[form_id]

    def read_deck(self, item):
        """Export native Slides or download uploaded PPTX from official Drive."""
        self.check()
        file_id = item.get("id", "")
        if not isinstance(file_id, str) or not FILE_ID.fullmatch(file_id):
            raise ConnectionError("invalid_submission_scope")
        mime = item.get("mimeType")
        if mime == NATIVE:
            path, params = "files/" + file_id + "/export", {"mimeType": PPTX}
        elif mime == PPTX:
            path, params = "files/" + file_id, {"alt": "media", "supportsAllDrives": "true"}
        else:
            raise ConnectionError("intake_deck_unsupported")
        raw = self._read(path, params, deck=True)
        result = _pptx_text(raw)
        self.check()
        return result
