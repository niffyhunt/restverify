"""`run --sandbox` — restore inside a disposable container (R45, I11).

Plan 1 (docs/V2-PLAN-ADDENDUM.md, Upgrade 4): optionally, the
restore runs inside an ephemeral container so the host never
touches restored attacker-influenceable data. The lifecycle is the plan's:
create container -> mount the tempstore dir -> exec restore -> destroy
container, with the purge enforced regardless of the verification's exit
code — a clean exit, an exception, a SIGINT and even a `kill -9` mid-restore
leave **zero orphan containers** (R17's ownership-marker sweep, extended to
containers via a runtime label).

How the isolation works (documented because it is a safety claim):

* a throwaway container is created with **no network** for local repos
  (`--network none`); a **remote** repo (sftp://, rclone:) needs its
  network to resolve, so it runs on the default bridge and the repo string
  is passed through unchanged — the container resolves it with its own
  resolver and the user's own credentials (an sftp sandbox needs the
  ssh key; `~/.ssh` is bind-mounted read-only for that case);
* the host's **restic binary** is `docker cp`'d into the container at
  /usr/local/bin/restic (no image build, no publish, no multi-runtime
  certification — one documented runtime, image `RESTVERIFY_SANDBOX_IMAGE`,
  default `ubuntu:24.04` whose glibc matches this box's restic);
* the restore target is a **tempstore dir** (same ownership-marker pattern
  as every restore, 0700) bind-mounted read-write; the exec runs as the
  **invoking user's uid/gid** (never root inside the container), so the
  restored tree is owned by the caller — exactly like a native restore;
* the **password itself is never handled** (R2/R24): the user's own
  `password_command` string rides RESTIC_PASSWORD_COMMAND into the exec;
* the **verification itself is unchanged**: manifest, sample and comparison
  run on the host against the restored tree, because the tempstore mount is
  the same directory every non-sandbox run uses. The isolation boundary
  protects the host from the restored *data* (it is only ever materialised
  by the container's restic process, in a throwaway container with no
  image persistence); the host-side read-only walk is the same code every
  native run uses.

**Single Spawner rule:** this module never spawns a process. Container
create/cp/start/exec/inspect/purge all go through the `restic.spawn_process`
seam — restic.py remains the only module that starts a process
(`test_n5_only_restic_py_spawns_processes` keeps passing unmodified).

No new exit code: a missing or failing container runtime surfaces as
ResticFailed (exit 1, kind "restic_failed") with a teaching hint (U2).
Windows is out of scope for v1 (the drill runs natively there — Ruling 1).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import restic
from .errors import ResticFailed

# One supported runtime, documented (V2-PLAN-ADDENDUM: "one supported runtime,
# documented"). Both overrides exist for tests and exotic setups only.
RUNTIME_ENV = "RESTVERIFY_SANDBOX_RUNTIME"     # default: docker
IMAGE_ENV = "RESTVERIFY_SANDBOX_IMAGE"         # default: ubuntu:24.04
PASSFILE_ENV = "RESTVERIFY_SANDBOX_PASSFILE"   # host file mounted ro at /restverify-pass
DEFAULT_RUNTIME = "docker"
DEFAULT_IMAGE = "ubuntu:24.04"                 # glibc matches the host restic
PASSFILE_INNER = "/restverify-pass"

OWNER_LABEL = "restverify.owner"               # the container ownership marker
NAME_PREFIX = "restverify-sandbox-"            # sweep never touches anything else
REPO_PREFIX = "/restverify-repo"               # read-only mount of a local repo
RESTORE_TARGET = "/restverify-restore"         # read-write tempstore mount
RESTIC_INNER = "/usr/local/bin/restic"         # host restic, cp'd in read-only


@dataclass
class SandboxRun:
    """What one sandboxed restore did (rides the run envelope additively)."""

    runtime: str
    image: str
    container: str
    purged: bool


def runtime_binary() -> str:
    """Locate the container-runtime CLI (teaching error if absent, U2)."""
    name = os.environ.get(RUNTIME_ENV) or DEFAULT_RUNTIME
    found = restic.find_binary(name)
    if not found:
        raise ResticFailed(
            f"the container runtime {name!r} was not found on PATH, so the "
            "sandboxed restore cannot start.",
            hint=f"install {name} (and make sure your user can reach its "
                 "socket — group membership or a rootless setup; never sudo), "
                 "or run without --sandbox",
        )
    return found


def _image() -> str:
    return os.environ.get(IMAGE_ENV) or DEFAULT_IMAGE


class Sandbox:
    """One ephemeral container: create -> exec restore -> purge on all exits.

    The purge is `rm --force --volumes`, which stops a running container
    first, so no separate `stop` round-trip is needed. `__exit__` purges even
    on exception; a `kill -9` (which runs no Python at all) is handled by
    `sweep_orphans`, which every sandboxed run invokes before starting —
    containers carry the label `restverify.owner=<pid>`, the tempstore
    marker idea in runtime form.
    """

    def __init__(self, repo: str, target: Path,
                 password_command: str | None = None,
                 image: str | None = None) -> None:
        self.repo = repo
        self.target = Path(target)      # the host tempstore dir, mounted rw
        self.password_command = password_command
        self.image = image or _image()
        self.runtime = runtime_binary()
        self.name = f"{NAME_PREFIX}{os.getpid()}-{int(time.time())}"
        self._created = False
        self._repo_in_container = self.repo
        self._passfile = os.environ.get(PASSFILE_ENV) or ""

    # ── lifecycle, every call through the restic.py seam ────────────────────

    def create(self) -> None:
        """Create (not start) the container with its mounts already attached."""
        ensure_image(self.runtime, self.image)
        mount_args: list[str] = []
        repo = Path(self.repo)
        if repo.exists() and repo.is_dir():
            # Local repository: bind-mount read-only; inside the container the
            # repo is the mount point (an opaque location, as ever — N3) and
            # the container needs no network at all.
            mount_args += ["--network", "none",
                           "--volume", f"{repo.resolve()}:{REPO_PREFIX}:ro"]
            self._repo_in_container = REPO_PREFIX
        else:
            # Remote location (sftp://, rclone:...): passed through unchanged;
            # the container resolves it with its own resolver and credentials.
            # No network restriction here — the backend needs it (documented).
            ssh_dir = Path.home() / ".ssh"
            if ssh_dir.is_dir():
                mount_args += ["--volume", f"{ssh_dir}:{ssh_dir}:ro"]
            mount_args += ["--volume", "/etc/ssl/certs:/etc/ssl/certs:ro"]
        mount_args += [
            # The restore target: the host tempstore dir (0700, ownership
            # marker included), bind-mounted read-write. The exec below runs
            # as the invoking uid, so the restored tree lands owned by the
            # caller and the host-side manifest walk sees it unchanged.
            "--volume", f"{self.target.resolve()}:{RESTORE_TARGET}",
            "--volume", "/etc/localtime:/etc/localtime:ro",
            "--label", f"{OWNER_LABEL}={os.getpid()}",
        ]
        if self._passfile:
            # Opt-in (R2/R24 honoured): the user mounts a host password
            # FILE read-only at a fixed in-container path and the in-container
            # restic reads it there. restverify never reads the file itself.
            mount_args += ["--volume", f"{Path(self._passfile).resolve()}:{PASSFILE_INNER}:ro"]
        restic.spawn_process(
            [self.runtime, "create", "--name", self.name, *mount_args,
             self.image, "sleep", "infinity"],
            timeout=120,
        )
        self._created = True

    def _copy_restic_in(self) -> None:
        """Give the container the host's restic binary (read-only usage)."""
        restic_path = restic.find_restic()
        restic.spawn_process(
            [self.runtime, "cp", restic_path, f"{self.name}:{RESTIC_INNER}"],
            timeout=120,
        )

    def exec_restore(self, snapshot_id: str,
                     excludes: list[str] | None = None) -> None:
        """Run `restic restore` INSIDE the container, into the mounted tempstore
        dir, as the invoking user. The host never runs a restic restore here."""
        cmd: list[str] = [
            RESTIC_INNER, "restore", snapshot_id,
            "--target", RESTORE_TARGET,
            "--repo", self._repo_in_container,
            # The local repo is mounted READ-ONLY, so restic cannot take its
            # usual lock file. Measured on restic 0.16.4: without this flag the
            # restore fails with "unable to create lock in backend ... read-only
            # file system". `--no-lock` is restic's own answer for read-only
            # repositories (documented limitation: skip concurrent-op safety
            # for the duration of the drill).
            "--no-lock",
        ]
        for pattern in excludes or []:
            cmd += ["--exclude", pattern]
        # --user keeps the restored files owned by the invoking user (no root
        # inside the container, no chown afterwards); HOME points somewhere
        # container-internal so restic never writes through a host path.
        # Password channel: the passfile override when provided, else the
        # user's own password_command string verbatim — note it runs INSIDE
        # the container, so commands that read host files need the passfile
        # override (documented limitation, stated in --help).
        password_channel = (f"cat {PASSFILE_INNER}" if self._passfile
                            else (self.password_command or ""))
        argv = [
            self.runtime, "exec",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--env", "HOME=/tmp",
            "--env", f"RESTIC_PASSWORD_COMMAND={password_channel}",
            self.name, *cmd,
        ]
        proc = restic.spawn_process(
            argv, check=False,
            timeout=int(os.environ.get("RESTVERIFY_TIMEOUT", "1800")),
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-4:]
            raise ResticFailed(
                f"the sandboxed restore failed (in-container exit {proc.returncode}).",
                stderr_tail=" | ".join(tail),
                hint="run `restic -r <repo> snapshots` by hand, or retry without "
                     "--sandbox to isolate the cause",
            )

    def purge(self) -> bool:
        """Destroy the container. Best-effort, idempotent, never raises."""
        if not self._created:
            return False
        try:
            restic.spawn_process(
                [self.runtime, "rm", "--force", "--volumes", self.name],
                timeout=120,
            )
            self._created = False
            return True
        except ResticFailed:
            return False

    def run(self, snapshot_id: str, excludes: list[str] | None = None) -> None:
        """create -> copy restic -> start -> exec restore; purge on ALL exits."""
        try:
            self.create()
            try:
                self._copy_restic_in()
                restic.spawn_process([self.runtime, "start", self.name],
                                     timeout=120)
                self.exec_restore(snapshot_id, excludes=excludes)
            finally:
                self.purge()
        except BaseException:
            self.purge()         # double-purge is safe; covers purge failures
            raise


