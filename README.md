# restverify

[![PyPI](https://img.shields.io/pypi/v/restverify)](https://pypi.org/project/restverify/)
[![Python](https://img.shields.io/pypi/pyversions/restverify)](https://pypi.org/project/restverify/)

**A backup you haven't restored is a hope, not a backup. restverify restores
your restic snapshots and proves the data came back — every night, on exit
codes you can automate.**

restverify restores a snapshot to a private temp directory, compares it
against the original source tree, and delivers a verdict your shell can act
on:

| Exit code | Meaning |
|-----------|---------|
| `0` | restore verified |
| `2` | restored data differs from the source |
| `1` | the run could not complete (restic failed, no snapshots, restic missing) |
| `64` | usage problem — never a verification outcome |

Wire those to cron or CI and a silently degraded backup turns red long before
you need it for real.

restverify never creates backups, never runs prune or forget, never writes to
your source paths, and never stores or transmits your repository password.

## Why

`restic check` validates the repository's internal structure. It does not
prove a restore works — the restic project tracks this as open issue
[#2332](https://github.com/restic/restic/issues/2332): *“Backups are only real
if they're able to be restored.”*

The usual answer is a hand-rolled `restore && diff -r` script. It works the
day you write it, then decays silently: excludes drift, the diff gets slow,
someone comments out the cron line — and the first time it really runs is the
day you need the backup.

restverify is that script, done properly: one command, a recorded history,
a machine-readable JSON envelope, and a 347-test suite that is verified
against real `restic` repositories — including a 1.02 GiB / 1253-file drill
over an SFTP backend run as an unprivileged user
([transcript](docs/I9-DRILL.md)).

## Quick start

```bash
python3 -m pip install restverify        # or: pipx install restverify
```

Requires Python 3.11+ and a `restic` binary on PATH. restverify itself has
**zero runtime dependencies**.

```bash
# one-time: save the repository (and optional source path) to the config
restverify init -r /srv/backup -s /srv/data

# verify the newest snapshot
restverify run -r /srv/backup
```

```text
✓ restored snapshot b61d4bc1: 1253 files / 1.02 GiB in 12s; 0 diffs
```

That's the whole loop. `run` is read-only on the repository and never writes
to your source paths. Restores land in a private (`0700`) temp directory that
is removed on every exit — if the process is killed mid-restore, the next run
sweeps the leftovers. Nothing of the verification is left behind.

Look before you leap:

```bash
restverify run -r /srv/backup --dry-run
```

## What a run compares

With a source path configured (`-s` or saved via `init`), both trees are
walked with the same excludes and compared on:

- the set of entries (files, directories, symlinks)
- per-file size and kind, and symlink targets
- file/byte/symlink totals
- content digests over a deterministic sample

**The sample, precisely** (it is deterministic, so any run can be reproduced):
every file if there are ≤ 200, otherwise a stride of `ceil(total / 200)` in
sorted-path order; the largest file is always included, plus up to 50 files
matching your exclude patterns. Each sampled file is hashed with SHA-256, and
the run digest is the SHA-256 over the serialised `path ␀ size ␀ file-sha256`
lines in sorted-path order. The exact sampled list is in every `--json`
response, so you can re-verify any claim independently.

Not compared, on purpose: mtimes and ordering — they never fail a run.
Reported as warnings, promoted to exit 2 by `--strict`: an empty directory
present on only one side, and file mode/ownership drift.

Symlinks are recorded with their target and never followed (following one
could read outside the restore target). A path that was a file and came back
as a symlink is an error, not a silent read.

**Honest scope:** without a source comparison (`--no-source`), a run proves
the restore *completed* — not that the data matches. The comparison is what
makes it verification; bring a source path when it matters.

## For machines: `--json`

```bash
restverify run -r /srv/backup --json > result.json
```

stdout carries exactly one JSON object and nothing else, in every mode —
success, dry-run, and every failure — so the redirect is always valid JSON.
Teaching text and errors go to stderr. The envelope is versioned
(`"schema": 1`); a breaking change bumps the schema rather than editing it
quietly. Errors carry a stable `kind` taxonomy
(`restic_missing`, `no_snapshots`, `restic_failed`, `source`, …) with a
human `what` and a `hint`.

The exit codes and the envelope are the entire integration surface: there is
nothing to scrape and nothing that changes under you between patch releases.

### Deliver the verdict: `--report-webhook`

```bash
restverify run -r /srv/backup --json \
  --report-webhook https://collector.example/hooks/restverify
```

After the run — pass, mismatch, or error — the exact JSON envelope is POSTed
to the URL (`Content-Type: application/json`, user agent
`restverify/<version>`), so a collector or pager sees the same object the
operator would have seen on stdout.

Delivery is best-effort by contract: a failure is one line on stderr and
**never changes the exit code** — the verdict is the verification's, not the
webhook's. Validation happens before anything runs (`https://` only, timeout
`0 < t <= 60` seconds, default 10; anything else is exit 64 and no network
attempt). Redirects are refused, credentials in the URL are never transmitted
or logged, and there are no retries — one attempt, one line of truth.

## On a schedule

```bash
restverify cron -r /srv/backup            # one ready-to-paste crontab line
restverify cron -r /srv/backup --systemd  # a .service + .timer pair instead
```

`cron` **prints and never installs**: no crontab is modified, `systemctl` is
never called, nothing is written into any unit directory. You stay in control
of your own scheduler.

One note for scheduled runs: a scheduler runs with a minimal environment, so
set `RESTIC_PASSWORD_COMMAND` (or `RESTIC_PASSWORD_FILE`) in the unit or
crontab itself. restverify never stores your password.

## Sandboxed restores

```bash
restverify run -r /srv/backup --sandbox
```

The restore executes inside a disposable container instead of directly on the
host: your own `restic` binary is copied in, your temp dir is bind-mounted,
and the process runs as *your* uid — restored files are owned by you, never
root. Local repositories mount read-only, and the container has no network.
Remote repos (`sftp://`, `rclone:`) resolve inside the container with your own
credentials; a `password_command` that reads a host file works via
`RESTVERIFY_SANDBOX_PASSFILE` (mounted read-only).

The container is purged on every exit — including `kill -9` mid-restore;
containers carry an ownership label, and any orphan from a killed run is
swept automatically by the next run. Requires `docker` (or a compatible
runtime) on PATH with socket access for your user — never sudo. The gate was
drilled against real Docker: a clean sandboxed run, a SIGKILL mid-restore,
and proof the next run swept the orphan and finished with zero leftovers
([transcript](docs/I11-SANDBOX.md)).

Verification, history and exit codes are identical with or without `--sandbox`.

## The nightly CI drill

The restore drill ships as a reusable GitHub Action in this repository, so
your backup repo can drill itself on a schedule:

- [`.github/actions/restore-drill/`](.github/actions/restore-drill) — installs
  restic, downloads the **released** restverify wheel pinned to a version,
  verifies its GPG signature against the release key shipped inside the
  action, and runs two drills: a healthy fixture must exit 0, and a tampered
  fixture (one flipped byte) must exit 2.
- [`templates/ci-drill/`](templates/ci-drill) — a ready-made scheduled
  workflow (point it at your restic repo via secrets) and `drill.sh`, the
  same drill as a standalone script for plain cron, systemd timers, or any
  other CI.

A drill that can't fail is decoration: the tampered-fixture leg exists so a
nightly run that "succeeds" at everything proves nothing. Copy the template,
add your secrets, and a degraded backup turns the job red before you ever
need it for real. The drill runs the same outside GitHub as a plain script —
no GitHub required.

## Signed releases

Every release is signed; verification needs no keyserver:

```bash
# verify the tag
git tag -v v0.2.1

# verify a wheel/sdist from PyPI against the in-repo release key
curl -O https://files.pythonhosted.org/packages/<path>/restverify-0.2.1-py3-none-any.whl
curl -O https://files.pythonhosted.org/packages/<path>/restverify-0.2.1-py3-none-any.whl.asc
gpg --import docs/release-key.asc
gpg --verify restverify-0.2.1-py3-none-any.whl.asc restverify-0.2.1-py3-none-any.whl
```

Release key: RSA3072, fingerprint
`C5F735E977D4D45C1663AA40E4CE56B6CEF87373`, identity *restverify release
signing*; the armored public key ships at
[`docs/release-key.asc`](docs/release-key.asc). Recorded sha256 digests for
the current release and the full upgrade-drill transcript are in
[`docs/RELEASE-VALIDATION-0.2.1.md`](docs/RELEASE-VALIDATION-0.2.1.md).

## History and trends

Every verification writes one row to a local SQLite store — failures
included, because a trend that hides failures is worse than no trend.

- store: `~/.local/state/restverify/history.db` (override: `RESTVERIFY_STATE`)
- retention: newest 1000 rows per repository, pruned on write (the first
  prune announces itself on stderr)
- read it with `restverify report`, or machine-read it with
  `restverify report --json`
- `--dry-run` writes nothing

Moving the whole layout (config + state) for CI or drills:
`RESTVERIFY_HOME=/some/home`. Explicit `RESTVERIFY_CONFIG` / `RESTVERIFY_STATE`
still win.

## Security posture

- restic is invoked **read-only only** — the verbs that mutate a repository
  (backup, forget, prune…) are refused at the call site, by construction, and
  a test pins it.
- One module is permitted to spawn processes; a test pins that too.
- Zero runtime dependencies: the supply chain is Python's standard library
  and your restic binary.
- Restores go to `0700` private temp dirs; nothing persists; source paths are
  never written.
- Details and their rationale: [`docs/SECURITY.md`](docs/SECURITY.md).

## Platform notes

- **Linux** — fully drilled (real restic 0.16.4, real Docker, SFTP backend,
  unprivileged user).
- **Windows** — the full test suite and the run pipeline work natively
  (real `restic.exe`, Windows-cut release artefacts). `--sandbox` needs a
  container runtime and is out of scope there.
- restic: read-only verbs only, tested against 0.16.4; any recent restic
  should behave identically. If yours doesn't, that's a bug — please open an
  issue with the transcript.

## What restverify is not

- **Not a backup tool.** It never calls `backup`, `forget`, or `prune`; it
  can't lose your data because it never deletes anything.
- **Not an orchestrator.** It doesn't schedule your backups, manage
  retention, or replace resticprofile / your systemd units / your scripts.
  It sits underneath all of them and answers the question they don't: *can
  this backup actually be restored?*
- **No cloud, no dashboards, no alerting service.** Exit codes and JSON are
  the whole integration surface — anything that can run a command can consume
  a verdict.

## Documentation

| Doc | Contents |
|-----|----------|
| [`CHANGELOG.md`](CHANGELOG.md) | every release, honestly annotated |
| [`docs/SECURITY.md`](docs/SECURITY.md) | security posture and its rationale |
| [`docs/RELEASE.md`](docs/RELEASE.md) | how releases are cut and signed |
| [`docs/RELEASE-VALIDATION-0.2.1.md`](docs/RELEASE-VALIDATION-0.2.1.md) | upgrade drill, exit-code matrix, release digests |
| [`docs/I9-DRILL.md`](docs/I9-DRILL.md) | the 1 GiB / 1253-file scale drill |
| [`docs/I10-CI.md`](docs/I10-CI.md) | the CI drill, rehearsed |
| [`docs/I11-SANDBOX.md`](docs/I11-SANDBOX.md) | the sandbox gate |

## Status

v0.2.1 is live on [PyPI](https://pypi.org/project/restverify/). Known open
item: `init --json` is not implemented yet.
