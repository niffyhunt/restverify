"""I7b: fake-vs-real reconciliation, pinned by regression tests.

Every restic string and exit code below was copied verbatim from a real
restic 0.16.4 run (restic 0.16.4 compiled with go1.22.2 on linux/amd64,
Ubuntu package 0.16.4-2ubuntu0.24.04.3) on 2026-09-24, on the host the operator
ruled for I7, after the I7a smoke test. The corrections these tests pin are
listed item by item in section 5 of the I7 briefing.

No test here needs a real restic binary: the fake in conftest.py speaks restic's
own words and exit codes, so the suite stays offline and deterministic.
"""
import subprocess

import pytest

from restverify import (EXIT_RESTORE_FAIL, excludes as excludesmod,
                        manifest as manifestmod)
from restverify.cli import main
from restverify.restic import _hint_for


# ── item 3: the stderr strings restic 0.16.4 really emits ────────────────────

REAL_WRONG_PASSWORD = "Fatal: wrong password or no key found"
REAL_MISSING_REPO = (
    "Fatal: unable to open config file: stat /root/i7-scratch/no-such-repo/config: "
    "no such file or directory\n"
    "Is there a repository at the following location?\n"
    "/root/i7-scratch/no-such-repo"
)
REAL_MISSING_SNAPSHOT = (
    'Fatal: failed to find snapshot: no matching ID found for prefix "deadbeefdeadbeef"'
)
# Older restic worded the password failure as a config-file complaint, which is
# why "unable to open config" has to stay in the password branch: the two
# wordings differ only in what follows the colon.
REAL_OLD_WRONG_PASSWORD = (
    "Fatal: unable to open config file: /srv/backup/config: wrong password or no key found"
)


def test_missing_repo_stderr_names_the_path_not_the_password():
    """D-I7-1: this exact string used to select the password hint."""
    hint = _hint_for(REAL_MISSING_REPO)
    assert hint.startswith("check the repository path")
    assert "password" not in hint


def test_wrong_password_stderr_still_names_the_password():
    hint = _hint_for(REAL_WRONG_PASSWORD)
    assert "password_command" in hint
    assert "repository path" not in hint


def test_older_password_wording_still_names_the_password():
    assert "password_command" in _hint_for(REAL_OLD_WRONG_PASSWORD)


def test_missing_snapshot_stderr_falls_through_to_the_generic_hint():
    """restic's wording matches no specific branch; the generic fallback is the contract."""
    hint = _hint_for(REAL_MISSING_SNAPSHOT)
    assert "restic -r <repo> snapshots" in hint
    assert "password" not in hint


