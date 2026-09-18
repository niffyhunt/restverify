"""Command-line surface for restverify.

Usability law U3: --help is documentation. The top-level help must let a user
succeed without opening the README, and every subcommand shows examples.

Usability law U1: zero-config-first. `restverify run -r <repo>` is a complete
first experience; the config file is for extra repos and power users only.

Usability law U2: failures teach. Nothing below raises a bare traceback at the
user; every error path returns a code and a next command.
"""
from __future__ import annotations

import argparse
import sys

from . import (__version__, EXIT_DIFF_MISMATCH, EXIT_PASS, EXIT_RESTORE_FAIL)

PROG = "restverify"

DESCRIPTION = (
    "Rehearse and verify restic restores locally, on a schedule, with a "
    "diff-proof report.\n\n"
    "restverify RESTORES and VERIFIES. It never creates backups, never runs "
    "forget/prune, and never stores your password."
)

EPILOG = """examples:
  restverify run -r /srv/backup            verify the newest snapshot (start here)
  restverify run -r /srv/backup --dry-run  show what would be restored, restore nothing
  restverify run --json                    machine-readable result for cron/CI
  restverify init                          add repos/watch paths to the config
  restverify report                        run history and trend, failures in plain words
  restverify cron                          print a ready-to-paste crontab line

exit codes:
  0  restore verified (or all repos verified, for a multi-repo run)
  1  restic could not restore the snapshot
  2  restored data differs from the source

next: run `restverify run -r <repo>` to verify your first snapshot
"""


class _Parser(argparse.ArgumentParser):
    """Parser that reports usage errors as teaching errors (U2), not tracebacks."""

    def error(self, message: str):  # pragma: no cover - exercised via CLI tests
        self.print_usage(sys.stderr)
        print(f"{PROG}: error: {message}", file=sys.stderr)
        print(f"  next: {PROG} --help", file=sys.stderr)
        raise SystemExit(2)


def _pending(cmd: str, increment: str) -> int:
    """Honest scaffold state (gate G6): name what is missing and where it lands."""
    print(
        f"{PROG}: '{cmd}' is not implemented yet in this build.\n"
        f"  why:   scaffold builds the CLI surface and contract first\n"
        f"  next:  implemented in increment {increment}; see docs/PHASE2-PLAN.md",
        file=sys.stderr,
    )
    return EXIT_RESTORE_FAIL


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true",
                   help="print a complete machine-readable result on stdout")
    p.add_argument("--config", metavar="PATH", default=None,
                   help="config file to use (default: ~/.config/restverify/config.toml)")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=PROG, description=DESCRIPTION, epilog=EPILOG,
                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version",
                        version=f"{PROG} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_init = sub.add_parser(
        "init", help="add repos and watch paths to the config (interactive)",
        description="Walk through adding a restic repo, then write the config.",
        epilog="examples:\n  restverify init\n  restverify init --config ./rv.toml",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_common(p_init)

    p_run = sub.add_parser(
        "run", help="restore the newest snapshot to a temp dir and verify it",
        description="Restore, compare, report. Read-only on the repo; never "
                    "writes to your source paths unless --strict compares them.",
        epilog="examples:\n  restverify run -r /srv/backup\n"
               "  restverify run -r /srv/backup --dry-run\n"
               "  restverify run --no-source --json",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p_run.add_argument("-r", "--repo", metavar="PATH",
                       help="restic repository (path or restic location string)")
    p_run.add_argument("--source", metavar="PATH",
                       help="original source path to compare against (overrides config)")
    p_run.add_argument("--dry-run", action="store_true",
                       help="show what would happen; restore nothing")
    p_run.add_argument("--no-source", action="store_true",
                       help="verify the restore succeeds without comparing to a source")
    p_run.add_argument("--strict", action="store_true",
                       help="fail on any file-count or hash difference (read-only check)")
    _add_common(p_run)

    p_report = sub.add_parser(
        "report", help="show run history and trend",
        description="Read the local run history and show pass/fail trend.",
        epilog="examples:\n  restverify report\n  restverify report --json",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_common(p_report)

    p_cron = sub.add_parser(
        "cron", help="print a crontab line for a scheduled verification",
        description="Print (never install) a crontab line you can paste.",
        epilog="examples:\n  restverify cron\n  restverify cron --repo /srv/backup",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p_cron.add_argument("-r", "--repo", metavar="PATH", help="restic repository")
    _add_common(p_cron)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_PASS
    if args.command == "init":
        return _pending("init", "I1 (interactive walkthrough) / I5 (CLI polish)")
    if args.command == "run":
        return _pending("run", "I1 (restore-to-temp) then I2/I3")
    if args.command == "report":
        return _pending("report", "I4")
    if args.command == "cron":
        return _pending("cron", "I5")
    parser.print_help()
    return EXIT_PASS


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
