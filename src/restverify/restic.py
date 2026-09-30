"""The only module allowed to invoke the restic binary (R6, R23, R27).

Exclusions are enforced in code, not just by convention (N1, N2, N6):
`_assert_verb_allowed` refuses any verb that would change a repository. The
absence tests in the suite assert those verbs appear nowhere in shipped code.

Secret handling (R2, R24): we only ever *pass through* the user's own
`password_command` as RESTIC_PASSWORD_COMMAND. We never accept, read, echo,
log or persist a password, and we never set RESTIC_PASSWORD ourselves.
"""
from __future__ import annotations

import errno
import glob
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePath

from .errors import NoSnapshots, ResticFailed, ResticMissing

# Verbs that only read. Everything restverify ships must be one of these.
READONLY_VERBS = frozenset({"snapshots", "restore", "ls", "cat", "find",
                            "stats", "diff", "dump", "version"})
# Verbs that mutate a repository or belong to other tools (N1, N2, N6).
FORBIDDEN_VERBS = frozenset({"backup", "forget", "prune", "init", "unlock",
                             "key", "migrate", "repair", "rewrite", "mount",
                             "serve", "copy"})

INSTALL_HINT = (
    "install restic first: `apt install restic`, `brew install restic`, "
    "or see https://restic.net"
)


@dataclass
class Snapshot:
    id: str
    short_id: str
    time: str
    paths: list[str]


def find_restic() -> str:
    """R27: missing restic must teach, never traceback."""
    found = shutil.which("restic")
    if not found:
        raise ResticMissing(
            "restic was not found on your PATH, and restverify shells out to it.",
            hint=INSTALL_HINT + "; then re-run `restverify run -r <repo>`",
        )
    return found


def find_binary(name: str) -> str | None:
    """Locate an external binary on PATH (None if absent — caller teaches).

    I11 seam: the sandbox needs the container-runtime CLI. Like find_restic,
    it only LOOKS; every spawn stays in this module.
    """
    return shutil.which(name)


def pid_alive(pid: int) -> bool:
    """True when a process exists (signal 0 probe); EPERM means 'exists, owned
    by someone else'. The sandbox orphan sweep uses this to decide whether a
    leftover container's owner is gone — the tempstore sweep's same logic."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def spawn_process(args: list[str], timeout: int | None = None,
                  check: bool = True, extra_env: dict | None = None
                  ) -> subprocess.CompletedProcess:
    """The generic spawn seam (I11): run ONE external command, captured.

    The Single Spawner rule says restic.py is the only module that spawns
    processes; the sandbox's container-runtime calls route through here so
    that rule keeps holding (`test_n5_only_restic_py_spawns_processes` reads
    the source tree — sandbox.py itself contains no spawn call). This is
    deliberately lower-level than run(): no verb assertion (a container CLI
    is not restic), but the SAME failure translation — a non-zero exit
    becomes a teaching ResticFailed, never a traceback.
    """
    env = build_env()
    if extra_env:
        env.update(extra_env)
    limit = timeout or int(os.environ.get("RESTVERIFY_TIMEOUT", "1800"))
    try:
        proc = subprocess.run(args, env=env, capture_output=True, text=True,
                              timeout=limit)
    except subprocess.TimeoutExpired as exc:
        raise ResticFailed(
            f"{args[0]} did not finish within {limit}s and was stopped.",
            hint="raise RESTVERIFY_TIMEOUT for slow operations, or retry without --sandbox",
        ) from exc
    except FileNotFoundError as exc:
        raise ResticFailed(
            f"{args[0]} disappeared from PATH mid-run.",
            hint="check that the tool is installed and on PATH",
        ) from exc
    if check and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-4:]
        raise ResticFailed(
            f"{args[0]} failed (exit {proc.returncode}).",
            stderr_tail=" | ".join(tail),
            hint=_hint_for(proc.stderr or ""),
        )
    return proc


def _assert_verb_allowed(args: list[str]) -> None:
    verb = args[0] if args else ""
    if verb in FORBIDDEN_VERBS:
        raise AssertionError(
            f"restverify must never call `restic {verb}` (out of scope: "
            "backup/prune/forget/borg/tar)"
        )
    if verb not in READONLY_VERBS:
        raise AssertionError(f"unexpected restic verb: {verb!r}")


def build_env(password_command: str | None = None) -> dict:
    """R2/R24: pass the user's own command through; never invent a password."""
    env = dict(os.environ)
    if password_command:
        env["RESTIC_PASSWORD_COMMAND"] = password_command
    return env


