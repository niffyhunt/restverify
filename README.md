# restverify

Rehearse and verify restic restores locally, on a schedule, with a diff-proof report.

> **Status: v0.1.0 — first release.** `run`, `report` and `cron` are implemented
> and verified against real repos; `init --json` is still deferred. Verified at
> scale too: a 1.02 GiB / 1253-file drill over an SFTP backend, run as a
> non-root user, passed with 0 diffs (docs/I9-DRILL.md). Verify with a repo
> you can afford to rehearse on.

## Why

`restic check` validates the repository's internal structure. It does not prove the
repository can be restored. The restic project tracks this as open issue #2332
("Backups are only real if they're able to be restored"). Today everyone hand-rolls a
`restore && diff` script that decays silently.

restverify is the verification layer: it restores to a temp directory, compares
against the source, records the run, and exits with a code your cron/CI can act on.
It does **not** orchestrate backups.

## Install

```bash
pipx install restverify
```

Requires Python 3.11+ and a `restic` binary on PATH.

## First run

```bash
restverify run -r /srv/backup
```

The run line keeps the Phase-2 field order — `✓ restored snapshot <id>: N files / X
in Ys; N diffs` — rather than the PDF's example order; three increments of
regression tests pin this order and the rewrite was cosmetic (I5 ruling R1).

## Scheduled verification

```bash
restverify cron -r /srv/backup            # one ready-to-paste crontab line
restverify cron -r /srv/backup --systemd  # a .service + .timer pair instead
```

`cron` **prints and never installs**: no crontab is modified, `systemctl` is never
called, and nothing is written into any unit directory. It prints one example
cadence and leaves the schedule to you. A scheduler runs with a minimal
environment, so set `RESTIC_PASSWORD_COMMAND` (or `RESTIC_PASSWORD_FILE`) there —
restverify never stores your password.

## History

Every verification writes one row to a local SQLite store — failures included,
because a trend that hides failures is worse than no trend:

- store: `~/.local/state/restverify/history.db` (XDG state dir; override with
  `RESTVERIFY_STATE=/some/dir`)
- retention: the newest 1000 rows per repository, pruned on write; the first
  prune announces itself on stderr
- read it with `restverify report` (human) or `restverify report --json`
  (a `"schema": 1` envelope)
- `--dry-run` writes nothing; disabling the store is not supported yet

## Honest limits (required by the contract)

- **Biggest risk:** operators who already run a full orchestrator (resticprofile,
  restickler) will not switch. Mitigation: restverify is verification-only — bring your
  own orchestration — and it offers an exit-code contract nothing else provides.
- Verification is as good as the comparison you ask for: without a source path it only
  proves the restore completed, not that the data matches.
- Not a backup tool. No cloud targets, no prune/forget, no dashboards, no bouncer.
