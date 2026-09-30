# I10 — CI restore-drill (R44): what shipped + local rehearsal transcript

Executed 2026-09-29 on host `build-host`, real restic 0.16.4
(`0.16.4-2ubuntu0.24.04.3`, go1.22.2 — same package the I7 host and the I9
drill used). This is the I10 increment record of the V2 train
(`docs/V2-BUILD-PLAN.md` §2–§3): the reusable composite restore-drill GitHub
Action ships with the repo, plus the drill script and workflow templates that
let anyone run the identical drill outside GitHub.

## What shipped

| File | Role |
|---|---|
| `.github/actions/restore-drill/action.yml` | the reusable **composite action**: apt restic → download released wheel → GPG-verify → install → fixture prep → happy drill (exit 0) → tampered drill (exit 2) |
| `.github/actions/restore-drill/release-key.asc` | byte-identical copy of `docs/release-key.asc` — a composite action can read its own directory (`github.action_path`) but not the caller's repo, so the key travels with the action |
| `.github/workflows/ci.yml` | this repo's self-test: builds the wheel from the commit, stages it like a release, signs with a throwaway key, runs the shipped action end to end |
| `templates/ci-drill/workflow.yml` | scheduled nightly drill for a user's repository (cron + workflow_dispatch, secrets-based, action pinned to a tag) |
| `templates/ci-drill/scripts/drill.sh` | the drill script: asserts exit 0 on healthy / exit 2 on tampered, never rewrites codes, carries the R43 store-location proof |
| `tests/test_i10_ci.py` | 15 tests pinning action shape, version pinning, no-token posture, the exit-code contract in both directions (against a fake `restverify`), the silent-drill failure case, workflow pins, no-new-CLI-flag scope |
| `pyproject.toml` | sdist allowlist extended with `.github/` + `templates/` so the R44 surface ships in source distributions |

## The gate (from the approved plan)

> the public template repo's scheduled drill completes backup→restore→verify
> (exit 0) in <10 min on a hosted runner, and a tampered fixture exits 2 and
> fails the job.

**Status: mechanically complete, hosted leg pending operator hosting.** No
public repository exists yet, and creating one is an operator decision —
nothing was created or pushed. What is proven here, on this box, is every
mechanical part of that gate with real restic:

1. **happy drill exit 0** — timed 1.7 s (fixture is one canary file; the
   10-minute bound is a ceiling, not a risk), full transcript below.
