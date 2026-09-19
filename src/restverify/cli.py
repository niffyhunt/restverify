"""Command-line surface for restverify.

U3: --help is documentation — the top-level help must let a user succeed
without opening the README, and every subcommand shows examples.
U1: zero-config-first — `restverify run -r <repo>` is a complete first run.
U2: failures teach — nothing here lets a bare traceback reach the user.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import (EXIT_DIFF_MISMATCH, EXIT_PASS, EXIT_RESTORE_FAIL, EXIT_USAGE, __version__)
from . import config as cfgmod
from . import manifest as manifestmod
from . import restic as resticmod
from . import tempstore
from .errors import ConfigError, RestverifyError

PROG = "restverify"

DESCRIPTION = (
    "Rehearse and verify restic restores locally, on a schedule, with a "
    "diff-proof report.\n\n"
    "restverify RESTORES and VERIFIES. It never creates backups, never runs "
    "forget/prune, and never stores your password."
)

EPILOG = """examples:
  restverify run -r /srv/backup            verify the newest snapshot (start here)
  restverify run -r /srv/backup --dry-run  show what would happen; restore nothing
  restverify init -r /srv/backup           save a repo (with source/excludes) to config
  restverify report                        run history and trend
  restverify cron                          print a ready-to-paste crontab line

exit codes:
  0   restore verified
  1   the run could not complete (restic failed, no snapshots, restic missing)
  2   restored data differs from the source
  64  usage problem (bad flag, unusable config) — never a verification outcome

run output (this build):
  ✓ restored snapshot <id>: N files / X in Ys
  manifest fields, the symlink policy and the sampling rule are in `run --help`
  the source comparison (and its diff count) lands in increment I2c

