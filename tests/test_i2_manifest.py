"""Increment I2a: restored-tree manifest (R8) — counts, bytes, roll-up, links.

The manifest is the evidence that a restore landed something. These tests pin
exact numbers on a planted tree, the symlink policy (recorded, never followed),
deep-nesting behaviour, the empty-tree case, the metadata-only guarantee, and
the honesty marker on --json (schema 1, no placeholder fields; gate G6).
"""
import json
import os
from pathlib import Path

import pytest

from restverify import EXIT_PASS, manifest as manifestmod
from restverify.cli import _restored_root, human_bytes, main
from restverify.errors import ManifestError
from restverify.restic import Snapshot
from conftest import symlinks_supported


def plant(root: Path) -> Path:
    """A small tree: nested file, empty dir, symlink, known exact byte total."""
    (root / "sub").mkdir(parents=True)
    (root / "empty-dir").mkdir()
    (root / "restored.txt").write_text("hello", encoding="utf-8")      # 5 B
    (root / "sub" / "nested.bin").write_bytes(b"\0" * 1500)            # 1500 B
    if symlinks_supported():
        (root / "link").symlink_to("restored.txt")
    # On hosts where symlink creation is not permitted (non-elevated Windows)
    # the link entry is omitted; tests can adapt via symlinks_supported().
    return root


# ── counts, bytes, roll-up (R8) ─────────────────────────────────────────────

def test_counts_and_bytes_are_exact(tmp_path):
    man = manifestmod.build(plant(tmp_path / "tree"))
    assert man.file_count == 2
    assert man.total_bytes == 1505
    assert man.symlink_count == (1 if symlinks_supported() else 0)
    assert man.other_count == 0
    assert man.directory_count == 3          # "" + sub + empty-dir
    assert man.max_depth == 1


def test_per_directory_rollup_is_direct_and_recursive(tmp_path):
    man = manifestmod.build(plant(tmp_path / "tree"))
    roll = man.directory_rollup()
    assert roll[""]["files"] == 1             # restored.txt sits at the root
    assert roll[""]["bytes"] == 5
    assert roll[""]["subdirs"] == 2           # sub + empty-dir
    assert roll[""]["files_recursive"] == 2
    assert roll[""]["bytes_recursive"] == 1505
    assert roll["sub"]["files"] == 1
    assert roll["empty-dir"]["files_recursive"] == 0


def test_largest_file_is_reported(tmp_path):
    man = manifestmod.build(plant(tmp_path / "tree"))
    largest = man.largest_file()
    assert largest.path == "sub/nested.bin"
    assert largest.size == 1500


# ── symlink policy: recorded, never followed (G3) ──────────────────────────

def test_symlinks_are_recorded_not_followed(tmp_path):
    tree = plant(tmp_path / "tree")
    outside = tmp_path / "outside"
    (outside / "subdir").mkdir(parents=True)
    (outside / "secret.bin").write_bytes(b"\xff" * 4096)      # must never be counted
    (outside / "subdir" / "deep.txt").write_text("outside data", encoding="utf-8")
    (tree / "escape").symlink_to(outside, target_is_directory=True)
    (tree / "escape-file").symlink_to(outside / "secret.bin")
    man = manifestmod.build(tree)
    kinds = {e.path: e.kind for e in man.entries}
    assert kinds["escape"] == manifestmod.KIND_SYMLINK
    assert kinds["escape-file"] == manifestmod.KIND_SYMLINK
    assert man.file_count == 2                 # nothing behind the links counted
    assert man.total_bytes == 1505             # the 4096 B secret is not included
    assert all(not e.path.startswith("escape/") for e in man.entries)
    assert man.directories.get("escape") is None
    assert man.symlink_count == 3


def test_symlink_targets_are_preserved(tmp_path):
    man = manifestmod.build(plant(tmp_path / "tree"))
    link = next(e for e in man.entries if e.path == "link")
    assert link.link_target == "restored.txt"
    assert link.size == 0


def test_symlinked_directory_is_not_descended_into(tmp_path):
    tree = tmp_path / "tree"
    real = tree / "real"
    real.mkdir(parents=True)
    (real / "a.txt").write_text("a", encoding="utf-8")
    (tree / "loop").symlink_to(real, target_is_directory=True)
    man = manifestmod.build(tree)
    assert [e.path for e in man.entries] == ["loop", "real/a.txt"]
    assert man.max_depth == 1


def test_owned_top_level_entries_are_ignored_visibly(tmp_path):
    """restverify's own tempstore marker is not restored data — and the skip is
    recorded in the manifest, never silent (G6). A nested file of the same name
    is *not* ours and must still be counted."""
    tree = plant(tmp_path / "tree")
    (tree / ".restverify-marker").write_text("{}", encoding="utf-8")
    (tree / "sub" / ".restverify-marker").write_text("{}", encoding="utf-8")
    man = manifestmod.build(tree, ignore_top_level=(".restverify-marker",))
    assert man.file_count == 3                  # top-level marker skipped
    assert man.ignored == [".restverify-marker"]
    assert "sub/.restverify-marker" in [e.path for e in man.entries]


# ── depth + empty tree ─────────────────────────────────────────────────────

def test_deep_nesting_is_reported_not_truncated(tmp_path):
    tree = tmp_path / "tree"
    deep = tree
    for i in range(12):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("x", encoding="utf-8")
    man = manifestmod.build(tree)
    assert man.max_depth == 12
    assert man.file_count == 1
    assert any(e.path.endswith("d11/leaf.txt") for e in man.entries)
    assert len(man.directory_rollup()) == 13   # "" + 12 levels


