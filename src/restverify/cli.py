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

from . import (EXIT_DIFF_MISMATCH, EXIT_PASS, EXIT_RESTORE_FAIL, EXIT_USAGE,
               SCHEMA_VERSION, __version__)
from . import compare as comparemod
from . import config as cfgmod
from . import excludes as excludesmod
from . import history as historymod
from . import manifest as manifestmod
from . import restic as resticmod
from . import sandbox as sandboxmod
from . import prove as provemod
from . import sampling as samplingmod
from . import tempstore
from . import webhook as webhookmod
from .errors import (CODE_BY_KIND, ConfigError, HistoryError, RestverifyError,
                     exit_code_for, kind_of)

PROG = "restverify"
DIFF_DISPLAY_LIMIT = 10

# Both are set by main() from the raw argv BEFORE argparse runs (I3 ruling 2).
# The scan decides output format only: no other flag is interpreted before
# argparse, and _RAW_ARGV exists solely so a usage error can name its command.
_JSON_MODE = False
_RAW_ARGV: list[str] = []
_KNOWN_COMMANDS = ("init", "run", "report", "cron", "prove")
_PRUNE_ANNOUNCED: set[str] = set()   # one retention announcement per repo/process


def _wants_json(argv) -> bool:
    """Ruling 2: look for the literal --json token in raw argv, nothing else."""
    return "--json" in argv


def _command_from_argv(argv) -> str:
    for token in argv:
        if token in _KNOWN_COMMANDS:
            return token
    return ""


def _error_payload(kind: str, what: str, exit_code: int, command: str = "",
                   hint: str | None = None, stderr_tail: str | None = None) -> dict:
    """The single error envelope (I3a). Uniform on every failure path."""
    return {
        "tool": PROG,
        "schema": SCHEMA_VERSION,
        "version": __version__,
        "command": command,
        "status": "error",
        "exit_code": exit_code,
        "error": {
            "kind": kind,
            "what": what,
            "hint": hint or None,
            "stderr_tail": stderr_tail or None,
        },
    }


def _emit_json(payload: dict) -> None:
    """stdout carries exactly this object and nothing else (ruling 1)."""
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _emit_error(kind: str, what: str, exit_code: int, command: str = "",
                hint: str | None = None, stderr_tail: str | None = None) -> None:
    """Emit the error envelope when --json was requested; the teaching text is
    always written to stderr separately, never into the JSON stream."""
    if _JSON_MODE:
        _emit_json(_error_payload(kind, what, exit_code, command, hint, stderr_tail))

DESCRIPTION = (
    "Rehearse and verify restic restores locally, on a schedule, with a "
    "diff-proof report.\n\n"
    "restverify RESTORES and VERIFIES. It never creates backups, never runs "
    "forget/prune, and never stores your password."
)

EPILOG = f"""examples:
  restverify run -r /srv/backup            verify the newest snapshot (start here)
  restverify run -r /srv/backup --dry-run  show what would happen; restore nothing
  restverify prove -r /srv/backup          restore a random sample and verify it
  restverify init -r /srv/backup           save a repo (with source/excludes) to config
  restverify report                        run history and trend
  restverify cron                          print a ready-to-paste crontab line

exit codes:
  0   restore verified
  1   the run could not complete (restic failed, no snapshots, restic missing)
  2   restored data differs from the source
  64  usage problem (bad flag, unusable config) — never a verification outcome

run output (this build):
  ✓ restored snapshot <id>: N files / X in Ys; N diffs
  manifest fields, the symlink policy, the sampling rule, the compare semantics
  and the JSON envelope ("schema": 1, stdout purity, error kinds) are in `run --help`

sample sha256 (this build):
  {samplingmod.RULE}
  digest = sha256 over "path NUL size NUL file-sha256 LF" per sampled file in
  sorted-path order; {samplingmod.REPRODUCE}

next: run `restverify run -r <repo>` to verify your first snapshot
"""