def run(args: list[str], password_command: str | None = None, timeout: int | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    """Invoke restic read-only. Translates failures into teaching errors."""
    _assert_verb_allowed(args)
    binary = find_restic()
    limit = timeout or int(os.environ.get("RESTVERIFY_TIMEOUT", "1800"))
    try:
        proc = subprocess.run([binary, *args], env=build_env(password_command),
                              capture_output=True, text=True, timeout=limit)
    except subprocess.TimeoutExpired as exc:
        raise ResticFailed(
            f"restic {args[0]} did not finish within {limit}s and was stopped.",
            hint="raise RESTVERIFY_TIMEOUT for slow remotes, or verify a smaller snapshot",
        ) from exc
    except FileNotFoundError as exc:  # pragma: no cover - find_restic already guards
        raise ResticMissing("restic disappeared from PATH mid-run.", hint=INSTALL_HINT) from exc
    if check and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-4:]
        raise ResticFailed(
            f"restic {args[0]} failed (exit {proc.returncode}).",
            stderr_tail=" | ".join(tail),
            hint=_hint_for(proc.stderr or ""),
        )
    return proc


def _hint_for(stderr: str) -> str:
    """U2b: name the most likely cause, from restic's own words.

    The order of these tests is load-bearing and was corrected in I7b against
    a real restic 0.16.4. A missing repository is reported as

        Fatal: unable to open config file: stat <repo>/config: no such file or
        directory
        Is there a repository at the following location?
        <repo>

    which contains "unable to open config" - the substring the password branch
    used to claim first, so a typo'd repo path was taught as a wrong password.
    Path evidence is therefore checked before password evidence. The password
    branch keeps "unable to open config" for older builds that append "wrong
    password or no key found" to that same line, and restic 0.16.4's own
    password failure ("Fatal: wrong password or no key found") contains no path
    wording, so the two can no longer be confused.
    """
    low = stderr.lower()
    if ("is there a repository at" in low or "does not exist" in low
            or "no such file" in low):
        return "check the repository path: `restic -r <repo> snapshots` by hand"
    if "wrong password" in low or "invalid password" in low or "unable to open config" in low:
        return ("the repository password looks wrong — set password_command in the "
                "config (never the password itself), or export RESTIC_PASSWORD_COMMAND")
    if "permission denied" in low:
        return "check read permissions on the repository for this user"
    if "no such host" in low or "connection refused" in low or "timeout" in low:
        return "check network access to the repository, or retry with RESTVERIFY_TIMEOUT set"
    return "run `restic -r <repo> snapshots` by hand to see the underlying error"


def list_snapshots(repo: str, password_command: str | None = None) -> list[Snapshot]:
    """R6: one call, parsed. Empty repository is a normal (reportable) state."""
    proc = run(["snapshots", "--json", "--repo", repo], password_command)
    try:
        raw = json.loads(proc.stdout or "[]")
    except ValueError as exc:
        raise ResticFailed(
            "restic returned output restverify could not read as JSON.",
            stderr_tail=(proc.stdout or "")[:200],
            hint="check that this restic build supports `snapshots --json`",
        ) from exc
    snaps = []
    for item in raw or []:
        snaps.append(Snapshot(
            id=item.get("id", ""),
            short_id=(item.get("short_id") or item.get("id", "")[:8]),
            time=item.get("time", ""),
            paths=list(item.get("paths") or []),
        ))
    return snaps


def newest_snapshot(repo: str, password_command: str | None = None,
                    selector: str = "latest") -> Snapshot:
    """R5: 'latest' is the default; an explicit id/short-id/tag also works."""
    snaps = list_snapshots(repo, password_command)
    if not snaps:
        raise NoSnapshots(
            f"no snapshots found in {repo}, so there is nothing to verify yet.",
            hint="take a backup first (`restic -r <repo> backup <path>`), then re-run",
        )
    if selector and selector != "latest":
        for snap in snaps:
            if snap.id == selector or snap.short_id == selector:
                return snap
        raise NoSnapshots(
            f"no snapshot in {repo} matches {selector!r}.",
            hint="list them with `restic -r <repo> snapshots` and use the short id",
        )
    return max(snaps, key=lambda s: s.time)