def test_missing_repo_run_teaches_the_path_end_to_end(fake_restic, tmp_base,
                                                      config_path, capsys):
    """The fake's no_such_repo mode had no test at all before I7b - that is how
    the wrong hint survived six increments and only a real run exposed it."""
    fake_restic.set_mode("no_such_repo")
    code = main(["run", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "check the repository path" in err
    assert "password looks wrong" not in err
    assert "restic said:" in err


# ── item 2: restic's real exit codes (the fake used 3 / 10 / 12) ─────────────

# verb + arguments that trigger each fake failure mode, and the exact stderr the
# real binary printed for the same failure on 2026-09-24. Target paths are
# formatted per test so the fake writes inside tmp_path.
REAL_FAILURES = {
    "wrong_password": (
        ["snapshots", "--json", "--repo", "/srv/backup"],
        "Fatal: wrong password or no key found",
    ),
    "no_such_repo": (
        ["snapshots", "--json", "--repo", "/srv/backup"],
        "Fatal: unable to open config file: stat /nope: no such file or directory\n"
        "Is there a repository at the following location?\n"
        "/nope",
    ),
    "fail_restore": (
        ["restore", "9f3a2c00", "--target", "{target}", "--repo", "/srv/backup"],
        'Fatal: failed to find snapshot: no matching ID found for prefix "9f3a2c00"',
    ),
}


def _run_fake(fake_restic, mode, *args):
    fake_restic.set_mode(mode)
    return subprocess.run([str(fake_restic.path), *args], capture_output=True, text=True)


@pytest.mark.parametrize("mode", sorted(REAL_FAILURES))
def test_fake_restic_failures_exit_1_and_speak_restic_words(fake_restic, mode, tmp_path):
    """The fake must not invent exit codes: restic 0.16.4 returns 1 for all three."""
    args, expected_stderr = REAL_FAILURES[mode]
    args = [a.format(target=str(tmp_path / "restore")) for a in args]
    proc = _run_fake(fake_restic, mode, *args)
    assert proc.returncode == 1, f"real restic exits 1 for {mode}"
    assert proc.stderr.strip() == expected_stderr


def test_restore_failure_teaches_with_restic_own_sentence(fake_restic, tmp_base,
                                                          config_path, capsys):
    """End to end: the tool quotes restic's real words, not the old fiction."""
    fake_restic.set_mode("fail_restore")
    code = main(["run", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "restic restore failed (exit 1)" in err
    assert "failed to find snapshot" in err
    assert "unable to load snapshot" not in err


# ── item 5: excludes.py must agree with restic's own matcher ────────────────

# The real probe. Source root /root/i7-scratch/srcB held app.log,
# cache/blob.bin, data/keep.txt and nested/trace.log; each pattern was passed to
# `restic restore <id> --exclude <pattern>` and the right column is what restic
# really left out of the restore (I7 briefing, section 5).
SOURCE_ROOT = "/root/i7-scratch/srcB"
SOURCE_FILES = ["app.log", "cache/blob.bin", "data/keep.txt", "nested/trace.log"]

REAL_EXCLUDE_MATRIX = [
    ("*.log", {"app.log", "nested/trace.log"}),
    ("data/*.txt", {"data/keep.txt"}),
    ("srcB/data/*.txt", {"data/keep.txt"}),                      # root-prefixed
    ("/root/i7-scratch/srcB/data/*.txt", {"data/keep.txt"}),      # absolute
    ("i7-scratch/srcB/data/*.txt", {"data/keep.txt"}),            # ancestor-prefixed
    ("keep.txt", {"data/keep.txt"}),
    ("cache", {"cache/blob.bin"}),
    ("cache/", {"cache/blob.bin"}),
    ("*cache*", {"cache/blob.bin"}),
    ("nested", {"nested/trace.log"}),
    ("nested/", {"nested/trace.log"}),
    ("srcB/nested/trace.log", {"nested/trace.log"}),
    ("*/trace.log", {"nested/trace.log"}),
    ("srcB/*/trace.log", {"nested/trace.log"}),
    ("**/trace.log", {"nested/trace.log"}),
    ("**trace.log", {"nested/trace.log"}),
    ("srcB/**/*.log", {"app.log", "nested/trace.log"}),           # '**/' spans zero too
    ("srcB/da*a/keep.txt", {"data/keep.txt"}),
    ("srcB/data*keep.txt", set()),          # restic: '*' does not cross '/'
    ("srcB/data?keep.txt", set()),          # restic: '?' does not cross '/'
]


@pytest.mark.parametrize("pattern,excluded", REAL_EXCLUDE_MATRIX,
                         ids=[p for p, _ in REAL_EXCLUDE_MATRIX])
def test_matcher_agrees_with_real_restic(pattern, excluded):
    matcher = excludesmod.compile_matcher([pattern], root=SOURCE_ROOT)
    assert {rel for rel in SOURCE_FILES if matcher.matches(rel)} == excluded


def test_excluded_directory_prunes_its_subtree():
    matcher = excludesmod.compile_matcher(["cache/"], root=SOURCE_ROOT)
    assert matcher.matches("cache")              # the directory itself: pruned
    assert matcher.matches("cache/blob.bin")     # so the walk never descends


def test_rootless_matcher_cannot_see_the_absolute_prefix():
    """The seam is explicit: with no root there is nothing for such a pattern to match."""
    assert not excludesmod.compile_matcher(["srcB/data/*.txt"]).matches("data/keep.txt")
    assert excludesmod.compile_matcher(
        ["srcB/data/*.txt"], root=SOURCE_ROOT).matches("data/keep.txt")


def test_source_walk_prunes_exactly_what_restic_pruned(tmp_path):
    """D-I7-2: a root-prefixed pattern used to leave the pruned file in the
    source manifest, so a healthy restore was reported as a mismatch (exit 2)."""
    source = tmp_path / "srcB"
    (source / "data").mkdir(parents=True)
    (source / "app.log").write_text("log\n", encoding="utf-8")
    (source / "data" / "keep.txt").write_text("keep\n", encoding="utf-8")
    man = manifestmod.build(
        source, exclude=excludesmod.compile_matcher(["srcB/data/*.txt"], root=source))
    assert [e.path for e in man.entries] == ["app.log"]
    assert man.excluded == ["data/keep.txt"]


def test_malformed_pattern_is_a_literal_not_a_crash():
    """A hostile or half-written glob must not raise out of the matcher."""
    matcher = excludesmod.compile_matcher(["[unterminated", "keep[", "*.txt"],
                                          root=SOURCE_ROOT)
    assert not matcher.matches("app.log")
    assert matcher.matches("data/keep.txt")     # only the real pattern bites
