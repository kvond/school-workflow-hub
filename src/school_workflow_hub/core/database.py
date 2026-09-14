"""SQLite connection, migration, and recovery helpers.

The active database must be on a user-chosen, local non-synced location before
real school data is ever permitted. Tests use disposable paths only.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
from typing import Iterator, Union


PathLike = Union[str, Path]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
LOCK_WAIT_MS = 2_000
LEGACY_MAC_RUNTIME_ROOT = Path("/private/tmp/school-workflow-hub")
SYNTHETIC_RUNTIME_ROOT = (LEGACY_MAC_RUNTIME_ROOT if sys.platform == "darwin"
                          else Path("/tmp/school-workflow-hub"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_synthetic_runtime_path(path: PathLike) -> Path:
    """Accept only the explicit, local runtime root used by the shipped CLI.

    Core helpers remain usable with disposable test paths, but user-facing CLI
    commands cannot create a database/report in the repository or a cloud-sync
    location by accident.  A future real-data pilot needs a separately approved
    path policy rather than an environment-variable escape hatch.
    """

    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("runtime path must be absolute and under the approved synthetic runtime root")
    # Preserve existing config/test fixtures on Linux without creating /private
    # with elevated permissions. This is one fixed legacy alias, not an
    # arbitrary environment-variable or caller-selected runtime-root override.
    if SYNTHETIC_RUNTIME_ROOT != LEGACY_MAC_RUNTIME_ROOT:
        try:
            relative = candidate.relative_to(LEGACY_MAC_RUNTIME_ROOT)
        except ValueError:
            pass
        else:
            candidate = SYNTHETIC_RUNTIME_ROOT / relative
    resolved = candidate.resolve(strict=False)
    root = SYNTHETIC_RUNTIME_ROOT.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(
            "runtime path must be under {} for this synthetic foundation".format(root)
        ) from error
    return resolved


def connect(database_path: PathLike) -> sqlite3.Connection:
    """Open a short-wait SQLite connection with foreign keys always enabled."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=LOCK_WAIT_MS / 1000, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {LOCK_WAIT_MS}")
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Use an explicit, bounded-wait writer transaction."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def apply_migrations(connection: sqlite3.Connection) -> None:
    """Apply each numbered SQL migration exactly once."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        row["version"]
        for row in connection.execute("SELECT version FROM schema_migrations")
    }
    for migration in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        version = migration.stem
        if version in applied:
            continue
        script = migration.read_text(encoding="utf-8")
        # Migration names are repository-controlled (not user input), so they are
        # safe to embed. Runtime data uses parameterized SQL everywhere else.
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{script}\n"
                "INSERT INTO schema_migrations(version, applied_at) "
                f"VALUES ('{version}', '{utc_now()}');\n"
                "COMMIT;"
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise


def initialize_database(database_path: PathLike) -> sqlite3.Connection:
    connection = connect(database_path)
    apply_migrations(connection)
    return connection


def backup_database(source_path: PathLike, backup_path: PathLike) -> Path:
    """Create a SQLite-consistent local backup without exposing its contents."""
    source_path = Path(source_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"backup source database does not exist: {source_path}")
    target_path = Path(backup_path)
    if target_path.exists():
        raise FileExistsError(f"refusing to overwrite existing backup: {target_path}")
    source = connect(source_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(str(target_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return target_path


def restore_database(backup_path: PathLike, target_path: PathLike) -> Path:
    """Restore a local backup to an explicitly named target database.

    This helper is for disposable/local targets only in the current build. It is
    intentionally not wired to any live system or cloud location.
    """
    backup_path = Path(backup_path)
    if not backup_path.is_file():
        raise FileNotFoundError(f"restore source backup does not exist: {backup_path}")
    target_path = Path(target_path)
    if target_path.exists():
        raise FileExistsError(f"refusing to overwrite existing restore target: {target_path}")
    source = connect(backup_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(str(target_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return target_path