class _Parser(argparse.ArgumentParser):
    """Usage errors teach instead of dumping a traceback (U2)."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{PROG}: error: {message}", file=sys.stderr)
        print(f"  next: {PROG} --help", file=sys.stderr)
        _emit_error("usage", message, CODE_BY_KIND["usage"],
                    command=_command_from_argv(_RAW_ARGV),
                    hint=f"{PROG} --help")
        raise SystemExit(CODE_BY_KIND["usage"])


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true",
                   help="machine-readable JSON on stdout (exactly one object; teaching "
                        "text goes to stderr; the envelope is documented in `run --help`)")
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
               "  contents are read only for the sampled files, never through a\n"
               "  symlink; a file that became a link is an error, not a silent read.\n"
               "\nsample sha256 (this build):\n"
               f"  {samplingmod.RULE}\n"
               "  digest = sha256 over 'path NUL size NUL file-sha256 LF' per sampled\n"
               "  file in sorted-path order. The empty tree hashes the empty input.\n"
               f"  {samplingmod.REPRODUCE}\n"
               "\nsource comparison (this build):\n"
               "  runs when a source is saved (or given with -s) and --no-source is\n"
               "  absent. Both trees are walked with the same excludes; any data\n"
               "  difference exits 2 and the offending paths are named. Compared:\n"
               "  the entry set, per-file size and kind, symlink targets, file/byte/\n"
               "  symlink totals, and the sampled sha256 (the sample rule above bounds\n"
               "  this - content outside the sample is not hashed). Not compared:\n"
               "  mtimes and ordering, so those never fail a run. Warned, not failed:\n"
               "  an empty directory present on only one side; --strict promotes that\n"
               "  warning to exit 2. A missing/unreadable source exits 1 and teaches.\n"
               "\njson envelope (--json):\n"
               "  stdout carries exactly one JSON object and nothing else in every mode\n"
               "  (success, --dry-run, --no-source skip, and every failure); teaching text\n"
               "  and argparse's usage message go to stderr, so `restverify ... --json >\n"
               "  out.json` is always valid JSON.\n"
               "  top-level keys: tool, schema, version, command, status, exit_code.\n"
               "  \"schema\": 1 is the public contract from I3c onward; a breaking change\n"
               "  increments it rather than editing it silently.\n"
               "  status: pass | diff_mismatch | dry_run | error. A mismatch is NOT an\n"
               "  error object: it is status diff_mismatch with exit 2.\n"
               "  on error, \"error\" carries kind/what/hint/stderr_tail; kind is one of\n"
               "  usage | config | restic_missing | restic_failed | no_snapshots |\n"
               "  source | manifest | sample | tempdir | interrupted.\n"
               "  exit_code mirrors the process exit code (0/1/2/64).\n",
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
                       help="promote warning-only divergence (for example an empty "
                            "directory present on one side only) to exit 2; data "
                            "differences always fail regardless")
    p_run.add_argument("--snapshot", metavar="SELECTOR", default=None,
                       help="snapshot to verify: 'latest' (default) or a short id")
    p_run.add_argument("--report-webhook", metavar="URL", default=None,
                       type=webhookmod.url_arg,
                       help="POST the exact --json envelope to this https:// URL after "
                            "the run (pass, mismatch or error; not --dry-run). "
                            "Best-effort: delivery failure is one stderr line and "
                            "never changes the exit code. https only, no retries, "
                            "redirects refused")
    p_run.add_argument("--webhook-timeout", metavar="SECONDS", default=None,
                       type=webhookmod.timeout_arg,
                       help="webhook delivery timeout in seconds, 0 < t <= 60 "
                            "(default: 10); validated before anything runs")
    p_run.add_argument("--sandbox", action="store_true",
                       help="restore inside a disposable container (R45): the "
                            "host restic binary runs there, the temp restore "
                            "dir is bind-mounted, and the container is purged "
                            "on every exit. Remote repos resolve inside the "
                            "container; a password_command that reads a host "
                            "file needs RESTVERIFY_SANDBOX_PASSFILE to mount "
                            "it read-only. Requires a container runtime on "
                            "PATH (docker; group access, never sudo)")
    _add_common(p_run)

    p_prove = sub.add_parser(
        "prove", help="restore a random sample of files and verify them against "
                      "the snapshot listing",
        description="Prove a snapshot by restoring a random sample of its files and "
                    "verifying each one against restic's own listing, PLUS a "
                    "cryptographic content check over the same share of the "
                    "repository's data packs (restic check --read-data-subset). "
                    "Per-file hashes are unavailable (ls --json exposes none), so "
                    "the per-file claim is existence + size; the content claim is "
                    "restic's own seal verification.",
        epilog="examples:\n"
               "  restverify prove -r /srv/backup                 prove 10% of the newest snapshot\n"
               "  restverify prove -r /srv/backup --sample 100    restore and check every file\n"
               "  restverify prove -r /srv/backup --seed 7        a different deterministic sample\n"
               "  restverify prove -r /srv/backup --dry-run       the plan without touching restic\n"
               "\nsampling (this build):\n"
               "  percent N (1-100, default 10) of the snapshot's files, files sorted by\n"
               "  path; the sample is drawn by a counter-mode AES-256 keystream DRBG\n"
               "  implemented on Python's standard library only. Default seed is\n"
               "  derived from the snapshot id, so the same snapshot always yields the\n"
               "  same sample; --seed changes it deterministically. The largest file\n"
               "  (size, then path) is always included. Reproduce any run: same file\n"
               "  list, same percent, same seed -> same sample.\n"
               "\nverification (this build):\n"
               "  only the sampled paths are restored (restic restore --include, one\n"
               "  glob-escaped pattern per path). Each sampled file must exist and\n"
               "  its size must equal the snapshot record. Per-file hashes are\n"
               "  unavailable (restic ls exposes none), so the per-file claim is\n"
               "  existence + size; content integrity comes from `restic check\n"
               "  --read-data-subset N%` over the SAME share (N = --sample; --sample\n"
               "  100 runs a full --read-data), which verifies the repository's own\n"
               "  cryptographic seals. Measured, restic 0.16.4: one flipped byte in\n"
               "  a pack leaves `restore` exiting 0 with a 0-byte file (the size\n"
               "  check catches that one) while `check` reports the corruption -\n"
               "  prove catches both.\n"
               "\nexit codes:\n"
               "  0   every sampled file verified (also: the snapshot lists no files)\n"
               "  1   the run could not complete (restic failed, no snapshots, bad ls)\n"
               "  2   at least one sampled file is missing or its size differs\n"
               "  64  usage problem (bad --sample/--seed, bad selector)\n"
               "\njson envelope (--json):\n"
               "  same top-level keys as run (schema 1, stdout purity, error kinds),\n"
               "  with `prove` in place of `compare`: files_listed, files_sampled,\n"
               "  files_verified, files_failed, hashes_available, content_check\n"
               "  (performed/ok/detail), percent, seed, failures\n"
               "  (path/reason/expected_size/actual_size), restore and history\n"
               "  blocks. History rows are written exactly like run's.\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p_prove.add_argument("-r", "--repo", metavar="PATH",
                         help="restic repository (or a repo name saved in the config)")
    p_prove.add_argument("--sample", metavar="N", type=provemod.percent_arg,
                         default=provemod.DEFAULT_PERCENT,
                         help="percent of files to sample, integer 1-100 (default: 10)")
    p_prove.add_argument("--seed", metavar="INT", type=int, default=None,
                         help="seed for the deterministic sample (default: derived "
                              "from the snapshot id, so runs are reproducible)")
    p_prove.add_argument("--snapshot", metavar="SELECTOR", default=None,
                         help="snapshot to prove: 'latest' (default) or a short id")
    p_prove.add_argument("--dry-run", action="store_true",
                         help="show the plan; restore nothing, record nothing")
    p_prove.add_argument("-x", "--exclude", action="append", default=[], metavar="PATTERN",
                         help="exclude pattern (repeatable) - honoured by the sample restore")
    _add_common(p_prove)

    p_report = sub.add_parser(
        "report", help="show run history and trend",
        description="Read the local run history and show pass/fail trend.",
        epilog="examples:\n  restverify report\n  restverify report --limit 25\n"
               "  restverify report --repo /srv/backup --json\n"
               "\ncolumns (newest first):\n"
               "  started  UTC start time of the run\n"
               "  status   pass | diff_mismatch | error\n"
               "  exit     0 verified, 1 could not complete, 2 differs from source\n"
               "  snapshot the short id that was verified\n"
               "  kind     the failure kind (empty when the run passed)\n"
               "  repo     the restic repository the run verified\n"
               "\nhistory store:\n"
               "  ~/.local/state/restverify/history.db (RESTVERIFY_STATE overrides;\n"
               "  RESTVERIFY_HOME relocates the whole layout for drill/CI users)\n"
               "  one row per verification, including failures; --dry-run writes none\n"
               "  retention: the newest 1000 rows per repository, pruned on write\n"
               "  (the first prune announces itself on stderr; disabling history is\n"
               "  not supported yet)\n"
               "\njson envelope (--json):\n"
               "  one object on stdout, schema 1, command \"report\", status \"report\";\n"
               "  teaching text and argparse's usage go to stderr, same purity rule as run.\n"
               "  report.report holds repo, runs (count in the window), limit, counts,\n"
               "  pass_rate, longest_green_streak, last_failure and recent (the newest\n"
               "  rows). report exits 0 whenever the store is readable (even if the last\n"
               "  run failed - run is what cron acts on) and 1 only when the store cannot\n"
               "  be read at all.\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p_report.add_argument("-r", "--repo", metavar="REPO",
                          help="only runs for this repository (path, or a name saved in config)")
    p_report.add_argument("--limit", type=int, default=10, metavar="N",
                          help="how many recent runs to list (default: 10)")
    _add_common(p_report)

    p_cron = sub.add_parser(
        "cron", help="print a crontab line for a scheduled verification",
        description="Print (never install) a crontab line you can paste.",
        epilog="examples:\n"
               "  restverify cron -r /srv/backup             print a crontab line\n"
               "  restverify cron -r backup --json           machine-readable envelope\n"
               "  restverify cron -r /srv/backup --systemd   .service + .timer pair\n"
               "\nschedule expression:\n"
               "  the line begins with the five crontab time fields (minute hour day\n"
               "  month weekday). restverify prints one example cadence and does not\n"
               "  choose a schedule for you: change the fields to your own rhythm.\n"
               "  the line logs human output; append --json to the `run` command inside\n"
               "  the line yourself if you want machine-readable cron logs (the flag on\n"
               "  THIS command only decides how cron's own output is formatted).\n"
               "\nthis command prints; it never installs:\n"
               "  no crontab is modified, systemctl is never called, and nothing is\n"
               "  written into any unit directory. Paste the line into `crontab -e`,\n"
               "  or save the units yourself at the privilege level you choose.\n"
               "  a scheduler runs with a minimal environment, so set\n"
               "  RESTIC_PASSWORD_COMMAND (or RESTIC_PASSWORD_FILE) there; restverify\n"
               "  never stores your password.\n"
               "\njson envelope (--json):\n"
               "  one object on stdout, schema 1, command \"cron\", status \"cron\";\n"
               "  same top-level keys as run and report. cron carries repo, binary,\n"
               "  schedule, line, installs (always false) and notes; with --systemd it\n"
               "  also carries systemd.service and systemd.timer. Teaching text goes to\n"
               "  stderr in human mode and is carried in cron.notes for --json, so\n"
               "  stdout stays a single valid object.\n"
               "\nwithout -r:\n"
               "  the line carries the placeholder '<repo>' and says so - a guess at\n"
               "  your repository would be worse than a placeholder.\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p_cron.add_argument("-r", "--repo", metavar="PATH",
                        help="restic repository (or a repo name saved in the config)")
    p_cron.add_argument("--systemd", action="store_true",
                        help="print a .service + .timer pair instead of a crontab line "
                             "(prints only; never installs)")
    _add_common(p_cron)
    return parser


def _note_json_deferred(args) -> None:
    """G6: `init`'s --json is still deferred and must not fake a schema.

    `run` (I3), `report` (I4b) and `cron` (I5a) are real JSON surfaces; `init`
    arrives in its own increment.
    """
    if getattr(args, "json", False):
        print(f"{PROG}: --json is not implemented for this command yet; "
              "showing the human-readable output instead.", file=sys.stderr)


def _record_error_run(args, kind: str, code: int, started: float) -> None:
    """A failed `run` still trends (every run writes a row, including failures).

    Only `run` writes rows: `init`/`report`/`cron` do not. Repo resolution is
    best-effort here because the failure may have happened before (or instead
    of) resolving the entry; an unresolvable repo is recorded as "(unknown)"
    rather than guessing.
    """
    if getattr(args, "command", None) not in ("run", "prove"):
        return
    repo = getattr(args, "repo", "") or ""
    try:
        entry = _resolve_entry(args, cfgmod.load_config(args.config))
        repo = entry.repo
    except RestverifyError:
        pass
    finished = time.time()
    _write_history(historymod.RunRecord(
        repo=repo or "(unknown)",
        status="error",
        exit_code=code,
        started_at=historymod.iso(started),
        finished_at=historymod.iso(finished),
        duration_ms=int((finished - started) * 1000),
        error_kind=kind,
        tool_version=__version__,
        schema=SCHEMA_VERSION,
    ))


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
    print(f"      restverify cron -r {entry.name}"
          "   # prints a crontab line for a scheduled check (it installs nothing)")
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
    # getattr, not attribute access: prove (I13) shares the repo resolution but
    # has no --source/--no-source/--strict flags.
    if getattr(args, "source", None):
        entry.source = args.source
    if getattr(args, "no_source", False):
        entry.no_source = True
    if getattr(args, "strict", False):
        entry.strict = True
    if getattr(args, "snapshot", None):
        entry.snapshot = args.snapshot
    return entry


def human_bytes(count: int) -> str:
    """R29: byte totals as binary units with one decimal (e.g. ``1.5 KiB``).

    The run line (R8 byte totals / R22 demo shape) and the I4 ``report`` both
    render totals through this function, so the format is contract, not
    incidental: bytes below 1024 print as ``N B``; above that the value is
    divided by 1024 per unit and printed with exactly one decimal in
    KiB/MiB/GiB/TiB/PiB.
    """
    if count < 1024:
        return f"{count} B"
    value = float(count)
    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB"):
        value /= 1024.0
        if value < 1024 or unit == "PiB":
            return f"{value:.1f} {unit}"
    return f"{count} B"  # pragma: no cover - the loop always returns


def _restored_root(target, snapshot):
    """The shared locator (moved to restic.py in I13); kept as a thin alias so
    `run`'s call sites and the tests that pin the behavior stay stable."""
    return resticmod.restored_root(target, snapshot)


