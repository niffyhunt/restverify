"""Restored-tree manifest (R8) — counts, bytes and a per-directory roll-up.

This is the evidence that a restore actually landed something, and how much:
file count, byte totals, a per-directory roll-up and the deepest directory
observed. Building the manifest is intentionally cheap — it uses metadata
(``scandir``/``stat``) only and **never reads file contents**. Content hashing
is the sampled digest's job (I2b), which keeps the expensive I/O in one place.

Symlink policy: **recorded, never followed.** A link is evidence of what the
repository held; following one could read data outside the restore target
(which would defeat the read-only and excludes guarantees) or loop forever.
Classification uses ``follow_symlinks=False``, so a symlink to a directory is
one link entry, not a subtree.

Deep nesting: the walk is iterative, not recursive, and imposes **no depth
limit**. ``max_depth`` reports the deepest directory observed so a reader can
see how deep the tree really went; the only ceiling is the operating system's
path-length limit, which the filesystem enforces before we ever see an entry.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .errors import ManifestError

MANIFEST_VERSION = 1
KIND_FILE = "file"
KIND_SYMLINK = "symlink"
KIND_OTHER = "other"           # fifo/socket/device: recorded so nothing is hidden
SYMLINK_POLICY = "recorded-not-followed"


@dataclass
class Entry:
    """One non-directory entry in the restored tree."""

    path: str                  # POSIX-style path relative to the manifest root
    kind: str
    size: int                  # regular-file bytes; 0 for links and other
    link_target: str | None = None

    def to_json(self) -> dict:
        data = {"path": self.path, "kind": self.kind, "size": self.size}
        if self.kind == KIND_SYMLINK:
            data["link_target"] = self.link_target
        return data


@dataclass
class DirStat:
    """Direct children of one directory (recursive totals are rolled up later)."""

    files: int = 0
    bytes: int = 0
    subdirs: int = 0


@dataclass
class Manifest:
    root: str
    entries: list[Entry] = field(default_factory=list)
    directories: dict[str, DirStat] = field(default_factory=dict)
    max_depth: int = 0
    ignored: list[str] = field(default_factory=list)   # top-level names we own
    excluded: list[str] = field(default_factory=list)  # paths the excludes pruned

    @property
    def file_count(self) -> int:
        return sum(1 for e in self.entries if e.kind == KIND_FILE)

    @property
    def symlink_count(self) -> int:
        return sum(1 for e in self.entries if e.kind == KIND_SYMLINK)

    @property
    def other_count(self) -> int:
        return sum(1 for e in self.entries if e.kind == KIND_OTHER)

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries if e.kind == KIND_FILE)

    @property
    def directory_count(self) -> int:
        return len(self.directories)

    def largest_file(self) -> Entry | None:
        files = [e for e in self.entries if e.kind == KIND_FILE]
        if not files:
            return None
        return max(files, key=lambda e: (e.size, e.path))

    def directory_rollup(self) -> dict:
        """Direct counts plus recursive totals per directory (R8).

        Deepest-first so a child's recursive totals are final before its parent
        absorbs them; ties at the same depth are independent by construction.
        """
        out = {
            path: {
                "files": stat.files,
                "bytes": stat.bytes,
                "subdirs": stat.subdirs,
                "files_recursive": stat.files,
                "bytes_recursive": stat.bytes,
            }
            for path, stat in self.directories.items()
        }
        for path in sorted(out, key=lambda p: p.count("/"), reverse=True):
            if not path:
                continue
            parent = path.rpartition("/")[0]
            if parent in out:
                out[parent]["files_recursive"] += out[path]["files_recursive"]
                out[parent]["bytes_recursive"] += out[path]["bytes_recursive"]
        return out

    def to_json(self) -> dict:
        largest = self.largest_file()
        return {
            "version": MANIFEST_VERSION,
            "root": self.root,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "symlink_count": self.symlink_count,
            "other_count": self.other_count,
            "directory_count": self.directory_count,
            "max_depth": self.max_depth,
            "symlink_policy": SYMLINK_POLICY,
            "directories": self.directory_rollup(),
            "largest_file": largest.to_json() if largest else None,
            "ignored": list(self.ignored),
            "excluded": list(self.excluded),
        }



def build(root: Path | str, ignore_top_level: Iterable[str] = (),
          exclude=None) -> Manifest:
    """Walk ``root`` and record every file, symlink and special entry.

    Never follows a symlink and never reads file contents. ``ignore_top_level``
    names entries restverify itself owns in the restore dir (the tempstore
    marker); they are skipped **by name at the top level only** and listed in
    ``manifest.ignored``, so the skip is visible rather than silent.
    ``exclude`` is an excludes.ExcludeMatcher; matching entries are skipped and
    matching directories are **not descended into** (so an excluded directory
    prunes the same subtree restic pruned), recorded in ``manifest.excluded``.
    Raises ``ManifestError`` (a teaching error) if the tree itself or any
    directory becomes unreadable, so a partial walk can never masquerade as a
    clean one.
    """
    base = Path(root)
    if not base.is_dir():
        raise ManifestError(
            f"the restored tree {base} is not a directory, so it cannot be read.",
            hint="this usually means restic restored nothing; re-run with "
                 "`restverify run -r <repo> --dry-run` to inspect the plan",
        )

    ignored_names = set(ignore_top_level)
    result = Manifest(root=str(base))
    result.directories[""] = DirStat()
    stack: list[tuple[Path, str, int]] = [(base, "", 0)]

    while stack:
        directory, rel_dir, depth = stack.pop()
        result.max_depth = max(result.max_depth, depth)
        try:
            with os.scandir(directory) as handle:
                children = sorted(handle, key=lambda entry: entry.name)
        except OSError as exc:
            raise ManifestError(
                f"could not read {directory} while building the manifest: "
                f"{exc.strerror or exc}",
                hint="check permissions on the restore temp dir; if the tree "
                     "vanished, the restore itself did not complete",
            ) from exc

        for child in children:
            rel = f"{rel_dir}/{child.name}" if rel_dir else child.name
            if not rel_dir and child.name in ignored_names:
                result.ignored.append(rel)
                continue
            if exclude is not None and exclude.matches(rel):
                result.excluded.append(rel)
                continue      # matching directories are pruned, not descended
            if child.is_symlink():
                try:
                    target = os.readlink(child.path)
                except OSError as exc:
                    raise ManifestError(
                        f"could not read the symlink {child.path}: "
                        f"{exc.strerror or exc}",
                        hint="re-run; if it persists the restore did not complete",
                    ) from exc
                result.entries.append(
                    Entry(path=rel, kind=KIND_SYMLINK, size=0, link_target=target))
                continue
            if child.is_dir(follow_symlinks=False):
                result.directories.setdefault(rel, DirStat())
                result.directories[rel_dir].subdirs += 1
                stack.append((Path(child.path), rel, depth + 1))
                continue
            if child.is_file(follow_symlinks=False):
                try:
                    size = child.stat(follow_symlinks=False).st_size
                except OSError as exc:
                    raise ManifestError(
                        f"could not stat {child.path}: {exc.strerror or exc}",
                        hint="re-run; if it persists the restore did not complete",
                    ) from exc
                result.entries.append(Entry(path=rel, kind=KIND_FILE, size=size))
                stat = result.directories[rel_dir]
                stat.files += 1
                stat.bytes += size
                continue
            # fifo/socket/device: not files, but not hidden either
            result.entries.append(Entry(path=rel, kind=KIND_OTHER, size=0))

    # deterministic order is a precondition for the I2b digest
    result.entries.sort(key=lambda entry: entry.path)
    return result
