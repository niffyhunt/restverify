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
import json, os, re, sys

LOG = os.environ.get("FAKE_RESTIC_LOG")
mode = os.environ.get("FAKE_RESTIC_MODE", "ok")
TREE = __FAKE_TREE__
args = sys.argv[1:]
if LOG:
    with open(LOG, "a", encoding="utf-8") as fh:
        print(" ".join(args), file=fh)
verb = args[0] if args else ""

# The listing the fake repository serves (I13): the tree files as /srv/data/
# paths (like a real snapshot of /srv/data) plus three synthetic files whose
# names carry glob metacharacters and a literal backslash. The synthetics
# exist ONLY in the listing and the --include-driven restore, so an escaping
# mistake in the caller shows up as a verification failure, exactly as it
# would against real restic (measured behaviour, see restic.restore_paths).
BSLASH = chr(92)
TREE_PATHS = {"/srv/data/" + rel: bytes.fromhex(blob)
              for rel, blob in TREE["files"].items()}
SYNTH_FILES = {
    "/srv/data/weird[1].txt": b"x" * 9,
    "/srv/data/back" + BSLASH + "slash.txt": b"y" * 11,
    "/srv/data/plain.txt": b"z" * 12,
}
SNAPSHOT_HEADER = {"struct_type": "snapshot", "id": "9f3a2c00" + "b" * 56,
                   "short_id": "9f3a2c00", "time": "2026-09-17T10:00:00Z",
                   "paths": ["/srv/data"]}

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

if verb == "ls":
    # NDJSON, one object per line: snapshot header first, then one object per
    # tree node (measured on restic 0.16.4; file nodes carry size, no hashes).
    if mode == "wrong_password":
        print("Fatal: wrong password or no key found", file=sys.stderr)
        sys.exit(1)
    if mode == "no_such_repo":
        print("Fatal: unable to open config file: stat /nope: no such file or directory", file=sys.stderr)
        sys.exit(1)
    if mode == "badjson":
        print("this is not json")
        sys.exit(0)
    print(json.dumps(SNAPSHOT_HEADER))
    if mode != "ls_empty":
        print(json.dumps({"struct_type": "node", "name": "data", "type": "dir",
                          "path": "/srv/data"}))
        for full in sorted(dict(TREE_PATHS, **SYNTH_FILES)):
            if full == "/srv/data/sub/nested.bin":
                print(json.dumps({"struct_type": "node", "name": "sub",
                                  "type": "dir", "path": "/srv/data/sub"}))
            content = dict(TREE_PATHS, **SYNTH_FILES)[full]
            print(json.dumps({"struct_type": "node", "type": "file",
                              "name": os.path.basename(full), "path": full,
                              "size": len(content)}))
    sys.exit(0)

