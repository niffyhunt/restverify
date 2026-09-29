# V2 PLAN — ADDENDUM: operator rulings + the three planning briefs

Addendum to `docs/V2-PLAN.md`, written 2026-09-29 after two operator inputs:
(1) a ruling on Windows-local release builds, and (2) three planning briefs
(sandbox / semantic drift / evidence manifest). Nothing here re-opens the
three committed upgrades; it extends, re-slots, or closes them against the
briefs. Id allocation continues per the standing rule (read
`docs/PHASE2-PLAN.md` first): after the main plan, R43 and R44 are taken;
this addendum mints **R45 and R46** and leaves R47+ free.

---

## Ruling 1 — Windows-local release builds are deliberate (do NOT "fix")

The 0.1.0 verification found the published artefacts carry CRLF (built on a
Windows machine) and the Part-1 write-up suggested making LF an explicit
build rule. **The operator overrules that suggestion.** The reason: releases
are cut locally, first on Linux to prove the product works there, then on
Windows to prove it works there too — the line-ending signature of the
artefact is deliberate evidence of which platform the release was validated
on. This is exactly the dual-platform proof R19 asks for, not a defect.

Consequences, committed into the record:

- The V2-PLAN release-checklist item "build on LF" is **withdrawn**. It is
  replaced with: RELEASE.md step 3 records the build host and platform for
  each artefact, and a release is only cut after the suite has passed on
  both Linux and native Windows (already true for 0.1.0: the Windows commit
  `6c0eb2a` exists precisely to make that possible).
- The stray-sdist-transcripts hygiene item (exclude `rv_testrun.txt`,
  `testsuite_*.txt`) stands unchanged — that one is accident, not signal.
- The wheel's CRLF content is byte-different but semantically identical to
  the repo; future verifications should normalise line endings before
  diffing (the procedure `docs/VERIFICATION-0.1.0.md` already uses).

## Ruling 2 — `docs/V2-PLAN.md` remains the base; briefs are reconciled below

