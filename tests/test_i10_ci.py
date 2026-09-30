"""I10 — the CI restore-drill surface, asserted without running GitHub (R44).

Why no workflow run happens here: the composite action's real gate is a
scheduled drill on a hosted runner (that leg is recorded as pending operator
hosting in docs/I10-CI.md — no public repository exists yet, and creating one
is the operator's call). A unit suite cannot press that button. What these
tests pin is everything a CI run depends on structurally:

  * the action exists, is a composite, and takes the documented inputs;
  * the install target is a pinned release version, never a moving branch;
  * drill.sh asserts the exit-code contract in BOTH directions (healthy
    fixture must exit 0, tampered fixture must exit 2) and fails a silent
    drill — proven here by executing the script against a fake `restverify`
    that obeys the contract, violates it, and goes silent;
  * the workflows point at the shipped action, keep the 10-minute bound and
    the R43 layout, and invent NO new CLI flag (composes existing verbs).

The tampered-fixture exit-2 half of the gate is additionally proven against
the real restic 0.16.4 on this box (transcript in docs/I10-CI.md), because a
contract asserter that never ran is prose, not a gate.

Test infrastructure (named per the PHASE2-PLAN rule): `fake_restverify` —
a bash stub on PATH that exits on demand and logs its argv, so drill.sh can
be exercised end to end without GitHub, restic, or a real repository.
"""
import os
import re
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github" / "actions" / "restore-drill" / "action.yml"
ACTION_KEY = ROOT / ".github" / "actions" / "restore-drill" / "release-key.asc"
DRILL = ROOT / "templates" / "ci-drill" / "scripts" / "drill.sh"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DRILL_TEMPLATE = ROOT / "templates" / "ci-drill" / "workflow.yml"

ALLOWED_FLAGS = {
    "--version",                  # action: install sanity check
    "-r", "--repo",               # run/init: repository selector
    "-s", "--source",             # init: comparison source
    "--password-command",         # init: the only credential-shaped input (R2)
    "--no-source",                # run: restore-only verification
    "--strict", "--dry-run", "--json", "--config",  # existing surface
}


def _action() -> str:
    return ACTION.read_text(encoding="utf-8")


def _ci() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8")


def _template() -> str:
    return DRILL_TEMPLATE.read_text(encoding="utf-8")


# ── the composite action ───────────────────────────────────────────────────

def test_action_is_a_composite_with_the_documented_inputs():
    text = _action()
    assert "using: 'composite'" in text, "R44 says reusable composite action"
    for input_name in ("version", "base_url", "release_key",
                       "restic_version", "drill_script"):
        assert re.search(rf"^  {input_name}:\s*$", text, re.MULTILINE), \
            f"input '{input_name}' is missing from the action"
    for step_name in ("Happy drill", "Tampered drill"):
        assert step_name in text, f"the '{step_name}' step is missing"
    assert "github.action_path" in text, \
        "the release key must ship inside the action (composite actions " \
        "cannot read the caller's repo)"
    assert "GOODSIG" in text, "the signature check must fail on a bad sig"


def test_action_installs_a_pinned_release_not_a_moving_target():
    """The drill exercises the release supply chain: the wheel comes from a
    literal version, never from a branch or 'latest' — installing a moving
    target would make the drill prove nothing (the I9 drill-method rule)."""
    text = _action()
    match = re.search(r"^  version:\n.*?default: '([^']+)'", text,
                      re.MULTILINE | re.DOTALL)
    assert match, "the version input must have a literal default"
    assert re.fullmatch(r"\d+\.\d+\.\d+", match.group(1)), \
        f"pinned version must be x.y.z, got {match.group(1)!r}"
    assert "ref: main" not in text and "@main" not in text


def test_action_uses_no_token_and_no_secret():
    """Scope discipline: no token beyond the automatic checkout token, no
    secrets surface at all (docs/V2-BUILD-PLAN.md §3, I10)."""
    assert "secrets." not in _action()
    assert "GITHUB_TOKEN" not in _action()


