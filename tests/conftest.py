"""Test bootstrap + the fake-restic harness (gate G1).

No test may need a real restic binary or a real repository: a fake `restic`
is placed on PATH so every code path (happy, failure, adversarial) is
deterministic and offline. The fake records its argv so tests can assert what
restverify actually asked restic to do — including that it never asked it to
mutate anything (N1/N2).

Implementation note: the fake uses print() rather than embedding newline
escapes, so the generated script cannot rot from double-escaping.
"""
import os
import sys

import pytest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

FAKE_RESTIC = '''#!/usr/bin/env python3
import json, os, sys

LOG = os.environ.get("FAKE_RESTIC_LOG")
mode = os.environ.get("FAKE_RESTIC_MODE", "ok")
args = sys.argv[1:]
if LOG:
    with open(LOG, "a", encoding="utf-8") as fh:
        print(" ".join(args), file=fh)
verb = args[0] if args else ""

if verb == "snapshots":
    if mode == "wrong_password":
        print("Fatal: wrong password or no key found", file=sys.stderr)
        sys.exit(12)
    if mode == "no_such_repo":
        print("Fatal: unable to open config file: stat /nope: no such file or directory", file=sys.stderr)
        sys.exit(10)
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
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "restored.txt"), "w", encoding="utf-8") as fh:
        fh.write("hello")
    if mode == "fail_restore":
        print("Fatal: unable to load snapshot 9f3a2c00", file=sys.stderr)
        sys.exit(3)
    print("restored")
    sys.exit(0)

print("fake restic: unexpected verb %s" % verb, file=sys.stderr)
sys.exit(1)
'''


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
