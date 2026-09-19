"""Deterministic sample hash of a tree (R9, R8's "sample sha256").

Full-tree hashing is unbounded; a sample that is *deterministic and documented*
is what makes "0 diffs" mean something without reading every byte on every
scheduled run. The rule is public (it is printed by ``run --help``):

  * every file when the tree holds <= SAMPLE_LIMIT files, otherwise
    every ``stride = ceil(total / SAMPLE_LIMIT)``-th file in sorted-path order
    (so the sample is <= SAMPLE_LIMIT files and independent of readdir order);
  * always the largest file (by size, then path) - a truncated large file is
    exactly the restore failure a stride can miss;
  * plus files matching the excludes patterns (capped at
    EXCLUDE_SAMPLE_CAP, first by path) - a tripwire: an excluded file that
    leaked into the restore changes the digest even though the compare skips it.

Digest serialisation (byte-exact, documented so a third party can reproduce):

    sha256( for each sampled file, in sorted-path order:
              <relative path> + NUL + <decimal size> + NUL +
              <file sha256 hex> + LF )

The empty tree hashes the empty byte string (sha256 e3b0c442...). Paths are
relative to the tree root, so the same rule can be applied to a source tree
and compared. Reads go through ``O_NOFOLLOW``: a path that became a symlink
after the manifest was built is an error, never a silent read outside the tree.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import excludes as excludesmod
from .errors import SampleError
from .manifest import KIND_FILE

SAMPLE_LIMIT = 200
EXCLUDE_SAMPLE_CAP = 50
READ_CHUNK = 1024 * 1024
DIGEST_PREFIX = "sha256:"

RULE = (
    f"every file if <= {SAMPLE_LIMIT} files, else sorted-path stride "
    f"ceil(total/{SAMPLE_LIMIT}); always the largest file (size, then path); "
    f"plus up to {EXCLUDE_SAMPLE_CAP} files matching the excludes patterns"
)
REPRODUCE = (
    "reproduce: apply the same rule, then sha256 the serialised lines "
    "'path NUL size NUL file-sha256 LF' in sorted-path order "
    "(the sampled list is in --json)"
)


@dataclass
class SampledFile:
    path: str
    size: int
    sha256: str

    def to_json(self) -> dict:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


@dataclass
class Sample:
    digest: str
    files: list[SampledFile] = field(default_factory=list)
    total_files: int = 0
    stride: int | None = None
    largest: str | None = None
    exclude_matches: list[str] = field(default_factory=list)
    exclude_matched_total: int = 0
    rule: str = RULE

    def to_json(self) -> dict:
        return {
            "algorithm": "sha256",
            "digest": self.digest,
            "rule": self.rule,
            "reproduce": REPRODUCE,
            "total_files": self.total_files,
            "sampled_files": len(self.files),
            "stride": self.stride,
            "largest": self.largest,
            "exclude_matches": list(self.exclude_matches),
            "exclude_matched_total": self.exclude_matched_total,
            "files": [f.to_json() for f in self.files],
        }


def serialise(files) -> bytes:
    """The documented digest input; keep byte-for-byte stable."""
    return b"".join(
        f"{f.path}\0{f.size}\0{f.sha256}\n".encode("utf-8") for f in files
    )


def digest_of(files) -> str:
    return DIGEST_PREFIX + hashlib.sha256(serialise(files)).hexdigest()



def select(entries, patterns=()):
    """Apply the public rule.

    Returns ``(chosen, stride, largest, exclude_matches, matched_total, total)``
    where ``chosen`` is sorted by path and ``exclude_matches`` lists the sampled
    files matching the excludes patterns (first EXCLUDE_SAMPLE_CAP by path) -
    the tripwire view, whether or not the file was already stride-selected.
    """
    files = sorted((e for e in entries if e.kind == KIND_FILE), key=lambda e: e.path)
    total = len(files)
    chosen: dict[str, object] = {}
    stride: int | None = None
    if total <= SAMPLE_LIMIT:
        for entry in files:
            chosen[entry.path] = entry
    else:
        stride = -(-total // SAMPLE_LIMIT)          # ceil, no float
        for entry in files[::stride]:
            chosen[entry.path] = entry

    largest: str | None = None
    if files:
        biggest = max(files, key=lambda e: (e.size, e.path))
        largest = biggest.path
        chosen[biggest.path] = biggest

    matcher = excludesmod.compile_matcher(patterns)
    exclude_matches: list[str] = []
    matched_total = 0
    if not matcher.empty:
        added = 0
        for entry in files:
            if not matcher.matches(entry.path):
                continue
            matched_total += 1
            if entry.path not in chosen and added < EXCLUDE_SAMPLE_CAP:
                chosen[entry.path] = entry
                added += 1

    ordered = [chosen[key] for key in sorted(chosen)]
    if not matcher.empty:
        exclude_matches = [e.path for e in ordered if matcher.matches(e.path)]
        exclude_matches = exclude_matches[:EXCLUDE_SAMPLE_CAP]
    return ordered, stride, largest, exclude_matches, matched_total, total


def _file_sha256(path: Path) -> str:
    """Hash one file without ever following a symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise SampleError(
            f"could not open {path} to hash it: {exc.strerror or exc}",
            hint="if that path is now a symlink the tree changed under us; re-run "
                 "the verification",
        ) from exc
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "rb") as handle:
            for chunk in iter(lambda: handle.read(READ_CHUNK), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SampleError(
            f"could not read {path} while hashing it: {exc.strerror or exc}",
            hint="re-run; if it persists the restore did not complete",
        ) from exc
    return digest.hexdigest()


def sample_tree(manifest, root, patterns=()) -> Sample:
    """Hash the selected files of one tree and return the reproducible digest."""
    chosen, stride, largest, exclude_matches, matched_total, total = select(
        manifest.entries, patterns)
    base = Path(root)
    sampled = [
        SampledFile(path=entry.path, size=entry.size,
                    sha256=_file_sha256(base / entry.path))
        for entry in chosen
    ]
    return Sample(
        digest=digest_of(sampled),
        files=sampled,
        total_files=total,
        stride=stride,
        largest=largest,
        exclude_matches=exclude_matches,
        exclude_matched_total=matched_total,
    )
