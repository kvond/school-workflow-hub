"""Local, aggregate-only teacher summary preview rendering."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from .runs import get_run


def render_teacher_summary(
    connection: sqlite3.Connection, run_id: str, output_path: str | Path
) -> Path:
    run = get_run(connection, run_id)
    proposal_count = connection.execute(
        "SELECT COUNT(*) AS total FROM proposed_actions WHERE run_id = ?", (run_id,)
    ).fetchone()["total"]
    review_count = connection.execute(
        "SELECT COUNT(*) AS total FROM review_items WHERE run_id = ?", (run_id,)
    ).fetchone()["total"]
    revision_count = connection.execute(
        "SELECT COUNT(*) AS total FROM source_revisions WHERE run_id = ?", (run_id,)
    ).fetchone()["total"]
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = (
        "# Teacher summary preview (synthetic)\n\n"
        f"- Run: `{run_id}`\n"
        f"- Status: `{run['status']}`\n"
        f"- Source revisions observed: {revision_count}\n"
        f"- Proposed actions: {proposal_count}\n"
        f"- Review items: {review_count}\n"
        f"- External writes: {run['external_writes']}\n"
        f"- Live model calls: {run['live_model_calls']}\n\n"
        "This is a local planning preview. It contains no execution authorization and no student content.\n"
    )
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == rendered:
            return path
        raise FileExistsError(f"refusing to overwrite existing report: {path}")
    path.write_text(rendered, encoding="utf-8")
    return path
