"""Test bootstrap + the fake-restic harness (gate G1).

No test may need a real restic binary or a real repository: a fake `restic`
is placed on PATH so every code path (happy, failure, adversarial) is
deterministic and offline. The fake records its argv so tests can assert what
restverify actually asked restic to do — including that it never asked it to
mutate anything (N1/N2).

Implementation note: the fake uses print() rather than embedding newline
escapes, so the generated script cannot rot from double-escaping.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# The tree the fake restic restores. Tests that exercise the source comparison
# plant the same bytes (plant_fake_tree / source_tree) so "identical" really is
# identical and any difference a test introduces is the only difference.
FAKE_FILES = {"restored.txt": b"hello", "sub/nested.bin": b"\0" * 1500}
FAKE_DIRS = ("empty-dir",)
FAKE_LINKS = {"link": "restored.txt"}
FAKE_TREE_JSON = json.dumps({
    "files": {rel: blob.hex() for rel, blob in FAKE_FILES.items()},
    "dirs": list(FAKE_DIRS),
    "links": dict(FAKE_LINKS),
})

FAKE_RESTIC = '''#!/usr/bin/env python3
# Provenance (I7b): every failure string and exit code below was copied from a
# real restic 0.16.4 run on 2026-09-24. restic exits 1 for a wrong password, a
# missing repository and a missing snapshot alike; before I7b this fake invented
# 12, 10 and 3 and worded the restore failure as "unable to load snapshot",
# which restic does not emit. No shipped code branched on those numbers and no
# test asserted them, so the fiction was invisible until a real binary ran.
import json, os, sys

LOG = os.environ.get("FAKE_RESTIC_LOG")
mode = os.environ.get("FAKE_RESTIC_MODE", "ok")
TREE = __FAKE_TREE__
args = sys.argv[1:]
if LOG:
    with open(LOG, "a", encoding="utf-8") as fh:
        print(" ".join(args), file=fh)
verb = args[0] if args else ""

if verb == "snapshots":
    if mode == "wrong_password":
        print("Fatal: wrong password or no key found", file=sys.stderr)
        sys.exit(1)
    if mode == "no_such_repo":
        print("Fatal: unable to open config file: stat /nope: no such file or directory", file=sys.stderr)
        print("Is there a repository at the following location?", file=sys.stderr)
        print("/nope", file=sys.stderr)
        sys.exit(1)
    if mode == "badjson":
        print("this is not json")
        sys.exit(0)
    if mode == "empty":
        print("[]")
        sys.exit(0)
    print(json.dumps([
        {"id": "a" * 64, "short_id": "aaaaaaaa", "time": "2026-09-01T00:00:00Z", "paths": ["/srv/data"]},
        {"id": "9f3a2c00" + "b" * 56, "short_id": "9f3a2c00", "time": "2026-09-17T10:00:00Z", "paths": ["/srv/data"]},
    ]))
    sys.exit(0)

if verb == "restore":
    target = args[args.index("--target") + 1]
    for rel, blob in TREE["files"].items():
        path = os.path.join(target, rel)
        os.makedirs(os.path.dirname(path) or target, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(bytes.fromhex(blob))
    for rel in TREE["dirs"]:
        os.makedirs(os.path.join(target, rel), exist_ok=True)
    for rel, dest in TREE["links"].items():
        os.symlink(dest, os.path.join(target, rel))
    if mode == "fail_restore":
        print("Fatal: failed to find snapshot: no matching ID found for prefix \\"9f3a2c00\\"", file=sys.stderr)
        sys.exit(1)
    print("restored")
    sys.exit(0)

print("fake restic: unexpected verb %s" % verb, file=sys.stderr)
sys.exit(1)
'''.replace("__FAKE_TREE__", f"json.loads({FAKE_TREE_JSON!r})")


def plant_fake_tree(root: Path) -> Path:
    """Write the same bytes the fake restic restores into ``root``."""
    for rel, blob in FAKE_FILES.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    for rel in FAKE_DIRS:
        (Path(root) / rel).mkdir(parents=True, exist_ok=True)
    for rel, dest in FAKE_LINKS.items():
        (Path(root) / rel).symlink_to(dest)
    return Path(root)


class _FakeRestic:
    def __init__(self, path, log, monkeypatch):
        self.path = path
        self.log = log
        self._mp = monkeypatch

    def set_mode(self, mode):
        self._mp.setenv("FAKE_RESTIC_MODE", mode)

    def calls(self):
        if not self.log.exists():
            return []
        return [line for line in self.log.read_text(encoding="utf-8").splitlines() if line]

    def verbs(self):
        return [line.split()[0] for line in self.calls()]


@pytest.fixture
def fake_restic(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "restic"
    exe.write_text(FAKE_RESTIC, encoding="utf-8")
    exe.chmod(0o755)
    log = tmp_path / "restic.log"
    monkeypatch.setenv("FAKE_RESTIC_LOG", str(log))
    monkeypatch.setenv("FAKE_RESTIC_MODE", "ok")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return _FakeRestic(exe, log, monkeypatch)


@pytest.fixture
def tmp_base(tmp_path, monkeypatch):
    """Redirect the restore temp area so cleanup is observable per test."""
    base = tmp_path / "tmpbase"
    base.mkdir()
    monkeypatch.setenv("RESTVERIFY_TMPDIR", str(base))
    return base


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("RESTVERIFY_CONFIG", str(path))
    return path


@pytest.fixture(scope="session")
def _state_base():
    """One state base per session, on tmpfs where the platform has it.

    The store does a real fsync per write (durability is the point), which is
    measurable on a slow /tmp: putting the test stores on /dev/shm (Linux, WSL2)
    keeps the same SQLite operations without the disk latency. Falls back to a
    normal temp dir elsewhere (e.g. native Windows). Teardown happens after every
    test-level monkeypatch is undone, so it cannot trip over a patched os.scandir.
    """
    shm = Path("/dev/shm")
    parent = shm if (shm.is_dir() and os.access(shm, os.W_OK)) else None
    base = Path(tempfile.mkdtemp(prefix="restverify-states-",
                                 dir=str(parent) if parent else None))
    yield base
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolated_state(_state_base, monkeypatch):
    """Every test gets its own durable-state dir, created lazily by history.py.

    Since I4a the run path writes a history row (history.py). Autouse on purpose:
    a new test cannot forget it, and no test can touch the real
    ~/.local/state/restverify. The path is deliberately *not* created here, so
    "the store is created on first run" stays observable.
    """
    state = _state_base / f"state-{uuid4().hex}"
    monkeypatch.setenv("RESTVERIFY_STATE", str(state))
    return state


@pytest.fixture
def plant_tree():
    """Return the tree-planter so a test can build a matching source tree."""
    return plant_fake_tree


@pytest.fixture
def source_tree(tmp_path):
    """A source directory that is byte-identical to what the fake restores."""
    root = tmp_path / "source"
    root.mkdir()
    return plant_fake_tree(root)