The three briefs arrive from a different planning session and speak as if
I2/I3/I4 never shipped ("Implement the logic…", "Build the Evidence
layer…"). Ground truth from the shipped code:

- **Plan 3 (Evidence Manifest)** — **already shipped in 0.1.0.**
  `manifest.py` walks iteratively with `scandir`/`stat`, never reads file
  contents during the manifest phase (module docstring, lines 6, 173–213),
  sorts entries (`sorted(handle, key=lambda e: e.name)`, line 174;
  directory roll-up emitted depth-first-sorted, line 112); `sampling.py`
  produces the deterministic sha256 sample; results ride the `schema: 1`
  envelope (`SCHEMA_VERSION`, cli.py). The Definition of Done (`run --json`
  with manifest + sample blocks matching the public contract) is the I3c
  contract test, green. **No new id, no new increment. Closed as SHIPPED
  (R8/R12/R29 + I2/I3).** The brief's remaining novelty — a *standalone*
  manifest subcommand emitting just the manifest block — is noted as a
  possible rider (see Open riders).
- **Plan 2 (Semantic Drift)** — **largely shipped; one genuine gap.**
  `compare.py` already classifies per-path drift with a `kind` + `severity`
  machinery (`Diff(path, kind, detail, severity)`), covers missing-from-
  source/restore, kind, symlink target, size, and sampled `content` drift;
  excludes are honoured (the matcher sits in front of both trees, per
  R4/R9); comparison is O(N) over the manifest; the tampered-tree DoD
  (specific paths + drift type, exit 2) is the I2 adversarial gate. The
  `--strict` flag exists (cli.py:221, 427–428) and promotes warnings to
  failure (cli.py:646–648) — but the **only** current warning class is the
  empty-directory asymmetric case, and the brief's three-tier vocabulary is
  richer than the code's current two-and-a-half tiers. The genuine gap:
  **"directory present but empty where the source has content" is recorded
  as a warning, not its own Info/strict-promotable drift class with a
  stable name**; and metadata drift (mode/ownership) is explicitly not
  compared. Folded into Upgrade 2 (I9) as an extension, below — it is the
  same comparison engine, exercised at scale.
- **Plan 1 (Detonation Sandbox)** — **genuinely new; adopted as V2's fourth
  upgrade, slotted behind I10.** No Docker/sandbox surface exists anywhere
  in `src/` (verified). Details and constraints below.

## Upgrade 4 (from Plan 1) — Restore inside a disposable container

**Name:** `run --sandbox` — restore and verify inside a purged container.

**What it does:** Adds an optional isolation boundary: the restore and
comparison run inside an ephemeral container, so the operator's host never
touches restored attacker-influenceable data and the drill is portable to
machines with no native restic. Lifecycle per the brief: create container →
mount the tempstore dir → exec restore → exec verify → destroy container,
with the purge enforced regardless of verification exit code.

**Requirement:** new id **R45 — `run --sandbox` executes restore+verify
inside an ephemeral container** with: the **Single Spawner rule preserved**
(container/runtime and in-container restic calls are routed through
`restic.spawn_process`'s seam — `restic.py` stays the only process-spawning
module; the existing absence test `test_n5_only_restic_py_spawns_processes`
must keep passing unmodified); **no root requirement** (socket access via
group membership or rootless runtime, documented, never `sudo` in the happy
path); **purge on all exits** including kill -9 (tempstore-style ownership
markers extended to containers); and **N-negative compatibility** — the
sandbox composes the existing read-only verbs; it never introduces
backup/forget/prune, network listeners, or a daemon.

**Increment:** **I11**, after I10.

**Measurable gate (single):** `restverify run -r <repo> --sandbox` on a real
repo exits 0, produces the manifest + report row identical in shape to the
non-sandbox run (`schema: 1` unchanged), and leaves **zero orphan
containers/images/volumes** — asserted by an adversarial test that SIGKILLs
the driver mid-restore and then proves the next invocation sweeps the
orphan (the I1 `kill -9` pattern, applied to containers).

**Does NOT include:** rootless-container certification across runtimes
(one supported runtime, documented), sandboxing on Windows v1 (the drill
runs natively there — see Ruling 1; a Windows-container path is its own
future id), container image build/publish automation, and any change to
tempstore semantics on the host side.

**Why this and not something else:** the operator asked for it explicitly,
it is the only brief that adds a *new* safety property (isolation) rather
than re-describing shipped behaviour, and it inherits the tempstore ownership
design rather than fighting it. Honest caveat recorded: this widens the
platform matrix before I9's scale corpus lands — the order I8→I9→I10→I11
keeps the riskiest validation (I9) ahead of the new surface.

## Reconciliation table (briefs → ids/increments)

| Brief | Status against shipped code | Where it lands |
|---|---|---|
| Plan 3 — Evidence Manifest | SHIPPED (manifest/sampling/envelope exist; DoD is the I3c contract) | Closed, no id; standalone manifest subcommand noted as an Open rider |
| Plan 2 — Semantic Drift | MOSTLY SHIPPED (Diff kind/severity, excludes, O(N), exit-2 DoD, --strict promotion exist) | Extension of Upgrade 2 (I9): named drift classes + strict-promotable empty-dir case + metadata-drift decision; **R46** mints the metadata rule |
| Plan 1 — Detonation Sandbox | NEW (no sandbox/Docker surface exists) | Upgrade 4, **I11**, new **R45** |

**R46 — metadata drift is declared, not compared.** Mode/ownership bits are
*reported* as an Info-level `metadata` class on a best-effort basis where the
platform exposes them (POSIX only; Windows restore semantics differ by
design, per Ruling 1's spirit), never fail a run, and are promoted only by
`--strict`. Rationale: ownership numbers are the classic false-positive
source on restores across users, and the exit-code contract must not decay
into noise — the opposite failure of the one R22's shape guards against.

## Open riders (explicitly NOT upgrades; may ride along in any increment)

- Standalone `manifest` subcommand printing the manifest block only (Plan
  3's residual; cheap, additive, needs no new id — covered by R8/R12).
- `init --json` (the deferred I3 ruling Q4; closes the last G6 label).
- Multi-repo report line in `report` (one query over the existing store).

## Updated release arithmetic

- Increments: **I8 (signed release) → I9 (scale corpus + R46 drift classes)
  → I10 (CI action) → I11 (sandbox, R45)**, strictly in order, no
  parallelisation (plan rule).
- Version: **0.2.0** unchanged. All four are additive; the JSON envelope
  stays `schema: 1`; exit codes unchanged (the sandbox does not mint a code;
  container-runtime failures surface as exit 1 with a teaching error, per
  the U2 pattern).
- V2 ships when I8–I10 are green. **I11 may be the V2.1 train** if its
  adversarial purge work slips — the release must not wait on the newest
  surface, and the changelog's honest-limits section says so either way.
- Release checklist (V2-PLAN) as amended by Ruling 1: signed tag verified,
  PyPI trusted publishing, changelog in the 0.1.0 shape, sdist transcript
  exclusion, build-host/platform recorded per artefact, suite green on both
  Linux and Windows before the artefacts are cut.
