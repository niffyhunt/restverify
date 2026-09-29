# V2 PLAN — three upgrades, one proposed release (0.2.0)

Planning document only — no V2 code is written here. Written 2026-09-29 on
`product/restverify/phase2` after the 0.1.0 release verification
(`docs/VERIFICATION-0.1.0.md`). Requirement ids are allocated against
`docs/PHASE2-PLAN.md`; **R43 was the next free id** at the time of writing and
is allocated here (plus R44). R45+ remain free.

Input constraints carried in from the PyPI README's own "Honest limits"
section and the plan's blockers:

- operators who already run a full orchestrator will not switch — the
  counter-position is verification-only + the exit-code contract;
- verification is only as good as the comparison you ask for;
- no cloud targets, no prune/forget, no dashboards (N3/N2/N4 — these stay).

Candidate list considered: (a) signed release, (b) real-repo corpus at scale,
(c) `--entities` mode, (d) mount-based verification, (e) systemd-timer
template, (f) GitHub Action restore-drill, (g) multi-repo board, (h)
`init --json`.

**(c) is rejected outright: `--entities` is not RestVerify's job — that is
hacheck's scope (PRODUCT 2 of the plan), and putting it here would breach the
one-repo-one-job gate (Gate F).**

---

## Upgrade 1 — Signed release train (closes R28)

**Name:** Sign the release: key material, signed tag, signed artefacts,
published hashes.