def ensure_image(runtime: str, image: str) -> None:
    """Pull the image only if absent. Teach on failure, never traceback."""
    probe = restic.spawn_process([runtime, "image", "inspect", image],
                                 timeout=60, check=False)
    if probe.returncode == 0:
        return
    pull = restic.spawn_process([runtime, "pull", image], timeout=600,
                                check=False)
    if pull.returncode != 0:
        tail = (pull.stderr or pull.stdout or "").strip().splitlines()[-3:]
        raise ResticFailed(
            f"the sandbox image {image!r} could not be pulled.",
            stderr_tail=" | ".join(tail),
            hint=f"pre-pull it by hand (`{runtime} pull {image}`) and re-run, "
                 "or run without --sandbox",
        )


def sweep_orphans() -> list[str]:
    """Remove leftover sandbox containers whose owner is gone (kill -9 story).

    Called at the start of every sandboxed run (and by tests directly). Only
    containers named `restverify-sandbox-*` AND labelled
    `restverify.owner=<pid>` are ever considered; anything else on the
    machine is none of ours. A container whose owner is still alive is kept.
    """
    runtime = runtime_binary()
    proc = restic.spawn_process(
        [runtime, "ps", "-a", "--format", "{{.Names}}\t{{.Labels}}"],
        timeout=60,
    )
    removed: list[str] = []
    for line in (proc.stdout or "").splitlines():
        name, _, labels = line.partition("\t")
        name = name.strip()
        if not name.startswith(NAME_PREFIX):
            continue
        owner = None
        for pair in labels.split(","):
            key, _, value = pair.partition("=")
            if key.strip() == OWNER_LABEL:
                owner = value.strip()
                break
        if owner is None:
            continue            # no ownership label: none of ours, never touch
        if restic.pid_alive(int(owner) if owner.isdigit() else -1):
            continue            # owner alive: keep it
        purge = restic.spawn_process(
            [runtime, "rm", "--force", "--volumes", name],
            timeout=120, check=False,
        )
        if purge.returncode == 0:
            removed.append(name)
    return removed


def sandboxed_restore(repo: str, snapshot_id: str, target: Path,
                      password_command: str | None = None,
                      excludes: list[str] | None = None) -> SandboxRun:
    """One sandboxed restore: orphans swept, container purged on every exit.

    This is what `cli._cmd_run` calls when --sandbox is set. The manifest,
    sample, comparison and history halves of the run are unchanged — they see
    the restored tree through the same tempstore dir a native run uses.
    """
    sweep_orphans()
    box = Sandbox(repo, target=Path(target), password_command=password_command)
    box.run(snapshot_id, excludes=excludes)
    return SandboxRun(runtime=box.runtime, image=box.image,
                      container=box.name, purged=True)
