"""Error translation (usability law U2/U4: no raw tracebacks reach the user).

Every user-facing failure must state (a) what happened, (b) the most likely
cause, (c) the exact next command to try. Tracebacks are bugs.

The error-kind vocabulary is **closed** (I3 ruling 5):

    usage | config | restic_missing | restic_failed | no_snapshots |
    source | manifest | sample | tempdir | interrupted

``diff_mismatch`` is deliberately **not** a kind: a data mismatch is a normal
run outcome — it uses the run envelope with ``"status": "diff_mismatch"`` and
exit 2 — not an error object. Do not add it here, and do not add a
``MismatchError`` class.

Exit codes are not assigned here: the single ``CODE_BY_ERROR`` table arrives in
I3b, so that "every subclass is mapped" can be tested exhaustively.
"""
from __future__ import annotations

ERROR_KINDS = frozenset({
    "usage", "config", "restic_missing", "restic_failed", "no_snapshots",
    "source", "manifest", "sample", "tempdir", "interrupted",
})

# Defensive default for the JSON envelope only. A subtype without its own kind
# is a bug that I3b's exhaustive test must catch; until then this keeps a
# traceback from reaching the user (U4) instead of blowing up mid-report.
FALLBACK_KIND = "restic_failed"


class RestverifyError(Exception):
    """Base class for errors that are safe and useful to show a user.

    ``hint`` is a single line naming the next command to try (U2c).
    ``kind`` is the closed-vocabulary member this failure reports as JSON.
    """

    kind: str = FALLBACK_KIND

    def __init__(self, what: str, hint: str | None = None) -> None:
        super().__init__(what)
        self.what = what
        self.hint = hint

    def render(self) -> str:
        if self.hint:
            return f"{self.what}\n  next: {self.hint}"
        return self.what


class ResticMissing(RestverifyError):
    kind = "restic_missing"


class ResticFailed(RestverifyError):
    """restic exited non-zero. Carries the tail of its stderr for the report."""

    kind = "restic_failed"

    def __init__(self, what: str, stderr_tail: str = "", hint: str | None = None) -> None:
        super().__init__(what, hint)
        self.stderr_tail = stderr_tail


class NoSnapshots(RestverifyError):
    """The repository, or the requested selector, yielded no snapshots.

    A reportable state, not a crash: exit 1 with kind ``no_snapshots``.
    """

    kind = "no_snapshots"


class ConfigError(RestverifyError):
    kind = "config"


class TempDirError(RestverifyError):
    """The restore directory could not be created (permissions, full disk)."""

    kind = "tempdir"


class ManifestError(RestverifyError):
    """The restored tree could not be walked (permissions, vanished entry)."""

    kind = "manifest"


class SampleError(RestverifyError):
    """A sampled file could not be read for hashing (vanished, symlinked, I/O)."""

    kind = "sample"


class SourceError(RestverifyError):
    """The comparison source is missing, unreadable, or not a directory (R14)."""

    kind = "source"


def kind_of(exc: BaseException) -> str:
    """The closed-vocabulary kind for an exception. Never raises, never returns
    a value outside ``ERROR_KINDS`` (see FALLBACK_KIND)."""
    kind = getattr(exc, "kind", FALLBACK_KIND)
    return kind if kind in ERROR_KINDS else FALLBACK_KIND
