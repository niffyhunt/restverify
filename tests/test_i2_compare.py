"""Increment I2c: excludes-aware source comparison + exit code 2 (R4, R9-R14, R26).

A clean tree exits 0; any data difference exits 2 and names the offender; a
broken source teaches and exits 1; a bad flag is 64. All four codes are asserted
in one cron-style test (gate G2). The source is proven untouched.

Test-only helper: `fingerprint()` is deliberately **not** a product API. It exists
so `test_source_is_never_written_to` can prove (size + mtime_ns + sha256) that the
source tree is untouched; it is evidence for R26/R23 and is named in
`docs/PHASE2-PLAN.md` under "Test infrastructure" (test-only helpers carry no
requirement id; shipped helpers do — e.g. R29 for `cli.human_bytes()`).
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from restverify import (EXIT_DIFF_MISMATCH, EXIT_PASS, EXIT_RESTORE_FAIL,
                        EXIT_USAGE, compare as comparemod, excludes as excludesmod,
                        manifest as manifestmod)
from restverify.cli import main


def configure(repo, source, *extra):
    """Save a repo + source (and any extra init args) into the test config."""
    return main(["init", "-r", repo, "-s", str(source), *extra])


def fingerprint(root):
    """Everything a write would disturb, so 'not written' is checkable."""
    result = {}
    for path in sorted(Path(root).rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            result[rel] = ("symlink", os.readlink(path))
        elif path.is_dir():
            result[rel] = ("dir",)
        else:
            stat = path.stat()
            result[rel] = ("file", stat.st_size, stat.st_mtime_ns,
                           hashlib.sha256(path.read_bytes()).hexdigest())
    return result


# ── the happy path and each mismatch class ────────────────────────────────

def test_identical_tree_is_zero_diffs(fake_restic, tmp_base, config_path,
                                      source_tree, capsys):
    configure("/srv/backup", source_tree)
    capsys.readouterr()
    code = main(["run", "-r", "/srv/backup"])
    out = capsys.readouterr().out
    assert code == EXIT_PASS
    assert "0 diff(s)" in out
    assert "compare  : match vs" in out
    assert "\u2717" not in out


def test_single_byte_change_is_exit_2_and_names_the_file(
        fake_restic, tmp_base, config_path, source_tree, capsys):
    configure("/srv/backup", source_tree)
    (source_tree / "restored.txt").write_bytes(b"hellp")     # same size, 1 byte
    capsys.readouterr()
    code = main(["run", "-r", "/srv/backup"])
    out = capsys.readouterr().out
    assert code == EXIT_DIFF_MISMATCH
    assert "restored.txt" in out
    assert "sampled sha256 differs" in out


def test_size_change_is_exit_2(fake_restic, tmp_base, config_path, source_tree, capsys):
    configure("/srv/backup", source_tree)
    (source_tree / "sub" / "nested.bin").write_bytes(b"z" * 1400)
    capsys.readouterr()
    code = main(["run", "-r", "/srv/backup"])
    out = capsys.readouterr().out
    assert code == EXIT_DIFF_MISMATCH
    assert "sub/nested.bin" in out
    assert "1400 B" in out


def test_all_offenders_are_named(fake_restic, tmp_base, config_path, source_tree, capsys):
    """>2 differing files: every one of them is named, not just the first."""
    configure("/srv/backup", source_tree)
    for name in ("extra-a.txt", "extra-b.txt", "extra-c.txt"):
        (source_tree / name).write_text("x", encoding="utf-8")
    capsys.readouterr()
    code = main(["run", "-r", "/srv/backup"])
    out = capsys.readouterr().out
    assert code == EXIT_DIFF_MISMATCH
    for name in ("extra-a.txt", "extra-b.txt", "extra-c.txt"):
        assert name in out


def test_symlink_target_difference_is_exit_2(fake_restic, tmp_base, config_path,
                                             source_tree, capsys):
    configure("/srv/backup", source_tree)
    (source_tree / "link").unlink()
    (source_tree / "link").symlink_to("somewhere-else")
    capsys.readouterr()
    code = main(["run", "-r", "/srv/backup"])
    out = capsys.readouterr().out
    assert code == EXIT_DIFF_MISMATCH
    assert "link" in out and "symlink" in out.lower()


# ── excludes (R4) ─────────────────────────────────────────────────────────

def test_excluded_path_differing_is_still_zero(fake_restic, tmp_base, config_path,
                                               source_tree, capsys):
    configure("/srv/backup", source_tree, "-x", "*.log", "-x", "cache/")
    (source_tree / "noisy.log").write_text("leaked", encoding="utf-8")
    (source_tree / "cache").mkdir()
    (source_tree / "cache" / "c.bin").write_text("different", encoding="utf-8")
    capsys.readouterr()
    code = main(["run", "-r", "/srv/backup"])
    out = capsys.readouterr().out
    assert code == EXIT_PASS
    assert "0 diff(s)" in out


def test_excluded_paths_are_recorded_in_the_manifest(tmp_path):
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "a.log").write_text("x", encoding="utf-8")
    (root / "sub" / "b.log").write_text("y", encoding="utf-8")
    (root / "keep.txt").write_text("k", encoding="utf-8")
    man = manifestmod.build(root, exclude=excludesmod.compile_matcher(["*.log"]))
    assert sorted(man.excluded) == ["a.log", "sub/b.log"]
    assert [e.path for e in man.entries] == ["keep.txt"]


def test_excluded_directory_is_pruned_not_descended(tmp_path):
    root = tmp_path / "tree"
    (root / "cache" / "deep").mkdir(parents=True)
    (root / "cache" / "deep" / "x.bin").write_text("x", encoding="utf-8")
    (root / "cache" / "y.bin").write_text("y", encoding="utf-8")
    (root / "keep.bin").write_text("k", encoding="utf-8")
    man = manifestmod.build(root, exclude=excludesmod.compile_matcher(["cache/"]))
    assert [e.path for e in man.entries] == ["keep.bin"]
    assert man.excluded == ["cache"]


def test_excludes_are_passed_to_restic(fake_restic, tmp_base, config_path,
                                       source_tree, capsys):
    """R4 has two halves: the compare honours the list, and restic receives it."""
    configure("/srv/backup", source_tree, "-x", "*.log", "-x", "cache/")
    capsys.readouterr()
    assert main(["run", "-r", "/srv/backup"]) == EXIT_PASS
    restore = [c for c in fake_restic.calls() if c.startswith("restore")][0]
    assert "--exclude *.log" in restore
    assert "--exclude cache/" in restore


# ── skipped comparison (R13/R14) ──────────────────────────────────────────

def test_no_source_flag_skips_compare_and_returns_zero(
        fake_restic, tmp_base, config_path, source_tree, capsys):
    configure("/srv/backup", source_tree)
    (source_tree / "restored.txt").write_bytes(b"DIFFERENT")   # would fail if compared
    capsys.readouterr()
    code = main(["run", "-r", "/srv/backup", "--no-source"])
    out = capsys.readouterr().out
    assert code == EXIT_PASS
    assert "compare  : skipped (--no-source)" in out


def test_no_source_configured_skips_with_a_teaching_note(
        fake_restic, tmp_base, config_path, capsys):
    code = main(["run", "-r", "/srv/backup"])
    out = capsys.readouterr().out
    assert code == EXIT_PASS
    assert "compare  : skipped (no source saved)" in out
    assert "only proves the restore completed" in out


def test_skipped_comparison_is_zero_and_labelled():
    comparison = comparemod.skipped("--no-source")
    assert comparison.exit_code() == EXIT_PASS
    assert comparison.status == "skipped"
    assert comparison.to_json()["enabled"] is False


# ── a broken source teaches and exits 1 (R14, U4) ─────────────────────────

def test_missing_source_is_a_teaching_error(fake_restic, tmp_base, config_path, capsys):
    code = main(["run", "-r", "/srv/backup", "-s", "/definitely/not/here"])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "does not exist" in err
    assert "/definitely/not/here" in err
    assert "next:" in err and "--no-source" in err
    assert "Traceback" not in err


def test_source_that_is_a_file_teaches(fake_restic, tmp_base, config_path,
                                       tmp_path, capsys):
    a_file = tmp_path / "not-a-dir.txt"
    a_file.write_text("x", encoding="utf-8")
    code = main(["run", "-r", "/srv/backup", "-s", str(a_file)])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "is not a directory" in err
    assert "next:" in err
    assert "Traceback" not in err


# ── --strict (R26): warnings become failures ──────────────────────────────

def test_strict_promotes_an_empty_directory_warning_to_exit_2(
        fake_restic, tmp_base, config_path, source_tree, capsys):
    configure("/srv/backup", source_tree)
    (source_tree / "extra-empty").mkdir()
    capsys.readouterr()
    assert main(["run", "-r", "/srv/backup"]) == EXIT_PASS
    out = capsys.readouterr().out
    assert "empty directory present only in the source" in out
    assert "warning(s); strict off" in out

    assert main(["run", "-r", "/srv/backup", "--strict"]) == EXIT_DIFF_MISMATCH
    out = capsys.readouterr().out
    assert "promoted by --strict" in out
    assert "strict on" in out


# ── gate G2: all four codes in one cron-style run ─────────────────────────

def test_four_exit_codes_in_one_cron_style_run(fake_restic, tmp_base, config_path,
                                               source_tree, capsys):
    configure("/srv/backup", source_tree)
    capsys.readouterr()
    codes = [main(["run", "-r", "/srv/backup"])]                      # 0 verified
    (source_tree / "restored.txt").write_bytes(b"hellp")
    codes.append(main(["run", "-r", "/srv/backup"]))                  # 2 mismatch
    codes.append(main(["run", "-r", "/srv/backup", "-s", "/nope/x"])) # 1 no source
    with pytest.raises(SystemExit) as exc:
        main(["run", "--nope"])                                       # 64 usage
    codes.append(exc.value.code)
    assert codes == [EXIT_PASS, EXIT_DIFF_MISMATCH, EXIT_RESTORE_FAIL, EXIT_USAGE]
    assert codes == [0, 2, 1, 64]


# ── the source is evidence, never a write target (R26, G3) ────────────────

def test_source_is_never_written_to(fake_restic, tmp_base, config_path,
                                    source_tree, capsys):
    configure("/srv/backup", source_tree)
    before = fingerprint(source_tree)
    capsys.readouterr()
    assert main(["run", "-r", "/srv/backup"]) == EXIT_PASS
    assert fingerprint(source_tree) == before


# ── --json compare block + help (R12, U3) ─────────────────────────────────

def test_compare_json_reports_offenders_and_both_digests(
        fake_restic, tmp_base, config_path, source_tree, capsys):
    configure("/srv/backup", source_tree)
    (source_tree / "restored.txt").write_bytes(b"hellp")
    capsys.readouterr()
    code = main(["run", "-r", "/srv/backup", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_DIFF_MISMATCH
    assert payload["status"] == "diff_mismatch"
    assert payload["exit_code"] == EXIT_DIFF_MISMATCH
    block = payload["compare"]
    assert block["enabled"] is True
    assert block["status"] == "mismatch"
    assert [e["path"] for e in block["errors"]] == ["restored.txt"]
    assert block["errors"][0]["kind"] == "content"
    assert block["digests"]["restored"] != block["digests"]["source"]
    assert payload["schema"] == 1                # I3c: schema is on every payload
    assert "incomplete" not in payload           # the I2 placeholder is gone


def test_help_documents_compare_and_strict(capsys):
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    out = capsys.readouterr().out
    assert "source comparison" in out
    assert "exit 2" in out
    assert "--strict" in out
    assert "empty directory" in out
