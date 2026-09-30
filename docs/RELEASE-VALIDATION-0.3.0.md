# Release validation — restverify 0.3.0

Build host: `build-host` (Linux, CPython 3.11.15, LF line endings), 2026-09-30.
Suite at release commit `b76dc7b` (I15 included): **443 passed,
2 skipped** (skips = the two real-restic integration tests, opt-in via
`RESTVERIFY_PROVE_REAL=1`; both pass when enabled — verified against a real
restic 0.16.4 probe repository).

## Train

| Commit | Increment |
|--------|-----------|
| `342c807` | I12 (R47) — `run --report-webhook` |
| `5ec11a4` | I13 (R48) — `prove` |
| `860b8a1` | I13a — prove content check (`restic check --read-data-subset`) |
| `3226e67` | I14 (R49) — `dashboard` |
| `a57626b` | release prep 0.3.0 (README/CHANGELOG/version) |
| `551e142` | product-voice pass (no behavior change) |
| `b76dc7b` | I15 (R50) — `init --json`, the last JSON surface |

Tag `v0.3.0` is signed (`git tag -s`); `git tag -v v0.3.0` output:

```
gpg: Signature made Wed Sep 30 09:40:02 2026 CEST
gpg:                using RSA key C5F735E977D4D45C1663AA40E4CE56B6CEF87373
gpg: Good signature from "restverify release signing <contact@wraithwall.online>" [ultimate]
```

## Artefacts

Built with `python -m build` from the tagged commit (rebuilt after the README fixups below; these are the authoritative digests). Wheel content inspected:
no `tests/`, no `__pycache__`, no `history.db`. Sdist is allowlist-based.

| Artefact | sha256 |
|----------|--------|
| `restverify-0.3.0-py3-none-any.whl` | `833e1e598f3a0365e11a437ba2e788f6886b037773f65e4a1d90b7ca0b596a3b` |
| `restverify-0.3.0.tar.gz` | `521d99616ece79940d7621c2aa32e4838f6740a1b31b4f5160e16602c11e137b` |

Both are GPG-detach-signed (`.asc` next to the artefacts); round-trip
`gpg --verify` passed for both. NOTE: the README fixups in this commit
changed the tree AFTER these artefacts were cut — if the final published
artefacts are rebuilt from the amended tag, the digests above are superseded
by the ones recorded at publish time. The published-digest rule stands: the
hash you publish is the hash a downloader verifies.

## Install smoke (clean venv, wheel)

```
$ restverify --version
restverify 0.3.0
$ pip freeze   # in the install venv
restverify==0.3.0            # zero runtime dependencies, as claimed
```

- `restverify --help` — all six commands listed (init/run/prove/report/cron/dashboard)
- `restverify cron -r /srv/backup` — prints the line + teaching notes, installs nothing
- `restverify run -r /srv/backup --dry-run` — PASS line, nothing written
- `restverify prove -r /srv/backup --dry-run` — PASS line, states both
  verification layers, nothing written
- `restverify dashboard --bind-all` — refused, exit 64, no socket

## Real-restic verification (restic 0.16.4, probe repository)

- `prove --sample 100 --json` on a healthy repo: `status pass`, exit 0,
  `files_verified == files_sampled`, `content_check.ok true`
  ("restic check (read-data) found no errors").
- Locked repository: ResticFailed → exit 1 (could-not-complete), explicitly
  NOT a corruption verdict.
- Escaping order for `restore --include` re-measured: double-backslash FIRST,
  then glob.escape; the other order fails on `weird[1].txt`.
- The flipped-byte drill remains the spine: restore exits 0 with a truncated
  file; check reports "repository contains errors"; prove catches both.

## Exit-code matrix (unchanged by this train)

| Code | Meaning |
|------|---------|
| 0 | verified |
| 1 | could not complete (restic failed, locked repo, no snapshots, restic missing) |
| 2 | data differs (source comparison, prove size/seal failure) |
| 64 | usage — never a verification outcome |