def _run_payload(entry, snapshot, man, sample, comparison, history_block, root,
                 elapsed, cleaned, sandbox_run=None) -> dict:
    """The run envelope. Every field is real, "schema" pins the shape, and the
    additive "history" block states whether this run reached the store.

    The "sandbox" block (I11) is additive and present only when --sandbox was
    requested — its absence means a native restore, which keeps every existing
    consumer of this envelope working unchanged (no-breakage contract)."""
    code = comparison.exit_code()
    payload = {
        "tool": PROG,
        "schema": SCHEMA_VERSION,
        "version": __version__,
        "command": "run",
        "status": "diff_mismatch" if code == EXIT_DIFF_MISMATCH else "pass",
        "exit_code": code,
        "history": history_block,
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
        "sample": sample.to_json(),
        "compare": comparison.to_json(),
        "strict": bool(entry.strict),
    }
    if sandbox_run is not None:
        payload["sandbox"] = {
            "runtime": sandbox_run.runtime,
            "image": sandbox_run.image,
            "container": sandbox_run.container,
            "purged": bool(sandbox_run.purged),
        }
    return payload


def _dry_run_payload(entry, selector: str) -> dict:
    """The --dry-run envelope: same top-level keys as every other payload, and
    no fabricated restore data (G6) — it states what *would* happen."""
    return {
        "tool": PROG,
        "schema": SCHEMA_VERSION,
        "version": __version__,
        "command": "run",
        "status": "dry_run",
        "exit_code": EXIT_PASS,
        "dry_run": {
            "repo": entry.repo,
            "snapshot": selector,
            "source": entry.source,
            "compare": bool(entry.source) and not entry.no_source,
            "excludes": list(entry.excludes),
            "restores_anything": False,
            "restic_invoked": False,
            "temp_dir": f"a fresh private dir under {tempstore.base_dir()}, removed afterwards",
        },
    }


