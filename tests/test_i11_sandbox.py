"""I11 — `run --sandbox` (R45): isolation, purge, and the standing contracts.

Why no real container runtime runs here: docker/podman in unit tests would be
slow and machine-dependent, and the gate's real proof (restore in a real
container, SIGKILL mid-restore, zero orphans) is an increment-gate drill run
against the real runtime on this box — pasted into the I11 briefing. What
these tests pin is everything else:

  * the Single Spawner rule as it extends to the new module: sandbox.py
    contains no spawn/listener call of its own — every process starts
    through restic.py's seam (the existing absence test keeps passing
    unmodified, and a source scan here makes the intent explicit);
  * the container lifecycle contract: create -> cp -> start -> exec ->
    rm --force, as the invoking uid, no network for local repos, mounts
    exactly as documented, and the purge fires even when the exec fails;
  * the kill -9 story: sweep_orphans removes ONLY restverify-sandbox-*
    containers whose owner pid is gone, and never touches live-owned or
    foreign containers;
  * the no-breakage contract: --sandbox mints no exit code (runtime failures
    surface as ResticFailed -> exit 1, kind "restic_failed"), the JSON
    envelope grows only an additive "sandbox" block, and the R22 demo line
    keeps its shape with the sandbox note on its own line;
  * the CLI surface: the flag exists on `run` only, and one offline
    integration test drives `run --sandbox` end to end through the suite's
    fake-restic harness plus the fake runtime below.

Test infrastructure (named per the PHASE2-PLAN rule): the `fake_runtime`
fixture — a bash stub standing in for docker that appends one line per call
(`verb arg arg ...`) to a log, implements the verbs the sandbox uses, and
"restores" by writing a canary into $FAKE_SANDBOX_TARGET (the stand-in for
the bind mount: the real runtime maps /restverify-restore to the host
tempstore dir; the fake maps it via the env var, so nothing is ever written
outside tmp).
"""
import json
import os
import sys
from pathlib import Path

import pytest

from restverify import EXIT_RESTORE_FAIL, errors
from restverify import restic as resticmod
from restverify import sandbox
from restverify.cli import build_parser, main

STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "{log}"
verb="$1"
case "$verb" in
  image) exit 0 ;;
  create) exit 0 ;;
  cp) exit 0 ;;
  start) exit 0 ;;
  rm) exit 0 ;;
  ps) printf '%b' "{ps_output}" ; exit 0 ;;
  exec)
    target="${{FAKE_SANDBOX_TARGET:-}}"
    [ -z "$target" ] && exit 0
    if [ "{fail_exec}" = "1" ]; then exit {exec_exit}; fi
    mkdir -p "$target"
    printf 'restored-in-container\n' > "$target/canary.txt"
    exit 0
    ;;
  *) exit 0 ;;
esac
"""


@pytest.fixture()
def fake_runtime(tmp_path, monkeypatch):
    """Install the docker stub on PATH; return the call log path."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)   # conftest's fake_restic shares tmp_path/bin
    log = tmp_path / "runtime-calls.log"
    monkeypatch.setenv("FAKE_SANDBOX_TARGET", str(tmp_path / "fake-restored"))

    def install(fail_exec: bool = False, exec_exit: int = 1, ps_output: str = ""):
        script = bindir / "docker"
        script.write_text(
            STUB.format(log=log, ps_output=ps_output,
                        fail_exec="1" if fail_exec else "0",
                        exec_exit=exec_exit),
            encoding="utf-8",
        )
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv(sandbox.RUNTIME_ENV, "docker")
        monkeypatch.delenv(sandbox.IMAGE_ENV, raising=False)
        monkeypatch.delenv(sandbox.PASSFILE_ENV, raising=False)
        return log

    return install


