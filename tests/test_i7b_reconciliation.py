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

from restverify import EXIT_RESTORE_FAIL
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
