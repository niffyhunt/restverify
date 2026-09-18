# PHASE 2.0 — PLANNING & VERIFICATION (blocking)

Contract: **"PRODUCT GAP RESEARCH — 2026-09-14 (final)"** = `GAP-RESEARCH-2026-09.pdf`
(`/home/deploy/ezmcyber/.firecrawl/GAP-RESEARCH-2026-09.pdf`, 4 pages, text-extracted
and read in full). Where the PDF, the master prompt, and this repo differ, this
document says so explicitly (see **Conflicts & deviations**). Nothing here is improvised.

Build order is mandated and not parallelised: **restverify → haccheck**.
Scaffolding + test infrastructure only may precede the GO gate below.

---

## 2.0.1 Spec extraction — numbered, testable requirements

### PRODUCT 1 — restverify (PDF lines 53–90)

| # | Requirement (PDF) | Testable assertion |
|---|---|---|
| R1 | `init` writes a multi-repo TOML config | config parses with `tomllib`; 2 repos round-trip |
| R2 | Passwords via keychain / `RESTIC_PASSWORD_COMMAND` **only** | no code path writes a password; absence test over the package |
| R3 | Config records source paths per repo | source persisted and overridable via `--source` |
| R4 | Config records excludes | excludes applied to both restore and compare |
| R5 | Snapshot selector | `latest` default; explicit id/tag selectable |
| R6 | `run` lists snapshots then picks the newest | `restic snapshots --json` is called once, newest chosen |
| R7 | Restore to a temp directory | restore target is under the system temp dir |
| R8 | Produce a manifest + sample sha256 | manifest counts/sizes; N files hashed deterministically |
| R9 | Optional excludes-aware source compare | `--no-source` skips it; excludes honored when enabled |
| R10 | `report` reads SQLite run history, PASS/FAIL | rows have verdict, snapshot id, duration, reason |
| R11 | Exit codes `0=pass 1=restore-fail 2=diff-mismatch` | G2: each code asserted by a test |
| R12 | `--json` | valid JSON on stdout, complete fields, non-zero exit still emits JSON |
| R13 | `--dry-run` | restores nothing; prints what would happen; PASS line still printed |
| R14 | `--no-source` | verification without a source comparison succeeds |
| R15 | `cron` prints a crontab line | output is a pasteable line; nothing is installed |
| R16 | Config at `~/.config/restverify` | default path asserted; `--config` overrides |
| R17 | Temp restore cleaned up on exit | cleanup verified incl. `kill -9` (G3) |
| R18 | No daemon | absence test: no long-running process/listener created |
| R19 | Linux/macOS/WSL | path handling avoids platform assumptions |
| R20 | pipx/pip + Homebrew | entry point installs; Homebrew formula stub shipped (unverifiable on Linux — see C5) |
| R21 | Install → first verified run < 5 min | V3 timed proof |
| R22 | 10-second demo shape `✓ restored N files / X GB in Ys; N diffs; snapshot <id>` | output asserted against this shape (U5) |
| R23 | Read-only on repositories | no write/delete call against a repo |
| R24 | Passwords never stored by the tool | absence test + no secret in logs/history |
| R25 | Zero telemetry | V2: no network imports in shipped paths |
| R26 | Never writes to sources; `--strict` is the explicit compare | source paths opened read-only; `--strict` only tightens comparison |
| R27 | `restic` binary is a documented prerequisite | missing restic → teaching error (U2), never a traceback |
| R28 | Pinned, signed releases | release process doc; pinned dev deps |

### Negative requirements — PRODUCT 1 (PDF line 63)

| # | Must NOT exist | Absence test |
|---|---|---|
| N1 | backup creation | no `restic backup` invocation anywhere in shipped paths |
| N2 | prune/forget | no `restic prune` / `restic forget` invocation |
| N3 | cloud targets | no provider SDK/credential handling; repo is an opaque restic location |
| N4 | dashboards/web UI | no web framework import; no listener |
| N5 | notification integrations | no SMTP/webhook/chat SDK; only exit codes + an optional command hook that the user supplies |
| N6 | borg/tar backends | no borg/tar code path |
| N7 | Windows UI | no Windows-specific code; no GUI toolkit |

### PRODUCT 2 — haccheck (PDF lines 143–168)

