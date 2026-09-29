# PyPI 0.1.0 verification — published artefact vs repo HEAD

Verification performed 2026-09-29, read-only, against
`/home/deploy/oss/restverify` @ `6c0eb2a` (branch `product/restverify/phase2`).

**Context on the base commit.** The task template named the I7c hash (`462bdb4`)
as HEAD, but the branch HEAD is one commit later: `6c0eb2a` ("windows: make
tests and run pipeline work on native Windows"), which touched shipped code
(`cli.py`, `excludes.py`, added `__main__.py`). This matters and is resolved
below: the published wheel was built from `6c0eb2a`, not from `462bdb4` —
proof in §4.

## 1. Download and digest verification

Downloaded with `python3 -m pip download restverify==0.1.0 --no-deps -d
/tmp/pypi_pull` (wheel) and `curl -O` from files.pythonhosted.org (sdist).
PyPI's JSON API (`https://pypi.org/pypi/restverify/0.1.0/json`) lists:

```
version: 0.1.0
requires_python: >=3.11
bdist_wheel restverify-0.1.0-py3-none-any.whl 5ae999e4bf6ffee59e3bfc67fc1a2727f510555723466f6787421a2346c3066e 45133
sdist        restverify-0.1.0.tar.gz          1a54b7dc3c2aff8ea9e7f5be1d3f61192784593a5fc11ed2da874d9a1502ab98 109885
```

`sha256sum` of the downloaded files, pasted next to the published values:

```
5ae999e4bf6ffee59e3bfc67fc1a2727f510555723466f6787421a2346c3066e  restverify-0.1.0-py3-none-any.whl   == PyPI wheel  MATCH
1a54b7dc3c2aff8ea9e7f5be1d3f61192784593a5fc11ed2da874d9a1502ab98  restverify-0.1.0.tar.gz             == PyPI sdist  MATCH
```

Both digests match exactly. Nothing was installed into any venv; everything
stayed under `/tmp/pypi_pull`.

## 2. Wheel contents — every shipped file

The wheel contains exactly 12 package modules + dist-info:

```
wheel_ext/restverify/__init__.py
wheel_ext/restverify/__main__.py
wheel_ext/restverify/cli.py
wheel_ext/restverify/compare.py
wheel_ext/restverify/config.py
wheel_ext/restverify/errors.py
wheel_ext/restverify/excludes.py
wheel_ext/restverify/history.py
wheel_ext/restverify/manifest.py
wheel_ext/restverify/restic.py
wheel_ext/restverify/sampling.py
wheel_ext/restverify/tempstore.py
wheel_ext/restverify-0.1.0.dist-info/{METADATA,RECORD,WHEEL,entry_points.txt}
```

No `tests/`, no `__pycache__/`, no `history.db` — the RELEASE.md wheel
hygiene rule holds. `entry_points.txt` = `restverify = restverify.cli:main`.
Generator: `hatchling 1.32.4`, tag `py3-none-any`, `Root-Is-Purelib: true`.

## 3. Version string in the published artefacts

```
wheel  __init__.py line 8:  __version__ = "0.1.0"
sdist  __init__.py line 8:  __version__ = "0.1.0"
sdist  pyproject.toml l.7:  version    = "0.1.0"
```

As predicted: the operator bumped dev0 → 0.1.0 to publish. Repo HEAD still
carries `0.1.0.dev0` in both files (closed by Part 2).

## 4. Module-by-module diff vs repo HEAD — the CRLF finding

A raw `cmp` reported every file as differing. The reason is **line endings
only**: the published artefacts were built on a Windows machine, so every
shipped file carries CRLF terminators, while the repo tree is LF:

```
wheel_ext/restverify/__init__.py:   Python script, Unicode text, UTF-8 text executable, with CRLF line terminators
/home/deploy/oss/restverify/src/restverify/__init__.py: Python script, Unicode text, UTF-8 text executable
```

Repeating the comparison with CR stripped (`diff <(tr -d '\r' …)`) — wheel
vs repo `src/restverify/`:

```
DIFFERS(ex CR): __init__.py
8c8
< __version__ = "0.1.0"
---
> __version__ = "0.1.0.dev0"
IDENTICAL(ex CR): __main__.py
IDENTICAL(ex CR): cli.py
IDENTICAL(ex CR): compare.py
IDENTICAL(ex CR): config.py
IDENTICAL(ex CR): errors.py
IDENTICAL(ex CR): excludes.py
IDENTICAL(ex CR): history.py
IDENTICAL(ex CR): manifest.py
IDENTICAL(ex CR): restic.py
IDENTICAL(ex CR): sampling.py
IDENTICAL(ex CR): tempstore.py
```

The sdist's `src/restverify/` gives the identical result (same 10 identical,
same single-line version diff). Wheel vs sdist modules: all 12 identical
(CR-stripped) — wheel and sdist agree with each other perfectly.

