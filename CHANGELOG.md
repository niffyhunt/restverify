# Changelog

## [Unreleased]

### Added — I1: config/contract + restore-to-temp + cleanup-on-exit
- `config.py`: TOML config at `~/.config/restverify/config.toml` (XDG-aware,
  `RESTVERIFY_CONFIG` override), multi-repo schema with source, excludes, snapshot
  selector and `password_command`. A **missing config is not an error** (U1).
  A config containing a plain `password` key is **refused** with a teaching error
  rather than silently ignored (R2/R24).
- `restic.py`: the only module permitted to invoke restic. Read-only verbs only;
  mutating verbs (`backup`/`forget`/`prune`/`init`/…) raise at the call site, so
  N1/N2/N6 are enforced in code, not by convention. Missing restic, wrong
  password, missing repo, timeout and unparsable output each produce a teaching
  error naming the likely cause and the next command.
- `tempstore.py`: private (0700) restore dirs carrying an ownership marker;
  removed on exit, and any leftover whose owning process died is swept by the
  next run — the only design that survives `kill -9`. Foreign directories are
  never touched.
- `cli.py`: `init` (saves/updates a repo, prints the next command) and `run`
  (`--dry-run` restores nothing and invokes no restic; `--no-source`, `--strict`,
  `--snapshot` wired). Usage errors exit 64 (see below).
- Exit codes: `0` verified, `1` run could not complete, `2` diff mismatch,
  `64` usage/config problem. **64 is an extension of the PDF contract**, which
  names only 0/1/2 — usage failures are not verification outcomes, so they must
  not borrow those codes. Documented in `--help`.
- Tests: 50 (was 12) — happy/failure/adversarial paths, `kill -9` sweep proof,
  plus absence tests for N1/N2/N6 and the no-network, no-stored-secret rules.

### Known gaps in I1 (honest, per gate G6)
- No file counts, sizes or sha256 manifest yet (I2); `run` says so on stdout.
- No source comparison yet (I2); the source is never passed to restic (I1).
- `--json` is accepted but prints a note that it completes in I3.

## [0.1.0.dev0] — Phase 2 scaffold
- Package layout, CLI surface with full help/examples, exit-code constants,
  12 CLI contract tests.