| # | Requirement (PDF) | Testable assertion |
|---|---|---|
| R30 | `haccheck <config-dir>` needs nothing else | positional arg only; works on a fixture dir |
| R31 | Semantic YAML parse: dup keys, multi-doc, quoting/bool pitfalls | each pitfall has a fixture and a distinct message |
| R32 | HA-aware unknown-key rule for a ~40-integration starter catalog | catalog ships ≥40 integrations; unknown key in a known integration is reported with file:line |
| R33 | Integration-name case rule | `Sensor` vs `sensor` produces a finding naming the correct form |
| R34 | Duplicate `entity_id` rule | cross-file duplicates reported once each |
| R35 | Automation trigger/action shape rule | malformed trigger/action reported with file:line |
| R36 | Broken `!include` / `!secret` | missing target reported; existing target not |
| R37 | Dangling `packages:` entries | dangling reference reported |
| R38 | Jinja compile, strict-undefined, stubbed context — **the real error, never the masked one** | the `#169958` fixture must show the underlying `TemplateAssertionError` text, not the generic schema message |
| R39 | Severity + plain-language hint per finding | every finding carries tier + one-line hint |
| R40 | Exit `0` / `1` | errors → 1; warnings/info only → 0 (with `--strict` escalating) |
| R41 | `--json` | complete machine-readable findings incl. suppressed counts |
| R42 | Bundled catalog, no runtime fetch | no network import; catalog read from package data |

### Negative requirements — PRODUCT 2 (PDF line 149)

| # | Must NOT exist | Absence test |
|---|---|---|
| N8 | full HA schema | no bundled complete schema; catalog is explicitly partial and self-describing |
| N9 | live-instance communication | no HTTP client to HA; no token/URL handling |
| N10 | auto-fixing | no write path to the config dir in v1 (read-only; asserted by making the fixture dir read-only) |
| N11 | YAML rewriting | no YAML dump/write in shipped paths |
| N12 | Fleet/Edge | absent |
| N13 | no false-error on unknown integrations | unknown integration → informational tier only (asserted) |

---

## 2.0.2 Environment audit (real output, this box)

| Probe | Result | Consequence |
|---|---|---|
| `python3 --version` | 3.12.3 | OK |
| python3.11 / 3.12 | 3.11.15 present, 3.12.3 present | OK |
| python3.10 | **absent** | `tomllib` needs 3.11 → see C1 |
| `uv --version` | 0.12.0 | usable for venvs/tools |
| `pipx` | **absent** | V3 wording says "pipx install"; `uv tool install` is the equivalent — see B2 |
| `restic` | **absent** | BLOCKS I7 real-repo validation — see B1 |
| `asciinema` | **absent** | V4 demo artifact needs an alternative — see B3 |
| `brew` | absent (Linux) | Homebrew formula can be authored, not verified here — see C5 |
| `pdftotext` | present | PDF read in full (232 lines extracted) |

### Git state (2.0.2 requires a clean tree + recorded base commit)

The two products are **standalone repos** (Gate F: one repo, one job). They are
created at `/home/deploy/oss/<product>`, so the WraithWall monorepo's dirty tree
(122 uncommitted entries at audit time, from unrelated in-flight work) does not
contaminate them.

- Parent monorepo `/home/deploy/ezmcyber`: branch `phase1-sandbox-contract`,
  HEAD at audit **`07e8e93`** (note: HEAD moved during this session — it was
  `b344f50` at the start; the intervening P4–P7 commits are breach-monitor work
  and unrelated to these products).
- Product repo `/home/deploy/oss/restverify`: new repo, branch
  **`product/restverify/phase2`**, first commit = this scaffold.

---

## 2.0.3 Dependency lock

| Product | Locked set (master prompt 2.0.3) | What we ship | Justification |
|---|---|---|---|
| restverify | stdlib + PyYAML (+optional keyring) | **stdlib only**; `keyring==25.5.0` optional extra | Zero runtime deps is strictly *inside* the lock (nothing outside it is introduced). The PDF's "PyYAML/JSON" was a suggestion of capability, and this design does not need YAML at all (see C1). |
| haccheck | PyYAML + Jinja2, exact pins | `PyYAML==6.0.2`, `Jinja2==3.1.4` (to be pinned at I1) | Exactly the locked set; nothing else. |
| dev (both) | — | `pytest==8.3.4` | Not shipped to users. |

Rationale for the optional keyring: PDF R2 says passwords come "via keychain /
`RESTIC_PASSWORD_COMMAND` only". `RESTIC_PASSWORD_COMMAND` needs no dependency;
keyring is an *optional extra* only (`restverify[keyring]`) and is never required.

---

## 2.0.4 Increment plan (commit-sized, each with a gate)

Every increment = one commit + one green test run + the standing gates
G1 (tests incl. ≥1 adversarial), G2 (exit codes), G3 (security: no network, no
write to sources, no secrets, temp cleanup under `kill -9`), G4 (U2/U3/U5 text),
G5 (diff ↔ requirement numbers), G6 (honest status).

### PRODUCT 1 — restverify (PDF D1–D7 → I1–I7)

