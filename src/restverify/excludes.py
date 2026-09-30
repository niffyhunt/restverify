"""Exclude-pattern matching shared by sampling and source comparison (R4).

restic's own matcher is authoritative for the *restore*: the excludes list is
handed to `restic restore --exclude ...` and restic decides. This module exists
so the *comparison* honours the same list without a repository.

Validated against a real restic 0.16.4 in I7b (I7 briefing, section 5, item 5).
restic matches a pattern against the path it is filtering, then retries with one
leading path component stripped at a time - so a pattern may be written relative
to the backup root, relative to any ancestor of it, or absolute - and its globs
are Go's `filepath.Match`: `*` and `?` do not cross `/`, `**` does, and `**/`
also matches zero segments. Measured on a source at <scratch-root>/i7-scratch/srcB
holding app.log, cache/blob.bin, data/keep.txt and nested/trace.log:

    *.log                        -> app.log, nested/trace.log
    data/*.txt                   -> data/keep.txt
    srcB/data/*.txt              -> data/keep.txt   (root-prefixed: matched)
    <scratch-root>/i7-scratch/srcB/...    -> data/keep.txt   (absolute: matched)
    keep.txt                     -> data/keep.txt
    cache/ , cache , *cache*     -> the cache subtree
    srcB/data*keep.txt           -> nothing         (* does not cross /)
    srcB/**/*.log                -> app.log, nested/trace.log

Before I7b only root-relative patterns were understood, so a root-prefixed or
absolute pattern made restic prune the restore while this matcher kept walking
the source: a healthy restore was reported as a mismatch (exit 2). Pass the
walk root - `compile_matcher(patterns, root=...)` - and the two agree.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

# A character class is only honoured when its body is plainly safe; anything
# else is treated as a literal '[' so a hostile or malformed pattern can never
# raise out of the matcher.
_SAFE_CLASS_BODY = re.compile(r"\A[A-Za-z0-9_.^-]+\Z")


def _translate(pattern: str) -> re.Pattern:
    """Translate a restic glob into a regex with Go's filepath.Match rules."""
    out = ["(?s)\\A"]
    index, size = 0, len(pattern)
    while index < size:
        char = pattern[index]
        if char == "*":
            if pattern[index:index + 2] == "**":
                out.append(".*")
                index += 2
                if index < size and pattern[index] == "/":
                    out.append("/?")      # '**/' also matches zero segments
                    index += 1
                continue
            out.append("[^/]*")
            index += 1
            continue
        if char == "?":
            out.append("[^/]")
            index += 1
            continue
        if char == "[":
            end = index + 1
            if end < size and pattern[end] in "!^":
                end += 1
            if end < size and pattern[end] == "]":
                end += 1
            while end < size and pattern[end] != "]":
                end += 1
            body = pattern[index + 1:end]
            if end < size and body and _SAFE_CLASS_BODY.match(body[1:] if body[0] in "!^" else body):
                if body[0] in "!^":
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                index = end + 1
                continue
            out.append(re.escape(char))    # unterminated or unsafe: a literal
            index += 1
            continue
        out.append(re.escape(char))
        index += 1
    out.append("\\Z")
    return re.compile("".join(out))


class ExcludeMatcher:
    """Pre-compiled pattern list; cheap to reuse across many paths."""

    def __init__(self, patterns, root=None) -> None:
        self.patterns = [p for p in (self._normalise(x) for x in patterns or []) if p]
        # The walk root is a filesystem path; the candidate matching below is
        # defined in POSIX terms (that is restic's own world), so Windows
        # separators are normalised here or the leading-strip retries never
        # reach the root-relative form of a pattern.
        self._root = (str(root).replace("\\", "/").rstrip("/")
                      if root is not None else "")
        compiled = []
        for pattern in self.patterns:
            compiled.append((pattern, _translate(pattern)))
            if pattern.startswith("/"):
                # A caller with no root cannot see the absolute prefix, so the
                # same pattern is also tried relative to the walk root (the
                # pre-I7b behaviour, kept so nothing that relied on it flips).
                compiled.append((pattern, _translate(pattern.lstrip("/"))))
        self._compiled = compiled

    @staticmethod
    def _normalise(pattern) -> str:
        # Trailing slashes are dropped so `cache/` and `cache` mean the same
        # thing; a leading slash is KEPT, because restic accepts absolute
        # patterns (I7b).
        text = str(pattern).strip()
        while text.endswith("/"):
            text = text[:-1]
        return text

    @property
    def empty(self) -> bool:
        return not self.patterns

    def _candidates(self, rel: str) -> tuple[str, ...]:
        """The path restic would filter, then each leading-stripped retry."""
        if not self._root:
            return (rel,)
        full = f"{self._root}/{rel}"
        out = [full]
        while "/" in full:
            full = full.split("/", 1)[1]
            out.append(full)
        return tuple(out)

    def _probe_paths(self, rel: str) -> tuple[str, ...]:
        """The path and each of its parent directories, restic-style.

        restic rejects a file when the file *or any parent directory* matches,
        which is how `--exclude '*cache*'` prunes a cache/ subtree even though
        the pattern never mentions the file. The walk in manifest.build prunes
        at the directory anyway, so this only matters when a path is judged on
        its own (sampling's tripwire view, and these tests).
        """
        parts = rel.split("/")
        out: list[str] = []
        for index in range(len(parts), 0, -1):
            out.extend(self._candidates("/".join(parts[:index])))
        return tuple(out)

    def matches(self, relpath) -> bool:
        rel = str(relpath).strip("/")
        if not rel:
            return False
        name = Path(rel).name
        candidates = self._probe_paths(rel)
        for pattern, regex in self._compiled:
            if any(regex.match(candidate) for candidate in candidates):
                return True
            if "/" not in pattern and fnmatch.fnmatchcase(name, pattern):
                return True
            if rel == pattern or rel.startswith(pattern + "/"):
                return True
        return False


def compile_matcher(patterns, root=None) -> ExcludeMatcher:
    """Compile ``patterns`` for walks rooted at ``root`` (see the module notes)."""
    return ExcludeMatcher(patterns, root=root)
