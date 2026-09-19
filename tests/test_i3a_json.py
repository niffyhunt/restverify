"""Increment I3a: one error envelope, pure-JSON stdout, argv pre-scan (R11-R14).

Contract proven here (I3 rulings 1, 2 and 5):

  * under --json, stdout is exactly one JSON object and nothing else, in every
    mode — success, --dry-run, --no-source skip, and every failure kind;
  * the teaching text (what + "next: ...") is written to stderr, so
    ``restverify run -r X --json > out.json`` can never be corrupted;
  * --json is detected by scanning raw argv before argparse runs, which is what
    lets a usage error (64) emit the same envelope with kind "usage";
  * human mode is unchanged from I1/I2: failures teach on **stderr** with an
    empty stdout. (The I3 prompt's parenthetical said stdout; the shipped and
    tested behaviour since I1 is stderr — recorded as deviation D-I3-1, and
    "human mode unchanged" is the instruction that was followed.)

No test in this module asserts anything about timing (standing rule a), and no
test spawns the fake restic more than once per run path (trend guard b).
"""
import json

import pytest

from restverify import EXIT_DIFF_MISMATCH, EXIT_PASS, EXIT_RESTORE_FAIL, EXIT_USAGE
from restverify.cli import main
from restverify.errors import ERROR_KINDS

TOP_LEVEL = {"tool", "version", "command", "status", "exit_code"}
THE_TEN_KINDS = {
    "usage", "config", "restic_missing", "restic_failed", "no_snapshots",
    "source", "manifest", "sample", "tempdir", "interrupted",
}


def pure_json(text: str) -> dict:
    """Parse stdout and prove there is nothing before or after the object."""
    stripped = text.lstrip()
    assert stripped.startswith("{"), f"stdout is not a JSON object: {text[:80]!r}"
    payload, end = json.JSONDecoder().raw_decode(stripped)
    assert stripped[end:].strip() == "", f"trailing non-JSON text: {stripped[end:]!r}"
    return payload


def run_json(capsys, argv):
    code = main(argv)
    return code, capsys.readouterr()


# ── the closed vocabulary itself (ruling 5) ────────────────────────────────

def test_error_kind_vocabulary_is_closed_and_has_no_diff_mismatch():
    assert ERROR_KINDS == THE_TEN_KINDS
    assert "diff_mismatch" not in ERROR_KINDS


# ── the six failure kinds, each in --json mode ─────────────────────────────

def test_config_error_is_json_64_kind_config(config_path, capsys):
    config_path.write_text("this is not = = toml\n", encoding="utf-8")
    code, captured = run_json(capsys, ["run", "-r", "/srv/backup", "--json"])
    assert code == EXIT_USAGE
    payload = pure_json(captured.out)
    assert set(payload) >= TOP_LEVEL
    assert payload["status"] == "error" and payload["exit_code"] == EXIT_USAGE
    assert payload["error"]["kind"] == "config"
    assert "next:" in captured.err                    # teaching line on stderr


def test_missing_restic_is_json_1_kind_restic_missing(monkeypatch, tmp_base,
                                                      config_path, capsys):
    monkeypatch.setenv("PATH", "/definitely/not/here")
    code, captured = run_json(capsys, ["run", "-r", "/srv/backup", "--json"])
    assert code == EXIT_RESTORE_FAIL
    payload = pure_json(captured.out)
    assert payload["error"]["kind"] == "restic_missing"
    assert "install restic" in captured.err


def test_empty_repo_is_json_1_kind_no_snapshots(fake_restic, tmp_base, config_path, capsys):
    fake_restic.set_mode("empty")
    code, captured = run_json(capsys, ["run", "-r", "/srv/backup", "--json"])
    assert code == EXIT_RESTORE_FAIL
    payload = pure_json(captured.out)
    assert payload["error"]["kind"] == "no_snapshots"
    assert "no snapshots found" in captured.err


def test_restore_failure_is_json_1_kind_restic_failed(fake_restic, tmp_base,
                                                      config_path, capsys):
    fake_restic.set_mode("fail_restore")
    code, captured = run_json(capsys, ["run", "-r", "/srv/backup", "--json"])
    assert code == EXIT_RESTORE_FAIL
    payload = pure_json(captured.out)
    assert payload["error"]["kind"] == "restic_failed"
    assert payload["error"]["stderr_tail"]            # restic's own words survive
    assert "restic said:" in captured.err