def _write_history(record: "historymod.RunRecord") -> dict:
    """Best-effort history write (I4 ruling 2).

    A store failure never changes the verification's exit code: it warns with a
    teaching line on stderr and is reported honestly in the JSON block. The
    caller decides whether a row is written at all (cli decides; history.py does
    not) — the same boundary as `--dry-run` and restic.

    Retention (I4c): when the write prunes rows for a repository, it is
    announced once per process per repository — never silently, never on every
    write.
    """
    global _PRUNE_ANNOUNCED
    try:
        path, pruned = historymod.record(record)
    except HistoryError as exc:
        print(f"{PROG}: could not record this run in history: {exc.what}",
              file=sys.stderr)
        if exc.hint:
            print(f"  next: {exc.hint}", file=sys.stderr)
        return {"recorded": False, "path": str(historymod.state_path()),
                "warning": exc.what}
    if pruned and record.repo not in _PRUNE_ANNOUNCED:
        _PRUNE_ANNOUNCED.add(record.repo)
        print(f"{PROG}: history pruned for {record.repo}: kept the newest "
              f"{historymod.RETENTION_PER_REPO} rows", file=sys.stderr)
    return {"recorded": True, "path": str(path), "pruned": pruned}


# ── webhook delivery (I12/R47) ──────────────────────────────────────────

def _webhook_config(args, entry) -> tuple[str | None, float]:
    """Resolve (url, timeout): the flag wins, then the saved config; default 10s.

    Called only after argparse has validated both via type= (parse_url /
    parse_timeout), so a ConfigError raised here is about a saved value, not a
    malformed command line.
    """
    url = getattr(args, "report_webhook", None)
    if url is None and entry is not None:
        url = getattr(entry, "report_webhook", None)
    if url is not None:
        try:
            url = webhookmod.parse_url(url)   # a saved value is re-checked here
        except ValueError:
            url = None
    timeout = getattr(args, "webhook_timeout", None)
    if timeout is None:
        timeout = 10.0
    return (url, float(timeout))


def _deliver_webhook(args, entry, payload: dict) -> None:
    """Deliver the finished envelope, best-effort (I12).

    Ordering rule: this runs AFTER the history write and AFTER the envelope is
    fully built, BEFORE the process exits — the receiver sees exactly the same
    JSON the operator would have seen on stdout. A delivery failure is one
    stderr line; it NEVER changes the exit code and NEVER reaches stdout.
    Errors here are swallowed by deliver()'s contract; the extra except is a
    belt-and-braces guard so a bug can never turn into a traceback after a
    successful verification.
    """
    url, timeout = _webhook_config(args, entry)
    if not url:
        return
    ok, line = webhookmod.deliver(url, webhookmod.envelope_bytes(payload), timeout)
    print(f"{PROG}: webhook {line}", file=sys.stderr)
    if not ok:
        print("  note: delivery failure does not change the exit code — the "
              "verification verdict above stands", file=sys.stderr)


