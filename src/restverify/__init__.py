"""restverify — rehearse and verify restic restores locally.

Scope contract (GAP-RESEARCH-2026-09.pdf): this tool RESTORES and VERIFIES.
It never creates backups, never runs forget/prune, and never asks for a
provider account. Passwords are never stored by this tool.
"""

__version__ = "0.2.1"

# Exit-code contract (PDF, normative). Exposed as constants so tests and
# callers reference the contract rather than magic numbers.
EXIT_PASS = 0            # restore + verification succeeded
EXIT_RESTORE_FAIL = 1    # restic could not restore the snapshot
EXIT_DIFF_MISMATCH = 2   # restored data differs from the source

# Usage-class failures (bad flag, unusable config, missing prerequisite) are NOT
# verification outcomes, so they must not borrow 1 or 2. 64 is the conventional
# "usage error" code from sysexits.h. Documented in --help and reported as a
# deliberate extension of the PDF contract (which only names 0/1/2).
EXIT_USAGE = 64

# Public JSON envelope contract (I3c). Every payload emitted by --json carries
# this number. A breaking shape change increments it; it is never edited
# silently. The envelope itself is documented in `restverify run --help`.
SCHEMA_VERSION = 1
