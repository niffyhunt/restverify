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
    "source", "manifest", "sample", "tempdir", "history", "interrupted",
})

# Ruling (I4): "history" was ADDED to the vocabulary after
# I3 froze it at ten members, because the durable store needs a kind of its own
# and no existing member fits (config -> 64 is a usage problem; the store is
# machine state whose failures are exit 1). That is an explicit amendment of
# I3 ruling 5, recorded in the I4 briefing.
#
# Exit-code policy for the store is asymmetric on purpose:
#   * `report` cannot read the store  -> HistoryError -> exit 1, kind "history";
#   * a `run` that verified cleanly but could not WRITE its row stays on the
#     verification's exit code and warns on stderr (history is auxiliary).
#     cli.py decides that, not this module (same boundary as --dry-run).

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


class HistoryError(RestverifyError):
    """The durable run history could not be read or written (R10, I4).

    Raised by history.py only. `report` treats it as fatal (exit 1); a `run`
    treats it as a warning and keeps the verification's exit code (ruling 2).
    """

    kind = "history"


def kind_of(exc: BaseException) -> str:
    """The closed-vocabulary kind for an exception. Never raises, never returns
    a value outside ``ERROR_KINDS`` (see FALLBACK_KIND)."""
    kind = getattr(exc, "kind", FALLBACK_KIND)
    return kind if kind in ERROR_KINDS else FALLBACK_KIND


# ── the exit-code map (I3b) ────────────────────────────────────────────────
#
# ONE table, consulted by every error path in the CLI. Every concrete
# RestverifyError subclass must appear here with its OWN key: a new subclass
# without an entry fails the exhaustive taxonomy test, which is what stops G2
# rotting as I4-I6 add error types. Subclassing a mapped class does not count
# as mapping it.
from . import EXIT_RESTORE_FAIL, EXIT_USAGE  # noqa: E402  (package constants)

CODE_BY_ERROR = {
    ResticMissing: EXIT_RESTORE_FAIL,
    ResticFailed: EXIT_RESTORE_FAIL,
    NoSnapshots: EXIT_RESTORE_FAIL,
    ConfigError: EXIT_USAGE,
    TempDirError: EXIT_RESTORE_FAIL,
    ManifestError: EXIT_RESTORE_FAIL,
    SampleError: EXIT_RESTORE_FAIL,
    SourceError: EXIT_RESTORE_FAIL,
    HistoryError: EXIT_RESTORE_FAIL,
}

# Kinds that have no exception class of their own: "usage" is raised by the
# argparse subclass, "interrupted" is a KeyboardInterrupt.
CODE_BY_KIND = {
    "usage": EXIT_USAGE,
    "interrupted": EXIT_RESTORE_FAIL,
}

# kind -> exit code, for every member of the closed vocabulary. I3c's contract
# test asserts that its keys are exactly ERROR_KINDS.
EXIT_CODE_BY_KIND = {klass.kind: code for klass, code in CODE_BY_ERROR.items()}
EXIT_CODE_BY_KIND.update(CODE_BY_KIND)

# Defensive only: an unmapped class must never traceback (U4). The taxonomy
# test makes this unreachable while it is green.
FALLBACK_EXIT_CODE = EXIT_RESTORE_FAIL


def error_classes() -> set[type]:
    """Every concrete RestverifyError subclass, however deeply nested."""
    found: set[type] = set()

    def walk(cls):
        for sub in cls.__subclasses__():
            found.add(sub)
            walk(sub)

    walk(RestverifyError)
    return found


def exit_code_for(exc: BaseException) -> int:
    """The contract exit code for an exception.

    Walks the MRO so a subclass of a mapped class inherits its parent's code at
    runtime, but the exhaustive test still demands an explicit entry for every
    concrete class. Never raises.
    """
    for klass in type(exc).__mro__:
        if klass in CODE_BY_ERROR:
            return CODE_BY_ERROR[klass]
    return FALLBACK_EXIT_CODE