def _cmd_run(args) -> int:
    config = cfgmod.load_config(args.config)
    entry = _resolve_entry(args, config)

    swept = tempstore.sweep_stale()
    if swept:
        print(f"  cleaned up {len(swept)} leftover restore dir(s) from a previous run")

    selector = entry.snapshot or cfgmod.DEFAULT_SNAPSHOT
    password_command = entry.password_command or os.environ.get("RESTIC_PASSWORD_COMMAND")

    if args.dry_run:
        if _JSON_MODE:
            _emit_json(_dry_run_payload(entry, selector))
            return EXIT_PASS
        print("dry run — nothing will be restored")
        print(f"  repo     : {entry.repo}")
        print(f"  snapshot : {selector}")
        print(f"  source   : {entry.source or '(none saved)'}")
        print(f"  compare  : {'off (--no-source)' if entry.no_source or not entry.source else 'on'}")
        print(f"  excludes : {', '.join(entry.excludes) if entry.excludes else '(none)'}")
        print(f"  temp dir : a fresh private dir under {tempstore.base_dir()}, removed afterwards")
        print("  manifest : file count, byte totals and a per-directory roll-up (this build)")
        print("  sample   : deterministic sha256 digest of the sampled files (this build)")
        print("  compare  : excludes-aware source comparison (I2c)")
        print("✓ PASS (dry run) — nothing was restored, nothing was written")
        return EXIT_PASS

    started = time.time()
    snapshot = resticmod.newest_snapshot(entry.repo, password_command, selector)
    sandbox_run = None
    with tempstore.restore_dir(entry.repo) as target:
        if getattr(args, "sandbox", False):
            # R45: the restore itself runs inside a disposable container; the
            # manifest/compare below are unchanged — they walk the same
            # tempstore dir through the bind mount.
            sandbox_run = sandboxmod.sandboxed_restore(
                entry.repo, snapshot.id, Path(target),
                password_command=password_command, excludes=entry.excludes)
        else:
            resticmod.restore(entry.repo, snapshot, target,
                              excludes=entry.excludes, password_command=password_command)
        restore_root = _restored_root(target, snapshot)
        matcher = excludesmod.compile_matcher(entry.excludes, root=restore_root)
        man = manifestmod.build(restore_root, ignore_top_level=(tempstore.MARKER,),
                                exclude=matcher)
        sample = samplingmod.sample_tree(man, restore_root, entry.excludes)
        cleaned = tempstore.cleanup(target)
    elapsed = int(time.time() - started)

    if entry.source and not entry.no_source:
        comparison = comparemod.compare(man, sample, entry.source,
                                        patterns=entry.excludes, strict=entry.strict)
    else:
        comparison = comparemod.skipped(
            "--no-source" if entry.no_source else "no source saved",
            source=entry.source)
    exit_code = comparison.exit_code()

    finished = time.time()
    history_block = _write_history(historymod.RunRecord(
        repo=entry.repo,
        status="pass" if exit_code == EXIT_PASS else "diff_mismatch",
        exit_code=exit_code,
        started_at=historymod.iso(started),
        finished_at=historymod.iso(finished),
        duration_ms=int((finished - started) * 1000),
        snapshot=snapshot.short_id,
        file_count=man.file_count,
        total_bytes=man.total_bytes,
        diff_count=(len(comparison.errors) if comparison.enabled else None),
        digest=sample.digest,
        tool_version=__version__,
        schema=SCHEMA_VERSION,
    ))

    # I12: one envelope, two consumers — stdout prints it, the webhook (if
    # configured) receives the very same bytes. History write has happened;
    # delivery is the last thing before the verdict leaves the process.
    payload = _run_payload(entry, snapshot, man, sample, comparison,
                           history_block, restore_root, elapsed, cleaned,
                           sandbox_run=sandbox_run)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    _deliver_webhook(args, entry, payload)
    if args.json:
        return exit_code

    size = human_bytes(man.total_bytes)
    mark = "✓" if not comparison.failed else "✗"
    tail = ""
    if comparison.enabled:
        tail = f"; {len(comparison.errors)} diff(s)"
        if comparison.strict and comparison.warnings:
            tail += (f" (+{len(comparison.warnings)} warning(s) promoted "
                     "by --strict)")
    print(f"{mark} restored snapshot {snapshot.short_id}: {man.file_count} files / "
          f"{size} in {elapsed}s{tail}")
    print(f"  manifest : {man.file_count} files, {size}, {man.directory_count} dirs, "
          f"max depth {man.max_depth}, {man.symlink_count} symlink(s) "
          f"({manifestmod.SYMLINK_POLICY}), {len(man.ignored)} tempstore file(s) ignored")
    print(f"  sample   : {sample.digest} ({len(sample.files)} of "
          f"{sample.total_files} files; {sample.rule})")
    if sandbox_run is not None:
        print(f"  sandbox  : restored via {sandbox_run.runtime} container "
              f"{sandbox_run.container} ({sandbox_run.image}); "
              f"purged={'yes' if sandbox_run.purged else 'NO'}")
    if comparison.enabled:
        print(f"  compare  : {comparison.status} vs {comparison.source} "
              f"({len(comparison.errors)} error(s), {len(comparison.warnings)} "
              f"warning(s); strict {'on' if comparison.strict else 'off'})")
        for diff in comparison.errors[:DIFF_DISPLAY_LIMIT]:
            print(f"    - {diff.path or '(totals)'}: {diff.detail}")
        remaining = len(comparison.errors) - DIFF_DISPLAY_LIMIT
        if remaining > 0:
            print(f"    ... and {remaining} more (full list in --json)")
        for diff in comparison.warnings[:DIFF_DISPLAY_LIMIT]:
            print(f"    ! {diff.path}: {diff.detail} (warning)")
    else:
        print(f"  compare  : skipped ({comparison.reason})")
        if not entry.source:
            print("    note: without a source this only proves the restore completed; "
                  "add one with `restverify init -r <repo> -s <path>`")
    if entry.strict and not comparison.enabled:
        print("  note     : --strict had nothing to tighten (compare skipped)")
    if not cleaned:
        print(f"  warning  : the temp restore dir at {restore_root} could not be removed")
    return exit_code


# ── report ──────────────────────────────────────────────────────────────────

# ── prove (I13/R48) ────────────────────────────────────────────────

def _prove_failures_line(failures: list[dict], limit: int = 10) -> None:
    """The failure list for human output; the full list is in --json."""
    for fail in failures[:limit]:
        expected = fail.get("expected_size")
        actual = fail.get("actual_size")
        size_note = ""
        if expected is not None or actual is not None:
            size_note = (f" (listing says {expected if expected is not None else '?'} B, "
                         f"restored {actual if actual is not None else '?'} B)")
        print(f"    - {fail['path']}: {fail['reason']}{size_note}")
    remaining = len(failures) - limit
    if remaining > 0:
        print(f"    ... and {remaining} more (full list in --json)")


