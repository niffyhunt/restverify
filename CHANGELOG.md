# Changelog

## [Unreleased]
### Added — I11: `run --sandbox` (R45) — the restore runs in a disposable container
- **`restverify run -r <repo> --sandbox`**: the restore executes inside an
  ephemeral container so the host never touches restored data directly. The
  host restic binary is copied into a throwaway `ubuntu:24.04` container
  (image/runtime overridable), the temp restore dir is bind-mounted, and the
  exec runs as the invoking user — restored files are owned by you, never
  root. Local repos mount read-only with no network; remote repos (sftp://,
  rclone:) resolve inside the container with your own credentials.
- **Purge on every exit**, including `kill -9`: containers carry an ownership
  label (`restverify.owner=<pid>`); every sandboxed run sweeps leftovers whose
  owner is gone first. Gate-drilled against real restic 0.16.4 + Docker
  29.1.3: sandboxed restore exit 0 (91 files / 540 MiB, 0 diffs, `schema: 1`
  with an additive `sandbox` block), SIGKILL mid-restore left an orphan, the
  next run removed it and finished clean — zero orphans
  (`docs/I11-SANDBOX.md`).
- **No new exit code**: container-runtime failures are teaching errors (exit
  1). The Single Spawner rule holds — restic.py stays the only process-
  spawning module (`test_n5_only_restic_py_spawns_processes` green,
  unmodified).
- Honest limits: one documented runtime (docker; group socket access, never
  sudo); a `password_command` that reads a host file needs
  `RESTVERIFY_SANDBOX_PASSFILE` (mounted read-only) because the command runs
  in-container; Windows is out of scope — the drill runs natively there.

## [0.2.0] — the signed-drill release — 2026-09-29

Train I8→I10 of docs/V2-BUILD-PLAN.md (approved); I11 (R45 sandbox) rides
0.2.1 — stated here, not hidden.

