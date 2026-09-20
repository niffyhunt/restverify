"""Increment I4b: `report` reads the history and renders a trend (R10).

What is proven here: empty history teaches (exit 1, kind "history"); one row and
mixed trends render; the last failure is explained in plain language, not as a
raw kind; `--json` is pure JSON with schema 1, command="report",
status="report"; `--repo`/`--limit` filter; a history of only failures does not
crash and still exits 0 (ruling 5); a corrupt store exits 1 and teaches; and
`report` never writes to the store — proven by fingerprinting the file, and by
showing it does not even create a missing store.
"""
import hashlib
import json
from pathlib import Path

import pytest

from restverify import EXIT_PASS, EXIT_RESTORE_FAIL, SCHEMA_VERSION, history
from restverify.cli import main


def seed(*rows) -> None:
    """Write rows directly (no restic needed for report's own tests)."""
    for index, row in enumerate(rows):
        history.record(history.RunRecord(
            repo=row.get("repo", "/srv/backup"),
            status=row["status"],
            exit_code=row["exit_code"],
            started_at=row.get("started_at", history.iso(1_700_000_000 + index)),
            finished_at=history.iso(1_700_000_001 + index),
            duration_ms=1000 + index,
            snapshot=row.get("snapshot", "9f3a2c00"),
            error_kind=row.get("error_kind"),
            file_count=row.get("file_count", 2),
            total_bytes=row.get("total_bytes", 1505),
            diff_count=row.get("diff_count"),
            digest=row.get("digest", "sha256:" + "ab" * 32),
            tool_version="0.1.0.dev0",
            schema=1,
        ))


def pure_json(text: str) -> dict:
    stripped = text.lstrip()
    payload, end = json.JSONDecoder().raw_decode(stripped)
    assert stripped[end:].strip() == "", f"trailing non-JSON: {stripped[end:]!r}"
    return payload


def fingerprint(path: Path) -> tuple:
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest())


PASS_ROW = {"status": "pass", "exit_code": 0, "diff_count": 0}
MISMATCH_ROW = {"status": "diff_mismatch", "exit_code": 2, "diff_count": 1}
ERROR_ROW = {"status": "error", "exit_code": 1, "error_kind": "source"}


# ── empty history teaches; nothing is created ─────────────────────────────

def test_empty_history_teaches(capsys):
    code = main(["report"])
    captured = capsys.readouterr()
    assert code == EXIT_RESTORE_FAIL
    assert "no runs recorded yet" in captured.err
    assert "next:" in captured.err
    assert captured.out == ""


def test_empty_history_json_is_an_error_envelope(capsys):
    code = main(["report", "--json"])
    payload = pure_json(capsys.readouterr().out)
    assert code == EXIT_RESTORE_FAIL
    assert payload["status"] == "error" and payload["error"]["kind"] == "history"
    assert payload["command"] == "report"


def test_report_does_not_create_the_store(capsys):
    assert not history.state_path().exists()
    main(["report"])
    capsys.readouterr()
    assert not history.state_path().exists()


# ── rendering ─────────────────────────────────────────────────────────────

def test_one_row_renders(capsys):
    seed(PASS_ROW)
    assert main(["report"]) == EXIT_PASS
    out = capsys.readouterr().out
    assert "restverify report — 1 recent run(s)" in out
    assert "1 pass, 0 diff, 0 error" in out
    assert "9f3a2c00" in out and "/srv/backup" in out


def test_mixed_trend_counts(capsys):
    seed(PASS_ROW, PASS_ROW, MISMATCH_ROW, ERROR_ROW, PASS_ROW)
    assert main(["report"]) == EXIT_PASS
    out = capsys.readouterr().out
    assert "3 pass, 1 diff, 1 error" in out
    assert "(60% pass)" in out


def test_longest_green_streak(capsys):
    seed(PASS_ROW, PASS_ROW, PASS_ROW, ERROR_ROW, PASS_ROW)
    main(["report"])
    assert "longest green streak 3 run(s)" in capsys.readouterr().out


def test_last_failure_reason_is_plain_language(capsys):
    seed(PASS_ROW, ERROR_ROW)
    main(["report"])
    out = capsys.readouterr().out
    assert "the comparison source could not be read" in out


def test_only_failures_does_not_crash_and_exits_zero(capsys):
    seed(MISMATCH_ROW, ERROR_ROW, MISMATCH_ROW)
    assert main(["report"]) == EXIT_PASS
    out = capsys.readouterr().out
    assert "0 pass, 2 diff, 1 error" in out
    assert "longest green streak 0 run(s)" in out


def test_last_failed_run_still_exits_zero(capsys):
    """Ruling 5: report reports; `run` is what cron acts on."""
    seed(PASS_ROW, ERROR_ROW)
    assert main(["report"]) == EXIT_PASS