def _cmd_prove(args) -> int:
    config = cfgmod.load_config(args.config)
    entry = _resolve_entry(args, config)
    password_command = entry.password_command or os.environ.get("RESTIC_PASSWORD_COMMAND")
    repo = entry.repo

    swept = tempstore.sweep_stale()
    if swept:
        print(f"  cleaned up {len(swept)} leftover restore dir(s) from a previous run")

    if args.dry_run:
        if _JSON_MODE:
            _emit_json(provemod.dry_run_payload(
                repo, args.sample, args.seed,
                args.snapshot or cfgmod.DEFAULT_SNAPSHOT))
            return EXIT_PASS
        for line in provemod.dry_run_lines(repo, args.sample, args.seed,
                                           args.snapshot or cfgmod.DEFAULT_SNAPSHOT):
            print(line)
        return EXIT_PASS

    started = time.time()
    snapshot = resticmod.newest_snapshot(repo, password_command,
                                         args.snapshot or cfgmod.DEFAULT_SNAPSHOT)
    nodes = resticmod.ls(repo, snapshot.id, password_command)
    files = provemod.file_list(nodes)

    percent = provemod.parse_percent(args.sample)
    seed, seed_desc = provemod.resolve_seed(args.seed, snapshot.id)
    files, excluded = provemod.filter_excludes(files, args.exclude)
    sample = provemod.sample(files, percent, seed, snapshot_id=snapshot.id)

    print(f"  listing  : {len(files)} file(s) in snapshot {snapshot.short_id}"
          + (f"; {excluded} excluded" if excluded else ""), file=sys.stderr)
    print(f"  sampling : {percent}% -> {len(sample)} of {len(files)} file(s); "
          f"seed {seed_desc}", file=sys.stderr)

    failures: list[dict] = []
    restore_root = None
    cleaned = True
    if sample:
        with tempstore.restore_dir(repo) as target:
            resticmod.restore_paths(repo, snapshot.id,
                                    [f["path"] for f in sample], target,
                                    password_command=password_command)
            restore_root = resticmod.restored_root(target, snapshot)
            failures = provemod.verify_sample(sample, restore_root,
                                              snapshot_paths=snapshot.paths)
            cleaned = tempstore.cleanup(target)

    # Content integrity (I13 follow-up, operator ruling: "why can't honest
    # scope be fixed"): ls --json has no per-file hashes, but the repository
    # SEALS its data. `restic check --read-data-subset N%` reads the same
    # share of packs and verifies the seal — catching corruptions the size
    # check alone cannot see (measured: restore exits 0 on a flipped byte,
    # check does not). Same percent as the sample; --sample 100 = full
    # --read-data. A failed check is a DATA verdict (exit 2), not a
    # could-not-complete: the check ran and the data did not verify.
    check_ok, check_line = True, "skipped (nothing sampled)"
    if sample:
        check_ok, check_line = resticmod.check_data_subset(repo, percent,
                                                           password_command)

    exit_code = EXIT_PASS if (not failures and check_ok) else EXIT_DIFF_MISMATCH
    finished = time.time()
    history_block = _write_history(historymod.RunRecord(
        repo=repo,
        status="pass" if not failures else "diff_mismatch",
        exit_code=exit_code,
        started_at=historymod.iso(started),
        finished_at=historymod.iso(finished),
        duration_ms=int((finished - started) * 1000),
        snapshot=snapshot.short_id,
        file_count=len(files),
        diff_count=len(failures) or None,
        tool_version=__version__,
        schema=SCHEMA_VERSION,
    ))

    payload = provemod.payload(entry, snapshot, files, percent, seed, seed_desc,
                               sample, failures, history_block, restore_root,
                               cleaned, exit_code, args.exclude,
                               files_excluded=excluded, check_ok=check_ok,
                               check_line=check_line)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    _deliver_webhook(args, None, payload)
    if args.json:
        return exit_code

    mark = "✓" if (not failures and check_ok) else "✗"
    print(f"{mark} proved snapshot {snapshot.short_id}: "
          f"{len(sample) - len(failures)}/{len(sample)} sampled file(s) verified; "
          f"{len(failures)} failed")
    if failures:
        print("  failures :")
        _prove_failures_line(failures)
    print(f"  content  : {check_line}")
    print("  verify   : sampled files checked for existence + size (restic ls exposes "
          "no per-file hashes), PLUS restic check --read-data-subset for "
          "cryptographic content integrity over the same share of packs")
    print(f"  sample   : {percent}% of {len(files)} file(s); seed {seed_desc}")
    if not cleaned:
        print(f"  warning  : the temp restore dir at {restore_root} could not be removed")
    return exit_code


FAILURE_REASONS = {
    "diff_mismatch": "the restored data differed from the source",
    "source": "the comparison source could not be read",
    "restic_failed": "restic failed while restoring",
    "restic_missing": "restic was not found on PATH",
    "no_snapshots": "there were no snapshots to verify",
    "tempdir": "the temporary restore directory could not be created",
    "manifest": "the restored tree could not be read",
    "sample": "the restored files could not be hashed",
    "history": "the run could not be written to the history store",
    "config": "the configuration could not be used",
    "usage": "the command line could not be parsed",
    "interrupted": "the run was interrupted",
}


def _failure_reason(row) -> str:
    """Plain language for the last failure, never the raw kind (U5)."""
    if row.status == "diff_mismatch":
        detail = f" ({row.diff_count} differing file(s))" if row.diff_count else ""
        return FAILURE_REASONS["diff_mismatch"] + detail
    return FAILURE_REASONS.get(row.error_kind or "", "the run did not complete")


def _longest_green_streak(rows) -> int:
    """Longest run of consecutive passes in chronological order."""
    best = streak = 0
    for row in reversed(rows):                 # rows arrive newest-first
        if row.status == "pass":
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def _row_json(row) -> dict:
    return {
        "started_at": row.started_at, "finished_at": row.finished_at,
        "duration_ms": row.duration_ms, "repo": row.repo, "snapshot": row.snapshot,
        "status": row.status, "exit_code": row.exit_code, "error_kind": row.error_kind,
        "file_count": row.file_count, "total_bytes": row.total_bytes,
        "diff_count": row.diff_count, "digest": row.digest,
        "tool_version": row.tool_version, "schema": row.schema,
    }