| I | Scope | Extra gate beyond the standing five |
|---|---|---|
| I1 | config/contract + restore-to-temp + cleanup-on-exit (R1,R2,R3,R5,R7,R16,R17,R23,R24,R27,N1,N2,N6) | adversarial: missing `restic`, empty repo, permission-denied temp dir; `kill -9` cleanup proof |
| I2 | manifest + sample sha256 + excludes-aware source compare (R4,R8,R9,R26) | N-diffs == 0 on an identical tree; a single-byte change is detected |
| I3 | error handling + exit codes + `--json` `--dry-run` `--no-source` (R11,R12,R13,R14) | **G2 fully**: all three codes asserted; JSON valid on both success and failure |
| I4 | SQLite run history + `report` (R10) | trend renders; last failure reason in plain language; history survives restart |
| I5 | CLI surface + `cron` printer (R15,R22) | demo line matches the PDF shape within tolerance; `cron` installs nothing |
| I6 | test hardening + security pass (R25,N3,N4,N5,N7) | V2 sweep run and pasted into the report |
| I7 | docs, packaging, Homebrew stub, **≥2 real restic repos**, release prep (R20,R21,R28) | V1–V6 executed; V3 timed proof recorded |

Usability focus carried through: `init` walks the user interactively with sane
defaults; the first `run` prints the PASS line even under `--dry-run`; `report`
shows the trend with the last failure explained in plain language.

### PRODUCT 2 — haccheck (PDF D1–D7 → I1–I7) — starts only after Product 1 ships

| I | Scope | Extra gate |
|---|---|---|
| I1 | config-dir walker + robust YAML loader (R30,R31) | malformed/dup-key/multi-doc fixtures; unicode paths |
| I2 | versioned catalog + unknown-key/case rules (R32,R33,R42) | ≥40 integrations; unknown integration is **info**, never error (N13) |
| I3 | Jinja compile, strict-undefined, stubbed context (R38) | **MANDATORY #169958 fixture.** If the underlying error cannot be surfaced, STOP and report — no degraded ship |
| I4 | `!include`/`!secret`/`packages:` + duplicate `entity_id` (R34,R35,R36,R37) | dangling vs resolvable cases distinguished |
| I5 | severity tiers + suppression + hints + exit 0/1 (R39,R40,R41) | suppression never hides silently: suppressed counts always printed |
| I6 | tests incl. real public HA config corpora + security pass (N8–N12) | read-only proof on a read-only fixture dir |
| I7 | docs, packaging, pre-commit stub, release prep | V1–V6 |

---

## 2.0.5 haccheck risk paragraph (load-bearing)

**Severity tiers.** Three tiers, and the tier assignment is the contract that keeps
false positives from killing adoption: `error` (exit 1) is reserved for conditions
Home Assistant itself will reject or that cannot work — invalid YAML, duplicate
keys, unresolvable `!include`/`!secret`, a Jinja compile failure (the #169958
class), duplicate `entity_id`, and an unknown key **inside a known integration**.
`warning` (exit 0; exit 1 only under `--strict`) covers suspicious-but-possible
shapes such as a deprecated key or an unusual automation trigger shape. `info`
(always exit 0) covers catalog-coverage gaps: an integration we do not know is
**never** an error (PDF mandate N13), it is reported as "not in catalog v0.1" with
the catalog version, so the tool stays honest about its own partiality instead of
inventing errors.

**Versioned catalog layout.** `src/haccheck/catalog/<version>/` containing
`integrations` (names + known keys), `rules` (rule id, tier, message template,
hint), and `meta` (`ha_min_version`, `generated_from`, per-rule
`false_positive_mode`). `--catalog` selects a version; an unknown version errors
with the list of shipped ones. New HA releases get a **new catalog version**
instead of silently editing the old one, so a user can pin behaviour and a
regression can be bisected to a catalog change. Drift mitigation follows the PDF's
"check what we know" philosophy: unknown keys only ever reach `warning` when the
catalog's `ha_min_version` predates the `--ha-version` the user declares, and
`--explain <rule-id>` prints that rule's documented false-positive mode so a user
can judge a hit rather than trust it.

**Suppression mechanism.** Two one-line forms, both requiring no config file:
`.haccheckignore` with `rule-id path-or-glob [reason]` per line, and an inline
`# haccheck: ignore=<rule-id>` on the offending line. `haccheck --suppress-help`
(also shown in the summary of any run that had suppressions) prints a
ready-to-paste example. Suppressed findings are **counted and printed** in both
text and `--json` output — suppression is visible, never silent, mirroring the
`fp_guard` posture already used in this codebase (a suppressed finding is demoted,
never deleted).

---

## Conflicts & deviations (required: "PDF wins over this prompt; inform the operator")

