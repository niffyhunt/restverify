"""Error translation (usability law U2/U4: no raw tracebacks reach the user).

Every user-facing failure must state (a) what happened, (b) the most likely
cause, (c) the exact next command to try. Tracebacks are bugs.
"""
from __future__ import annotations


class RestverifyError(Exception):
    """Base class for errors that are safe and useful to show a user.

    ``hint`` is a single line naming the next command to try (U2c).
    """

    def __init__(self, what: str, hint: str | None = None) -> None:
        super().__init__(what)
        self.what = what
        self.hint = hint

    def render(self) -> str:
        if self.hint:
            return f"{self.what}\n  next: {self.hint}"
        return self.what


class ResticMissing(RestverifyError):
    pass


class ResticFailed(RestverifyError):
    """restic exited non-zero. Carries the tail of its stderr for the report."""

    def __init__(self, what: str, stderr_tail: str = "", hint: str | None = None) -> None:
        super().__init__(what, hint)
        self.stderr_tail = stderr_tail


class ConfigError(RestverifyError):
    pass