next: run `restverify run -r <repo>` to verify your first snapshot
"""


class _Parser(argparse.ArgumentParser):
    """Usage errors teach instead of dumping a traceback (U2)."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{PROG}: error: {message}", file=sys.stderr)
        print(f"  next: {PROG} --help", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true",
                   help="machine-readable JSON; the sample hash completes in I2b, "
                        "the compare block in I2c, the full schema in I3")
    p.add_argument("--config", metavar="PATH", default=None,
                   help=f"config file to use (default: {cfgmod.default_config_path()})")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=PROG, description=DESCRIPTION, epilog=EPILOG,
                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_init = sub.add_parser(
        "init", help="save a repository (and optional source/excludes) to the config",
        description="Add a restic repository to the config. With no --repo and a "
                    "terminal attached, restverify asks for it.",
        epilog="examples:\n  restverify init -r /srv/backup\n"
               "  restverify init -r /srv/backup -s /srv/data -x '*.log' -x cache/\n"
               "  restverify init -r b2:bucket:path --password-command 'pass show restic/srv'",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p_init.add_argument("-r", "--repo", metavar="PATH", help="restic repository location")
    p_init.add_argument("-s", "--source", metavar="PATH",
                        help="original source path to compare restores against")
    p_init.add_argument("-x", "--exclude", action="append", default=[], metavar="PATTERN",
                        help="exclude pattern (repeatable)")
    p_init.add_argument("--name", metavar="NAME", help="short name for this repo (default: last path segment)")
    p_init.add_argument("--password-command", metavar="CMD",
                        help="command that prints the repo password; the password itself is never stored")
    p_init.add_argument("--snapshot", metavar="SELECTOR", default=cfgmod.DEFAULT_SNAPSHOT,
                        help="snapshot to verify: 'latest' (default) or a short id")
    _add_common(p_init)

    p_run = sub.add_parser(
        "run", help="restore the newest snapshot to a temp dir and verify it",
        description="Restore, compare, report. Read-only on the repository; never writes "
                    "to your source paths.",
        epilog="examples:\n  restverify run -r /srv/backup\n"
               "  restverify run -r /srv/backup --dry-run\n  restverify run --no-source\n"
               "  restverify run -r /srv/backup -s /srv/data --strict\n"
               "\nmanifest (this build):\n"
               "  after the restore, restverify walks the restored tree and reports the\n"
               "  file count, byte totals, a per-directory roll-up (direct + recursive),\n"
               "  the deepest directory seen, and how many symlinks were recorded.\n"
               "  symlinks: recorded with their target, never followed (following one\n"
               "  could read outside the restore target); a symlinked directory is one\n"
               "  link entry, not a subtree. No depth limit is imposed by restverify.\n"
               "  contents are not read in this build - the sampled sha256 is I2b.\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p_run.add_argument("-r", "--repo", metavar="PATH",
                       help="restic repository (or a repo name saved in the config)")
    p_run.add_argument("-s", "--source", metavar="PATH",
                       help="original source path to compare against (overrides config)")
    p_run.add_argument("--dry-run", action="store_true",
                       help="show what would happen; restore nothing")
    p_run.add_argument("--no-source", action="store_true",
                       help="verify the restore completes without comparing to a source")
    p_run.add_argument("--strict", action="store_true",
                       help="tighten comparison (parsed now; enforced when the "
                            "comparison lands in increment I2c)")
    p_run.add_argument("--snapshot", metavar="SELECTOR", default=None,
                       help="snapshot to verify: 'latest' (default) or a short id")
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
        epilog="examples:\n  restverify cron\n  restverify cron -r /srv/backup",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p_cron.add_argument("-r", "--repo", metavar="PATH", help="restic repository")
    _add_common(p_cron)
    return parser


def _note_json_deferred(args) -> None:
    """G6: --json is documented as incomplete (I3) and must not fake a schema."""
    if getattr(args, "json", False):
        print(f"{PROG}: --json is not complete until increment I3; "
              "showing the human-readable output instead.", file=sys.stderr)


def _pending(cmd: str, increment: str) -> int:
    print(f"{PROG}: '{cmd}' is not implemented yet in this build.\n"
          f"  why:   scaffold builds the CLI surface and contract first\n"
          f"  next:  implemented in increment {increment}; see docs/PHASE2-PLAN.md",
          file=sys.stderr)
    return EXIT_RESTORE_FAIL


# ── init ────────────────────────────────────────────────────────────────────

def _cmd_init(args) -> int:
    _note_json_deferred(args)
    repo = args.repo
    if not repo and sys.stdin.isatty():
        try:
            repo = input("restic repository to verify (path, or b2:s3:... location): ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{PROG}: nothing added.", file=sys.stderr)
            return EXIT_USAGE
    if not repo:
        raise ConfigError(
            "no repository given, so there is nothing to save.",
            hint="run `restverify init -r /path/to/repo` (add -s /source and -x pattern as needed)",
        )

    config = cfgmod.load_config(args.config)
    entry = cfgmod.RepoEntry(
        name=args.name or cfgmod.RepoEntry(name="", repo=repo).name
             or os.path.basename(repo.rstrip("/")) or repo,
        repo=repo,
        source=args.source,
        excludes=list(args.exclude),
        snapshot=args.snapshot or cfgmod.DEFAULT_SNAPSHOT,
        password_command=args.password_command,
    )
    existing = config.find(repo)
    if existing:
        config.repos[config.repos.index(existing)] = entry
        verb = "updated"
    else:
        config.repos.append(entry)
        verb = "added"
    path = cfgmod.save_config(config, args.config)

    print(f"✓ {verb} '{entry.name}' in {path}")
    if not entry.source:
        print("  note: no source path yet, so `run` will verify the restore completes "
              "without comparing files\n        add one with `restverify init -r <repo> -s <source>`")
    if not entry.password_command:
        print("  note: no password_command saved; set RESTIC_PASSWORD_COMMAND or "
              "re-run init with --password-command")
    print(f"\nnext: restverify run -r {entry.name}")
    return EXIT_PASS


# ── run ────────────────────────────────────────────────────────────────────

def _resolve_entry(args, config):
    """U1: -r wins; a config name also works; else the single configured repo."""
    entry = None
    if args.repo:
        entry = config.find(args.repo)
        if entry is None:
            entry = cfgmod.RepoEntry(name=args.repo, repo=args.repo)
    elif len(config.repos) == 1:
        entry = config.repos[0]
    elif len(config.repos) > 1:
        names = ", ".join(r.name for r in config.repos)
        raise ConfigError(
            f"{len(config.repos)} repositories are configured, so one must be named.",
            hint=f"run `restverify run -r <name>` — saved names: {names}",
        )
    else:
        raise ConfigError(
            "no repository given and none is saved in the config.",
            hint="run `restverify run -r /path/to/repo` (or save it with `restverify init -r ...`)",
        )
    if args.source:
        entry.source = args.source
    if args.no_source:
        entry.no_source = True
    if args.strict:
        entry.strict = True
    if args.snapshot:
        entry.snapshot = args.snapshot
    return entry


def human_bytes(count: int) -> str:
    """Binary units with one decimal, matching restic's own reporting."""
    if count < 1024:
        return f"{count} B"
    value = float(count)
    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB"):
        value /= 1024.0
        if value < 1024 or unit == "PiB":
            return f"{value:.1f} {unit}"
    return f"{count} B"  # pragma: no cover - the loop always returns


def _restored_root(target, snapshot):
    """Locate the subtree the snapshot's own paths restored into (B1 assumption).

    `restic restore <id> --target <dir>` recreates the snapshot's absolute
    paths *under* the target, so a source comparison needs that subtree, not
    the target itself. Any candidate that resolves outside the target is
    refused: repository metadata must not be able to aim our walk at arbitrary
    filesystem paths.
    """
    base = Path(target).resolve()
    for raw in snapshot.paths or []:
        candidate = Path(target) / str(raw).lstrip("/")
        try:
            resolved = candidate.resolve()
            resolved.relative_to(base)
        except (OSError, ValueError):
            continue
        if resolved.is_dir():
            return resolved
    return Path(target)


def _run_payload(entry, snapshot, man, root, elapsed, cleaned) -> dict:
    """R12 + G6: the real fields that exist now, and explicit incompleteness."""
    return {
        "tool": PROG,
        "version": __version__,
        "command": "run",
        "status": "pass",
        "exit_code": EXIT_PASS,
        "snapshot": {
            "id": snapshot.id,
            "short_id": snapshot.short_id,
            "time": snapshot.time,
            "paths": snapshot.paths,
        },
        "restore": {
            "repo": entry.repo,
            "target": str(root),
            "target_removed": bool(cleaned),
            "excludes": list(entry.excludes),
            "elapsed_seconds": elapsed,
        },
        "manifest": man.to_json(),
        "sample": {"implemented": False, "lands_in": "I2b"},
        "compare": {"implemented": False, "lands_in": "I2c"},
        "strict": bool(entry.strict),
        "incomplete": [
            "sample sha256 (I2b)",
            "source comparison (I2c)",
            "full JSON schema (I3)",
        ],
    }


def _cmd_run(args) -> int:
    config = cfgmod.load_config(args.config)
    entry = _resolve_entry(args, config)

    swept = tempstore.sweep_stale()
    if swept:
        print(f"  cleaned up {len(swept)} leftover restore dir(s) from a previous run")

    selector = entry.snapshot or cfgmod.DEFAULT_SNAPSHOT
    password_command = entry.password_command or os.environ.get("RESTIC_PASSWORD_COMMAND")

    if args.dry_run:
        print("dry run — nothing will be restored")
        print(f"  repo     : {entry.repo}")
        print(f"  snapshot : {selector}")
        print(f"  source   : {entry.source or '(none saved)'}")
        print(f"  compare  : {'off (--no-source)' if entry.no_source or not entry.source else 'on'}")
        print(f"  excludes : {', '.join(entry.excludes) if entry.excludes else '(none)'}")
        print(f"  temp dir : a fresh private dir under {tempstore.base_dir()}, removed afterwards")
        print("  manifest : file count, byte totals and a per-directory roll-up (this build)")
        print("  sample   : deterministic sha256 digest of the sampled files (I2b)")
        print("  compare  : excludes-aware source comparison (I2c)")
        print("✓ PASS (dry run) — nothing was restored, nothing was written")
        return EXIT_PASS

    started = time.time()
    snapshot = resticmod.newest_snapshot(entry.repo, password_command, selector)
    with tempstore.restore_dir(entry.repo) as target:
        resticmod.restore(entry.repo, snapshot, target,
                          excludes=entry.excludes, password_command=password_command)
        restore_root = _restored_root(target, snapshot)
        man = manifestmod.build(restore_root, ignore_top_level=(tempstore.MARKER,))
        cleaned = tempstore.cleanup(target)
    elapsed = int(time.time() - started)

    if args.json:
        print(json.dumps(_run_payload(entry, snapshot, man, restore_root, elapsed, cleaned),
                         indent=2, ensure_ascii=False))
        return EXIT_PASS

    size = human_bytes(man.total_bytes)
    print(f"✓ restored snapshot {snapshot.short_id}: {man.file_count} files / "
          f"{size} in {elapsed}s")
    print(f"  manifest : {man.file_count} files, {size}, {man.directory_count} dirs, "
          f"max depth {man.max_depth}, {man.symlink_count} symlink(s) "
          f"({manifestmod.SYMLINK_POLICY}), {len(man.ignored)} tempstore file(s) ignored")
    print("  sample   : deterministic sha256 digest of the sampled files (I2b)")
    print("  compare  : excludes-aware source comparison (I2c)")
    if entry.strict:
        print("  note     : --strict has no effect until the comparison lands (I2c)")
    if not cleaned:
        print(f"  warning  : the temp restore dir at {restore_root} could not be removed")
    return EXIT_PASS


# ── dispatch ────────────────────────────────────────────────────────────────

_DISPATCH = {"init": _cmd_init, "run": _cmd_run}
_PENDING = {"report": "I4", "cron": "I5"}


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_PASS
    try:
        handler = _DISPATCH.get(args.command)
        if handler:
            return handler(args)
        return _pending(args.command, _PENDING[args.command])
    except ConfigError as exc:
        print(f"{PROG}: {exc.render()}", file=sys.stderr)
        return EXIT_USAGE
    except RestverifyError as exc:
        print(f"{PROG}: {exc.render()}", file=sys.stderr)
        tail = getattr(exc, "stderr_tail", "")
        if tail:
            print(f"  restic said: {tail}", file=sys.stderr)
        return EXIT_RESTORE_FAIL
    except KeyboardInterrupt:
        print(f"\n{PROG}: interrupted.", file=sys.stderr)
        return EXIT_RESTORE_FAIL


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
