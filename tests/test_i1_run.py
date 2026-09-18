"""Increment I1: config/contract + restore-to-temp + cleanup-on-exit.

Covers R1, R2, R3, R5, R7, R16, R17, R23, R24, R27 and the exit-code contract
(G2), with the adversarial inputs G1 requires: missing restic, empty repo,
unusable temp dir, and a killed process.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from restverify import EXIT_PASS, EXIT_RESTORE_FAIL, EXIT_USAGE
from restverify import config as cfgmod
from restverify import tempstore
from restverify.cli import main

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))


# ── happy path (R5, R7, R17, R23) ──────────────────────────────────────────

def test_verifies_newest_snapshot_and_cleans_up(fake_restic, tmp_base, config_path, capsys):
    code = main(["run", "-r", "/srv/backup"])
    out = capsys.readouterr().out
    assert code == EXIT_PASS
    assert "\u2713 restored snapshot 9f3a2c00" in out   # newest, not the older one
    assert "leftover" not in out                        # no stale sweep needed
    assert list(Path(tmp_base).iterdir()) == []         # R17: nothing left behind
    assert fake_restic.verbs() == ["snapshots", "restore"]  # R23: read-only verbs only


def test_restore_target_is_a_private_temp_dir(fake_restic, tmp_base, config_path, capsys):
    main(["run", "-r", "/srv/backup"])
    restore_call = [c for c in fake_restic.calls() if c.startswith("restore")][0]
    target = Path(restore_call.split("--target")[1].split()[0])
    assert target.name.startswith(tempstore.PREFIX)
    assert str(tmp_base) in str(target)


# ── dry run (U6, R13 early) ────────────────────────────────────────────────

def test_dry_run_restores_nothing_and_invokes_no_restic(fake_restic, tmp_base, config_path, capsys):
    code = main(["run", "-r", "/srv/backup", "--dry-run"])
    out = capsys.readouterr().out
    assert code == EXIT_PASS
    assert "dry run" in out
    assert "\u2713 PASS (dry run)" in out
    assert fake_restic.calls() == []
    assert list(Path(tmp_base).iterdir()) == []


# ── failure paths teach (U2) and still clean up (R17) ─────────────────────

def test_restore_failure_exits_1_and_teaches(fake_restic, tmp_base, config_path, capsys):
    fake_restic.set_mode("fail_restore")
    code = main(["run", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "restic restore failed" in err
    assert "next:" in err
    assert "restic said:" in err
    assert list(Path(tmp_base).iterdir()) == []   # cleanup ran despite the failure


def test_empty_repo_is_an_explicit_failure(fake_restic, tmp_base, config_path, capsys):
    fake_restic.set_mode("empty")
    code = main(["run", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "no snapshots found" in err
    assert "next:" in err and "take a backup first" in err


def test_missing_restic_teaches_installation(monkeypatch, tmp_base, config_path, capsys):
    monkeypatch.setenv("PATH", "/definitely/not/here")
    code = main(["run", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "restic was not found" in err
    assert "install restic" in err          # R27


def test_wrong_password_hint_names_password_command(fake_restic, tmp_base, config_path, capsys):
    fake_restic.set_mode("wrong_password")
    code = main(["run", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "password_command" in err          # U2b: likely cause named


def test_unsupported_restic_output_is_translated(fake_restic, tmp_base, config_path, capsys):
    fake_restic.set_mode("badjson")
    code = main(["run", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "could not read as JSON" in err


def test_unusable_temp_dir_is_a_teaching_error(fake_restic, tmp_path, monkeypatch, config_path, capsys):
    """Adversarial (G1). run as root ignores mode bits, so an absent base dir is
    used to reach the same OS-error path deterministically."""
    monkeypatch.setenv("RESTVERIFY_TMPDIR", str(tmp_path / "does-not-exist"))
    code = main(["run", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "could not create a restore directory" in err
    assert "RESTVERIFY_TMPDIR" in err          # the fix is named


def test_no_repo_configured_names_the_next_command(config_path, capsys):
    code = main(["run"])
    err = capsys.readouterr().err
    assert code == EXIT_USAGE
    assert "no repository given" in err
    assert "restverify run -r" in err


# ── kill -9 cleanup (R17, gate G3) ─────────────────────────────────────────

def test_sigkill_leftover_is_swept_by_the_next_run(fake_restic, tmp_base, config_path, capsys):
    prog = ("import sys; sys.path.insert(0, %r);"
            "from restverify import tempstore;"
            "print(tempstore.make_restore_dir('/srv/backup'));"
            "sys.stdout.flush(); import time; time.sleep(30)" % SRC)
    child = subprocess.Popen([sys.executable, "-c", prog], stdout=subprocess.PIPE, text=True)
    leftover = Path(child.stdout.readline().strip())
    assert leftover.is_dir()
    child.kill()                      # SIGKILL: no finally block, no atexit
    child.wait(timeout=10)
    assert leftover.is_dir(), "SIGKILL must leave the dir behind - that is the point"

    code = main(["run", "-r", "/srv/backup"])
    out = capsys.readouterr().out
    assert code == EXIT_PASS
    assert "cleaned up 1 leftover" in out
    assert not leftover.exists()


def test_sweep_leaves_live_and_foreign_dirs_alone(tmp_base):
    mine = tempstore.make_restore_dir("/srv/backup", base=tmp_base)   # live pid
    foreign = tmp_base / "someone-elses-dir"
    foreign.mkdir()
    (foreign / "precious.txt").write_text("keep me", encoding="utf-8")
    removed = tempstore.sweep_stale(base=tmp_base)
    assert removed == []
    assert mine.is_dir() and foreign.is_dir()
    assert (foreign / "precious.txt").exists()


def test_cleanup_refuses_dirs_without_our_marker(tmp_base):
    foreign = tmp_base / "not-ours"
    foreign.mkdir()
    assert tempstore.cleanup(foreign) is False
    assert foreign.is_dir()


# ── config contract (R1, R2, R3, R5, R16, R24) ─────────────────────────────

def test_config_round_trips_two_repos(config_path):
    cfg = cfgmod.Config(repos=[
        cfgmod.RepoEntry(name="srv", repo="/srv/backup", source="/srv/data",
                         excludes=["*.log", "cache/"], snapshot="latest",
                         password_command="pass show restic/srv"),
        cfgmod.RepoEntry(name="nas", repo="/mnt/nas/repo", no_source=True),
    ])
    cfgmod.save_config(cfg, config_path)
    loaded = cfgmod.load_config(config_path)
    assert [r.name for r in loaded.repos] == ["srv", "nas"]
    assert loaded.repos[0].excludes == ["*.log", "cache/"]
    assert loaded.repos[0].password_command == "pass show restic/srv"
    assert loaded.repos[1].no_source is True
    assert loaded.find("nas").source is None
    assert loaded.find("/srv/backup").name == "srv"


def test_missing_config_is_not_an_error(config_path):
    loaded = cfgmod.load_config(config_path)          # U1
    assert loaded.repos == []
    assert loaded.path == config_path


def test_broken_config_teaches_instead_of_raising(config_path, capsys):
    config_path.write_text("this is not = = toml\n", encoding="utf-8")
    code = main(["run", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == EXIT_USAGE
    assert "not valid TOML" in err
    assert "next:" in err


def test_default_config_path_is_under_the_user_config_dir(monkeypatch, tmp_path):
    monkeypatch.delenv(cfgmod.CONFIG_ENV, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cfgmod.default_config_path() == tmp_path / "restverify" / "config.toml"   # R16


# ── init (R1, R3) ──────────────────────────────────────────────────────────

def test_init_saves_repo_and_prints_the_next_command(config_path, capsys):
    code = main(["init", "-r", "/srv/backup", "-s", "/srv/data", "-x", "*.log"])
    out = capsys.readouterr().out
    assert code == EXIT_PASS
    assert "\u2713 added 'backup'" in out
    assert "next: restverify run -r backup" in out
    saved = cfgmod.load_config(config_path)
    assert saved.repos[0].source == "/srv/data"
    assert saved.repos[0].excludes == ["*.log"]


def test_init_updates_instead_of_duplicating(config_path, capsys):
    main(["init", "-r", "/srv/backup"])
    capsys.readouterr()
    code = main(["init", "-r", "/srv/backup", "-s", "/srv/data"])
    out = capsys.readouterr().out
    assert code == EXIT_PASS
    assert "\u2713 updated" in out
    assert len(cfgmod.load_config(config_path).repos) == 1


def test_init_without_a_repo_teaches_non_interactively(config_path, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", type("S", (), {"isatty": lambda self: False})())
    code = main(["init"])
    err = capsys.readouterr().err
    assert code == EXIT_USAGE
    assert "no repository given" in err
    assert "restverify init -r" in err
