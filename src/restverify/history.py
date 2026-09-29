"""SQLite run history at the XDG state dir (R10, I4a).

This is the **only** module allowed to write durable state outside the config
file and the temp restore dirs (I4 standing rule (c)). The other boundaries are
unchanged: `restic.py` spawns subprocesses, `tempstore.py` owns the temp area,
`history.py` owns the store.

Why a store at all: a verification that leaves no trace cannot show a trend, and
a trend that hides failures is worse than no trend — so **every run writes a row,
including failures**. Two things deliberately do *not* write:

  * `--dry-run` (nothing was restored, so there is nothing to trend) — decided by
    the caller in cli.py, never here;
  * `report` (reads only).

Shape (store schema 1): `runs` (one row per run; its `schema` column mirrors the
JSON envelope contract at write time) plus `meta` (key/value; `store_schema`). A
newer `store_schema` is refused with a teaching error rather than guessed at; an
older one would be migrated here when a trivial migration exists (none yet).

Failure policy (`HistoryError`, kind "history"):
  * corrupt / foreign database     -> teaching error naming the path, no delete;
  * state dir not writable         -> teaching error naming the path and cause;
  * database locked by another run -> teaching error suggesting a retry.
`report` treats these as fatal (exit 1); a `run` warns on stderr and keeps the
verification's exit code, because the verification is the product (ruling 2).
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import HOME_ENV
from .errors import HistoryError

STATE_ENV = "RESTVERIFY_STATE"
DB_NAME = "history.db"
STORE_SCHEMA = 1
RETENTION_PER_REPO = 1000          # pruned on write (I4c); announced once

_COLUMNS = ("id, started_at, finished_at, duration_ms, repo, snapshot, status, "
            "exit_code, error_kind, file_count, total_bytes, diff_count, digest, "
            "tool_version, schema")
_INSERT = _COLUMNS.replace("id, ", "")

_DDL = (
    """CREATE TABLE IF NOT EXISTS runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at   TEXT    NOT NULL,
        finished_at  TEXT    NOT NULL,
        duration_ms  INTEGER NOT NULL,
        repo         TEXT    NOT NULL,
        snapshot     TEXT,
        status       TEXT    NOT NULL,
        exit_code    INTEGER NOT NULL,
        error_kind   TEXT,
        file_count   INTEGER,
        total_bytes  INTEGER,
        diff_count   INTEGER,
        digest       TEXT,
        tool_version TEXT    NOT NULL,
        schema       INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS runs_repo_id ON runs(repo, id)",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)


def state_dir(base: Path | str | None = None) -> Path:
    """One place that derives the state directory (ruling 1).

    Explicit base > RESTVERIFY_STATE > RESTVERIFY_HOME/.local/state (R43,
    which also beats XDG_STATE_HOME — see config.xdg_base) >
    XDG_STATE_HOME/restverify > ~/.local/state/restverify. Not the config
    dir, not the temp dir.
    """
    if base:
        return Path(base)
    override = os.environ.get(STATE_ENV)
    if override:
        return Path(override).expanduser()
    home = os.environ.get(HOME_ENV)
    if home:
        root = Path(home).expanduser() / ".local" / "state"
    else:
        xdg = os.environ.get("XDG_STATE_HOME")
        root = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return root / "restverify"


def state_path(base: Path | str | None = None) -> Path:
    return state_dir(base) / DB_NAME


@dataclass
class RunRecord:
    """One row of `runs`; every field is what the run actually produced."""

    repo: str
    status: str                       # "pass" | "diff_mismatch" | "error"
    exit_code: int
    started_at: str
    finished_at: str
    duration_ms: int
    snapshot: str | None = None
    error_kind: str | None = None
    file_count: int | None = None
    total_bytes: int | None = None
    diff_count: int | None = None
    digest: str | None = None
    tool_version: str = "0"
    schema: int = STORE_SCHEMA

    def as_row(self) -> tuple:
        return (self.started_at, self.finished_at, self.duration_ms, self.repo,
                self.snapshot, self.status, self.exit_code, self.error_kind,
                self.file_count, self.total_bytes, self.diff_count, self.digest,
                self.tool_version, self.schema)