def _report_json(rows, repo_filter: str | None, limit: int) -> dict:
    """command="report", status="report": a report is not a run (I4 ruling 4)."""
    counts = {"pass": 0, "diff_mismatch": 0, "error": 0}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    total = len(rows)
    failure = next((row for row in rows if row.status != "pass"), None)
    return {
        "tool": PROG,
        "schema": SCHEMA_VERSION,
        "version": __version__,
        "command": "report",
        "status": "report",
        "exit_code": EXIT_PASS,
        "report": {
            "repo": repo_filter,
            "runs": total,
            "limit": limit,
            "counts": counts,
            "pass_rate": (counts["pass"] / total) if total else None,
            "longest_green_streak": _longest_green_streak(rows),
            "last_failure": None if failure is None else {
                "started_at": failure.started_at,
                "repo": failure.repo,
                "status": failure.status,
                "exit_code": failure.exit_code,
                "kind": failure.error_kind,
                "reason": _failure_reason(failure),
            },
            "recent": [_row_json(row) for row in rows],
        },
    }


def _cmd_report(args) -> int:
    """Reads the store; never writes to it (the fingerprint test proves it)."""
    repo_filter = args.repo
    if repo_filter:
        try:
            entry = cfgmod.load_config(args.config).find(repo_filter)
        except RestverifyError:
            entry = None
        if entry is not None:
            repo_filter = entry.repo          # a saved name is enough
    limit = max(1, int(args.limit))
    rows = historymod.read_recent(limit=limit, repo=repo_filter)
    if not rows:
        raise HistoryError(
            f"no runs recorded yet for {repo_filter or 'any repository'}.",
            hint="run your first verification (`restverify run -r <repo>`), then "
                 "re-run report",
        )

    if _JSON_MODE:
        _emit_json(_report_json(rows, repo_filter, limit))
        return EXIT_PASS

    counts = {"pass": 0, "diff_mismatch": 0, "error": 0}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    total = len(rows)
    rate = f"{counts['pass'] * 100 // total}% pass" if total else "n/a"
    failure = next((row for row in rows if row.status != "pass"), None)
    print(f"{PROG} report — {total} recent run(s)")
    print(f"  repo     : {repo_filter or 'all repositories'}")
    print(f"  trend    : {counts['pass']} pass, {counts['diff_mismatch']} diff, "
          f"{counts['error']} error  ({rate})")
    print(f"  streak   : longest green streak {_longest_green_streak(rows)} run(s)")
    if failure is None:
        print("  last fail: none in this window")
    else:
        print(f"  last fail: {failure.started_at} — {_failure_reason(failure)}")
    print("recent runs (newest first):")
    for index, row in enumerate(rows, 1):
        print(f"  {index:>3}  {row.started_at}  {row.status:<13} "
              f"exit {row.exit_code:<2} {row.snapshot or '-':<9} "
              f"{row.error_kind or '-':<13} {row.repo}")
    return EXIT_PASS


# ── cron (I5a) ──────────────────────────────────────────────────────────────
#
# Doctrine for this command: it PRINTS, it never installs. It renders text for
# the operator to paste; it must not touch a crontab, call systemctl, or write
# into any unit directory. The tests assert exactly that, and the I5 security
# self-check greps this module for the calls that would break it.

EXAMPLE_SCHEDULE = "0 3 * * *"          # one example cadence (ruling R4: neutral)
SYSTEMD_SERVICE_HEADER = "# ----- restverify.service -----"
SYSTEMD_TIMER_HEADER = "# ----- restverify.timer -----"


def _cron_binary() -> str:
    """The absolute path a scheduler should call.

    An absolute path is not decoration: cron runs with a minimal PATH, so a bare
    `restverify` is the classic scheduled-job failure. When the console script is
    not installed (running from a checkout) this falls back to
    `<python> -m restverify`, which is the same entry point.
    """
    found = shutil.which(PROG)
    if found:
        return os.path.abspath(found)
    return f"{os.path.abspath(sys.executable)} -m restverify"


def _cron_repo(args) -> tuple[str, bool]:
    """Return (repo, resolved).

    A saved config name resolves to its repository path, same as `run`/`report`.
    With no -r the line carries the literal placeholder `<repo>` and the caller
    says so in the notes — a guess would be worse than a placeholder.
    """
    raw = getattr(args, "repo", "") or ""
    if not raw:
        return "<repo>", False
    try:
        entry = cfgmod.load_config(args.config).find(raw)
    except RestverifyError:
        entry = None
    if entry is not None:
        return entry.repo, True
    return raw, True


def _cron_line(binary: str, repo: str) -> str:
    """The one crontab line: five time fields, then the command.

    `--json` is deliberately NOT appended here even when the caller asked for the
    JSON envelope: output format must not change what the scheduled job does
    (I3 ruling 2). The help text documents that an operator who wants
    machine-readable cron logs appends `--json` to the `run` command themselves.
    """
    return f"{EXAMPLE_SCHEDULE} {binary} run -r {repo}"


def _systemd_units(binary: str, repo: str) -> tuple[str, str]:
    """(.service, .timer) as INI text — returned, never written.

    The two units are separated by the header comments above, so each half parses
    on its own (the test splits on them and parses both with configparser).
    """
    exec_line = f"{binary} run -r {repo}"
    service = "\n".join([
        "[Unit]",
        f"Description=restverify scheduled verification ({repo})",
        "",
        "[Service]",
        "Type=oneshot",
        f"ExecStart={exec_line}",
        "# restverify never stores your password: give the scheduler the secret.",
        "# Environment=RESTIC_PASSWORD_COMMAND=/usr/bin/pass show restic/backup",
        "",
    ])
    timer = "\n".join([
        "[Unit]",
        "Description=Run restverify on a schedule (set OnCalendar to your rhythm)",
        "",
        "[Timer]",
        "OnCalendar=*-*-* 03:00:00",
        "Persistent=true",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])
    return service, timer