### Added — I10: CI restore-drill (R44) — the drill ships as a GitHub Action
- **Reusable composite action** `.github/actions/restore-drill/`: apt-installs
  restic, downloads the **released** restverify wheel pinned to a literal
  `x.y.z`, verifies its GPG signature against the release key that ships
  with the action (a copy of `docs/release-key.asc` — a composite action can
  read its own directory, not the caller's repo), installs it, and runs two
  drills: a healthy fixture must exit 0 and a tampered fixture must exit 2.
  No token beyond checkout, no secrets, no artefact uploads, ubuntu runners
  only, **no new CLI flag** — everything composes the existing
  `run`/`init`/`--version` verbs (pinned by test).
- **`templates/ci-drill/`**: a scheduled nightly drill workflow for a user's
  repository (cron + `workflow_dispatch`, repo location and password via
  secrets, action pinned to a tag) and `scripts/drill.sh` — the drill as a
  standalone script for cron/systemd/any CI, asserting the exit-code
  contract in both directions and failing a silent drill (a tampered fixture
  that still exits 0 is the loudest failure there is).
- **This repo's CI** (`.github/workflows/ci.yml`): builds the wheel from the
  commit, stages it like a release, signs it with a throwaway job key, and
  runs the shipped action end to end — CI installs what the commit just
  built, per the I9 drill-method rule. The real release key never appears.
- **Local rehearsal on real restic 0.16.4** (`docs/I10-CI.md`): happy drill
  exit 0 in 1.7 s, tampered exit 2 with the exact diff named, restored
  fixture exit 0 again, R43 store-location proof passed. Two action defects
  were caught and fixed by rehearsal (missing `restic init`; `.sig` vs
  `.asc` signature extension) before they could fail a runner. The drill
  also caught the operator mislabelling an unrestored fixture as pristine.
- Hosted-runner leg of the gate (a nightly scheduled drill on a public
  template repo) **pending operator hosting** — no repo was created or
  pushed.
### Added — I9: restore-drill at scale (B1 residual closes) + R43/R46
- **Scale drill (gate):** a ≥1 GB / ≥1000-file corpus (1253 files, 1.02 GiB
  logical, unicode names, symlink, sparse file, empty dir) backed up to an
  **SFTP backend** and verified by a **non-root user** (`rvdrill`, own venv,
  I8's wheel): exit 0, 0 diffs, 12 s — transcript + reconciliation in
  `docs/I9-DRILL.md`. No fake-vs-real corrections were needed; four
  assumptions confirmed.
- **R43 `RESTVERIFY_HOME`:** one variable relocates the whole layout for
  drill/CI users (config + state under the artificial home), beats XDG vars
  (an inherited XDG_STATE_HOME must not leak drill rows), loses to explicit
  `RESTVERIFY_CONFIG`/`RESTVERIFY_STATE`; absence resolves exactly as before
  (pinned by tests). Field-proven in the drill.
- **R46 metadata drift declared, not asserted:** entries record mode/uid
  best-effort; divergence is an Info warning in the existing `warnings`
  array (no new envelope keys), never fails alone, `--strict` promotes it
  (field-proven: exit 2 with the promoted-warning line). No double report
  where a data diff already explains the path.
- Drill-method rule: rebuild + reinstall the wheel from HEAD before every
  drill (take 1 silently proved the stale 0.1.0 artefact).
### Added — I8: signed release train (R28 closes from PARTIAL)
- Dedicated sign-only release key (RSA3072, fingerprint `C5F735E9…7373`,
  pinned in `docs/RELEASE.md`); the armored public key ships at
  `docs/release-key.asc` so artefacts are verifiable without a keyserver.
- Signed tag `v0.1.0` cut retroactively at the published tree (`49b111b`) —
  the 0.1.0 artefacts predate the key; the remediation is documented in the
  tag message. Future tags are signed at release time.
- `docs/RELEASE.md` now executes the signing: key-material step, `git tag -v`
  verification with pasted output, detached artefact signatures with
  round-trip proof, PyPI trusted-publishing posture, and per-artefact
  build-host/platform provenance (the Windows-leg evidence rule).
- The sdist is an explicit allowlist: the five stray test transcripts found
  in the published 0.1.0 sdist (`docs/VERIFICATION-0.1.0.md`) can never ship
  again — asserted by regression test.

### Standing statuses (long-running items, as of this release)
- **B1 CLOSED** — verification validated at scale: a 1.02 GiB / 1253-file
  corpus over an SFTP backend, run as a non-root user, exit 0 with 0 diffs
  (`docs/I9-DRILL.md`); the earlier two-repo proof stands at I7a.
- **R28 CLOSED** — v0.1.0's signed tag was cut retroactively; from 0.2.0 the
  tag and both artefacts are signed at release time and the public key ships
  in-repo.
- **U7 stands at 8.22 s** install-to-first-verified-run (I7c measurement;
  target was under five minutes).
- **C1 unchanged** — TOML config via `tomllib` stands; PyYAML remains unused
  and `dependencies = []` is asserted by test.
- **R44 hosted leg pending** — the nightly drill's mechanics are proven
  locally against real restic 0.16.4 (`docs/I10-CI.md`); the scheduled run on
  a hosted runner awaits the public template repository, which is an operator
  decision (nothing was created or pushed).

### What changed for users
- Releases are now signed: `git tag -v` verifies the tag; each artefact has a
  detached `.asc` signature next to it and its sha256 recorded in the release
  notes. Verify without a keyserver using `docs/release-key.asc`.
- `RESTVERIFY_HOME` (new): one variable relocates the whole restverify layout
  — config and state — for drill/CI users; it beats inherited XDG vars and
  loses to explicit `RESTVERIFY_CONFIG`/`RESTVERIFY_STATE`.
- Metadata drift (file mode/ownership) is now declared, not asserted: it
  reports as an Info warning and never fails a run; `--strict` promotes it.
- The restore-drill ships as a reusable GitHub Action
  (`.github/actions/restore-drill/`) plus a standalone script and scheduled
  workflow template (`templates/ci-drill/`): point it at your restic repo and
  a degraded backup turns the nightly job red before you need it for real.
  The action installs the released wheel only after verifying its signature.
- Source distributions now ship the CI surface (`.github/`, `templates/`)
  via the sdist allowlist.

### Still open (honest)
- `init --json` is deferred.
- C1 (PyYAML ruling) remains open.
- `run --sandbox` (R45, increment I11) rides the 0.2.1 train — it does not
  gate this release.
- The hosted nightly-drill leg awaits repository hosting (see standing
  statuses); everything it will run is in this release and rehearsed.
- Verify with a repo you can afford to rehearse on.

## [0.1.0] — first release — 2026-09-28

### Shipped (I1→I7)
- **Config + contract (I1):** TOML config (`RESTVERIFY_CONFIG` override; U1: a
  missing config is not an error); a plain `password` key is refused with a
  teaching error, never silently ignored (R2/R24). `restic.py` is the only
  module permitted to invoke restic, and only read-only verbs — mutating verbs
  raise at the call site (N1/N2/N6).
- **Restore + cleanup (I1):** private (0700) temp dirs carrying ownership
  markers, removed on exit; leftovers from a killed process are swept by the
  next run; foreign directories are never touched.
- **Manifest + comparison (I2):** restored-tree manifest, deterministic sample
  sha256, excludes-aware source comparison, exit code 2 on drift.
- **JSON + taxonomy (I3):** `"schema": 1` envelope on every path; stdout stays
  pure; exhaustive exit-code taxonomy test.
- **History + report (I4):** SQLite store at the XDG state dir (failures
  included), `report` trend, 1000-row retention per repository.
- **Cron (I5):** `cron` / `--systemd` print a line or a unit pair and never
  install anything.
- **Security posture (I6):** the negative requirements as executable
  assertions, the dependency claim as a test, `docs/SECURITY.md` with a
  staleness test.
- **Real-repo validation (I7):** fake-restic fidelity (real exit behaviour),
  missing-repo teaching, restic-glob exclude semantics — **B1 closed**, **U7
  measured**, and the report can no longer rot.
- **Native Windows support:** the full suite and the run pipeline work on
  Windows (real `restic.exe` fixture, symlink-privilege probe, suffix-matched
  restore root, UTF-8 output pin, and the previously missing
  `python -m restverify` entry point).

### Standing statuses (long-running items, as of this release)
- **B1 CLOSED** — a real restic 0.16.4 (Ubuntu `0.16.4-2ubuntu0.24.04.3`) was
  exercised against two real repositories, eight commands, exit codes
  0/0/0/0/2/0/1/1; three fake-vs-real assumptions were wrong and were
  corrected in I7b (transcript: `restverify-I7-briefing.md` §4–5).
- **U7 MEASURED** — install-to-first-verified-run **8.22 s** (pipx install
  6.84 s + `restverify init` 0.07 s + first `restverify run` 1.31 s; asciicast
  span 8.62 s) against a target of under five minutes. Pasted transcript, not
  a clock assertion.
- **R28 PARTIAL** — the release checklist (`docs/RELEASE.md`) exists and this
  release published recorded sha256 hashes for the wheel and sdist, but there
  is **no signed tag yet** and the artefacts carry no signature. Closing R28
  (key material, signed tag, signed artefacts) is planned for the next
  release train.
- **C1 unchanged** — TOML config via `tomllib` stands; PyYAML remains unused
  and `dependencies = []` is asserted by test.
- **B2 RESOLVED** — pipx 1.4.3, wheel install, fresh-install dry-run PASS
  (I5c; re-measured on the I7 host at I7c).

### What changed for users
- First public version. Install with `pipx install restverify` (B2 ruling);
  requires Python 3.11+ and a `restic` binary on PATH.
- The exit codes are the integration surface: `0` verified, `1` run could not
  complete, `2` diff mismatch, `64` usage/config — cron/CI act on them; `64`
  never borrows a verification code.
- Verification only: bring your own orchestration; restores always go to
  private temp dirs and your password is never stored.

### Still open (honest)
- `init --json` is deferred.
- C1 (PyYAML ruling) remains open.
- R28: no signed tag yet (see Standing statuses above).
- Verify with a repo you can afford to rehearse on.

## [0.1.0.dev0] — Phase 2 scaffold
- Package layout, CLI surface with full help/examples, exit-code constants,
  12 CLI contract tests.