**What it does:** Turns the 0.1.0 posture (hashes recorded, nothing signed —
R28 is PARTIAL by the changelog's own admission) into a verifiable supply
chain. The operator generates a dedicated signing key, the release tag is
signed (`git tag -s`), and the wheel/sdist get attached signatures (GPG
detached sigs published next to the artefacts; GitHub artefact attestation as
the belt-and-braces layer). `docs/RELEASE.md` steps 6–7 stop being prose and
become executed, pasteable proof.

**Requirement:** extends **R28** ("Pinned, signed releases") — no new id
needed. R43 stays free for Upgrade 2.

**Increment:** **I8**.

**Measurable gate (single):** a fresh verify of the published 0.2.0 artefacts
succeeds end-to-end: `git tag -v v0.2.0` passes against the release key AND
the sha256 recorded in the release notes matches the PyPI-published digest
(repeat of the Part 1 procedure, now with signatures).

**Does NOT include:** key rotation policy, a public keyserver strategy, HSM
or hardware-key ceremony, signing automation in CI (secrets in CI is its own
risk), reproducible-build pledges. Also does NOT include PyPI trusted
publishing as a *signed- artefact* substitute — trusted publishing removes
the long-lived API token, it does not sign anything; both land, they solve
different problems (see release side below).

**Why this and not something else:** R28 is the only standing requirement
this release explicitly failed to close, and every future user-facing claim
("verify what you downloaded") is built on it; the Part 1 verification showed
the verification *procedure* works — signatures are the missing half.

---

## Upgrade 2 — Restore-drill at scale (closes the B1 residual)

**Name:** Real-repo validation corpus at scale: remote backends, thousands of
files, non-root user.

**What it does:** B1 was closed at I7 with two real repos — both small, both
local-backend, both root. This upgrade extends the validation corpus to what
operators actually run: a multi-gigabyte tree (thousands of files, mixed
sizes, unicode names, symlinks, sparse files), a remote backend (SFTP or
rclone backend of restic), and a dedicated non-root user so the failure modes
of permission-restricted restores are exercised, not assumed. Where the drill
surfaces real restic behaviour that the fake cannot emulate, the fake is
corrected the same way I7b did it.

**Requirement:** extends the **B1 residual scope** and **R19**
(Linux/macOS/WSL platform honesty); needs one new id for the state-location
rule the remote/non-root drill forces: **R43 — `RESTVERIFY_HOME` overrides
the XDG state/config resolution for the drill user**, so a non-root drill and
CI runs never write the invoking user's real `~/.local/state/restverify`
(with absence proof that without the variable nothing changes).

**Increment:** **I9**.

**Measurable gate (single):** one drill run against a ≥1 GB / ≥1000-file
native-backup repo created by a non-root user over a remote (SFTP) backend,
completing with exit 0 and **0 diffs** — pasted transcript, plus the I7-style
list of fake-vs-real corrections it produced.

**Does NOT include:** cloud-object-storage targets (S3/B2 — that trips N3's
neighbourhood and the honest-limits "no cloud targets" line; SFTP/rclone are
restic backends the *user* configures, not provider integrations we ship),
performance benchmarks as a product claim, parallel/multi-repo scheduling,
any dashboard of results. No new compare modes — R9/R26 semantics untouched.

**Why this and not something else:** the changelog still says "verify with a
repo you can afford to rehearse on" — that sentence is the product's biggest
honesty gap, and closing the B1 residual is the only candidate that makes the
existing claim ("verified against real repos") cover the repos people
actually have.

---

## Upgrade 3 — CI restore-drill as a GitHub Action (demo-visible value)

**Name:** `restverify/restore-drill-action@v0` — one-step restore drill in any
CI.

**What it does:** A reusable GitHub Action that installs restverify, installs
restic, restores a repository, runs the drill, and publishes the exit code as
the job result — the exit-code contract (0/1/2/64) becomes a CI surface,
which is exactly the integration the README promises ("exits with a code your
cron/CI can act on"). It is the demo: a scheduled nightly job on a public
template repo showing `✓ restored N files / X in Ys; N diffs` in real CI
logs, on a schedule nobody has to remember.

**Requirement:** extends **R21** (install → first verified run < 5 min, in a
cold CI runner), **R22** (demo line shape), and the restverify analogue of
R40 — the exit-code contract as the machine surface (R11/R12 in the CI
context);
needs one new id for the shipped action itself: **R44 — a reusable composite
action ships with the repo**, pinned to a restverify version, requiring no
token beyond checkout, and failing the job exactly on drill exit ≠ 0.

**Increment:** **I10**.

**Measurable gate (single):** the public template repo's scheduled workflow
completes a full drill (backup → restore → verify → exit 0) in **under 10
minutes** on a GitHub-hosted runner, with the run link recorded in the
briefing — and a deliberately corrupted fixture run in the same repo exits 2
and fails the job (both sides of the contract proven).

**Does NOT include:** a marketplace listing push (the action ships, listing
is a click someone else makes), org-level dashboards, artifact upload of
restore trees (logs only — restores stay ephemeral), runner matrix beyond
ubuntu-latest for v0, any new restverify CLI flag (the action composes
existing verbs only — if it needs a flag, that flag goes through its own
requirement id first).

**Why this and not something else:** of the remaining candidates it is the
only one whose value is visible in 10 seconds of watching the demo (a green
nightly restore-drill badge), and it multiplies Upgrade 1's work — CI
installs the *signed* artefact, so the supply-chain proof is exercised by the
demo itself.

---

## Why not the others (scope discipline, one line each)

- **(d) mount-based verification:** attractive (no full copy), but
  `restic mount` needs FUSE — unavailable in most CI and most containers —
  and it changes the comparison semantics R9/R26 pin; revisit only if
  restore-time becomes a measured complaint (no such measurement exists).
- **(e) systemd-timer template:** already half-shipped (`cron --systemd`
  prints the unit pair); the residual is a copy-paste convenience, not an
  upgrade — fold any improvement into a patch release, not V2.
- **(g) multi-repo board:** a report across repos is one SQL query over the
  existing store — genuinely nice, but it is reporting polish, and "no
  dashboards" is one of the three honest limits we advertise; shipping the
  smallest version of it can ride along in any V2.x without being a
  headline upgrade.
- **(h) `init --json`:** closes the last G6 label and is cheap, but it is
  plumbing for tools that do not exist yet; it can land in any increment as
  a rider without being one of the three.

---

## Release side

**Version number:** **0.2.0.** All three upgrades are additive (new ids, no
contract change: the JSON envelope stays `schema: 1`, exit codes unchanged),
so no major bump; the release-before was 0.1.0, so the natural next is 0.2.0.
If — and only if — Upgrade 2 forces a config-schema change in `config.py`
beyond additive keys, the envelope rule (`SCHEMA_VERSION`, I3c) governs and
the bump discussion reopens; nothing planned here touches it.

**Increments that must land before V2 ships:** **I8, I9, I10 — all three.**
The plan's build order stands: no parallelisation; I8 first (it hardens the
channel the other two will be announced through), then I9 (it is the only
one with real-repo risk that can fork into fake-vs-real corrections), then
I10 (it consumes both). A V2 release missing I9 ships with "verified against
real repos" still scoped to small local backends — that would reopen the
honesty gap the changelog just documented.

**What the release checklist adds for V2** (on top of the existing seven
steps in `docs/RELEASE.md`):

1. **Signed tag** — step 6 (`git tag -s`) becomes executed-and-verified, not
   aspirational: `git tag -v v0.2.0` output pasted into the release notes
   (this is I8's gate, folded into the checklist).
2. **PyPI trusted publishing** — replace the long-lived API token with
   OIDC-based trusted publishing from the tagged GitHub workflow; the token
   is retired in the same release. This complements signing: trusted
   publishing authenticates the *publisher*, signatures authenticate the
   *bytes*.
3. **Changelog entry** — the 0.2.0 entry keeps the 0.1.0 shape: what shipped
   (I8–I10, one line each, requirement ids named), what changed for users,
   still-open honest limits — with the B1-residual line re-evaluated (either
   closed for the corpus in scope, or its new boundary stated).
4. **Carry-over hygiene from the 0.1.0 verification** — two items Part 1
   found and no id owns: build on LF (the 0.1.0 artefacts are CRLF because
   they were cut on Windows; make the build box rule explicit in RELEASE.md
   step 3), and exclude the stray test transcripts
   (`rv_testrun.txt`, `testsuite_*.txt`) from the sdist.