def test_release_key_ships_with_the_action():
    text = ACTION_KEY.read_text(encoding="utf-8")
    assert text.lstrip().startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")
    assert "-----END PGP PUBLIC KEY BLOCK-----" in text
    assert ACTION_KEY.read_bytes() == (ROOT / "docs" / "release-key.asc").read_bytes(), \
        "the action's key is a copy of docs/release-key.asc and must not drift"


# ── drill.sh: the exit-code contract, both directions, without GitHub ──────

def test_drill_script_is_executable_and_names_the_codes():
    mode = DRILL.stat().st_mode
    assert mode & stat.S_IXUSR, "drill.sh must be executable"
    text = DRILL.read_text(encoding="utf-8")
    assert "happy) EXPECTED=0" in text, "healthy fixture must expect exit 0"
    assert "tamper) EXPECTED=2" in text, "tampered fixture must expect exit 2"
    assert "exit 64" in text, "a bogus mode is usage error 64, not a drill code"
    assert "history.db" in text and "RESTVERIFY_HOME" in text, \
        "the R43 store-location proof must stay in the drill"


@pytest.fixture()
def fake_restverify(tmp_path, monkeypatch):
    """A `restverify` stub that logs argv and exits with $FAKE_EXIT."""
    calls_file = tmp_path / "calls.txt"
    bindir = tmp_path / "fake-bin"
    bindir.mkdir()
    stub = bindir / "restverify"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$CALLS_FILE\"\n"
        "exit \"$FAKE_EXIT\"\n"
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("CALLS_FILE", str(calls_file))
    return calls_file


def _drill(tmp_path, monkeypatch, mode: str, fake_exit: int) -> subprocess.CompletedProcess:
    monkeypatch.setenv("FAKE_EXIT", str(fake_exit))
    return subprocess.run(
        ["bash", str(DRILL), str(tmp_path / "repo"), str(tmp_path / "fixture"), mode],
        capture_output=True, text=True, timeout=30,
    )


def test_drill_happy_leg_passes_on_exit_0_and_calls_run_with_repo(
        tmp_path, monkeypatch, fake_restverify):
    proc = _drill(tmp_path, monkeypatch, "happy", 0)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "healthy fixture exited 0" in proc.stdout
    lines = fake_restverify.read_text(encoding="utf-8").splitlines()
    assert lines == [f"run -r {tmp_path / 'repo'}"], \
        "the drill composes the existing `run -r` verb only"


def test_drill_tamper_leg_passes_on_exit_2_and_flips_a_byte(
        tmp_path, monkeypatch, fake_restverify):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "canary.txt").write_text("original\n", encoding="utf-8")
    proc = _drill(tmp_path, monkeypatch, "tamper", 2)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "detects drift" in proc.stdout
    assert "tampered-by-drill" in (fixture / "canary.txt").read_text(encoding="utf-8"), \
        "the tamper leg must actually corrupt the fixture"


def test_drill_fails_a_violating_restverify(tmp_path, monkeypatch, fake_restverify):
    """A degraded restore that exits 1 must fail the drill with the real code
    in the message — the drill never rewrites exit codes (R11)."""
    proc = _drill(tmp_path, monkeypatch, "happy", 1)
    assert proc.returncode == 1
    assert "expected exit 0, got 1" in proc.stderr


def test_drill_fails_a_silent_tamper(tmp_path, monkeypatch, fake_restverify):
    """THE adversarial case: a tampered fixture that still exits 0 means the
    verification is broken, and the drill must scream, not pass."""
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "canary.txt").write_text("original\n", encoding="utf-8")
    proc = _drill(tmp_path, monkeypatch, "tamper", 0)
    assert proc.returncode == 1
    assert "expected exit 2, got 0" in proc.stderr


def test_drill_rejects_a_bogus_mode_as_usage_error(
        tmp_path, monkeypatch, fake_restverify):
    proc = _drill(tmp_path, monkeypatch, "bogus", 0)
    assert proc.returncode == 64


