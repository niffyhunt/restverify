"""Release 0.3.0 regression checks (I12/I13/I13a/I14 train).

The spec's release step requires one guarantee above all: a user who never
asks for the new features gets EXACTLY the old behavior. The sharpest form
of that: `run` with and without --json carries no webhook trace and the
envelope keys are unchanged when no webhook is configured. (Values like the
temp-dir name and elapsed seconds legitimately vary run to run — the
contract is about the webhook machinery not leaking into unused runs.)
"""
import json

from restverify import __version__
from restverify.cli import main


def test_run_human_output_free_of_webhook_noise(
        fake_restic, tmp_base, config_path, capsys):
    """Human `run` output: the webhook feature is present but unused — no
    trace of it may appear, and the run verdict line is unchanged."""
    main(["run", "-r", "/srv/backup"])
    out = capsys.readouterr().out
    assert "webhook" not in out.lower()
    assert "✓ restored snapshot 9f3a2c00" in out


def test_run_json_envelope_carries_no_webhook_trace_without_the_flag(
        fake_restic, tmp_base, config_path, capsys):
    """No --report-webhook flag, no report_webhook config key: the envelope
    must contain no webhook key or stderr delivery line at all."""
    main(["run", "-r", "/srv/backup", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "webhook" not in captured.err.lower()
    assert "webhook" not in json.dumps(payload).lower()
    assert payload["status"] == "pass" and payload["exit_code"] == 0


def test_run_json_envelope_keys_stable_across_the_version_bump(
        fake_restic, tmp_base, config_path, capsys):
    """The envelope shape is the public contract: the 0.3.0 train added keys
    to OTHER commands, but run's top-level keys are exactly the 0.2.1 set."""
    main(["run", "-r", "/srv/backup", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"tool", "schema", "version", "command", "status",
                            "exit_code", "history", "snapshot", "restore",
                            "manifest", "sample", "compare", "strict"}
    assert payload["version"] == __version__


def test_version_is_030():
    assert __version__ == "0.3.0"


def test_zero_runtime_dependencies_hold():
    """The supply-chain claim, re-checked at release time."""
    import tomllib
    from pathlib import Path
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["dependencies"] == []