def restore(repo: str, snapshot: Snapshot, target, excludes: list[str] | None = None,
            password_command: str | None = None) -> None:
    """R7/R4: restore into the temp target, honouring excludes. Read-only on the repo."""
    args = ["restore", snapshot.id, "--target", str(target), "--repo", repo]
    for pattern in excludes or []:
        args += ["--exclude", pattern]
    run(args, password_command)


def restore_paths(repo: str, snapshot_id: str, paths: list[str], target,
                  excludes: list[str] | None = None,
                  password_command: str | None = None) -> None:
    """Selective restore for `prove` (I13): restore ONLY the listed paths.

    Measured on restic 0.16.4 (throwaway repo, 2026-09-29): `restore --files-from`
    does not exist in 0.16.4; `-i/--include pattern` (repeatable) does, and the
    pattern is a GLOB. Filenames containing glob metacharacters or literal
    backslashes must therefore be escaped, and the order is load-bearing: double
    literal backslashes FIRST (glob sees them as an escaped backslash), then
    glob.escape for the metacharacters. Both orders were probed against a real
    repo: escape-then-double breaks on `back[1].txt`; double-then-escape restores
    `weird[1].txt`, `back\\slash.txt` and plain paths alike.
    """
    args = ["restore", snapshot_id, "--target", str(target), "--repo", repo]
    for path in paths:
        args += ["--include", glob.escape(path.replace("\\", "\\\\"))]
    for pattern in excludes or []:
        args += ["--exclude", pattern]
    run(args, password_command)


def ls(repo: str, snapshot_id: str, password_command: str | None = None) -> list[dict]:
    """`restic ls --json <id>` for `prove` (I13): the snapshot's file list.

    Measured on restic 0.16.4: the output is NDJSON — line 1 is the snapshot
    header ("struct_type": "snapshot"), then one object per tree node
    ("struct_type": "node") with name/type/path/uid/gid/mode/permissions/
    mtime/atime/ctime/inode. File nodes carry "size"; there is NO content
    hash or blob id of any kind — verification against this listing is
    therefore size-only (hashes_available=false), stated wherever `prove` is
    documented. Raises ResticFailed (exit 1, kind restic_failed) when restic
    fails or emits a line restverify cannot parse — reading the file list is
    part of completing the run, not a data verdict.
    """
    proc = run(["ls", "--json", snapshot_id, "--repo", repo], password_command)
    nodes: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError as exc:
            raise ResticFailed(
                "restic ls returned output restverify could not read as JSON.",
                stderr_tail=line[:200],
                hint="check that this restic build supports `ls --json`",
            ) from exc
        if item.get("struct_type") == "node":
            nodes.append(item)
    return nodes


def restored_root(target, snapshot) -> "object":
    """Locate the subtree the snapshot's own paths restored into (B1 assumption).

    `restic restore <id> --target <dir>` recreates the snapshot's absolute
    paths *under* the target, so verification needs that subtree, not the
    target itself. The strip rule is restic's own, and it is not uniform:
    POSIX paths keep their shape (`/srv/data` lands at `target/srv/data`),
    while a Windows path lands under just its last component (`C:\\...\\src`
    was measured to land at `target/src`, restic 0.19.1). Rather than fork
    the logic per platform, each path is tried as progressively shorter
    suffixes - longest first - and the first existing directory wins. Every
    candidate stays inside the target, so repository metadata cannot aim our
    walk at arbitrary filesystem paths.

    Moved here from cli.py in I13 so `run` and `prove` share ONE implementation
    of restic's restore layout instead of two copies that can rot apart.
    """
    base = Path(target).resolve()
    for raw in snapshot.paths or []:
        parts = [p for p in PurePath(str(raw)).parts
                 if p not in ("/", "\\") and not p.endswith((":\\", ":/"))]
        for start in range(len(parts)):
            candidate = Path(target).joinpath(*parts[start:])
            try:
                resolved = candidate.resolve()
                resolved.relative_to(base)
            except (OSError, ValueError):
                continue
            if resolved.is_dir():
                return resolved
    return Path(target)
