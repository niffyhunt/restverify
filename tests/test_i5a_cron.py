"""I5a — the cron printer (R15).

Doctrine under test: `cron` PRINTS a schedule line and installs nothing. The
canonical adversarial case is therefore "nothing changed" — no crontab mutation,
no systemctl call, no unit file written — and the two tests that prove it skip
with a named reason when the host cannot support the proof (no `crontab` binary),
rather than passing vacuously.
"""
import configparser
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from restverify import EXIT_PASS, SCHEMA_VERSION
from restverify.cli import (EXAMPLE_SCHEDULE, SYSTEMD_SERVICE_HEADER,
                            SYSTEMD_TIMER_HEADER, main)


# ── the crontab line ────────────────────────────────────────────────────────

def test_cron_prints_exactly_one_crontab_line(capsys):
    """R15: one line, five time fields, then the command."""
    code = main(["cron", "-r", "/srv/backup"])
    captured = capsys.readouterr()
    assert code == EXIT_PASS
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1, "cron must print exactly one pasteable line"
    fields = lines[0].split()
    assert fields[:5] == EXAMPLE_SCHEDULE.split()
    assert "run" in fields
    assert "/srv/backup" in lines[0]
    assert "restverify" in lines[0] or "-m" in lines[0]


def test_cron_line_uses_an_absolute_binary(capsys):
    """A bare name is the classic cron failure: the scheduler's PATH is minimal."""
    main(["cron", "-r", "/srv/backup"])
    line = capsys.readouterr().out.strip()
    command = line.split(None, 5)[5]
    # Absolute on either platform: "/..." on POSIX, "C:\..." on Windows.
    assert Path(command.split()[0]).is_absolute(), command


