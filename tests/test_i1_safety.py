"""Increment I1 security + scope attestation (gate G3, R23/R24/R25, N1/N2/N6).

These tests are the machine-checkable half of the honesty claim: they assert
what restverify *cannot* do, not just what it does.
"""
import re
from pathlib import Path

import pytest

from restverify import config as cfgmod
from restverify import restic as resticmod
from restverify.cli import main

SRC = Path(__file__).resolve().parents[1] / "src" / "restverify"
SHIPPED = sorted(SRC.rglob("*.py"))


def _all_source():
    return "\n".join(p.read_text(encoding="utf-8") for p in SHIPPED)


# ── N1/N2/N6: no mutating restic verbs, no borg/tar ────────────────────────

@pytest.mark.parametrize("verb", ["backup", "forget", "prune", "init", "unlock", "copy", "mount"])
def test_guard_refuses_mutating_verbs(verb):
    with pytest.raises(AssertionError):
        resticmod._assert_verb_allowed([verb])


def test_only_readonly_verbs_are_ever_invoked(fake_restic, tmp_base, config_path):
    main(["run", "-r", "/srv/backup"])
    assert set(fake_restic.verbs()) <= set(resticmod.READONLY_VERBS)


@pytest.mark.parametrize("verb", ["backup", "forget", "prune"])
def test_forbidden_verbs_appear_only_in_the_guard(verb):
    """A mutating verb may be *named* (to be refused) only in restic.py.

    Anywhere else it would mean an actual invocation site.
    """
    offenders = [p.name for p in SHIPPED
                 if f'"{verb}"' in p.read_text(encoding="utf-8") and p.name != "restic.py"]
    assert offenders == [], f"{verb!r} is invoked outside the guard: {offenders}"


def test_no_borg_or_tar_code_paths():
    """N6 is about code paths, not vocabulary: naming borg as out-of-scope is
    fine (the guard does), importing or calling it is not."""
    body = _all_source()
    for token in ("import tarfile", "tarfile.", "make_archive(", "unpack_archive(",
                  "import borg", "from borg"):
        assert token not in body, f"out-of-scope backend code path present: {token}"
    offenders = [p.name for p in SHIPPED
                 if "borg" in p.read_text(encoding="utf-8").lower() and p.name != "restic.py"]
    assert offenders == [], f"borg referenced outside the guard: {offenders}"


# ── R25/V2: no network in shipped paths ───────────────────────────────────

def test_no_network_imports_in_shipped_paths():
    body = _all_source()
    for token in ("import socket", "import requests", "urllib.request",
                  "urllib.error", "http.client", "ftplib", "smtplib", "telnetlib"):
        assert token not in body, f"network import found: {token}"


# ── R23/R26: never writes to the user's source ────────────────────────────

def test_source_path_is_never_handed_to_restic(fake_restic, tmp_base, config_path):
    """The source is a comparison target (I2), never a restic argument in I1."""
    main(["init", "-r", "/srv/backup", "-s", "/srv/data"])
    main(["run", "-r", "/srv/backup"])
    for call in fake_restic.calls():
        assert "/srv/data" not in call


def test_restore_target_is_never_the_source(fake_restic, tmp_base, config_path):
    main(["run", "-r", "/srv/backup"])
    restore = [c for c in fake_restic.calls() if c.startswith("restore")][0]
    target = restore.split("--target")[1].split()[0]
    assert "/srv/data" not in target


# ── R2/R24: passwords are never stored, never invented ────────────────────

def test_build_env_passes_the_command_and_never_a_password(monkeypatch):
    monkeypatch.delenv("RESTIC_PASSWORD", raising=False)
    env = resticmod.build_env("pass show restic/srv")
    assert env["RESTIC_PASSWORD_COMMAND"] == "pass show restic/srv"
    assert "RESTIC_PASSWORD" not in env


def test_written_config_contains_no_secret(config_path):
    cfg = cfgmod.Config(repos=[cfgmod.RepoEntry(
        name="srv", repo="/srv/backup", password_command="pass show restic/srv")])
    cfgmod.save_config(cfg, config_path)
    body = config_path.read_text(encoding="utf-8")
    assert "password_command" in body
    assert not re.search(r"(?m)^\s*(password|restic_password)\s*=", body)
    assert "RESTIC_PASSWORD=" not in body


def test_config_refuses_a_stored_password(config_path, capsys):
    config_path.write_text('[[repo]]\nrepo = "/srv/backup"\npassword = "hunter2"\n',
                           encoding="utf-8")
    code = main(["run", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == 64
    assert "never stores passwords" in err
    assert "password_command" in err
    assert "hunter2" not in err          # never echo the secret back
