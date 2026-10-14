"""Save one real week as an offline snapshot, so a preflight can be re-run and repaired without live access.

    python scripts/snapshot_week.py --week 8 --out snapshots/wk08

Writes agenda.xlsx, drive/files.json, drive/exports/* and an empty schoology.json.
Snapshots hold teaching materials; keep them out of git (snapshots/ is ignored).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from preflight.auth import google_credentials
from preflight.config import load_config
from preflight.context import build_context
from preflight.sources import agenda as ag
from preflight.sources.gdrive import DOCX_MIME, PDF_MIME, PPTX_MIME, GoogleDrive


class RecordingDrive(GoogleDrive):
    def __init__(self, credentials, out: Path):
        super().__init__(credentials)
        self.out = out
        self.seen: dict[str, dict] = {}

    def _keep(self, f):
        if f is not None:
            self.seen[f.id] = {"id": f.id, "name": f.name, "mimeType": f.mime_type, "parents": f.parents,
                               "trashed": f.trashed,
                               "modifiedTime": f.modified.isoformat() if f.modified else None}
        return f

    def get(self, file_id):
        return self._keep(super().get(file_id))

    def children(self, folder_id):
        return [self._keep(f) for f in super().children(folder_id)]

    def search_folders(self, name_contains):
        return [self._keep(f) for f in super().search_folders(name_contains)]

    def export(self, file_id, mime_type):
        data = super().export(file_id, mime_type)
        if data:
            ext = {PPTX_MIME: "pptx", PDF_MIME: "pdf", DOCX_MIME: "docx", "text/plain": "txt"}[mime_type]
            (self.out / "drive" / "exports").mkdir(parents=True, exist_ok=True)
            (self.out / "drive" / "exports" / f"{file_id}.{ext}").write_bytes(data)
        return data


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--week", type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--config", default="config.yaml")
    args = p.parse_args()

    cfg = load_config(args.config)
    creds = google_credentials()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from googleapiclient.discovery import build

    xlsx = build("drive", "v3", credentials=creds, cache_discovery=False).files().export(
        fileId=cfg.agenda_id, mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet").execute()
    (out / "agenda.xlsx").write_bytes(xlsx)

    drive = RecordingDrive(creds, out)
    ctx = build_context(cfg, ag.load_xlsx(out / "agenda.xlsx"), drive, None, args.week)
    for cc in ctx.courses:  # worksheets the converter would read
        for f in cc.folder_files:
            if f.mime_type == DOCX_MIME:
                drive.export(f.id, DOCX_MIME)
    (out / "drive").mkdir(exist_ok=True)
    (out / "drive" / "files.json").write_text(json.dumps(list(drive.seen.values()), indent=1))
    (out / "schoology.json").write_text("{}")
    print(f"Saved {len(drive.seen)} files to {out}")


if __name__ == "__main__":
    main()