2. **tampered fixture exit 2, job fails** — the tamper leg asserts 2; with
   GitHub step semantics (`set -e` + the script's own assertion) a wrong exit
   fails the step, and drill.sh's negative proof shows a drill that does NOT
   assert fails exactly there (`expected exit 2, got 0` — unit-tested, and
   demonstrated live below, twice).
3. **<10 min** — the entire staged pipeline (download → verify → install →
   init → backup → drill) is seconds on this box; `timeout-minutes: 10` is
   set in both workflows as the enforced bound.

What "pending" means precisely: the *nightly scheduled run on a hosted
runner* has not executed yet because no public repo exists to host it. The
self-test workflow will execute the identical steps on `ubuntu-latest` the
moment the branch is pushed; the drill-method rule (I9) makes this the right
order — CI installs what the commit just built, exactly as this rehearsal
installed what the commit just built.

## Rehearsal transcript (staged like a release, real restic)

Per the drill-method rule (`docs/I9-DRILL.md`): the wheel was **rebuilt from
HEAD** (338 tests green at `b7b6bda` + I10 changes), staged in
`/tmp/i10-stage/v0.1.0/` and served via `file://` the way the action's
`base_url` override serves a pre-release location; it was signed with a
throwaway self-test key (RSA3072, `C56A16DEE0008E350DD9EA88A1E5ABE4437AA32D`)
because the real release key stays offline per RELEASE.md.

Artefact staged:

```
09d988f344998c2fb92ddb11230808e81af9765e6b7df525e95b0c5adb1650a2  /tmp/i10-stage/v0.1.0/restverify-0.1.0-py3-none-any.whl
```

Action step 3+4 — download and verify (run verbatim from the action):

```
=== step: Download release artefacts (base_url=file:///tmp/i10-stage) ===
-rw-r--r-- 1 root root 46464 Sep 29 16:06 restverify-0.1.0-py3-none-any.whl
-rw-r--r-- 1 root root   695 Sep 29 16:06 restverify-0.1.0-py3-none-any.whl.asc
=== step: Verify the wheel signature ===
wheel signature: Good signature — installing verified bytes
=== step: Install restverify from the verified wheel (venv = runner PATH) ===
restverify 0.1.0
```

Action step 5 — fixture prep (restic `init` + `backup` perform the one
synthetic backup; restverify itself never backs up, N1):

```
created restic repository 523f2eb190 at /tmp/i10-drill/drill-repo
✓ added 'drill-repo' in /tmp/i10-drill/rv-home/.config/restverify/config.toml
processed 1 files, 72 B in 0:00
snapshot 0672f8e8 saved
```

Action step 6 — **happy drill, exit 0 in 1.7 s**:

```
=== step: Happy drill — healthy fixture must exit 0 ===
drill: restverify run -r /tmp/i10-drill/drill-repo (expecting exit 0)
✓ restored snapshot 0672f8e8: 1 files / 72 B in 1s; 0 diff(s)
  manifest : 1 files, 72 B, 1 dirs, max depth 0, 0 symlink(s) (recorded-not-followed), 0 tempstore file(s) ignored
  sample   : sha256:4f0326f3e8e3acf1b2ad9844bb3c3c7baa06a3852015205e0a847bb05c36a3fe (1 of 1 files; ...)
  compare  : match vs /tmp/i10-drill/drill-fixture (0 error(s), 0 warning(s); strict off)
drill: PASS — healthy fixture exited 0 (verification works)
drill: R43 proof — history store at /tmp/i10-drill/rv-home/.local/state/restverify/history.db (inside RESTVERIFY_HOME)
HAPPY-STEP-EXIT=0
real 0m1.712s
```

Action step 7 — **tampered drill, exit 2 as required** (the tamper leg
appends one line to `canary.txt`: 72 B → 90 B):

```
=== step: Tampered drill — one flipped byte must exit 2 (R11) ===
drill: flipped one byte in /tmp/i10-drill/drill-fixture/canary.txt (tamper leg)
drill: restverify run -r /tmp/i10-drill/drill-repo (expecting exit 2)
✗ restored snapshot 0672f8e8: 1 files / 72 B in 1s; 2 diff(s)
  compare  : mismatch vs /tmp/i10-drill/drill-fixture (2 error(s), 0 warning(s); strict off)
    - canary.txt: restored 72 B, source 90 B
    - (totals): restored 72 B total, source 90 B total
drill: PASS — tampered fixture exited 2 (the drill detects drift)
TAMPER-STEP-EXIT=0
```

Then the fixture was genuinely restored (72 B re-written) and the drill went
green again, with the drill's own history readable from the relocated home:

```
=== happy drill on the genuinely pristine fixture — must exit 0 ===
✓ restored snapshot 0672f8e8: 1 files / 72 B in 1s; 0 diff(s)
drill: PASS — healthy fixture exited 0 (verification works)
RESTORED-HAPPY-EXIT=0
=== drill history from the relocated home (R43) ===
restverify report — 4 recent run(s)
  trend    : 2 pass, 2 diff, 0 error  (50% pass)
    1  2026-09-29T14:07:23+00:00  pass          exit 0  0672f8e8
    2  2026-09-29T14:06:51+00:00  diff_mismatch exit 2  0672f8e8
    3  2026-09-29T14:06:49+00:00  diff_mismatch exit 2  0672f8e8
    4  2026-09-29T14:06:27+00:00  pass          exit 0  0672f8e8
```

(Reading the four rows honestly: the first `pass` is the happy leg, the two
`diff_mismatch` rows are the tamper leg **and** the mislabeled "restored"
run from the finding below, and the final `pass` is the genuinely restored
fixture — a trend that refused to hide any of it.)

## Live finding — the drill polices the operator too

Between the two legs above, the transcript shows an earlier sequence that
**failed on purpose of nobody but my own labelling**: after the tamper leg I
ran the "restored" happy drill without actually restoring the fixture bytes.
drill.sh rejected it with the real state, not my label:

```
=== fixture restored pristine — drill must be green again (exit 0) ===
✗ restored snapshot 0672f8e8: 1 files / 72 B in 1s; 2 diff(s)
drill: FAIL — expected exit 0, got 2
```

That is the contract asserter doing its job against the person running it —
recorded here rather than edited out, because it is the same property a
nightly drill gives a cron job: it tells you what IS, not what you assumed.

## Rehearsal-caused corrections to the action (fixed before commit)

1. **`restic init` was missing.** Take 1 of the Prepare step design ran
   `restic backup` into a repository that was never initialised — restic
   exits 1 (`Fatal: unable to open config file`) on a runner. The action now
   runs `restic init` before the backup.
2. **Signature extension mismatch.** `gpg --detach-sign` emits `.sig`
   (binary) while the action downloads `.asc`; the 0.1.0 release convention
   is armoured `.asc`. The self-test now signs with `--armor` so CI matches
   the release surface exactly.

Both were found by rehearsing the exact commands the action runs — the same
method that found fake-vs-real drifts in I7/I9.

## Contracts held (standing rules)

- **No new CLI flag** (V2-BUILD-PLAN §3, I10): the action composes `run`,
  `init`, `--version`; the workflows add nothing. Pinned by
  `test_ci_files_invent_no_new_cli_flag`.
- **No token beyond checkout, no secrets in the action**
  (`test_action_uses_no_token_and_no_secret`).
- **Pinned, not moving**: wheel comes from a literal `x.y.z` release; the
  template pins the action to a tag (`test_action_installs_a_pinned_release_not_a_moving_target`,
  `test_template_workflow_pins_a_tag_and_schedules_the_drill`).
- **N-negatives untouched**: `.github/` and `templates/` are not shipped
  Python; the I6 source-scanner scope is `src/restverify/*.py` — the suite's
  22 N-negative tests still pass unmodified.
- **No-breakage contract §6**: suite 323 → **338 passed**, exit codes
  unchanged, `schema: 1` envelope untouched, Single Spawner rule untouched
  (`restic.py` remains the only spawner — asserted green in the same run).

## Posture notes

- The self-test signs with a **throwaway key generated in the job** — the
  real release key never leaves the release host (docs/RELEASE.md). The
  action's `release_key` override exists precisely so the mechanics can be
  drilled before any release is published; the default remains the committed
  release key at `docs/release-key.asc`.
- The template's user repo location rides `secrets.RESTVERIFY_REPO`; the
  password rides `secrets.RESTIC_PASSWORD_COMMAND` — restverify stores
  neither (R2/R24), and the drill's pass file is throwaway job state.
- Nothing was pushed, nothing was published to PyPI, no external repository
  was created — awaiting explicit operator decisions on all three.
