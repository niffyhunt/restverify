# 0.2.1 release validation — upgrade from the published first release

Executed 2026-09-29 on host `build-host`. This is the final overall test
before staging 0.2.1: prove that an operator who installed the **published
0.1.0 from PyPI** can upgrade in place and nothing they rely on breaks —
same config, same history, same exit codes, same demo line — and that the
one new surface (`run --sandbox`) works against real restic 0.16.4 + real
Docker 29.1.3 (gate transcript: `docs/I11-SANDBOX.md`). Everything below is
pasted output, not a summary.

## The matrix

```
=== [A] fresh venv, PUBLISHED 0.1.0 from PyPI ===
restverify 0.1.0
=== [B] 0.1.0 verifies (its run row lands in the XDG store) ===
✓ restored snapshot ee5fdd4f: 2 files / 39 B in 1s; 0 diff(s)
  manifest : 2 files, 39 B, 1 dirs, max depth 0, 0 symlink(s) (recorded-not-followed), 0 tempstore file(s) ignored
  sample   : sha256:6ff22e33…4e068 (2 of 2 files; every file if <= 200 files, …)
  compare  : match vs /tmp/upgrade-drill/fixture (0 error(s), 0 warning(s); strict off)
010-exit=0
=== [C] UPGRADE in place: pip install --upgrade to 0.2.1 ===
restverify 0.2.1
=== [D] nothing broken: 0.2.1 reads the SAME config + history written by 0.1.0 ===
restverify report — 2 recent run(s)
  repo     : /tmp/upgrade-drill/repo
  trend    : 2 pass, 0 diff, 0 error  (100% pass)
  streak   : longest green streak 2 run(s)
  last fail: none in this window
recent runs (newest first):
    1  2026-09-29T15:44:26+00:00  pass          exit 0  ee5fdd4f  -             /tmp/upgrade-drill/repo
    2  2026-09-29T15:43:31+00:00  pass          exit 0  ee5fdd4f  -             /tmp/upgrade-drill/repo
=== [E] 0.2.1 verifies the same repo: PASS line + exit 0 ===
✓ restored snapshot ee5fdd4f: 2 files / 39 B in 1s; 0 diff(s)
021-exit=0
R22 LINE IDENTICAL (modulo timing)
```

Reading [D]: the store rows at 15:43:31 were written **by 0.1.0**; the row at
15:44:26 by 0.2.1. One SQLite file, two tool versions, one continuous trend —
that is the nothing-broken claim in one screenshot of `report`.

## The exit-code contract on the upgraded install (0/1/2/64)

```
=== exit 0: healthy verify ===
exit=0
=== exit 2: tampered source ===
exit=2
=== exit 1: missing repo ===
exit=1
=== exit 64: bad flag ===
exit=64
```

## The new surface works after the upgrade (sandbox, R45)

```
=== sandbox still functional in the upgraded venv ===
✓ restored snapshot ee5fdd4f: 2 files / 39 B in 3s
zero orphans
```

## Live findings from the drill (recorded, not edited out)

1. **My own sequencing error, caught by re-reading the transcript:** the
   first "upgrade" leg ran `pip upgrade` before the 0.1.0 verification leg,
   so the line labelled `[B] 0.1.0 verifies` was actually produced by 0.2.1.
   The drill was scrapped and redone from a fresh venv. A transcript that
   shows what IS, not what was assumed — the same lesson the drill script
   teaches (docs/I10-CI.md).
2. **Product finding (documentation-level, honest):** the *published* 0.1.0
   predates R43, so `RESTVERIFY_HOME` does not exist there — a 0.1.0 run
   under that variable writes to the **real** home. Discovered because the
   first upgrade drill set `RESTVERIFY_HOME` and the files landed in
   `~/.config/restverify` + `~/.local/state/restverify`. The files were
   archived out of the real home (`/tmp/upgrade-drill/home-real-archive/`)
   and the drill redone on the XDG layout 0.1.0 actually honours. Impact on
   users: none at upgrade time — config/history resolve identically without
   the variable, and 0.2.1 honours both. It is an upgrade *note*, not a
   defect: **do not set `RESTVERIFY_HOME` until after upgrading.**
3. restic's `restore` takes a repository lock (learned in the I11 gate) —
   fixed by `--no-lock` on the in-container restore; see `docs/I11-SANDBOX.md`.

## Release checklist position (docs/RELEASE.md)

- Step 3 suite-green-before-artefacts: **347 passed** at `07b3a0c`.
- Artefacts built from the committed tree; wheel/sdist content checks clean
  (no tests/, no transcripts; `.github/` ships in the sdist; `sandbox.py`
  present in both).
- Smoke test on a fresh venv: `restverify 0.2.0 → 0.2.1` banner, dry-run
  `✓ PASS (dry run)` exit 0.
- Signed tag `v0.2.1` → `07b3a0cb93b0e92203998241b34e154f3c55d49c`,
  verified `gpg: Good signature from "restverify release signing
  <contact@wraithwall.online>" [ultimate]`.
- Artefact signatures round-trip clean with the release key.

Recorded sha256 (build host `build-host`, Linux, CPython 3.11.15, LF — the
Linux-leg evidence):

```
28369b6f40ac226687b3241bef620879a98326b7e43a45ab489ddbea7e78bfe2  restverify-0.2.1-py3-none-any.whl
818e19e4d1dc519574bcf8d5b4ff4158b7d21f098962f3a7428da935517b21ed  restverify-0.2.1.tar.gz
```

## PUBLISHED — 2026-09-29

Released to PyPI with an operator-provided token (used once via twine from
this host, then shredded from disk; the operator revoked it after the
release). Post-publish verification against the live JSON API:

```
restverify-0.2.1-py3-none-any.whl: 28369b6f40ac226687b3241bef620879a98326b7e43a45ab489ddbea7e78bfe2  == MATCH
restverify-0.2.1.tar.gz:           818e19e4d1dc519574bcf8d5b4ff4158b7d21f098962f3a7428da935517b21ed  == MATCH
version: 0.2.1 | requires_python: >=3.11 | deps: none at runtime (extras only)
```

- 0.2.0 remains deliberately unpublished (the train's version arithmetic
  supersedes it; the tag still exists for provenance).
- End-to-end from PyPI: fresh venv, `pip install restverify==0.2.1` →
  `restverify 0.2.1` → dry-run `✓ PASS (dry run)`. (One transient CDN lag:
  the first install attempt 404'd ~15 s after upload; a retry succeeded.)
- Release page: https://pypi.org/project/restverify/0.2.1/
- The detached `.asc` signatures and this repo's `docs/release-key.asc`
  remain the artefact-authentication path (PyPI does not host signatures).
