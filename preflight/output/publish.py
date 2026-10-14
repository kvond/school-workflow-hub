"""Write the week's preflight into the output folder: the report Doc and one print-packet PDF.

These are the only writes the job makes, and only to files it creates itself
(drive.file scope). Teaching files, the Agenda and Schoology are never modified.
"""

from __future__ import annotations

import io
import json
import logging

from pypdf import PdfReader, PdfWriter

from preflight import report
from preflight.models import PrintItem
from preflight.runner import Result
from preflight.sources.gdrive import DOCX_MIME, PDF_MIME, DriveSource

log = logging.getLogger(__name__)


def cover_page(items: list[PrintItem], title: str) -> bytes:
    """A one-page PDF listing what is in the packet and how many copies of each."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, title)
    c.setFont("Helvetica", 10)
    for it in items:
        y -= 16
        if y < 60:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = 750
        c.drawString(50, y, f"[ ] {it.course} · {it.day} · {it.item[:70]} · copies: {it.copies}")
    c.save()
    return buf.getvalue()


def build_packet(drive: DriveSource, items: list[PrintItem], title: str) -> tuple[bytes, list[str]]:
    """Merge every printable file into one PDF. Returns (pdf bytes, items that could not be included)."""
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(cover_page(items, title))).pages:
        writer.add_page(page)
    missing: list[str] = []
    for it in items:
        data = None
        if it.file_id:
            meta = drive.get(it.file_id)
            if meta is not None:
                data = drive.export(it.file_id, PDF_MIME)
                if data is None and meta.mime_type == DOCX_MIME:
                    missing.append(f"{it.item} (Word file: open and print, or convert to a Google Doc)")
                    continue
        if data is None:
            missing.append(it.item)
            continue
        for page in PdfReader(io.BytesIO(data)).pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), missing


def publish(result: Result, credentials, folder_id: str) -> dict[str, str]:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload

    svc = build("drive", "v3", credentials=credentials, cache_discovery=False)
    label = result.ctx.label
    links: dict[str, str] = {}

    def upload(name: str, data: bytes, mime: str, as_mime: str | None = None) -> str:
        """Replace this week's file if an earlier run made it (Thursday run, then the Sunday re-check)."""
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
        safe = name.replace("'", "\\'")
        found = svc.files().list(
            q=f"name = '{safe}' and '{folder_id}' in parents and trashed = false",
            fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute().get("files", [])
        if found:
            f = svc.files().update(fileId=found[0]["id"], media_body=media, fields="id,webViewLink",
                                   supportsAllDrives=True).execute()
        else:
            body = {"name": name, "parents": [folder_id]}
            if as_mime:
                body["mimeType"] = as_mime
            f = svc.files().create(body=body, media_body=media, fields="id,webViewLink",
                                   supportsAllDrives=True).execute()
        return f["webViewLink"]

    links["report"] = upload(f"Preflight — {label}", report.to_html(result).encode(), "text/html",
                             "application/vnd.google-apps.document")
    if result.prints:
        pdf, missing = build_packet(result.ctx.drive, result.prints, f"Print packet — {label}")
        links["packet"] = upload(f"Print packet — {label}.pdf", pdf, PDF_MIME)
        if missing:
            log.info("Not in the packet: %s", missing)
    links["json"] = upload(f"preflight-{label.replace(' ', '')}.json",
                           json.dumps(report.to_json(result), indent=1, default=str).encode(), "application/json")
    return links