def _cron_notes(repo: str, resolved: bool) -> list[str]:
    """The teaching text. Human mode writes it to stderr; --json carries it in
    the envelope instead, so stdout stays a single pure object (I3 contract)."""
    notes = [
        f"example cadence '{EXAMPLE_SCHEDULE}': change the five time fields to your "
        "own rhythm — restverify does not choose a schedule for you",
        "a scheduler runs with a minimal environment: set RESTIC_PASSWORD_COMMAND "
        "(or RESTIC_PASSWORD_FILE) there, because restverify never stores your password",
        "this command prints and never installs: paste the line into `crontab -e` "
        "(or save the units yourself, at the privilege level you choose)",
        "prefer a systemd timer? add --systemd for a .service + .timer pair",
    ]
    if not resolved:
        notes.insert(0, "no repository given: the line carries the placeholder "
                        "'<repo>' — pass -r <path>, or -r <name> for a saved repo")
    return notes


def _cron_payload(binary: str, repo: str, resolved: bool,
                  systemd: bool) -> dict:
    """command="cron", status="cron": printing a schedule is not a verification."""
    payload = {
        "tool": PROG,
        "schema": SCHEMA_VERSION,
        "version": __version__,
        "command": "cron",
        "status": "cron",
        "exit_code": EXIT_PASS,
        "cron": {
            "repo": None if repo == "<repo>" else repo,
            "repo_resolved": resolved,
            "binary": binary,
            "schedule": EXAMPLE_SCHEDULE,
            "line": _cron_line(binary, repo),
            "installs": False,
            "systemd": None,
            "notes": _cron_notes(repo, resolved),
        },
    }
    if systemd:
        service, timer = _systemd_units(binary, repo)
        payload["cron"]["systemd"] = {"service": service, "timer": timer}
    return payload


def _cmd_cron(args) -> int:
    """R15: render a schedule for the operator to install themselves."""
    binary = _cron_binary()
    repo, resolved = _cron_repo(args)
    systemd = bool(getattr(args, "systemd", False))

    if _JSON_MODE:
        _emit_json(_cron_payload(binary, repo, resolved, systemd))
        return EXIT_PASS

    if systemd:
        service, timer = _systemd_units(binary, repo)
        print(SYSTEMD_SERVICE_HEADER)
        print(service, end="")
        print(SYSTEMD_TIMER_HEADER)
        print(timer, end="")
    else:
        print(_cron_line(binary, repo))
    for note in _cron_notes(repo, resolved):
        print(f"  note: {note}", file=sys.stderr)
    return EXIT_PASS


# ── dispatch ────────────────────────────────────────────────────────────────

_DISPATCH = {"init": _cmd_init, "run": _cmd_run, "prove": _cmd_prove,
             "report": _cmd_report, "cron": _cmd_cron}
# Empty since I5a: `cron` was the last pending command, so every command in the
# parser is now real (gate G6). The helper below stays for the next increment.
_PENDING: dict[str, str] = {}


def main(argv=None) -> int:
    global _JSON_MODE, _RAW_ARGV, _PRUNE_ANNOUNCED
    # A legacy console or redirect codepage (Windows cp1252/cp437) cannot
    # encode the output glyphs ("\u2713" / "\u2717") and argparse would die
    # printing its own help. Pin UTF-8 with replacement so output can never
    # raise on encode; stream objects without reconfigure (test captures,
    # exotic wrappers) are left alone.
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            try:
                _reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    raw = list(sys.argv[1:] if argv is None else argv)
    _JSON_MODE = _wants_json(raw)      # ruling 2: format decision only
    _RAW_ARGV = raw
    _PRUNE_ANNOUNCED.clear()           # one announcement per process

    parser = build_parser()
    try:
        args = parser.parse_args(raw)
    except KeyboardInterrupt:
        print(f"\n{PROG}: interrupted.", file=sys.stderr)
        _emit_error("interrupted", "interrupted before the command started.",
                    CODE_BY_KIND["interrupted"], command=_command_from_argv(raw),
                    hint="re-run when ready; nothing was left behind")
        return CODE_BY_KIND["interrupted"]
    if not args.command:
        parser.print_help()
        return EXIT_PASS
    started = time.time()
    try:
        handler = _DISPATCH.get(args.command)
        if handler:
            return handler(args)
        return _pending(args.command, _PENDING.get(args.command, "a later increment"))
    except ConfigError as exc:
        # exit 64 is a usage problem, not a verification outcome: no run row.
        print(f"{PROG}: {exc.render()}", file=sys.stderr)
        code = exit_code_for(exc)
        _emit_error(kind_of(exc), exc.what, code, command=args.command,
                    hint=exc.hint)
        return code
    except RestverifyError as exc:
        print(f"{PROG}: {exc.render()}", file=sys.stderr)
        tail = getattr(exc, "stderr_tail", "")
        if tail:
            print(f"  restic said: {tail}", file=sys.stderr)
        code = exit_code_for(exc)
        _record_error_run(args, kind_of(exc), code, started)
        payload = _error_payload(kind_of(exc), exc.what, code, command=args.command,
                                 hint=exc.hint, stderr_tail=tail or None)
        _emit_error(kind_of(exc), exc.what, code, command=args.command,
                    hint=exc.hint, stderr_tail=tail or None)
        if args.command in ("run", "prove"):
            # I12: an error outcome is still a verification outcome — the
            # receiver learns about the failure like any other verdict.
            _deliver_webhook(args, None, payload)
        return code
    except KeyboardInterrupt:
        print(f"\n{PROG}: interrupted.", file=sys.stderr)
        code = CODE_BY_KIND["interrupted"]
        _record_error_run(args, "interrupted", code, started)
        payload = _error_payload("interrupted", "interrupted before the run finished.",
                                 code, command=args.command,
                                 hint="re-run when ready; nothing was left behind")
        _emit_error("interrupted", "interrupted before the run finished.",
                    code, command=args.command,
                    hint="re-run when ready; nothing was left behind")
        if args.command in ("run", "prove"):
            _deliver_webhook(args, None, payload)
        return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
