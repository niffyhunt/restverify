"""Increment I2b: deterministic sample hash (R9).

Same tree twice -> identical digest; one byte changed -> different digest; the
rule is applied exactly as documented (stride + largest + exclude tripwire), and
the digest is recomputable by a third party from the serialisation contract.
"""
import hashlib
import json
from pathlib import Path

import pytest

from restverify import EXIT_PASS, manifest as manifestmod, sampling as samplingmod
from restverify.cli import main
from restverify.errors import SampleError

SHIPPED = sorted((Path(__file__).resolve().parents[1] / "src" / "restverify").rglob("*.py"))
EMPTY_SHA = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def make_tree(count, tmp_path, big_index=0, suffix=".bin"):
    root = tmp_path / f"tree{count}-{big_index}{suffix}"
    root.mkdir(parents=True)
    for i in range(count):
        (root / f"f{i:04d}{suffix}").write_bytes(b"x" * (1 + i))
    (root / f"f{big_index:04d}{suffix}").write_bytes(b"y" * 10_000)
    return root


def sample_of(root, patterns=()):
    return samplingmod.sample_tree(manifestmod.build(root), root, patterns)


# ── determinism (R9) ───────────────────────────────────────────────────────

def test_same_tree_twice_has_the_same_digest(tmp_path):
    root = make_tree(3, tmp_path)
    first, second = sample_of(root), sample_of(root)
    assert first.digest == second.digest
    assert [f.path for f in first.files] == [f.path for f in second.files]


def test_one_byte_change_changes_the_digest(tmp_path):
    root = make_tree(3, tmp_path)
    before = sample_of(root)
    target = root / before.files[0].path
    target.write_bytes(target.read_bytes() + b"!")
    assert sample_of(root).digest != before.digest


def test_a_rename_changes_the_digest(tmp_path):
    root = make_tree(3, tmp_path)
    before = sample_of(root)
    (root / "f0001.bin").rename(root / "renamed.bin")
    assert sample_of(root).digest != before.digest


def test_digest_is_reproducible_from_the_documented_serialisation(tmp_path):
    """The contract, executable: a third party with the sample list gets the digest."""
    sample = sample_of(make_tree(5, tmp_path))
    raw = b"".join(f"{f.path}\0{f.size}\0{f.sha256}\n".encode("utf-8")
                   for f in sample.files)
    assert sample.digest == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_empty_tree_hashes_the_empty_input(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    sample = sample_of(root)
    assert sample.digest == EMPTY_SHA
    assert sample.files == []
    assert sample.total_files == 0


# ── the public rule ────────────────────────────────────────────────────────

def test_small_tree_samples_every_file(tmp_path):
    sample = sample_of(make_tree(3, tmp_path))
    assert sample.total_files == 3
    assert len(sample.files) == 3
    assert sample.stride is None


def test_large_tree_samples_by_sorted_stride(tmp_path):
    root = make_tree(500, tmp_path)
    chosen, stride, largest, extra, matched, total = samplingmod.select(
        manifestmod.build(root).entries)
    assert total == 500
    assert stride == 3                      # ceil(500 / 200)
    assert len(chosen) <= samplingmod.SAMPLE_LIMIT
    assert largest == "f0000.bin"
    assert extra == [] and matched == 0


def test_largest_file_is_always_sampled_even_off_stride(tmp_path):
    root = make_tree(500, tmp_path, big_index=7)
    sample = sample_of(root)
    assert sample.largest == "f0007.bin"
    assert "f0007.bin" in [f.path for f in sample.files]
    assert sample.stride == 3


def test_exclude_matching_files_are_a_tripwire(tmp_path):
    root = make_tree(4, tmp_path)
    (root / "should-not-be-here.log").write_bytes(b"leaked")
    sample = sample_of(root, patterns=["*.log"])
    assert "should-not-be-here.log" in [f.path for f in sample.files]
    assert sample.exclude_matches == ["should-not-be-here.log"]


def test_exclude_tripwire_is_capped(tmp_path):
    root = make_tree(300, tmp_path, suffix=".log")
    sample = sample_of(root, patterns=["*.log"])
    assert sample.exclude_matched_total == 300
    assert len(sample.exclude_matches) == samplingmod.EXCLUDE_SAMPLE_CAP
    assert len(sample.files) <= samplingmod.SAMPLE_LIMIT


# ── adversarial (G1) + single-module guarantee (G3) ───────────────────────

def test_symlink_swapped_after_the_manifest_is_a_teaching_error(tmp_path):
    """O_NOFOLLOW: a path that became a link is an error, never a read of the
    link target (which could be outside the restore tree entirely)."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    man = manifestmod.build(root)
    (root / "a.txt").unlink()
    (root / "a.txt").symlink_to("/etc/passwd")
    with pytest.raises(SampleError) as exc:
        samplingmod.sample_tree(man, root)
    assert "could not open" in exc.value.what
    assert "symlink" in exc.value.hint


def test_hashing_is_confined_to_sampling():
    hits = sorted(p.name for p in SHIPPED
                  if "hashlib" in p.read_text(encoding="utf-8"))
    assert hits == ["sampling.py"]


# ── public interface: help + --json + human line ──────────────────────────

def test_help_documents_the_sampling_rule_and_reproduction(capsys):
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    out = capsys.readouterr().out
    assert "sample sha256" in out
    assert "sorted-path stride" in out
    assert "reproduce" in out.lower()
    assert "empty" in out.lower()


def test_json_sample_block_is_reproducible_end_to_end(fake_restic, tmp_base,
                                                      config_path, capsys):
    code = main(["run", "-r", "/srv/backup", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_PASS
    sample = payload["sample"]
    assert sample["algorithm"] == "sha256"
    assert sample["digest"].startswith("sha256:")
    assert sample["sampled_files"] == 2 and sample["total_files"] == 2
    assert sample["rule"] == samplingmod.RULE
    assert [f["path"] for f in sample["files"]] == ["restored.txt", "sub/nested.bin"]
    raw = b"".join(f"{f['path']}\0{f['size']}\0{f['sha256']}\n".encode("utf-8")
                   for f in sample["files"])
    assert sample["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_run_human_line_prints_the_digest(fake_restic, tmp_base, config_path, capsys):
    main(["run", "-r", "/srv/backup"])
    out = capsys.readouterr().out
    assert "sha256:" in out
    assert "2 of 2 files" in out
    assert "sample sha256" not in out          # that is help text, not run output
