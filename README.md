# restverify

Rehearse and verify restic restores locally, on a schedule, with a diff-proof report.

> **Status: pre-release scaffold (Phase 2).** Commands are wired and documented but
> not yet implemented; running one says which increment implements it. Do not rely
> on this for real verification yet.

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

## Honest limits (required by the contract)

- **Biggest risk:** operators who already run a full orchestrator (resticprofile,
  restickler) will not switch. Mitigation: restverify is verification-only — bring your
  own orchestration — and it offers an exit-code contract nothing else provides.
- Verification is as good as the comparison you ask for: without a source path it only
  proves the restore completed, not that the data matches.
- Not a backup tool. No cloud targets, no prune/forget, no dashboards, no bouncer.