def test_python_dash_m_fallback_actually_runs():
    """_cron_binary falls back to `python -m restverify` when running from a
    checkout, so that invocation has to work: it used to die with
    "No module named restverify.__main__" on every platform. The forced
    cp1252 stream also pins the legacy-codepage fix — the help text carries
    a check-mark glyph a charmap console cannot encode."""
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run([sys.executable, "-m", "restverify", "--help"],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert "usage" in proc.stdout.lower()


def test_cron_teaching_goes_to_stderr_not_stdout(capsys):
    """Stdout stays pasteable; the notes never pollute it."""
    main(["cron", "-r", "/srv/backup"])
    captured = capsys.readouterr()
    assert "note:" not in captured.out
    assert "note:" in captured.err
    assert "install" in captured.err.lower()


def test_cron_without_repo_names_the_placeholder(capsys):
    """No -r: a placeholder plus a note, never a guessed repository."""
    code = main(["cron"])
    captured = capsys.readouterr()
    assert code == EXIT_PASS
    assert "<repo>" in captured.out
    assert "placeholder" in captured.err


def test_cron_resolves_a_saved_name_from_config(config_path, capsys):
    """`-r <name>` resolves through the config, same as run/report."""
    assert main(["init", "-r", "/srv/backup", "--name", "backup"]) == EXIT_PASS
    capsys.readouterr()
    main(["cron", "-r", "backup"])
    line = capsys.readouterr().out.strip()
    assert "/srv/backup" in line


# ── the JSON envelope (I3 stdout-purity contract) ────────────────────────────

def test_cron_json_is_one_pure_object(capsys):
    code = main(["cron", "-r", "/srv/backup", "--json"])
    captured = capsys.readouterr()
    assert code == EXIT_PASS
    assert captured.err == "", "successful --json must leave stderr empty"
    payload = json.loads(captured.out)          # no leading/trailing text
    assert set(payload) == {"tool", "schema", "version", "command", "status",
                            "exit_code", "cron"}
    assert payload["command"] == "cron"
    assert payload["status"] == "cron"
    assert payload["schema"] == SCHEMA_VERSION
    assert payload["exit_code"] == EXIT_PASS
    assert payload["cron"]["installs"] is False
    assert payload["cron"]["schedule"] == EXAMPLE_SCHEDULE
    assert payload["cron"]["notes"], "the envelope carries the teaching text"


def test_cron_json_line_equals_the_human_line(capsys):
    """The envelope reports the real line, not a parallel rendering of it."""
    main(["cron", "-r", "/srv/backup", "--json"])
    envelope_line = json.loads(capsys.readouterr().out)["cron"]["line"]
    main(["cron", "-r", "/srv/backup"])
    human_line = capsys.readouterr().out.strip()
    assert envelope_line == human_line


# ── --systemd ───────────────────────────────────────────────────────────────

def test_systemd_units_parse_as_ini(capsys):
    """R2: a .service + .timer pair, both INI."""
    code = main(["cron", "-r", "/srv/backup", "--systemd"])
    captured = capsys.readouterr()
    assert code == EXIT_PASS
    assert captured.out.count(SYSTEMD_SERVICE_HEADER) == 1
    assert captured.out.count(SYSTEMD_TIMER_HEADER) == 1
    service_text = captured.out.split(SYSTEMD_SERVICE_HEADER)[1].split(
        SYSTEMD_TIMER_HEADER)[0]
    timer_text = captured.out.split(SYSTEMD_TIMER_HEADER)[1]

    service = configparser.ConfigParser()
    service.read_string(service_text)
    assert service.has_section("Unit")
    assert service.has_section("Service")
    assert "run -r /srv/backup" in service.get("Service", "ExecStart")

    timer = configparser.ConfigParser()
    timer.read_string(timer_text)
    assert timer.has_section("Timer")
    assert timer.get("Timer", "OnCalendar")
    assert timer.get("Install", "WantedBy") == "timers.target"


def test_systemd_units_also_ride_the_envelope(capsys):
    code = main(["cron", "-r", "/srv/backup", "--systemd", "--json"])
    captured = capsys.readouterr()
    assert code == EXIT_PASS
    assert captured.err == ""
    payload = json.loads(captured.out)
    systemd = payload["cron"]["systemd"]
    assert "[Service]" in systemd["service"] and "[Timer]" in systemd["timer"]
    assert payload["cron"]["installs"] is False


# ── the adversarial case: it installs nothing ───────────────────────────

def _dir_state(path: Path):
    """Every path under ``path`` with its mtime, or None when it is absent."""
    if not path.exists():
        return None
    return sorted((str(item), item.stat().st_mtime_ns) for item in path.rglob("*"))


def test_cron_never_touches_a_crontab(capsys):
    """Adversarial (G1/G3): the crontab is byte-identical afterwards.

    Skipped, with the reason, when this host has no `crontab` binary — an absent
    binary would make the comparison vacuous instead of proving anything.
    """
    crontab = shutil.which("crontab")
    if not crontab:
        pytest.skip("no `crontab` binary on this host: the comparison would be vacuous")
    before = subprocess.run([crontab, "-l"], capture_output=True, text=True)
    for argv in (["cron", "-r", "/srv/backup"],
                 ["cron", "-r", "/srv/backup", "--json"],
                 ["cron", "-r", "/srv/backup", "--systemd"]):
        main(argv)
    after = subprocess.run([crontab, "-l"], capture_output=True, text=True)
    assert (before.returncode, before.stdout) == (after.returncode, after.stdout)
    capsys.readouterr()


def test_cron_writes_no_systemd_unit(capsys):
    """Adversarial (G1/G3): no unit file appears, user or system scope."""
    user_units = Path.home() / ".config" / "systemd"
    system_units = Path("/etc/systemd")
    before_user, before_system = _dir_state(user_units), _dir_state(system_units)

    main(["cron", "-r", "/srv/backup", "--systemd"])
    main(["cron", "-r", "/srv/backup", "--systemd", "--json"])

    assert _dir_state(user_units) == before_user
    assert _dir_state(system_units) == before_system
    capsys.readouterr()


def test_cron_help_documents_schedule_json_and_systemd(capsys):
    """G4/U3: the help is the documentation for this surface."""
    with pytest.raises(SystemExit) as excinfo:
        main(["cron", "--help"])
    out = capsys.readouterr().out
    assert excinfo.value.code == 0
    assert "examples:" in out.lower()
    assert "schedule" in out.lower()
    assert "--json" in out
    assert "--systemd" in out
    assert "never installs" in out.lower()
