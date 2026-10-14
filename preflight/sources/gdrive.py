"""Read-only access to Google Drive, plus an offline snapshot with the same interface.

The preflight never edits a teaching file. The only writes live in
preflight/output/publish.py, and they go to a dedicated output folder.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Protocol

from preflight.models import DriveFile

FOLDER_MIME = "application/vnd.google-apps.folder"
SLIDES_MIME = "application/vnd.google-apps.presentation"
DOCS_MIME = "application/vnd.google-apps.document"
SHEETS_MIME = "application/vnd.google-apps.spreadsheet"
FORMS_MIME = "application/vnd.google-apps.form"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"

_ID = r"([A-Za-z0-9_-]{20,})"
_URL_PATTERNS = [
    ("presentation", re.compile(r"docs\.google\.com/presentation/d/" + _ID)),
    ("document", re.compile(r"docs\.google\.com/document/d/" + _ID)),
    ("spreadsheet", re.compile(r"docs\.google\.com/spreadsheets/d/" + _ID)),
    ("form", re.compile(r"docs\.google\.com/forms/d/(?:e/)?" + _ID)),
    ("folder", re.compile(r"drive\.google\.com/drive/(?:u/\d+/)?folders/" + _ID)),
    ("file", re.compile(r"drive\.google\.com/file/d/" + _ID)),
    ("file", re.compile(r"drive\.google\.com/open\?id=" + _ID)),
]
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")


def parse_google_url(url: str) -> tuple[str, str] | None:
    """Return (kind, id) for a Google Drive/Docs URL, or None for anything else."""
    for kind, pattern in _URL_PATTERNS:
        m = pattern.search(url)
        if m:
            return kind, m.group(1)
    return None


def url_mode(url: str) -> str:
    """What a student lands on: copy, edit, view, preview, template, viewform, or ''."""
    tail = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower()
    if tail in {"copy", "edit", "view", "preview", "viewform", "pub", "present"}:
        return tail
    if "template/preview" in url:
        return "template"
    return ""


def find_urls(text: str) -> list[str]:
    return [u.rstrip(".,;") for u in URL_RE.findall(text or "")]


class DriveSource(Protocol):
    def get(self, file_id: str) -> DriveFile | None: ...

    def children(self, folder_id: str) -> list[DriveFile]: ...

    def export(self, file_id: str, mime_type: str) -> bytes | None: ...

    def search_folders(self, name_contains: str) -> list[DriveFile]: ...


def _parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _drive_file(meta: dict) -> DriveFile:
    return DriveFile(
        id=meta["id"],
        name=meta.get("name", ""),
        mime_type=meta.get("mimeType", ""),
        parents=list(meta.get("parents", [])),
        trashed=bool(meta.get("trashed", False)),
        modified=_parse_time(meta.get("modifiedTime")),
        web_link=meta.get("webViewLink", ""),
        shared_with_domain=meta.get("shared_with_domain"),
    )


class GoogleDrive:
    """Drive v3 client. Uses the teacher's OAuth credentials (see deploy/README.md)."""

    FIELDS = "id,name,mimeType,parents,trashed,modifiedTime,webViewLink"

    def __init__(self, credentials):
        from googleapiclient.discovery import build

        self._svc = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self._cache: dict[str, DriveFile | None] = {}

    def get(self, file_id: str) -> DriveFile | None:
        from googleapiclient.errors import HttpError

        if file_id in self._cache:
            return self._cache[file_id]
        try:
            meta = (
                self._svc.files()
                .get(fileId=file_id, fields=self.FIELDS, supportsAllDrives=True)
                .execute()
            )
            found = _drive_file(meta)
        except HttpError as err:
            if err.resp.status in (403, 404):
                found = None
            else:
                raise
        self._cache[file_id] = found
        return found

    def children(self, folder_id: str) -> list[DriveFile]:
        out: list[DriveFile] = []
        token = None
        while True:
            resp = (
                self._svc.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields=f"nextPageToken, files({self.FIELDS})",
                    pageSize=200,
                    pageToken=token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            out.extend(_drive_file(m) for m in resp.get("files", []))
            token = resp.get("nextPageToken")
            if not token:
                return out

    def export(self, file_id: str, mime_type: str) -> bytes | None:
        from googleapiclient.errors import HttpError

        meta = self.get(file_id)
        if meta is None:
            return None
        try:
            if meta.is_google_native:
                return self._svc.files().export(fileId=file_id, mimeType=mime_type).execute()
            if meta.mime_type == mime_type:
                return self._svc.files().get_media(fileId=file_id, supportsAllDrives=True).execute()
        except HttpError as err:
            if err.resp.status in (403, 404):
                return None
            raise
        return None

    def search_folders(self, name_contains: str) -> list[DriveFile]:
        safe = name_contains.replace("'", "\\'")
        resp = (
            self._svc.files()
            .list(
                q=f"mimeType = '{FOLDER_MIME}' and name contains '{safe}' and trashed = false",
                fields=f"files({self.FIELDS})",
                pageSize=50,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        return [_drive_file(m) for m in resp.get("files", [])]


class SnapshotDrive:
    """Offline Drive for tests and dry runs.

    Layout:
        files.json            [{id, name, mimeType, parents, trashed, modifiedTime}, ...]
        exports/<id>.pptx     exported decks
        exports/<id>.pdf      exported printables
        exports/<id>.txt      plain-text exports
    Any id not listed in files.json behaves like a dead or inaccessible link.
    """

    _EXT = {PPTX_MIME: "pptx", PDF_MIME: "pdf", "text/plain": "txt", DOCX_MIME: "docx"}

    def __init__(self, root: Path):
        self.root = Path(root)
        metas = json.loads((self.root / "files.json").read_text())
        self._files = {m["id"]: _drive_file(m) for m in metas}

    def get(self, file_id: str) -> DriveFile | None:
        return self._files.get(file_id)

    def children(self, folder_id: str) -> list[DriveFile]:
        return [f for f in self._files.values() if folder_id in f.parents and not f.trashed]

    def export(self, file_id: str, mime_type: str) -> bytes | None:
        ext = self._EXT.get(mime_type)
        path = self.root / "exports" / f"{file_id}.{ext}"
        return path.read_bytes() if ext and path.exists() else None

    def search_folders(self, name_contains: str) -> list[DriveFile]:
        needle = name_contains.lower()
        return [
            f
            for f in self._files.values()
            if f.mime_type == FOLDER_MIME and needle in f.name.lower() and not f.trashed
        ]
