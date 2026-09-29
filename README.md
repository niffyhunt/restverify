# restverify

Rehearse and verify restic restores locally, on a schedule, with a diff-proof report.

> **Status: v0.2.0 — signed releases + CI drill.** `run`, `report` and `cron` are
> implemented and verified against real repos; the release train is signed
> (tag + artefacts); a 1.02 GiB / 1253-file drill over an SFTP backend, run as a
> non-root user, passed with 0 diffs (docs/I9-DRILL.md); and the drill ships as a
> GitHub Action (docs/I10-CI.md). `init --json` is still deferred. Verify with a
> repo you can afford to rehearse on.

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

## Sandboxed verification (optional isolation)

```bash
restverify run -r /srv/backup --sandbox
```

The restore runs inside a disposable container (your restic binary, your
temp dir, your uid — no image builds, nothing persists) and is purged on
every exit, including a `kill -9` mid-restore: the next run sweeps any
leftover container automatically. Local repos mount read-only with no
network; the verification, history and exit codes are identical to a native
run. Requires docker (or a compatible runtime) on PATH with socket access
for your user — never sudo. See `docs/I11-SANDBOX.md` for the measured
gate.

## CI: the nightly restore-drill

A scheduled GitHub Action that drills your backup ships in this repository
(`templates/ci-drill/`, R44): it installs restic, downloads and GPG-verifies
the released restverify wheel, restores and verifies a fixture (exit 0), then
proves the drill still bites by tampering one byte (exit 2). Copy the
workflow into your repository, point it at your restic repo via secrets, and
a degraded backup turns the nightly job red before you ever need it for
real. The same drill runs outside GitHub as a plain script
(`templates/ci-drill/scripts/drill.sh`). Transcript and proof:
`docs/I10-CI.md`.

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