def test_drill_r43_proof_checks_the_store_inside_the_artificial_home(
        tmp_path, monkeypatch, fake_restverify):
    """With RESTVERIFY_HOME set, the happy leg asserts the history store
    landed inside the artificial home — and fails the drill if it did not."""
    home = tmp_path / "rv-home"
    store = home / ".local" / "state" / "restverify"
    monkeypatch.setenv("RESTVERIFY_HOME", str(home))

    # store missing -> the drill must fail the R43 proof
    proc = _drill(tmp_path, monkeypatch, "happy", 0)
    assert proc.returncode == 1
    assert "R43 layout broken" in proc.stderr

    # store present -> the proof passes and names the location
    store.mkdir(parents=True)
    (store / "history.db").write_bytes(b"")
    proc = _drill(tmp_path, monkeypatch, "happy", 0)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"R43 proof — history store at {store / 'history.db'}" in proc.stdout


# ── the workflows ──────────────────────────────────────────────────────────

CMD_POSITION_RE = re.compile(r"(?:^|[;&|]|\$\(|\bsudo\s+|\bbash\s+-c\s+)restverify(?=\s)")


def _flags_used(text: str) -> set[str]:
    """Flags on lines that actually *invoke* restverify (plus one continuation
    line, since the action wraps long commands). A command-position match is
    required so a wheel filename (`restverify-0.1.0-...whl`) or a gpg user-id
    string (`"restverify self-test"`) is not mistaken for a CLI call — the
    scope claim is about the restverify CLI, nothing else."""
    flags: set[str] = set()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#") or not CMD_POSITION_RE.search(line):
            continue
        joined = line
        if line.rstrip().endswith("\\") and i + 1 < len(lines):
            joined += " " + lines[i + 1]
        flags.update(re.findall(r"(?<![\w-])(--?[\w][\w-]*)", joined))
    return flags


def test_ci_files_invent_no_new_cli_flag():
    """Scope discipline (docs/V2-BUILD-PLAN.md §3, I10): the action composes
    existing verbs only. A flag here that the CLI does not ship fails this
    test before it can fail a runner."""
    for label, text in (("action", _action()), ("ci workflow", _ci()),
                        ("template workflow", _template()),
                        ("drill.sh", DRILL.read_text(encoding="utf-8"))):
        unknown = _flags_used(text) - ALLOWED_FLAGS
        assert unknown == set(), f"{label} uses unknown flags: {sorted(unknown)}"


def test_selftest_workflow_runs_the_shipped_action_under_the_gate_bounds():
    text = _ci()
    assert "uses: ./.github/actions/restore-drill" in text, \
        "CI must exercise the action as shipped, not a copy"
    assert "timeout-minutes: 10" in text, "the plan's <10 min bound"
    assert "RESTVERIFY_HOME" in text, "the R43 layout is exercised in CI"
    assert "python -m build --wheel" in text, "the wheel is built from the commit"
    assert "--detach-sign" in text and "file://" in text, \
        "the self-test signs a throwaway key and serves the artefact locally"
    assert "contents: read" in text, "no write scopes for CI"


def test_template_workflow_pins_a_tag_and_schedules_the_drill():
    text = _template()
    match = re.search(r"uses: niffyhunt/restverify/\.github/actions/restore-drill@(\S+)",
                      text)
    assert match, "the template must pin the shipped action"
    assert re.fullmatch(r"v\d+\.\d+\.\d+", match.group(1)), \
        f"pin must be a release tag, got {match.group(1)!r}"
    assert "cron:" in text and "workflow_dispatch" in text
    assert "timeout-minutes: 10" in text
    assert "RESTVERIFY_HOME" in text
    assert "secrets.RESTVERIFY_REPO" in text, \
        "the user's repository location stays a secret"
    assert "--no-source" in text, "the restore-only placeholder stays as documented"


# ── shipping surface ───────────────────────────────────────────────────────

def test_sdist_ships_the_ci_surface():
    """The action and templates are part of the R44 deliverable; a source
    distribution must carry them (docs/RELEASE.md allowlist, I8)."""
    with (ROOT / "pyproject.toml").open("rb") as fh:
        include = tomllib.load(fh)["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    for expected in (".github/", "templates/"):
        assert expected in include, f"sdist must ship {expected}"
