# RESTVERIFY — V2 BUILD PLAN (consolidated, for operator approval)

**Status: AWAITING OPERATOR APPROVAL — nothing in this document has been
built.** On approval, building starts at I8 and proceeds strictly in order.

This single document merges three inputs so they ship together in one train:

1. **My V2 plan** (commit `5b56eaf`, `docs/V2-PLAN.md`): signed release,
   validation at scale, CI restore-drill.
2. **The operator's three briefs + Windows ruling** (commit `49b457f`,
   `docs/V2-PLAN-ADDENDUM.md`): sandbox, semantic drift, evidence manifest.
3. **Release-1 corrections** (commit `84299f0`, `docs/VERIFICATION-0.1.0.md`
   + the `49b111b` reconciliation): what the 0.1.0 verification found and
   the fixes already applied.

"Ships together without breaking anything" means: one ordered increment
train, one release (0.2.0), every existing contract held (exit codes 0/1/2/64
unchanged, JSON envelope stays `schema: 1`, the Single Spawner rule, the
N-negative rules, R22's demo shape), and the suite green after every
increment. Details in §6.

---

## 1. Where V2 starts — state after release 1

- 0.1.0 is on PyPI, verified identical to HEAD `6c0eb2a` (modulo version
  string + line endings). Repo version now `0.1.0`. 307 tests green.
- Standing items entering V2: **R28 PARTIAL** (no signed tag), **B1
  residual** (validated only on small local repos), **`init --json`
  deferred** (last G6 label), **C1 unchanged** (TOML stands, PyYAML unused).
- Release-1 hygiene findings, adopted into this plan: stray test
  transcripts must be excluded from the sdist (`rv_testrun.txt`,
  `testsuite_*.txt`); artefact platform provenance is recorded, not
  "corrected" (CRLF from a Windows build is evidence of the Windows leg,
  per the operator's ruling — see §7).

## 2. The increment train (strict order, one commit set each)

| # | Increment | Source | Requirement ids | Gate (single, measurable) |
|---|---|---|---|---|
| 1 | **I8 — Signed release train** | my plan (a) | extends R28 | `git tag -v v0.2.0` verifies against the release key AND the recorded sha256 matches the PyPI-published digest |
| 2 | **I9 — Restore-drill at scale + drift classes** | my plan (b) + operator Plan 2 | B1 residual + R19; **R43** `RESTVERIFY_HOME`; **R46** metadata-drift rule | one drill on a ≥1 GB / ≥1000-file repo, non-root user, SFTP backend: exit 0, 0 diffs, pasted transcript + list of fake-vs-real corrections |
| 3 | **I10 — CI restore-drill GitHub Action** | my plan (e) | extends R21/R22 + the R11/R12 exit contract; **R44** shipped composite action | the public template repo's scheduled drill completes backup→restore→verify (exit 0) in <10 min on a hosted runner, and a tampered fixture exits 2 and fails the job |
| 4 | **I11 — Sandbox restore (`run --sandbox`)** | operator Plan 1 | **R45** ephemeral-container execution | sandboxed run on a real repo exits 0 with an unchanged `schema: 1` payload and **zero orphan containers** after a SIGKILL mid-restore (kill -9 sweep pattern) |

I8 closes the R28 blocker. I9 closes the B1 residual. I10 is the
demo-visible value (a green nightly drill badge). I11 is the operator's
sandbox, landed last so the riskiest validation (I9) precedes the newest
surface.

## 3. What each increment contains (and does NOT)

### I8 — Signed release train
- Does: dedicated signing key; signed tag; detached signatures for wheel +
  sdist published next to the artefacts; RELEASE.md steps 6–7 become
  executed proof, not prose; sdist transcript exclusion fix.
- Does NOT include: key rotation policy, keyservers, HSM ceremony, CI-held
  signing secrets, reproducible-build pledges. Trusted publishing (§8)
  complements it: it authenticates the publisher, signatures authenticate
  the bytes.

### I9 — Scale corpus + semantic drift (merged increment)
- Does: the ≥1 GB / ≥1000-file / unicode / symlink / sparse corpus; SFTP
  backend; non-root drill user (`RESTVERIFY_HOME`, **R43**, keeps CI and
  drills off the real `~/.local/state`); the drift-engine gap the operator's
  Plan 2 exposed that the shipped code does not yet have — **named drift
  classes** and the strict-promotable empty-dir case; **R46**: metadata
  (mode/ownership) drift declared Info-only, best-effort, POSIX-only,
  never fails a run, promotable only by `--strict`.
- Does NOT include: cloud-object-storage targets (S3/B2 — N3 neighbourhood;
  SFTP/rclone are the user's own restic backends), perf benchmarks as
  claims, new compare modes beyond the named classes, Windows-container
  work.

### I10 — CI restore-drill Action
- Does: `restverify/restore-drill-action@v0` composite action (install
  restverify + restic, drill, exit-code-as-job-result); public template
  repo with a nightly scheduled run; installs the **signed** artefact from
  I8 (the supply chain exercises itself).
- Does NOT include: marketplace push, org dashboards, artefact uploads of
  restore trees, runner matrix beyond ubuntu-latest, **any new CLI flag**
  (the action composes existing verbs only).

### I11 — Sandbox restore
- Does: `run --sandbox`; lifecycle create → mount tempstore dir → exec
  restore → exec verify → destroy; **Single Spawner preserved** — runtime
  and in-container restic calls route through the `restic.py` seam so
  `test_n5_only_restic_py_spawns_processes` passes unmodified; no root
  required (group/rootless path documented); purge on every exit including
  kill -9, ownership-marker pattern extended to containers.
- Does NOT include: multi-runtime certification (one supported runtime,
  documented), Windows container path (drills run natively on Windows per
  the ruling), image build/publish automation, host-side tempstore changes.

## 4. Closed without building (recorded so nobody re-plans them)

- **Operator Plan 3 (evidence manifest): SHIPPED in 0.1.0.** `manifest.py`
  walks `scandir`/`stat`-only (never reads contents in the manifest phase),
  sorted deterministic output; `sampling.py` gives the deterministic sha256
  fingerprint; results ride `schema: 1`. Its DoD is the green I3c contract.
  Rider only: a standalone `manifest` subcommand (additive, R8/R12 cover
  it — no new id).
- **`init --json`, systemd template, multi-repo board:** riders, not
  upgrades — they may land in any increment as additive riders.
- **`--entities` mode:** not restverify's (that is hacheck); rejected.

## 5. Version arithmetic

- Train version: **0.2.0** — all four increments are additive; no contract
  change; nothing removed.
- **I8–I10 must land before 0.2.0 ships.** If I11's purge work slips, it
  rides the **0.2.1** train without holding the release; the changelog's
  honest-limits section states which is true at ship time.

## 6. The no-breakage contract (how "without breaking anything" is enforced)

1. Exit codes 0/1/2/64 keep their meanings; I11 mints no new code —
   container-runtime failures surface as exit 1 with a teaching error
   (the U2 pattern).
2. The JSON envelope stays `schema: 1` for the whole train; additive fields
   only (drift classes ride the existing `diffs` array with their `kind`/
   `severity` fields).
3. The Single Spawner rule is asserted by the existing test after every
   increment; a second spawner fails the suite, not production.
4. R22's demo shape and the README honest-limits section are updated, never
   silently widened.
5. Suite green (currently 307) is the per-increment floor; adversarial
   coverage grows (kill -9 container sweep, tampered-fixture CI exit 2,
   non-root drill), it never shrinks.
6. The Windows story is untouched: suite green on Linux **and** native
   Windows before any artefacts are cut.

## 7. Release-1 corrections — what was already fixed, what is policy now

Already applied in release 1 (no V2 work): version reconciliation to
`0.1.0`, README banner pulled forward from PyPI, changelog standing
statuses. Adopted as V2 policy: sdist transcript exclusion (I8), artefact
platform provenance recorded per artefact (RELEASE.md step 3 wording),
CRLF recognised as deliberate Windows-leg evidence — future verifications
normalise line endings before diffing (procedure in
`docs/VERIFICATION-0.1.0.md`).

## 8. Release checklist additions for V2

1. Signed tag verified (`git tag -v` output pasted into release notes) — I8.
2. PyPI **trusted publishing** replaces the long-lived API token in the
   same release; the token is retired.
3. Changelog entry in the 0.1.0 shape: what shipped (I8–I11, one line each,
   ids named), what changed for users, honest limits re-evaluated (B1
   residual either closed for the corpus in scope or its new boundary
   stated).
4. Sdist hygiene: transcripts excluded; build host + platform recorded per
   artefact; suite green on both OSes.

## 9. Id ledger

- **R43** — `RESTVERIFY_HOME` state/config override for drill/CI users (I9).
- **R44** — shipped reusable composite GitHub Action (I10).
- **R45** — `run --sandbox` ephemeral-container execution with no-root,
  single-spawner, kill -9 purge (I11).
- **R46** — metadata drift declared Info-only, strict-promotable, POSIX
  best-effort (I9).
- **R47+** — free.

## 10. Approval block

- [ ] Approve the train as merged (I8 → I9 → I10 → I11, ship 0.2.0 on I8–I10)
- [ ] Approve with changes (name them; the plan is re-cut, not improvised)
- [ ] Approve I8 only first, review the rest after the signing procedure exists
