"""Increment I4c: retention + documentation + the row/envelope contract.

Retention is 1000 rows per repository, pruned on write, announced once per
process per repository (ruling 2). The 1001-row proof below bulk-inserts its
seed rows with one `executemany` instead of 1001 `record()` calls - standing
rule (b) caps a new test at 500 ms, and the behaviour under test is the prune,
not 1001 individual fsyncs. One real `record()` still creates the store, and a
real CLI run performs the prune.

The row/envelope contract test covers all four states in one test because the
I4 prompt requires it -> the 750 ms multi-mode allowance applies (four
fake-restic runs).
"""
import json
import sqlite3

import pytest

from restverify import (EXIT_PASS, EXIT_RESTORE_FAIL, SCHEMA_VERSION, errors,
                        history)
from restverify.cli import _write_history, main

REPO = "/srv/backup"


def bulk_seed(repo: str, rows: int) -> None:
    """One real record creates the store; then bulk-insert `rows` more."""
    history.record(history.RunRecord(
        repo=repo, status="pass", exit_code=0,
        started_at=history.iso(0), finished_at=history.iso(1), duration_ms=1,
        tool_version="0.1.0.dev0", schema=1))
    values = [(history.iso(1_700_000_000 + i), history.iso(1_700_000_001 + i), i,
               repo, "9f3a2c00", "pass", 0, None, None, None, None, None,
               "0.1.0.dev0", 1) for i in range(rows)]
    connection = sqlite3.connect(history.state_path())
    connection.executemany(
        "INSERT INTO runs (started_at, finished_at, duration_ms, repo, snapshot,"
        " status, exit_code, error_kind, file_count, total_bytes, diff_count,"
        " digest, tool_version, schema) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        values)
    connection.commit()
    connection.close()


def id_span() -> tuple:
    connection = sqlite3.connect(history.state_path())
    low, high, count = connection.execute(
        "SELECT MIN(id), MAX(id), COUNT(*) FROM runs").fetchone()
    connection.close()
    return low, high, count


def test_retention_keeps_the_newest_1000_rows(fake_restic, tmp_base, config_path,
                                              capsys):
    bulk_seed(REPO, 1000)                     # 1 real + 1000 bulk = 1001 rows
    code = main(["run", "-r", REPO])          # this row triggers the prune
    captured = capsys.readouterr()
    assert code == EXIT_PASS
    low, high, count = id_span()
    assert count == history.RETENTION_PER_REPO == 1000
    assert low == 3                           # the two oldest rows are gone
    assert high == 1002                       # this run's row survived
    assert "history pruned for /srv/backup: kept the newest 1000 rows" in captured.err


def test_prune_is_announced_once_per_process(capsys):
    from restverify import cli as climod
    climod._PRUNE_ANNOUNCED.clear()           # a fresh process, as main() does
    bulk_seed(REPO, 999)                      # 1 real + 999 bulk = exactly 1000
    record = history.RunRecord(
        repo=REPO, status="pass", exit_code=0, started_at=history.iso(0),
        finished_at=history.iso(1), duration_ms=1, tool_version="0.1.0.dev0",
        schema=1)
    first = _write_history(record)            # 1001 -> prune 1 -> announce
    second = _write_history(record)           # 1001 -> prune 1 -> suppressed
    captured = capsys.readouterr()
    assert first["pruned"] == 1 and second["pruned"] == 1   # the cap holds
    assert captured.err.count("history pruned") == 1        # announced once


def test_retention_never_touches_other_repositories(capsys):
    bulk_seed(REPO, 1000)
    bulk_seed("/srv/other", 3)
    _write_history(history.RunRecord(
        repo=REPO, status="pass", exit_code=0, started_at=history.iso(0),
        finished_at=history.iso(1), duration_ms=1, tool_version="0.1.0.dev0",
        schema=1))
    connection = sqlite3.connect(history.state_path())
    other = connection.execute(
        "SELECT COUNT(*) FROM runs WHERE repo = '/srv/other'").fetchone()[0]
    connection.close()
    assert other == 4                          # untouched by REPO's prune


# ── the row/envelope contract (all four states, one test) ─────────────────

def run_json(capsys, argv):
    code = main(argv)
    stripped = capsys.readouterr().out.lstrip()
    payload, end = json.JSONDecoder().raw_decode(stripped)
    assert stripped[end:].strip() == ""
    return code, payload


def test_row_matches_the_envelope_for_all_four_states(
        fake_restic, tmp_base, config_path, source_tree, capsys):
    """I4c contract test: the row a run writes matches its JSON envelope
    (status/exit_code/schema) for success, mismatch, skip and error. The I4
    prompt requires all four states in ONE test -> 750 ms allowance applies."""
    main(["init", "-r", REPO, "-s", str(source_tree)])
    capsys.readouterr()

    def one_state(argv):
        code, payload = run_json(capsys, argv)
        row = history.read_recent(limit=1)[0]
        assert (row.status, row.exit_code, row.schema) == (
            payload["status"], payload["exit_code"], payload["schema"])
        assert row.schema == SCHEMA_VERSION
        return row.status, row.error_kind

    states = [one_state(["run", "-r", REPO, "--json"])]           # success
    (source_tree / "restored.txt").write_bytes(b"hellp")          # 1 byte, same size
    states.append(one_state(["run", "-r", REPO, "--json"]))       # mismatch
    (source_tree / "restored.txt").write_bytes(b"hello")          # restore
    states.append(one_state(["run", "-r", REPO, "--no-source", "--json"]))  # skip
    states.append(one_state(["run", "-r", REPO, "-s", "/nope/x", "--json"]))  # error

    assert states == [("pass", None), ("diff_mismatch", None),
                      ("pass", None), ("error", "source")]


def test_help_documents_the_store_and_retention(capsys):
    with pytest.raises(SystemExit):
        main(["report", "--help"])
    out = capsys.readouterr().out
    for token in ("history.db", "RESTVERIFY_STATE",
                  "newest 1000 rows per repository",
                  "pruned on write", "not supported yet"):
        assert token in out, f"report --help does not document: {token}"


def test_read_recent_still_refuses_a_foreign_store(isolated_state):
    isolated_state.mkdir(parents=True, exist_ok=True)
    db = isolated_state / history.DB_NAME
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE something_else(x)")
    connection.commit()
    connection.close()
    with pytest.raises(errors.HistoryError) as exc:
        history.read_recent(limit=1)
    assert "not a restverify history store" in exc.value.what