**Which commit was the wheel built from?** At I7c (`462bdb4`) the package had
11 files — `git ls-tree 462bdb4 src/restverify/` shows no `__main__.py`. The
published wheel contains `__main__.py` and its content is byte-identical
(CR-stripped) to the version added in `6c0eb2a`. Conclusion: **the artefacts
were built from current HEAD `6c0eb2a`**, i.e. they include the Windows fix.
That is *better* than the template assumed, not worse.

## 5. Sdist-only files

The sdist contains the shipped modules plus, as expected: `pyproject.toml`,
`README.md`, `CHANGELOG.md`, `docs/` (PHASE2-PLAN.md, RELEASE.md,
SECURITY.md), `tests/` (20 files), `scripts/security_sweep.py`, `.gitignore`.

- `pyproject.toml`: only diff is `version = "0.1.0"` vs repo `"0.1.0.dev0"`.
- `docs/` (all three), `scripts/`, and all 20 `tests/*.py`: **identical** to
  repo HEAD (CR-stripped).
- `README.md`: differs in the status banner only (expected — see below).
- `CHANGELOG.md`: the sdist carries the release entry; the repo still has the
  pre-release changelog (expected — Part 2 adopts the same entry).

**Finding — stray build files in the sdist (hygiene, not code drift):** the
sdist ships five files that are not in git at HEAD:
`rv_testrun.txt`, `testsuite_final.txt`, `testsuite_full.txt`,
`testsuite_run2.txt`, `testsuite_run3.txt` (test-run transcripts, ~90 KB of
the 110 KB sdist). They were present in the build directory when the sdist
was cut. Harmless (no code, no secrets scanned — they are test transcripts),
but a future release should exclude them from the sdist. Logged as a note
against R28/I5c hygiene, **no new requirement id minted** (R43 stays free for
Part 3).

**README direction finding (feeds Part 2):** the published README's status
banner is NEWER than the repo's. Published: "Status: v0.1.0 — first release.
`run`, `report` and `cron` are implemented and verified against real repos;
`init --json` is still deferred." Repo: "Status: pre-release (Phase 2,
increment I5) … Not yet release-ready." Everything else in the README body
(Why / Install / First run / Scheduled verification / History / Honest
limits, including the "Requires Python 3.11+ and a `restic` binary on PATH"
line) is identical. **Correction direction: repo → published.** Part 2 pulls
the published banner INTO the repo.

## 6. Verdict

**Yes — the published wheel is the same code as this repo at HEAD `6c0eb2a`,
except for the version string bump and line endings.** Complete difference
list, wheel vs repo:

1. `__init__.py` line 8: `0.1.0` (published) vs `0.1.0.dev0` (repo) — the
   expected release bump.
2. CRLF line endings throughout the published artefacts (built on Windows)
   vs LF in the repo — zero content difference after normalisation.
3. (sdist only) version-field equivalent of (1) in `pyproject.toml`, plus the
   README status banner and the CHANGELOG release entry that Part 2 adopts,
   plus the five stray test-transcript files noted in §5.

No behavioural difference. No unexplained drift. **Proceeding to Part 2.**