### C1 — Config format vs the dependency lock vs the Python floor (BLOCKING, needs your call)
The PDF says two things that cannot both hold literally:
- MVP: "`restverify init` (multi-repo **TOML** config)" and "config at `~/.config/restverify`".
- Architecture: "Python 3.10+ CLI (stdlib + **PyYAML**/JSON + sqlite3)".

The master prompt then locks "stdlib + PyYAML (+optional keyring)". A TOML config
written by the tool needs a TOML *writer*; `tomllib` (stdlib, read-only) only
exists on **3.11+**, while the PDF's floor is 3.10.

Options:
1. **TOML config, Python 3.11+** — `tomllib` reads; we write a ~20-line serializer for
   our simple schema. No new dependency, PDF's TOML is honoured, floor raised to 3.11.
   *(Recommended.)*
2. TOML config, keep 3.10 — requires a hand-rolled minimal TOML reader (more code,
   more adversarial surface, departs further from the lock).
3. YAML config via PyYAML — honours the lock and 3.10, but contradicts the PDF's
   explicit "TOML" and the prompt's U1 spirit (a config the user must not need to touch).

**Recommendation: option 1.** Scaffold already reflects it (`requires-python = ">=3.11"`,
zero runtime deps). I will not proceed on this without your GO.

### C2 — haccheck catalog format: the PDF contradicts itself
Architecture line says "TOML schema catalog"; the security line says "**bundled JSON**
catalog". Same document, two formats.
**Recommendation:** TOML (`tomllib`, read-only, versioned dir under `catalog/`) because
it is human-diffable in PRs and matches the rest of the tool's config surface; JSON
only if you prefer the security line to win. Recorded here so the choice is yours, not mine.

### C3 — Dependency lock vs "as few as possible"
The PDF permits PyYAML; this design ships **zero** runtime dependencies. That is stricter
than the lock, not looser, so nothing outside the permitted set is introduced. Flagged
because it is a deliberate deviation from the PDF's stated stack, in the safe direction.

### C4 — No license specified anywhere in the PDF
Both products are to be open-source (prompt: "free, open-source CLI tools") and V5
requires an honest README, but the PDF names no license. **Your call: MIT or Apache-2.0.**
restverify shells out to the `restic` binary (BSD-2-Clause) and links no restic code, so
there is no copyleft entanglement either way. This is a release-blocking (V-gate) item,
not a coding blocker.

### C5 — Homebrew stub cannot be verified on this box
`brew` is absent (Linux). I7 will author the formula and mark it **UNVERIFIED**;
claiming otherwise would breach G6/V-gates.

---

## Blockers (must be resolved before the increments that need them)

| # | Blocker | Needed by | Proposed resolution |
|---|---|---|---|
| B1 | `restic` not installed on this box | **I7** (PDF: "validation against ≥2 real restic repos, local backend acceptable") | install restic (apt/release binary) and build two throwaway local repos; I1–I6 can proceed without it using a fake-restic harness for unit tests |
| B2 | `pipx` absent (V3 says "pipx install from local artifact") | **V3 / I7** | install pipx, or record `uv tool install` as the equivalent and state the substitution explicitly in the V3 proof |
| B3 | `asciinema` absent (V4 demo artifact) | **V4 / I7** | install asciinema, or ship a scripted `script(1)` typescript + the exact command transcript |
| B4 | Biggest product risk (PDF): operators running a full orchestrator will not switch | design, not tooling | mitigation is the PDF's own: verification-only positioning + the exit-code contract; stated verbatim in the README's honest-limits section |

---

## HALT-gate check before GO

- PDF vs repo divergence found? **Yes → reported** (C1, C2, C3, C5 above, none improvised).
- Dependency outside the locked set forced? **No** (C3 makes the set smaller).
- A PDF claim failing under test? **Not yet tested** — the first such claim to test is the
  #169958 masking behaviour at haccheck I3; per the halt rule, if it no longer reproduces
  I will report the delta and **not** improvise.

---

## GO GATE (2.0.4 requires STOP here)

**Status: scaffold committed; no product behaviour implemented. Awaiting operator GO.**

On `GO`, I proceed in order: **restverify I1 → I2 → … → I7**, then **haccheck I1 → … → I7**,
reporting the full R1–R9 completion report per product. No parallelisation. No Phase 3.

Outstanding decisions requested:
1. **C1** — TOML + Python 3.11+ (recommended) / TOML + 3.10 custom reader / YAML.
2. **C2** — haccheck catalog: TOML (recommended) or JSON.
3. **C4** — license: MIT or Apache-2.0.
4. **B2/B3** — may I install `pipx` and `asciinema` on this box, or should I record the
   `uv tool install` / scripted-typescript substitutions for V3/V4?
5. **B1** — may I install `restic` + create two throwaway local repos for I7 validation?