def iso(epoch: float) -> str:
    """UTC ISO-8601, second precision; the store's only timestamp format."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def _connect(path: Path, create: bool = False) -> sqlite3.Connection:
    """Open and verify the store. Raises HistoryError.

    ``create`` is True only for writers: a reader (`report`) must not create the
    directory, the tables or the meta row — "report reads only" is then provable
    by fingerprinting the file, not by convention.
    """
    if create:
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise HistoryError(
                f"could not create the history state directory {path.parent}: "
                f"{exc.strerror or exc}",
                hint="fix permissions, or point RESTVERIFY_STATE at a writable "
                     "directory (the store is machine state, not config)",
            ) from exc
    try:
        connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    except sqlite3.Error as exc:
        raise HistoryError(
            f"could not open the history store {path}: {exc}",
            hint="check permissions on the file and its directory",
        ) from exc

    def refuse(reason: str, hint: str):
        connection.close()
        raise HistoryError(
            f"the history store {path} could not be used: {reason}", hint=hint)

    try:
        # A valid SQLite file that is not ours, or a corrupt one, must never be
        # deleted or overwritten: refuse with a path the user can move aside.
        found = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError as exc:
        refuse(f"{exc}", f"move it aside (`mv {path} {path}.bak`) or set "
                         f"{STATE_ENV} to a fresh directory, then re-run")
    if create:
        if found and not {"runs", "meta"} <= found:
            refuse("it is a SQLite file that is not a restverify history store",
                   f"move it aside (`mv {path} {path}.bak`) or set {STATE_ENV} to "
                   "a fresh directory")
    elif not {"runs", "meta"} <= found:
        refuse("it is not a restverify history store (missing tables)",
               f"move it aside (`mv {path} {path}.bak`) or set {STATE_ENV} to a "
               "fresh directory")
    try:
        if create:
            for statement in _DDL:
                connection.execute(statement)
        version = connection.execute(
            "SELECT value FROM meta WHERE key='store_schema'").fetchone()
        if version is None:
            if create:
                connection.execute(
                    "INSERT INTO meta(key, value) VALUES('store_schema', ?)",
                    (str(STORE_SCHEMA),))
            else:
                refuse("it carries no store schema marker",
                       "move it aside or point RESTVERIFY_STATE at a fresh directory")
        elif int(version[0]) > STORE_SCHEMA:
            refuse(f"its store schema is {version[0]}, newer than this build's "
                   f"{STORE_SCHEMA}",
                   "upgrade restverify, or point RESTVERIFY_STATE at a fresh "
                   "directory to start a new history")
    except sqlite3.OperationalError as exc:
        refuse(f"{exc}", "another restverify run may be writing; retry, or check "
                         "permissions on the state directory")
    except sqlite3.DatabaseError as exc:
        refuse(f"{exc}", f"the file may be corrupt; move it aside (`mv {path} "
                         f"{path}.bak`) or set {STATE_ENV} to a fresh directory")
    return connection


def record(record_: RunRecord, base: Path | str | None = None) -> tuple[Path, int]:
    """Append one row atomically, then prune (I4c).

    Returns ``(store path, pruned row count)``. The insert and the prune share
    one BEGIN IMMEDIATE/COMMIT transaction, so a crash cannot leave a half
    state. The prune keeps the newest ``RETENTION_PER_REPO`` rows of this record's
    repository only — other repositories are never touched. Raises HistoryError.
    """
    path = state_path(base)
    connection = _connect(path, create=True)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f"INSERT INTO runs ({_INSERT}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            record_.as_row())
        pruned = 0
        kept = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE repo = ?", (record_.repo,)).fetchone()[0]
        if kept > RETENTION_PER_REPO:
            excess = kept - RETENTION_PER_REPO
            connection.execute(
                "DELETE FROM runs WHERE repo = ? AND id IN "
                "(SELECT id FROM runs WHERE repo = ? ORDER BY id ASC LIMIT ?)",
                (record_.repo, record_.repo, excess))
            pruned = excess
        connection.execute("COMMIT")      # crash before this -> rolled back
    except sqlite3.Error as exc:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise HistoryError(
            f"could not write this run to the history store {path}: {exc}",
            hint="check free space and permissions on the state directory",
        ) from exc
    finally:
        connection.close()
    return path, pruned


def read_recent(limit: int = 10, repo: str | None = None,
                base: Path | str | None = None) -> list[RunRecord]:
    """Newest first. Raises HistoryError when the store cannot be read."""
    path = state_path(base)
    if not path.exists():
        raise HistoryError(
            f"no runs recorded yet (no history store at {path}).",
            hint="run your first verification (`restverify run -r <repo>`), then "
                 "re-run report",
        )
    connection = _connect(path)
    try:
        sql = f"SELECT {_COLUMNS} FROM runs"
        params: tuple = ()
        if repo:
            sql += " WHERE repo = ?"
            params = (repo,)
        sql += " ORDER BY id DESC LIMIT ?"
        rows = connection.execute(sql, (*params, limit)).fetchall()
    except sqlite3.Error as exc:
        raise HistoryError(
            f"could not read the history store {path}: {exc}",
            hint="check permissions; the file may be corrupt (move it aside)",
        ) from exc
    finally:
        connection.close()
    return [_row_to_record(row) for row in rows]


def count(repo: str | None = None, base: Path | str | None = None) -> int:
    path = state_path(base)
    connection = _connect(path)
    try:
        sql = "SELECT COUNT(*) FROM runs"
        params: tuple = ()
        if repo:
            sql += " WHERE repo = ?"
            params = (repo,)
        return int(connection.execute(sql, params).fetchone()[0])
    except sqlite3.Error as exc:
        raise HistoryError(
            f"could not read the history store {path}: {exc}",
            hint="check permissions; the file may be corrupt (move it aside)",
        ) from exc
    finally:
        connection.close()


def _row_to_record(row: tuple) -> RunRecord:
    return RunRecord(
        started_at=row[1], finished_at=row[2], duration_ms=row[3], repo=row[4],
        snapshot=row[5], status=row[6], exit_code=row[7], error_kind=row[8],
        file_count=row[9], total_bytes=row[10], diff_count=row[11], digest=row[12],
        tool_version=row[13], schema=row[14],
    )

