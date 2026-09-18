"""Temp restore directories with guaranteed cleanup (R7, R17).

Why this is more than tempfile.mkdtemp: R17 requires cleanup that survives the
process dying. None of `finally`, `atexit` or signal handlers run under
SIGKILL, so per-run cleanup can never be complete on its own. The design is:

  * every restore dir contains a marker file naming its owning pid and start
    time;
  * a normal run removes its own dir (finally);
  * the next run sweeps leftovers whose owner is gone (``sweep_stale``).

Safety: only directories that contain our marker are ever removed, and only
when they live directly under the system temp dir. A directory without the
marker is left alone, always.
"""
from __future__ import annotations

import errno
import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import TempDirError

PREFIX = "restverify-restore-"
MARKER = ".restverify-marker"


BASE_ENV = "RESTVERIFY_TMPDIR"


def base_dir(base: Path | None = None) -> Path:
    """Where restore dirs live: explicit base > RESTVERIFY_TMPDIR > system temp.

    Centralised so any caller (the CLI, a cron wrapper, a future embedder)
    honours the same override; an env var that only worked through the CLI
    would be a trap.
    """
    if base:
        return Path(base)
    override = os.environ.get(BASE_ENV)
    return Path(override) if override else Path(tempfile.gettempdir())


def _base(base: Path | None = None) -> Path:  # kept for internal call sites
    return base_dir(base)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM  # exists but owned by someone else
    return True


def make_restore_dir(repo: str, base: Path | None = None) -> Path:
    """Create a private (0700) restore directory carrying an ownership marker."""
    parent = _base(base)
    try:
        path = Path(tempfile.mkdtemp(prefix=f"{PREFIX}{os.getpid()}-", dir=str(parent)))
    except OSError as exc:
        raise TempDirError(
            f"could not create a restore directory under {parent}: {exc.strerror or exc}",
            hint="point RESTVERIFY_TMPDIR at a writable directory with room for a "
                 "full restore, or fix permissions on the temp dir",
        ) from exc
    os.chmod(path, 0o700)
    marker = {"pid": os.getpid(), "started": time.time(), "repo": repo}
    (path / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    return path


def cleanup(path: Path | str | None) -> bool:
    """Remove a restore dir. Refuses anything without our marker."""
    if not path:
        return False
    target = Path(path)
    if not target.is_dir() or not (target / MARKER).exists():
        return False
    try:
        shutil.rmtree(target)
        return True
    except OSError:
        return False


def sweep_stale(base: Path | None = None, ttl_seconds: int = 3600) -> list[Path]:
    """Remove leftover restore dirs whose owning process is gone.

    Called at the start of every run, this is what makes cleanup survivable
    under `kill -9`/power loss rather than only under a clean exit.
    """
    removed: list[Path] = []
    parent = _base(base)
    if not parent.is_dir():
        return removed
    now = time.time()
    for candidate in parent.glob(f"{PREFIX}*"):
        marker_file = candidate / MARKER
        if not candidate.is_dir() or not marker_file.exists():
            continue  # not ours: never touch it
        try:
            marker = json.loads(marker_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker = {}
        pid = int(marker.get("pid") or -1)
        started = float(marker.get("started") or 0)
        abandoned = (not _pid_alive(pid)) or (now - started > ttl_seconds and not _pid_alive(pid))
        if abandoned and cleanup(candidate):
            removed.append(candidate)
    return removed


@contextmanager
def restore_dir(repo: str, base: Path | None = None):
    """Yield a restore dir and remove it on the way out, success or failure."""
    path = make_restore_dir(repo, base=base)
    try:
        yield path
    finally:
        cleanup(path)
