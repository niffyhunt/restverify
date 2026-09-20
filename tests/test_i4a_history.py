"""Increment I4a: the SQLite run history (R10).

The store is the only durable state restverify writes (besides the config file),
so it gets the same treatment as the temp dirs: a teaching error for a corrupt or
foreign database (never a silent delete), a teaching error for an unwritable
state dir, and — per the operator's I4 ruling 2 — a store failure during a
`run` warns on stderr and does NOT change the verification's exit code.

No timing assertions (standing rule (a)); every test writes into the isolated
per-test state dir from conftest (no test touches ~/.local/state).
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from restverify import (EXIT_DIFF_MISMATCH, EXIT_PASS, EXIT_RESTORE_FAIL,
                        __version__, errors, history)
from restverify.cli import main

SRC = str(Path(__file__).resolve().parents[1] / "src")


def sample_record(**over) -> history.RunRecord:
    fields = dict(
        repo="/srv/backup", status="pass", exit_code=0,
        started_at=history.iso(0), finished_at=history.iso(1), duration_ms=1000,
        snapshot="9f3a2c00", file_count=2, total_bytes=1505, diff_count=0,
        digest="sha256:abc", tool_version=__version__, schema=1,
    )
    fields.update(over)
    return history.RunRecord(**fields)


def write_corrupt(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / history.DB_NAME).write_bytes(b"this is not a database at all")
    return path / history.DB_NAME


# ── path derivation, in one place (ruling 1) ──────────────────────────────

def test_state_path_is_under_xdg_state(monkeypatch, tmp_path):
    monkeypatch.delenv(history.STATE_ENV, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert history.state_path() == tmp_path / "restverify" / history.DB_NAME


def test_restverify_state_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(history.STATE_ENV, str(tmp_path / "custom"))
    assert history.state_path() == tmp_path / "custom" / history.DB_NAME


def test_store_is_created_on_first_run(fake_restic, tmp_base, config_path,
                                       capsys, isolated_state):
    assert not isolated_state.exists()
    assert main(["run", "-r", "/srv/backup"]) == EXIT_PASS
    assert history.state_path().exists()


# ── round-trip and durability ─────────────────────────────────────────────

def test_round_trip_write_read():
    record = sample_record()
    history.record(record)
    assert history.read_recent(limit=5) == [record]      # all 15 fields
    assert history.count() == 1


def test_row_survives_a_process_restart(isolated_state):
    history.record(sample_record(repo="/srv/nas"))
    program = (f"import sys; sys.path.insert(0, {SRC!r});"
               "from restverify import history;"
               "row = history.read_recent(limit=1)[0];"
               "print(row.repo, row.status, row.schema, row.tool_version)")
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True,
        env={**os.environ, history.STATE_ENV: str(isolated_state)})
    assert result.returncode == 0
    assert result.stdout.split() == ["/srv/nas", "pass", "1", __version__]


# ── rows for pass / mismatch / error; none for --dry-run ──────────────────

def test_run_writes_a_row_on_pass(fake_restic, tmp_base, config_path, capsys):
    assert main(["run", "-r", "/srv/backup"]) == EXIT_PASS
    row = history.read_recent(limit=1)[0]
    assert (row.status, row.exit_code, row.error_kind) == ("pass", 0, None)
    assert row.repo == "/srv/backup" and row.snapshot == "9f3a2c00"
    assert row.file_count == 2 and row.total_bytes == 1505
    assert row.schema == 1 and row.tool_version == __version__
    assert row.duration_ms >= 0 and row.finished_at >= row.started_at


def test_run_writes_a_row_on_mismatch(fake_restic, tmp_base, config_path,
                                      source_tree, capsys):
    main(["init", "-r", "/srv/backup", "-s", str(source_tree)])
    (source_tree / "restored.txt").write_bytes(b"hellp")
    capsys.readouterr()
    assert main(["run", "-r", "/srv/backup"]) == EXIT_DIFF_MISMATCH
    row = history.read_recent(limit=1)[0]
    assert (row.status, row.exit_code, row.error_kind) == ("diff_mismatch", 2, None)
    assert row.diff_count == 1


def test_run_writes_a_row_on_error_with_the_kind(fake_restic, tmp_base,
                                                 config_path, capsys):
    assert main(["run", "-r", "/srv/backup", "-s", "/definitely/not/here"]) == \
        EXIT_RESTORE_FAIL
    row = history.read_recent(limit=1)[0]
    assert (row.status, row.exit_code, row.error_kind) == ("error", 1, "source")
    assert row.file_count is None and row.digest is None    # nothing verified


def test_config_error_writes_no_row(config_path, capsys):
    config_path.write_text("not = = toml\n", encoding="utf-8")
    assert main(["run", "-r", "/srv/backup"]) == 64
    assert not history.state_path().exists()


def test_dry_run_writes_no_row(fake_restic, tmp_base, config_path, capsys):
    assert main(["run", "-r", "/srv/backup", "--dry-run"]) == EXIT_PASS
    assert not history.state_path().exists()


def test_no_source_run_still_writes_a_row(fake_restic, tmp_base, config_path, capsys):
    assert main(["run", "-r", "/srv/backup", "--no-source"]) == EXIT_PASS
    row = history.read_recent(limit=1)[0]
    assert row.status == "pass" and row.diff_count is None


# ── the additive JSON block ───────────────────────────────────────────────

def test_json_envelope_reports_the_written_row(fake_restic, tmp_base,
                                               config_path, capsys):
    main(["run", "-r", "/srv/backup", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["history"]["recorded"] is True
    assert payload["history"]["path"] == str(history.state_path())
    assert "warning" not in payload["history"]


# ── corrupt / foreign / unwritable stores ─────────────────────────────────

def test_foreign_sqlite_file_is_refused_and_never_deleted(isolated_state):
    isolated_state.mkdir(parents=True, exist_ok=True)
    db = isolated_state / history.DB_NAME
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE something_else(x)")
    connection.commit()
    connection.close()
    with pytest.raises(errors.HistoryError) as exc:
        history.read_recent(limit=1)
    assert "not a restverify history store" in exc.value.what
    assert "move it aside" in exc.value.hint
    assert db.exists()                                  # never deleted


def test_corrupt_file_is_a_teaching_error(isolated_state):
    db = write_corrupt(isolated_state)
    with pytest.raises(errors.HistoryError) as exc:
        history.record(sample_record())
    assert "could not be used" in exc.value.what
    assert "move it aside" in exc.value.hint
    assert db.exists()


def test_newer_store_schema_is_refused(isolated_state):
    """A real v1 store whose meta says a newer schema must be refused, not guessed."""
    history.record(sample_record())                 # creates a genuine v1 store
    db = isolated_state / history.DB_NAME
    connection = sqlite3.connect(db)
    connection.execute("UPDATE meta SET value='99' WHERE key='store_schema'")
    connection.commit()
    connection.close()
    with pytest.raises(errors.HistoryError) as exc:
        history.read_recent(limit=1)
    assert "newer than this build" in exc.value.what


def test_corrupt_store_warns_but_does_not_fail_a_clean_run(
        fake_restic, tmp_base, config_path, capsys):
    """Ruling 2: verification is the product; history is auxiliary."""
    write_corrupt(history.state_dir())
    code = main(["run", "-r", "/srv/backup"])
    captured = capsys.readouterr()
    assert code == EXIT_PASS
    assert "could not record this run in history" in captured.err
    assert "next:" in captured.err
    assert "Traceback" not in captured.err


def test_json_reports_a_failed_history_write_without_changing_exit(
        fake_restic, tmp_base, config_path, capsys):
    write_corrupt(history.state_dir())
    code = main(["run", "-r", "/srv/backup", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_PASS and payload["exit_code"] == EXIT_PASS
    assert payload["history"]["recorded"] is False
    assert payload["history"]["warning"]


def test_unwritable_state_dir_is_a_teaching_error(monkeypatch, tmp_path):
    """As root, mode bits are ignored — so point the state dir at a *file*."""
    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file, not a directory", encoding="utf-8")
    monkeypatch.setenv(history.STATE_ENV, str(blocked))
    with pytest.raises(errors.HistoryError) as exc:
        history.record(sample_record())
    assert "could not create the history state directory" in exc.value.what
    assert history.STATE_ENV in exc.value.hint


def test_unwritable_state_dir_warns_but_does_not_fail_a_clean_run(
        fake_restic, tmp_base, config_path, tmp_path, monkeypatch, capsys):
    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file, not a directory", encoding="utf-8")
    monkeypatch.setenv(history.STATE_ENV, str(blocked))
    code = main(["run", "-r", "/srv/backup"])
    captured = capsys.readouterr()
    assert code == EXIT_PASS
    assert "could not record this run in history" in captured.err
    assert "next:" in captured.err