def test_empty_restored_tree_is_explicit(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    man = manifestmod.build(tree)
    assert man.file_count == 0
    assert man.total_bytes == 0
    assert man.symlink_count == 0
    assert man.max_depth == 0
    assert man.largest_file() is None
    assert man.directory_rollup() == {"": {"files": 0, "bytes": 0, "subdirs": 0,
                                           "files_recursive": 0, "bytes_recursive": 0}}
    assert man.to_json()["largest_file"] is None


def test_special_files_are_recorded_so_nothing_is_hidden(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("no mkfifo on this platform")
    tree = tmp_path / "tree"
    tree.mkdir()
    os.mkfifo(tree / "pipe")
    man = manifestmod.build(tree)
    assert man.file_count == 0
    assert man.other_count == 1
    assert man.entries[0].kind == manifestmod.KIND_OTHER


# ── adversarial (G1) + metadata-only guarantee (G3) ───────────────────────

def test_missing_tree_is_a_teaching_error(tmp_path):
    with pytest.raises(ManifestError) as exc:
        manifestmod.build(tmp_path / "nope")
    assert "is not a directory" in exc.value.what
    assert "--dry-run" in exc.value.hint


def test_a_vanishing_directory_teaches_instead_of_partial_manifest(tmp_path, monkeypatch):
    tree = plant(tmp_path / "tree")
    real_scandir = manifestmod.os.scandir

    def boom(path):
        if Path(path).name == "sub":
            raise FileNotFoundError(2, "No such file or directory")
        return real_scandir(path)

    monkeypatch.setattr(manifestmod.os, "scandir", boom)
    with pytest.raises(ManifestError) as exc:
        manifestmod.build(tree)
    assert "could not read" in exc.value.what
    assert "restore itself did not complete" in exc.value.hint


def test_manifest_building_never_reads_file_contents():
    """I2a is metadata-only; content reads are the sampled hash's job (I2b)."""
    body = Path(manifestmod.__file__).read_text(encoding="utf-8")
    for token in (".read_bytes(", ".read_text(", "open("):
        assert token not in body, f"manifest.py reads content via {token!r}"


def test_restored_root_refuses_paths_outside_the_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "outside").mkdir()
    snap = Snapshot(id="x", short_id="x", time="", paths=["../outside"])
    assert _restored_root(target, snap) == target


def test_restored_root_finds_the_snapshot_subtree(tmp_path):
    target = tmp_path / "target"
    (target / "srv" / "data").mkdir(parents=True)
    snap = Snapshot(id="x", short_id="x", time="", paths=["/srv/data"])
    assert _restored_root(target, snap) == (target / "srv" / "data").resolve()


# ── wired into `run`: human line + --json ─────────────────────────────────

def test_run_human_line_reports_counts_and_size(fake_restic, tmp_base, config_path, capsys):
    code = main(["run", "-r", "/srv/backup"])
    out = capsys.readouterr().out
    assert code == EXIT_PASS
    assert "\u2713 restored snapshot 9f3a2c00: 2 files / 1.5 KiB in" in out
    assert "max depth 1" in out
    links = 1 if symlinks_supported() else 0
    assert f"{links} symlink(s) (recorded-not-followed)" in out
    assert "arrive in increment I2" not in out


def test_json_run_emits_manifest_compare_and_schema(
        fake_restic, tmp_base, config_path, capsys):
    code = main(["run", "-r", "/srv/backup", "--json"])
    captured = capsys.readouterr()
    assert code == EXIT_PASS
    payload = json.loads(captured.out)          # stdout must be pure JSON
    assert payload["status"] == "pass" and payload["exit_code"] == EXIT_PASS
    assert payload["schema"] == 1               # I3c: no placeholder payloads
    man = payload["manifest"]
    assert man["file_count"] == 2
    assert man["total_bytes"] == 1505
    assert man["symlink_count"] == (1 if symlinks_supported() else 0)
    assert man["symlink_policy"] == "recorded-not-followed"
    assert man["largest_file"]["path"] == "sub/nested.bin"
    assert man["ignored"] == [".restverify-marker"]   # our marker, not restored data
    assert payload["sample"]["algorithm"] == "sha256"
    assert payload["sample"]["digest"].startswith("sha256:")
    assert payload["sample"]["sampled_files"] == 2
    # no source is configured in this test, so the compare block is a real
    # skipped block, not a placeholder (I2c)
    assert payload["compare"]["implemented"] is True
    assert payload["compare"]["status"] == "skipped"
    assert payload["compare"]["reason"] == "no source saved"
    assert payload["status"] == "pass"
    assert "incomplete" not in payload           # the I2 label died at I3c
    assert payload["restore"]["target_removed"] is True
    assert list(Path(tmp_base).iterdir()) == []
    assert "not implemented for this command yet" not in captured.err


@pytest.mark.parametrize("count,expected", [
    (0, "0 B"), (1023, "1023 B"), (1024, "1.0 KiB"), (1505, "1.5 KiB"),
    (1024 ** 2, "1.0 MiB"), (1024 ** 3, "1.0 GiB"),
])
def test_human_bytes_units(count, expected):
    """R29: the byte-total format is contract, not incidental."""
    assert human_bytes(count) == expected