def _lines(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines()


def _verbs(log: Path) -> list[str]:
    return [line.split(" ", 1)[0] for line in _lines(log)]


def _dead_pid() -> int:
    """A pid that does not exist right now (signal-0 probe fails)."""
    for candidate in range(400000, 500000, 7):
        if not resticmod.pid_alive(candidate):
            return candidate
    raise AssertionError("no free pid found for the test")  # pragma: no cover


# ── the seam: sandbox.py spawns nothing itself ──────────────────────────────

def test_sandbox_module_contains_no_spawn_or_listener_calls():
    """The Single Spawner rule, extended: restic.py stays the only spawner."""
    import ast
    tree = ast.parse(
        (Path(__file__).resolve().parents[1] / "src" / "restverify" / "sandbox.py")
        .read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert "subprocess" not in ast.dump(node.func), \
                "sandbox.py must never call subprocess directly"
            if isinstance(node.func, ast.Name):
                assert node.func.id not in ("system", "popen", "execv",
                                            "execvp", "spawnv"), node.func.id


def test_seam_helpers_exist_and_translate_failures():
    assert resticmod.find_binary("definitely-not-a-binary-xyz") is None
    assert callable(resticmod.spawn_process) and callable(resticmod.pid_alive)
    with pytest.raises(errors.ResticFailed):
        resticmod.spawn_process(["definitely-not-a-binary-xyz"])


# ── the lifecycle contract, against a fake runtime ──────────────────────────

def test_lifecycle_order_and_mounts(tmp_path, fake_runtime):
    """create -> cp -> start -> exec -> rm --force, in that order, with the
    documented mounts and the ownership label; exec runs as the invoking uid
    with the password channel passed through (never a password)."""
    log = fake_runtime()
    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "target"

    run = sandbox.sandboxed_restore(str(repo), "abc12345", target,
                                    password_command="cat /tmp/pw")
    assert run.purged and run.runtime.endswith("/docker")

    assert _verbs(log) == ["ps", "image", "create", "cp", "start", "exec", "rm"], \
        _verbs(log)
    lines = _lines(log)
    create = next(line for line in lines if line.startswith("create "))
    assert "--network none" in create, "local repo: the container needs no network"
    assert f"--volume {repo.resolve()}:/restverify-repo:ro" in create
    assert f"--volume {target.resolve()}:/restverify-restore" in create, \
        "the tempstore dir is the read-write mount"
    assert f"--label restverify.owner={os.getpid()}" in create

    exec_line = next(line for line in lines if line.startswith("exec "))
    assert f"--user {os.getuid()}:{os.getgid()}" in exec_line
    assert "RESTIC_PASSWORD_COMMAND=cat /tmp/pw" in exec_line
    assert "/usr/local/bin/restic restore abc12345" in exec_line
    assert "--target /restverify-restore" in exec_line
    assert "--repo /restverify-repo" in exec_line

    assert lines[-1].startswith("rm --force --volumes restverify-sandbox-"), \
        "purge is rm --force --volumes, and it is the last call"


def test_exec_failure_still_purges_and_teaches(tmp_path, fake_runtime):
    log = fake_runtime(fail_exec=True, exec_exit=3)
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(errors.ResticFailed) as excinfo:
        sandbox.sandboxed_restore(str(repo), "abc", tmp_path / "t",
                                  password_command="cat /tmp/pw")
    assert "in-container exit 3" in excinfo.value.what
    assert "without --sandbox" in excinfo.value.hint
    assert _verbs(log).count("rm") == 1 and _verbs(log)[-1] == "rm", \
        "purged exactly once, on the failure path"


def test_no_new_exit_code_runtime_failure_is_exit_1(tmp_path, fake_runtime):
    """The container-runtime failure path reuses ResticFailed -> exit 1 with
    kind restic_failed. No new exit code, no new error kind (no-breakage)."""
    fake_runtime(fail_exec=True, exec_exit=2)
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(errors.ResticFailed) as excinfo:
        sandbox.sandboxed_restore(str(repo), "abc", tmp_path / "t")
    assert errors.exit_code_for(excinfo.value) == EXIT_RESTORE_FAIL
    assert errors.kind_of(excinfo.value) == "restic_failed"


# ── the kill -9 story: the orphan sweep ─────────────────────────────────────

def test_sweep_removes_only_dead_owner_containers(fake_runtime):
    """Only restverify-sandbox-* containers with a dead owner pid are removed;
    live-owner and foreign containers are never touched."""
    dead = _dead_pid()
    ps_output = (
        f"restverify-sandbox-999-{dead}\\trestverify.owner={dead}\\n"
        f"restverify-sandbox-998-{os.getpid()}\\trestverify.owner={os.getpid()}\\n"
        f"unrelated-container\\trestverify.owner={dead}\\n"
        f"restverify-sandbox-997-x\\t\\n"
    )
    log = fake_runtime(ps_output=ps_output)
    removed = sandbox.sweep_orphans()
    assert removed == [f"restverify-sandbox-999-{dead}"], removed
    joined = "\n".join(_lines(log))
    assert "unrelated-container" not in joined, "foreign containers are never touched"
    assert "restverify-sandbox-998" not in joined, "a live owner's container is kept"


# ── CLI wiring: the flag exists and the envelope grows additively ────────────

def test_sandbox_flag_is_declared_on_run_only():
    ns = build_parser().parse_args(["run", "-r", "x", "--sandbox"])
    assert ns.sandbox is True
    ns = build_parser().parse_args(["run", "-r", "x"])
    assert ns.sandbox is False          # default: native restore
    for argv in (["init", "-r", "x", "--sandbox"],
                 ["report", "--sandbox"], ["cron", "--sandbox"]):
        with pytest.raises(SystemExit):
            build_parser().parse_args(argv)


def test_run_payload_grows_only_an_additive_sandbox_block(tmp_path):
    """No-breakage at the payload level: no 'sandbox' key unless a sandboxed
    restore happened; when present, its shape is pinned."""
    from restverify import compare, config, manifest, sampling
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "canary.txt").write_text("hi\n", encoding="utf-8")
    man = manifest.build(tree, ignore_top_level=())
    sample = sampling.sample_tree(man, tree, [])
    comparison = compare.skipped("no source saved", source=None)
    entry = config.RepoEntry(name="r", repo="/tmp/r")
    snapshot = resticmod.Snapshot(id="a" * 64, short_id="abc12345",
                                  time="2026-09-29T00:00:00Z", paths=["/x"])
    native = main.__globals__["_run_payload"](
        entry, snapshot, man, sample, comparison,
        {"recorded": False}, tree, 1, True)
    assert "sandbox" not in native, "a native run must not grow a sandbox block"

    run = sandbox.SandboxRun(runtime="docker", image="ubuntu:24.04",
                             container="c", purged=True)
    sandboxed = main.__globals__["_run_payload"](
        entry, snapshot, man, sample, comparison,
        {"recorded": False}, tree, 1, True, sandbox_run=run)
    assert sandboxed["sandbox"] == {
        "runtime": "docker", "image": "ubuntu:24.04",
        "container": "c", "purged": True,
    }
    assert set(sandboxed) - set(native) == {"sandbox"}


# ── offline integration: --sandbox run over the fake-restic harness ──────────

def test_run_sandbox_end_to_end_offline(tmp_path, monkeypatch, fake_restic,
                                        fake_runtime, capsys):
    """The full `run --sandbox` pipeline with the suite's fake restic and the
    fake runtime: exit 0, the R22 line intact, the sandbox note printed, and
    the JSON envelope carrying the additive block with a recorded history row."""
    fake_runtime()               # install the stub BEFORE any runtime lookup
    repo = tmp_path / "repo"
    repo.mkdir()
    proc = main(["run", "-r", str(repo), "--sandbox", "--no-source"])
    assert proc == 0
    out = capsys.readouterr().out
    assert "✓ restored snapshot 9f3a2c00: 0 files / 0 B in" in out, out  # R22 shape
    # the sandbox line names the runtime binary as found on PATH (resolved)
    assert "/docker container restverify-sandbox-" in out, out
    assert "purged=yes" in out, out
    assert "compare  : skipped (--no-source)" in out, out

    proc = main(["run", "-r", str(repo), "--sandbox", "--no-source", "--json"])
    assert proc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == 1 and payload["command"] == "run"
    assert payload["sandbox"]["purged"] is True
    assert payload["history"]["recorded"] is True