def test_missing_source_is_json_1_kind_source(fake_restic, tmp_base, config_path, capsys):
    code, captured = run_json(
        capsys, ["run", "-r", "/srv/backup", "-s", "/definitely/not/here", "--json"])
    assert code == EXIT_RESTORE_FAIL
    payload = pure_json(captured.out)
    assert payload["error"]["kind"] == "source"
    assert "/definitely/not/here" in payload["error"]["what"]
    assert "next:" in captured.err


def test_usage_error_is_json_64_kind_usage(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["run", "--json", "--nope"])
    captured = capsys.readouterr()
    assert exc.value.code == EXIT_USAGE
    payload = pure_json(captured.out)
    assert payload["error"]["kind"] == "usage"
    assert payload["command"] == "run"                # argv pre-scan named it
    assert "error:" in captured.err and "next:" in captured.err


# ── purity in the non-error modes ──────────────────────────────────────────

def test_success_json_is_pure_and_compare_is_skipped(fake_restic, tmp_base,
                                                     config_path, capsys):
    code, captured = run_json(capsys, ["run", "-r", "/srv/backup", "--json"])
    assert code == EXIT_PASS
    payload = pure_json(captured.out)
    assert payload["status"] == "pass"
    assert payload["compare"]["status"] == "skipped"
    assert captured.err == ""


def test_dry_run_json_is_pure_and_invokes_no_restic(fake_restic, tmp_base,
                                                    config_path, capsys):
    code, captured = run_json(capsys, ["run", "-r", "/srv/backup", "--dry-run", "--json"])
    assert code == EXIT_PASS
    payload = pure_json(captured.out)
    assert payload["status"] == "dry_run"
    assert payload["dry_run"]["restic_invoked"] is False
    assert fake_restic.calls() == []                  # nothing was spawned
    assert captured.err == ""


def test_mismatch_json_is_pure_and_is_not_an_error_object(
        fake_restic, tmp_base, config_path, source_tree, capsys):
    """Exit 2 is a normal run outcome: status diff_mismatch, and no error.kind
    (ruling 5 — diff_mismatch is deliberately not in the vocabulary)."""
    main(["init", "-r", "/srv/backup", "-s", str(source_tree)])
    (source_tree / "restored.txt").write_bytes(b"hellp")
    capsys.readouterr()
    code, captured = run_json(capsys, ["run", "-r", "/srv/backup", "--json"])
    payload = pure_json(captured.out)
    assert code == EXIT_DIFF_MISMATCH
    assert payload["status"] == "diff_mismatch"
    assert payload["exit_code"] == EXIT_DIFF_MISMATCH
    assert payload.get("error") is None
    assert captured.err == ""


def test_every_mode_shares_the_top_level_keys(fake_restic, tmp_base, config_path,
                                              source_tree, capsys):
    main(["init", "-r", "/srv/backup", "-s", str(source_tree)])
    capsys.readouterr()
    payloads = [
        pure_json(run_json(capsys, ["run", "-r", "/srv/backup", "--json"])[1].out),
        pure_json(run_json(capsys,
                           ["run", "-r", "/srv/backup", "--dry-run", "--json"])[1].out),
    ]
    fake_restic.set_mode("empty")
    payloads.append(
        pure_json(run_json(capsys, ["run", "-r", "/srv/backup", "--json"])[1].out))
    for payload in payloads:
        assert TOP_LEVEL <= set(payload), payload.get("status")


# ── human mode is unchanged (deviation D-I3-1) ─────────────────────────────

def test_human_failure_still_teaches_on_stderr_with_empty_stdout(
        fake_restic, tmp_base, config_path, capsys):
    fake_restic.set_mode("fail_restore")
    code = main(["run", "-r", "/srv/backup"])
    captured = capsys.readouterr()
    assert code == EXIT_RESTORE_FAIL
    assert captured.out == ""                         # no JSON, no human text
    assert "restic restore failed" in captured.err


def test_human_usage_error_is_unchanged(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["run", "--nope"])
    captured = capsys.readouterr()
    assert exc.value.code == EXIT_USAGE
    assert captured.out == ""
    assert "error:" in captured.err and "next:" in captured.err


def test_init_json_stays_deferred(config_path, capsys):
    """Ruling 4: no speculative schema for init/report/cron at I3."""
    code = main(["init", "-r", "/srv/backup", "--json"])
    captured = capsys.readouterr()
    assert code == EXIT_PASS
    assert "not complete until increment I3" in captured.err
    assert captured.out.lstrip().startswith("\u2713")
