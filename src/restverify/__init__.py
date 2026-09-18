"""restverify — rehearse and verify restic restores locally.

Scope contract (GAP-RESEARCH-2026-09.pdf): this tool RESTORES and VERIFIES.
It never creates backups, never runs forget/prune, and never asks for a
provider account. Passwords are never stored by this tool.
"""

__version__ = "0.1.0.dev0"

# Exit-code contract (PDF, normative). Exposed as constants so tests and
# callers reference the contract rather than magic numbers.
EXIT_PASS = 0            # restore + verification succeeded
EXIT_RESTORE_FAIL = 1    # restic could not restore the snapshot
EXIT_DIFF_MISMATCH = 2   # restored data differs from the source
