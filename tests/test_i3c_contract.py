"""Increment I3c: "schema": 1, documented envelope, no placeholder survives.

Contract proven here:
  * every JSON mode (success, --dry-run, --no-source skip, error) carries
    "schema": 1 and the same required top-level keys, with only the known
    mode-specific blocks differing;
  * every error carries a kind from the closed vocabulary;
  * the four exit codes (0/1/2/64) are all asserted in JSON mode in ONE test;
  * no "incomplete" placeholder exists anywhere in the shipped package;
  * `run --help` documents the envelope, schema, kinds and stdout purity.
"""
import json
from pathlib import Path

import pytest

from restverify import (EXIT_DIFF_MISMATCH, EXIT_PASS, EXIT_RESTORE_FAIL,
                        EXIT_USAGE, SCHEMA_VERSION, errors)
from restverify.cli import main

SHIPPED = sorted((Path(__file__).resolve().parents[1] / "src" / "restverify").rglob("*.py"))
REQUIRED_KEYS = {"tool", "schema", "version", "command", "status", "exit_code"}
MODE_BLOCKS = {"snapshot", "restore", "manifest", "sample", "compare", "strict",
               "dry_run", "error", "history"}


def pure_json(text: str) -> dict:
    stripped = text.lstrip()
    payload, end = json.JSONDecoder().raw_decode(stripped)
    assert stripped[end:].strip() == "", f"trailing non-JSON: {stripped[end:]!r}"
    return payload


def run_mode(capsys, argv):
    code = main(argv)
    payload = pure_json(capsys.readouterr().out)
    assert payload["exit_code"] == code, f"envelope disagrees with process code: {argv}"
    return code, payload


def test_schema_and_contract_keys_in_every_mode(fake_restic, tmp_base, config_path,
                                                source_tree, capsys):
    main(["init", "-r", "/srv/backup", "-s", str(source_tree)])
    capsys.readouterr()
    modes = {
        "success": run_mode(capsys, ["run", "-r", "/srv/backup", "--json"])[1],
        "dry_run": run_mode(capsys,
                            ["run", "-r", "/srv/backup", "--dry-run", "--json"])[1],
        "skip": run_mode(capsys,
                         ["run", "-r", "/srv/backup", "--no-source", "--json"])[1],
        "error": run_mode(capsys, ["run", "-r", "/srv/backup",
                                   "-s", "/definitely/not/here", "--json"])[1],
    }
    for name, payload in modes.items():
        assert payload["schema"] == SCHEMA_VERSION == 1, name
        assert REQUIRED_KEYS <= set(payload), name           # shared contract keys
        assert set(payload) - REQUIRED_KEYS <= MODE_BLOCKS, name   # no stray keys
        assert "incomplete" not in payload, name
    assert modes["error"]["error"]["kind"] in errors.ERROR_KINDS
    assert modes["error"]["error"]["kind"] == "source"


def test_all_four_exit_codes_in_json_mode(fake_restic, tmp_base, config_path,
                                          source_tree, capsys):
    main(["init", "-r", "/srv/backup", "-s", str(source_tree)])
    capsys.readouterr()
    results = [run_mode(capsys, ["run", "-r", "/srv/backup", "--json"])]
    (source_tree / "restored.txt").write_bytes(b"hellp")
    results.append(run_mode(capsys, ["run", "-r", "/srv/backup", "--json"]))
    results.append(run_mode(capsys, ["run", "-r", "/srv/backup",
                                     "-s", "/nope/missing", "--json"]))
    with pytest.raises(SystemExit) as exc:
        main(["run", "--json", "--nope"])
    usage = pure_json(capsys.readouterr().out)
    results.append((exc.value.code, usage))

    assert [code for code, _ in results] == [EXIT_PASS, EXIT_DIFF_MISMATCH,
                                             EXIT_RESTORE_FAIL, EXIT_USAGE]
    assert [payload["status"] for _, payload in results] == \
        ["pass", "diff_mismatch", "error", "error"]
    assert all(payload["schema"] == 1 for _, payload in results)


def test_no_incomplete_placeholder_in_shipped_source():
    hits = sorted(p.name for p in SHIPPED if "incomplete" in p.read_text(encoding="utf-8"))
    assert hits == [], f"placeholder text still shipped in: {hits}"


def test_help_documents_the_envelope(capsys):
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    out = capsys.readouterr().out
    for token in (
        "json envelope",
        '"schema": 1 is the public contract',
        "stdout carries exactly one JSON object",
        "top-level keys: tool, schema, version, command, status, exit_code",
        "usage | config | restic_missing | restic_failed | no_snapshots",
        "source | manifest | sample | tempdir | interrupted",
        "exit_code mirrors the process exit code",
    ):
        assert token in out, f"run --help does not document: {token}"