if verb == "restore":
    target = args[args.index("--target") + 1]
    includes = []
    i = 0
    while "--include" in args[i:]:
        j = args.index("--include", i)
        includes.append(args[j + 1])
        i = j + 2

    def _include_rx(pattern):
        # restic's include is Go filepath.Match: a backslash escapes the next
        # byte (so \\\\ matches ONE literal backslash), [[]= matches a literal
        # '[', '*' and '?' do not cross '/'. A plain fnmatch of the escaped
        # pattern gets both wrong, which would hide real escaping bugs.
        # (No string escapes below: the fake must not rot from double-escaping,
        # and fullmatch() needs no ^/$ anchors.)
        out = []
        i = 0
        while i < len(pattern):
            c = pattern[i]
            if c == BSLASH and i + 1 < len(pattern):
                out.append(re.escape(pattern[i + 1]))
                i += 2
            elif c == "*":
                out.append("[^/]*")
                i += 1
            elif c == "?":
                out.append("[^/]")
                i += 1
            elif c == "[":
                j = pattern.find("]", i)
                if j == -1:
                    out.append(re.escape(c))
                    i += 1
                else:
                    body = pattern[i + 1:j]
                    if body.startswith(("^", "!")):
                        out.append("[^" + re.escape(body[1:]) + "]")
                    else:
                        out.append("[" + re.escape(body) + "]")
                    i = j + 1
            else:
                out.append(re.escape(c))
                i += 1
        return re.compile("".join(out))

    rxs = [_include_rx(p) for p in includes]

    def wanted(full):
        if not rxs:
            return True
        return any(rx.fullmatch(full) for rx in rxs)

    for full in sorted(TREE_PATHS):
        if not wanted(full):
            continue
        content = b"" if mode == "corrupt" else TREE_PATHS[full]
        path = os.path.join(target, full[len("/srv/data/"):])
        os.makedirs(os.path.dirname(path) or target, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(content)
    if includes:
        # Synthetic listing files materialise only on a selective restore —
        # a plain `run` restore must keep writing exactly the old tree.
        for full in sorted(SYNTH_FILES):
            if not wanted(full):
                continue
            content = b"" if mode == "corrupt" else SYNTH_FILES[full]
            path = os.path.join(target, full[len("/srv/data/"):])
            os.makedirs(os.path.dirname(path) or target, exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(content)
    for rel in TREE["dirs"]:
        os.makedirs(os.path.join(target, rel), exist_ok=True)
    for rel, dest in TREE["links"].items():
        try:
            os.symlink(dest, os.path.join(target, rel))
        except OSError:
            pass  # no symlink privilege (non-elevated Windows): omit it
    if mode == "fail_restore":
        print("Fatal: failed to find snapshot: no matching ID found for prefix \\"9f3a2c00\\"", file=sys.stderr)
        sys.exit(1)
    print("restored")
    sys.exit(0)

if verb == "check":
    # Mirrors the measured real behaviour: healthy repo exits 0; a corrupted
    # pack makes real restic exit non-zero with "repository contains errors"
    # while `restore` still exits 0 — which is exactly why prove runs check.
    if mode == "check_fails":
        print("checking 3 packs", file=sys.stderr)
        print("pack 0006: damaged and cannot be repaired", file=sys.stderr)
        print("Fatal: repository contains errors", file=sys.stderr)
        sys.exit(1)
    print("no errors were found", file=sys.stderr)
    sys.exit(0)

print("fake restic: unexpected verb %s" % verb, file=sys.stderr)
sys.exit(1)
'''.replace("__FAKE_TREE__", f"json.loads({FAKE_TREE_JSON!r})")


_SYMLINK_SKIP_TESTS = frozenset({
    # These assert symlink semantics end-to-end, so they can only run where
    # os.symlink is permitted (POSIX; Windows with Developer Mode or admin).
    "test_symlink_target_difference_is_exit_2",
    "test_symlinks_are_recorded_not_followed",
    "test_symlink_targets_are_preserved",
    "test_symlinked_directory_is_not_descended_into",
    "test_symlink_swapped_after_the_manifest_is_a_teaching_error",
})

_SYMLINKS_OK = None


def symlinks_supported() -> bool:
    """Whether os.symlink can actually create a link on this machine.

    Windows only honours symlink creation for elevated processes or with
    Developer Mode enabled; elsewhere every attempt raises WinError 1314
    ("A required privilege is not held by the client").
    """
    global _SYMLINKS_OK
    if _SYMLINKS_OK is None:
        probe = Path(tempfile.mkdtemp(prefix="rv-symlink-probe-"))
        try:
            os.symlink("target", probe / "link")
            _SYMLINKS_OK = True
        except OSError:
            _SYMLINKS_OK = False
        finally:
            shutil.rmtree(probe, ignore_errors=True)
    return _SYMLINKS_OK


def pytest_collection_modifyitems(config, items):
    if symlinks_supported():
        return
    reason = ("os.symlink is not permitted here "
              "(Windows: enable Developer Mode or run elevated)")
    for item in items:
        if item.name.split("[", 1)[0] in _SYMLINK_SKIP_TESTS:
            item.add_marker(pytest.mark.skip(reason=reason))


def build_win_restic_shim(dest: Path) -> Path:
    """Write a real ``restic.exe`` that re-enters the fake ``restic`` script.

    Windows specifics that make the POSIX fake unrunnable there as-is:

    * ``shutil.which()`` only matches PATHEXT extensions (.COM/.EXE/.BAT/...),
      so the extension-less fake named ``restic`` is invisible to the lookup
      and a real restic.exe on PATH wins instead;
    * ``CreateProcess`` cannot launch a ``.bat`` shim either (WinError 193),
      so the shim must be a genuine PE executable.

    pip ships distlib's launcher template for console scripts; this builds a
    stub the same way pip does: launcher bytes + ``#!`` shebang + a zip whose
    ``__main__.py`` runs the script named by FAKE_RESTIC_SCRIPT via runpy.
    """
    import io as _io
    import zipfile
    import pip._vendor.distlib as _distlib
    launcher = Path(_distlib.__file__).with_name(
        "t64.exe" if sys.maxsize > 2 ** 32 else "t32.exe")
    payload = (
        "import os, runpy, sys\n"
        "script = os.environ.get('FAKE_RESTIC_SCRIPT')\n"
        "if not script:\n"
        "    sys.stderr.write('restic shim: FAKE_RESTIC_SCRIPT not set\\n')\n"
        "    raise SystemExit(97)\n"
        "runpy.run_path(script, run_name='__main__')\n"
    )
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("__main__.py", payload)
    dest.write_bytes(
        launcher.read_bytes()
        + b"#!" + os.fsencode(sys.executable) + b"\n"
        + buf.getvalue())
    return dest


def plant_fake_tree(root: Path) -> Path:
    """Write the same bytes the fake restic restores into ``root``."""
    for rel, blob in FAKE_FILES.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    for rel in FAKE_DIRS:
        (Path(root) / rel).mkdir(parents=True, exist_ok=True)
    for rel, dest in FAKE_LINKS.items():
        if symlinks_supported():
            (Path(root) / rel).symlink_to(dest)
        # Without symlink permission (non-elevated Windows) the link entry is
        # omitted from planted trees; link-specific tests skip themselves.
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
    if os.name == "nt":
        build_win_restic_shim(bin_dir / "restic.exe")
        monkeypatch.setenv("FAKE_RESTIC_SCRIPT", str(exe))
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
