"""Excludes-aware source comparison (R4, R9, R11, R13, R14, R26).

Read-only in both directions: the source is walked with ``scandir``/``stat``
and sampled with ``O_NOFOLLOW`` reads; nothing is ever created, written or
deleted on either side. It is the caller's job to have already disposed of the
restore temp dir, so a comparison failure cannot leave one behind.

What is compared
  * the file/symlink/other entry set (missing or extra paths fail);
  * per-entry kind, size, and symlink target;
  * file count, total bytes and symlink count;
  * the sampled sha256 of every file in the sample set (same public rule and
    excludes on both sides, so the two digests are comparable).
Not compared: mtimes, directory ordering, and the *content* of files outside
the sample - that limit is the documented price of a bounded scheduled run.

Warning class (confirmed ruling Q1): divergence with no data difference - an
empty directory present on only one side. Warnings are printed and do not fail
the run unless ``--strict`` promotes them. Any difference in data fails with
exit 2 unconditionally.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import excludes as excludesmod
from . import manifest as manifestmod
from . import sampling as samplingmod
from .errors import ManifestError, SourceError
from .manifest import KIND_FILE, KIND_SYMLINK
from . import EXIT_DIFF_MISMATCH, EXIT_PASS

# kinds that already explain a digest difference (so the digest guard stays quiet)
_DATA_KINDS = frozenset({
    "count", "bytes", "symlink_count", "missing_from_restore",
    "missing_from_source", "kind", "symlink_target", "size", "content",
})


@dataclass
class Diff:
    path: str            # "" for whole-tree findings (counts, digest guard)
    kind: str
    detail: str
    severity: str = "error"

    def to_json(self) -> dict:
        return {"path": self.path, "kind": self.kind, "detail": self.detail,
                "severity": self.severity}


@dataclass
class Comparison:
    enabled: bool = False
    status: str = "skipped"          # match | mismatch | skipped
    reason: str = ""
    source: str | None = None
    strict: bool = False
    errors: list[Diff] = field(default_factory=list)
    warnings: list[Diff] = field(default_factory=list)
    compared_files: int = 0
    restored_digest: str | None = None
    source_digest: str | None = None

    @property
    def failed(self) -> bool:
        return bool(self.errors) or (self.strict and bool(self.warnings))

    def exit_code(self) -> int:
        return EXIT_DIFF_MISMATCH if self.failed else EXIT_PASS

    def to_json(self) -> dict:
        return {
            "implemented": True,
            "enabled": self.enabled,
            "status": self.status,
            "reason": self.reason or None,
            "source": self.source,
            "strict": self.strict,
            "compared_files": self.compared_files,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "digests": {"restored": self.restored_digest, "source": self.source_digest},
            "errors": [d.to_json() for d in self.errors],
            "warnings": [d.to_json() for d in self.warnings],
        }


def skipped(reason: str, source: str | None = None) -> Comparison:
    return Comparison(enabled=False, status="skipped", reason=reason, source=source)



def _describe(entry) -> str:
    if entry.kind == KIND_SYMLINK:
        return f"symlink -> {entry.link_target!r}"
    if entry.kind == KIND_FILE:
        return f"file, {entry.size} B"
    return f"{entry.kind} entry"


def _entry_diffs(restored_man, source_man) -> list[Diff]:
    restored = {e.path: e for e in restored_man.entries}
    source = {e.path: e for e in source_man.entries}
    diffs: list[Diff] = []
    for path in sorted(set(restored) | set(source)):
        mine, theirs = restored.get(path), source.get(path)
        if theirs is None:
            diffs.append(Diff(path, "missing_from_source",
                              f"restored {_describe(mine)}; the source has no such path"))
        elif mine is None:
            diffs.append(Diff(path, "missing_from_restore",
                              f"the source has {_describe(theirs)}; the restore does not"))
        elif mine.kind != theirs.kind:
            diffs.append(Diff(path, "kind",
                              f"restored kind '{mine.kind}', source kind '{theirs.kind}'"))
        elif mine.kind == KIND_SYMLINK and mine.link_target != theirs.link_target:
            diffs.append(Diff(path, "symlink_target",
                              f"restored -> {mine.link_target!r}, source -> {theirs.link_target!r}"))
        elif mine.kind == KIND_FILE and mine.size != theirs.size:
            diffs.append(Diff(path, "size",
                              f"restored {mine.size} B, source {theirs.size} B"))
    return diffs


def _totals_diffs(restored_man, source_man) -> list[Diff]:
    diffs: list[Diff] = []
    if restored_man.file_count != source_man.file_count:
        diffs.append(Diff("", "count", f"restored {restored_man.file_count} file(s), "
                                      f"source {source_man.file_count} file(s)"))
    if restored_man.total_bytes != source_man.total_bytes:
        diffs.append(Diff("", "bytes", f"restored {restored_man.total_bytes} B total, "
                                       f"source {source_man.total_bytes} B total"))
    if restored_man.symlink_count != source_man.symlink_count:
        diffs.append(Diff("", "symlink_count",
                          f"restored {restored_man.symlink_count} symlink(s), "
                          f"source {source_man.symlink_count} symlink(s)"))
    return diffs


def _sample_diffs(restored_sample, source_sample, already_flagged) -> list[Diff]:
    mine = {f.path: f.sha256 for f in restored_sample.files}
    theirs = {f.path: f.sha256 for f in source_sample.files}
    diffs: list[Diff] = []
    for path in sorted(set(mine) | set(theirs)):
        if path in already_flagged or mine.get(path) == theirs.get(path):
            continue
        diffs.append(Diff(path, "content",
                          "the sampled sha256 differs (path and size are identical)"))
    return diffs


def _structure_warnings(restored_man, source_man) -> list[Diff]:
    """Divergence with no data difference: an empty directory on only one side."""
    restored_dirs = set(restored_man.directories)
    source_dirs = set(source_man.directories)
    data_paths = {e.path for tree in (restored_man, source_man) for e in tree.entries}
    warnings: list[Diff] = []
    for path in sorted((restored_dirs ^ source_dirs) - {""}):
        prefix = path + "/"
        if any(p == path or p.startswith(prefix) for p in data_paths):
            continue      # the data difference is already an error; no double report
        side = "restore" if path in restored_dirs else "source"
        warnings.append(Diff(path, "empty_directory",
                             f"empty directory present only in the {side}",
                             severity="warning"))
    return warnings


def compare(restored_man, restored_sample, source_root, patterns=(), strict=False) -> Comparison:
    """Compare a restored tree against its source, honouring the same excludes."""
    matcher = excludesmod.compile_matcher(patterns)
    source_path = Path(str(source_root)).expanduser()
    if not source_path.exists():
        raise SourceError(
            f"the comparison source {source_path} does not exist.",
            hint="fix the source path (`restverify init -r <repo> -s <path>`) or "
                 "verify without it: `restverify run -r <repo> --no-source`",
        )
    if not source_path.is_dir():
        raise SourceError(
            f"the comparison source {source_path} is not a directory.",
            hint="point -s/--source at the directory the snapshot was taken from, "
                 "or pass --no-source",
        )
    try:
        source_man = manifestmod.build(source_path, exclude=matcher)
    except ManifestError as exc:
        raise SourceError(
            f"the comparison source {source_path} could not be read: {exc.what}",
            hint=exc.hint,
        ) from exc
    source_sample = samplingmod.sample_tree(source_man, source_path, patterns)

    errors = _entry_diffs(restored_man, source_man)
    errors += _totals_diffs(restored_man, source_man)
    flagged = {d.path for d in errors if d.path}
    errors += _sample_diffs(restored_sample, source_sample, flagged)
    warnings = _structure_warnings(restored_man, source_man)
    # Defensive: a digest disagreement must never be a PASS, even if we could
    # not name the file. (Reached only in a pathological case.)
    if restored_sample.digest != source_sample.digest and not errors:
        errors.append(Diff("", "digest",
                           "the sample digests differ but no individual difference "
                           "was identified"))

    comparison = Comparison(
        enabled=True,
        source=str(source_path),
        strict=bool(strict),
        errors=errors,
        warnings=warnings,
        compared_files=len(set(restored_man.directories) | set(source_man.directories)),
        restored_digest=restored_sample.digest,
        source_digest=source_sample.digest,
    )
    comparison.status = "mismatch" if comparison.failed else "match"
    return comparison
