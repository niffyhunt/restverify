"""Exclude-pattern matching shared by sampling and source comparison (R4).

restic's own matcher is authoritative for the *restore*: the excludes list is
handed to `restic restore --exclude ...` and restic decides. This module exists
so the *comparison* honours the same list without a repository.

Approximation (B1): it matches a path when a pattern matches the relative path,
its final component, or a directory prefix of it (so `cache/` prunes the
subtree). restic's exact semantics may differ; the symptom would be extra or
missing comparison entries, which I7 must validate against a real restic.
Patterns are matched against paths relative to the compared root, so an
absolute pattern is normalised by dropping its leading slash.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path


class ExcludeMatcher:
    """Pre-compiled pattern list; cheap to reuse across many paths."""

    def __init__(self, patterns) -> None:
        self.patterns = [p for p in (self._normalise(x) for x in patterns or []) if p]

    @staticmethod
    def _normalise(pattern) -> str:
        return str(pattern).strip().strip("/")

    @property
    def empty(self) -> bool:
        return not self.patterns

    def matches(self, relpath) -> bool:
        rel = str(relpath).strip("/")
        if not rel:
            return False
        name = Path(rel).name
        for pattern in self.patterns:
            if fnmatch.fnmatchcase(rel, pattern):
                return True
            if "/" not in pattern and fnmatch.fnmatchcase(name, pattern):
                return True
            if rel == pattern or rel.startswith(pattern + "/"):
                return True
        return False


def compile_matcher(patterns) -> ExcludeMatcher:
    return ExcludeMatcher(patterns)