def test_limit_is_honoured(capsys):
    seed(PASS_ROW, PASS_ROW, PASS_ROW, PASS_ROW, PASS_ROW)
    code = main(["report", "--limit", "2", "--json"])
    payload = pure_json(capsys.readouterr().out)
    assert code == EXIT_PASS
    assert payload["report"]["limit"] == 2 and payload["report"]["runs"] == 2


# ── --json contract and purity (block 12 standard) ────────────────────────

def test_json_shape_and_purity(capsys):
    seed(PASS_ROW, MISMATCH_ROW)
    code = main(["report", "--json"])
    captured = capsys.readouterr()
    payload = pure_json(captured.out)
    assert code == EXIT_PASS and captured.err == ""
    assert set(payload) >= {"tool", "schema", "version", "command", "status", "exit_code"}
    assert payload["schema"] == SCHEMA_VERSION == 1
    assert payload["command"] == "report" and payload["status"] == "report"
    block = payload["report"]
    assert set(block) >= {"repo", "runs", "limit", "counts", "pass_rate",
                          "longest_green_streak", "last_failure", "runs"}
    assert block["counts"] == {"pass": 1, "diff_mismatch": 1, "error": 0}
    assert block["pass_rate"] == 0.5
    assert block["last_failure"]["kind"] is None
    assert block["last_failure"]["reason"] == \
        "the restored data differed from the source (1 differing file(s))"
    assert [row["status"] for row in block["recent"]] == ["diff_mismatch", "pass"]


def test_json_last_failure_reason_is_plain_language(capsys):
    seed(ERROR_ROW)
    main(["report", "--json"])
    payload = pure_json(capsys.readouterr().out)
    failure = payload["report"]["last_failure"]
    assert failure["kind"] == "source"
    assert failure["reason"] == "the comparison source could not be read"


# ── filtering ─────────────────────────────────────────────────────────────

def test_repo_filter_selects_one_repo(capsys):
    seed({"status": "pass", "exit_code": 0, "repo": "/srv/backup", "diff_count": 0},
         {"status": "pass", "exit_code": 0, "repo": "/srv/nas", "diff_count": 0},
         {"status": "pass", "exit_code": 0, "repo": "/srv/nas", "diff_count": 0})
    code = main(["report", "--repo", "/srv/nas", "--json"])
    payload = pure_json(capsys.readouterr().out)
    assert code == EXIT_PASS
    assert payload["report"]["runs"] == 2
    assert {row["repo"] for row in payload["report"]["recent"]} == {"/srv/nas"}


def test_repo_filter_accepts_a_saved_name(config_path, capsys):
    main(["init", "-r", "/srv/backup", "-s", "/srv/data"])
    capsys.readouterr()
    seed({"status": "pass", "exit_code": 0, "repo": "/srv/backup", "diff_count": 0})
    code = main(["report", "--repo", "backup", "--json"])
    payload = pure_json(capsys.readouterr().out)
    assert code == EXIT_PASS and payload["report"]["repo"] == "/srv/backup"


def test_filter_with_no_matching_runs_teaches(capsys):
    seed({"status": "pass", "exit_code": 0, "repo": "/srv/backup", "diff_count": 0})
    code = main(["report", "--repo", "/srv/other"])
    captured = capsys.readouterr()
    assert code == EXIT_RESTORE_FAIL
    assert "no runs recorded yet for /srv/other" in captured.err


# ── corruption, read-only proof, help ─────────────────────────────────────

def test_corrupt_store_exits_1_with_a_teaching_error(capsys):
    history.state_dir().mkdir(parents=True, exist_ok=True)
    history.state_path().write_bytes(b"this is not a database at all")
    code = main(["report"])
    captured = capsys.readouterr()
    assert code == EXIT_RESTORE_FAIL
    assert "could not be used" in captured.err
    assert "move it aside" in captured.err
    assert "Traceback" not in captured.err


def test_report_never_writes_to_the_store(capsys):
    seed(PASS_ROW, MISMATCH_ROW)
    before = fingerprint(history.state_path())
    assert main(["report"]) == EXIT_PASS
    main(["report", "--json"])
    main(["report", "--repo", "/srv/backup", "--limit", "1"])
    capsys.readouterr()
    assert fingerprint(history.state_path()) == before      # byte-for-byte


def test_help_documents_the_columns_and_the_envelope(capsys):
    with pytest.raises(SystemExit):
        main(["report", "--help"])
    out = capsys.readouterr().out
    for token in ("columns (newest first)", "started", "status", "exit", "kind",
                  "repo", "json envelope", "schema 1", '"report"',
                  "longest_green_streak", "read at all"):
        assert token in out, f"report --help does not document: {token}"

