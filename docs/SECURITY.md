# restverify — security report (increment I6)

This is the security section of increment I6. It converts the negative requirements
(N3, N4, N5, N7) and R25 ("zero telemetry") from prose into executable assertions,
and pastes the V2 sweep output that proves the current tree is clean.

Reproduce everything in this document with two commands:

```bash
.venv/bin/python scripts/security_sweep.py    # the sweep (exit 0 = clean)
.venv/bin/python -m pytest -v tests/test_i6_negatives.py tests/test_i6_sweep.py
```

## 1. What is asserted, and where

| id | claim | test | assertion | adversarial proof |
|---|---|---|---|---|
| N3 | no cloud-provider SDK, no credential handling | `tests/test_i6_negatives.py::test_n3_*` | no import of 12 cloud SDK names; no *named* `AWS_/AZURE_/GOOGLE_/GCP_` env read (AST) and no such literal (grep); no `access_key`/`secret_key`/`client_secret` token and no amazonaws/googleapis/azure/oracle URL under `src/` | `_scratch_n3.py` (`import boto3`, `os.environ.get("AWS_SECRET_ACCESS_KEY")`, an `s3.eu-central-1.amazonaws.com` URL) → 3 failed; scratch removed → 17 passed |
| N4 | no web framework, no listener | `tests/test_i6_negatives.py::test_n4_*` | no import of 12 web frameworks; no listener call site (`socket.bind/listen`, `ssl.wrap_socket`, `*.serve_forever`, server constructors); no `http.server`/`socketserver`/`ssl` import | `_scratch_n4.py` (`import http.server`, `sock.bind`, `sock.listen`, `ssl.wrap_socket`) → 1 failed; `_scratch_n4b.py` (`import http.server, flask`) → 2 failed; removed → 13 passed |
| N5 | no notification SDK, no webhook | `tests/test_i6_negatives.py::test_n5_*` | no import of 10 notification modules (stdlib `smtplib`/`email` included); no URL in a spawned process' arguments; `restic.py` is the only module that spawns at all | `_scratch_n5.py` (`import smtplib`, `subprocess.run(["curl", …, "https://hooks.slack.com/…"])`) → 3 failed; removed → 12 passed |
| N7 | no Windows code, no GUI | `tests/test_i6_negatives.py::test_n7_*` | no import of 8 GUI toolkits or 6 Windows APIs; no `win32`/`HKEY_`/`C:\` literal; no `os.name`/`sys.platform`/`platform.system` branch for Windows | `_scratch_n7.py` (`tkinter`, `winreg`, `HKEY_LOCAL_MACHINE`, `C:\ProgramData`, branch) → 3 failed; `_scratch_n7b.py` (branch only) → 1 failed; removed → 15 passed |
| R25 | zero telemetry | `tests/test_i6_sweep.py::test_no_telemetry_symbol_or_vendor_url` | no telemetry import, no telemetry-named attribute, no vendor URL (13 hosts) anywhere under `src/` | covered by the group proofs above: the same AST walk that fails on `boto3` fails on a telemetry import |
| dep | zero runtime dependencies | `tests/test_i6_sweep.py::test_every_import_resolves_to_stdlib_or_local` | every import in every shipped module resolves to stdlib or local, using `sys.stdlib_module_names` as the oracle | `import requests` in a scratch tree → the sweep exits 1 and names both rows (below) |

Why the scoping matters, in two lines: `restic.py` legitimately holds the teaching
URL `https://restic.net`, so N3 forbids *provider* URLs (not any URL) and N5 forbids
URLs in *subprocess arguments*; and `restic.py` does a bulk `dict(os.environ)` copy,
so N3 forbids *named* reads of credential variables, not any contact with the
environment.

A vacuity guard (`test_the_source_index_actually_sees_the_shipped_modules`) fails
loudly if the glob or the AST walk ever breaks — otherwise every absence test would
pass for free.

## 2. The V2 sweep, verbatim

Command: `.venv/bin/python scripts/security_sweep.py` (exit code 0)

```
restverify V2 security sweep (I6b)
  root            /home/deploy/oss/restverify/src/restverify
  modules         11
  python          3.11.15
  third-party     0

IMPORTS
  __init__.py      (none)
  cli.py           ., .errors, __future__, argparse, json, os, pathlib, shutil, sys, time
  compare.py       ., .errors, .manifest, __future__, dataclasses, pathlib
  config.py        .errors, __future__, dataclasses, os, pathlib, tomllib
  errors.py        ., __future__
  excludes.py      __future__, fnmatch, pathlib
  history.py       .errors, __future__, dataclasses, datetime, os, pathlib, sqlite3
  manifest.py      .errors, __future__, dataclasses, os, pathlib, typing
  restic.py        .errors, __future__, dataclasses, json, os, shutil, subprocess
  sampling.py      ., .errors, .manifest, __future__, dataclasses, hashlib, os, pathlib
  tempstore.py     .errors, __future__, contextlib, errno, json, os, pathlib, shutil, tempfile, time

RESOLUTION
  import                       origin       modules
  __future__                   stdlib       cli.py, compare.py, config.py, errors.py, excludes.py, history.py, manifest.py, restic.py, sampling.py, tempstore.py
  argparse                     stdlib       cli.py
  contextlib                   stdlib       tempstore.py
  dataclasses                  stdlib       compare.py, config.py, history.py, manifest.py, restic.py, sampling.py
  datetime                     stdlib       history.py
  errno                        stdlib       tempstore.py
  fnmatch                      stdlib       excludes.py
  hashlib                      stdlib       sampling.py
  json                         stdlib       cli.py, restic.py, tempstore.py
  os                           stdlib       cli.py, config.py, history.py, manifest.py, restic.py, sampling.py, tempstore.py
  pathlib                      stdlib       cli.py, compare.py, config.py, excludes.py, history.py, manifest.py, sampling.py, tempstore.py
  shutil                       stdlib       cli.py, restic.py, tempstore.py
  sqlite3                      stdlib       history.py
  subprocess                   stdlib       restic.py
  sys                          stdlib       cli.py
  tempfile                     stdlib       tempstore.py
  time                         stdlib       cli.py, tempstore.py
  tomllib                      stdlib       config.py
  typing                       stdlib       manifest.py
  .                            local        cli.py, compare.py, errors.py, sampling.py
  .errors                      local        cli.py, compare.py, config.py, history.py, manifest.py, restic.py, sampling.py, tempstore.py
  .manifest                    local        compare.py, sampling.py

WORD MENTIONS (raw grep pass, for review — prose is not a violation)
  cli.py:160: 'segment' in: p_init.add_argument("--name", metavar="NAME", help="short name for this re
  manifest.py:32: 'socket' in: KIND_OTHER = "other"           # fifo/socket/device: recorded so nothing i
  manifest.py:221: 'socket' in: # fifo/socket/device: not files, but not hidden either

VERDICTS
  R25 zero telemetry                   PASS
  N3 no cloud SDK / no credentials     PASS
  N4 no web framework / no listener    PASS
  N5 no notification SDK / no webhook  PASS
  N7 no Windows code / no GUI          PASS
  third-party imports                  PASS  (none)
  network modules                      PASS  (none)

REVIEW NOTES (flagged by the sweep, not forbidden by any requirement)
  asyncio    absent    not a network module, but suspicious in a single-threaded CLI

CROSS-CHECK
  grep found 20 import statements; AST walk found 23 distinct modules
  grep roots the AST walk did not see: none
  grep subset of AST: yes

RESULT: PASS — every group clean
```

Refreshed at I7c: the counts moved from 19/22 to 20/23 because `excludes.py` now
imports `re` for restic's glob semantics (I7b-3). `re` is stdlib, so third-party
and network stay at none and every verdict above is unchanged.

## 3. The adversarial proof (I6b)

A scratch module under `src/` is added, the sweep is run, the scratch is removed,
and the sweep is run again. Transcript (elided to the verdict rows):

```
### 1. clean tree
exit=0
  third-party     0
  R25 zero telemetry                   PASS
  N3 no cloud SDK / no credentials     PASS
  N4 no web framework / no listener    PASS
  N5 no notification SDK / no webhook  PASS
  N7 no Windows code / no GUI          PASS
  third-party imports                  PASS  (none)
  network modules                      PASS  (none)
RESULT: PASS — every group clean

### 2. src/restverify/_scratch_net.py added (import socket)
exit=1
  network modules                      FAIL  _scratch_net.py: socket
RESULT: FAIL — network modules

### 3. src/restverify/_scratch_n5.py added (import requests) - a named N-group row
exit=1
  N5 no notification SDK / no webhook  FAIL  _scratch_n5.py: requests
  network modules                      FAIL  _scratch_n5.py: requests
RESULT: FAIL — N5 no notification SDK / no webhook, third-party imports, network modules

### 4. scratch removed
exit=0
  N5 no notification SDK / no webhook  PASS
  network modules                      PASS  (none)
RESULT: PASS — every group clean

### 5. state
ls: cannot access 'src/restverify/_scratch_*.py': No such file or directory
(empty status above = src is untouched)
```

One proof per group (N3, N4, N5, N7) is recorded in section 1's last column; two of
them needed a second scratch because an earlier assertion in the same test
short-circuited the later one (N4's import half, N7's platform-branch half).

## 4. Deviations

**D-I6-1 — a vacuous assertion in a new test, found by its own adversarial proof.**
No shipped code violated anything. The first draft of N7's platform-branch check
looked for the substring `os.name` inside `ast.dump(node)`; that literal never appears
there (`ast.dump` renders `attr='name'` inside `Attribute(value=Name(id='os'))`), so
the check passed even with `os.name == "nt"` in the tree. The N7 scratch proof
(`_scratch_n7b.py`) showed the test staying green, which is exactly what the proof
exists to catch. Fixed in the same commit (`_platform_branches()` compares dotted names
and string literals): the scratch now fails the test, and removing it passes again.

**D-I6-2 — `asyncio` is reviewed, not forbidden.** The increment asks for asyncio to be
flagged rather than banned: it is not a network module, but it would be suspicious in a
single-threaded CLI that is prohibited from spawning anything but `restic`. The sweep
prints it under REVIEW NOTES with status `absent`; the tests assert that a scratch tree
importing `asyncio` still exits 0, while `asyncio` plus `socket` exits 1.

**Scope of the claim (not a deviation, but stated so it is not overread).** The sweep
covers shipped code: `src/restverify/*.py`. It does not sweep `tests/` (which imports
`pytest` and `subprocess` by design) or `scripts/` (which is neither shipped nor
packaged). "Zero third-party imports" therefore means zero in the wheel.

## 5. The no-new-writer boundary

Unchanged by I6, and now partly executable: `restic.py` is the only module that spawns
a subprocess (`test_n5_only_restic_py_spawns_processes` fails if a second spawner
appears); `tempstore.py` owns the temp area; `history.py` owns durable state;
`config.py` owns config. I6 added no writer, no dependency and no code path that emits
an exit code — 260 → 267 tests are tests-only changes plus the sweep script.

## 6. Standing blockers

- **B1 CLOSED at I7a** — a real `restic` 0.16.4 (Ubuntu package
  `0.16.4-2ubuntu0.24.04.3`, go1.22.2) was installed on the operator-ruled I7 host
  `vmi3416386` and exercised against two real repositories, eight commands, exit
  codes 0/0/0/0/2/0/1/1 as specified. Three fake-vs-real assumptions were wrong
  and corrected in I7b (hint selection, the fake's exit codes, the exclude
  matcher); the full transcript is in `restverify-I7-briefing.md` §4–5.
- **C1 awaiting** — PyYAML remains unused, TOML via `tomllib` stays; `dependencies = []`
is asserted by test.
- **B2 resolved at I5c** — pipx 1.4.3, wheel install, fresh-install dry run PASS.
Re-measured on the I7 host at I7c with the same pipx 1.4.3.
- **U7 MEASURED at I7c** — install-to-first-verified-run on the I7 host: pipx install
6.84 s + `restverify init` 0.07 s + first `restverify run` 1.31 s = **8.22 s**
(asciicast span 8.62 s), against a target of under five minutes. No test reads a
clock to assert this; the measurement is a pasted transcript, not an assertion.

## 7. The Go companion (roadmap context, no code here)

The sweep's assertions are written against Python: they parse `ast`, resolve against
`sys.stdlib_module_names`, and read `.py` files. When `restverify-go` exists after I7,
these assertions **must be re-run for the Go binary** — a different toolchain means a
different import surface, a different dependency story (Go modules), and a different
way to prove "no listener, no telemetry". Nothing in I6 anticipates that; this note
exists so the gap is not discovered later.

## 8. Commit citations

- **I6a** `3148d6b` — the negative requirements as executable assertions
- **I6b** `759dec6` — the V2 security sweep and its tests
- **I6c** — the commit that adds this document (see `git log --oneline -1 -- docs/SECURITY.md`)
- **I5c** `eb32e35` — the packaging increment this one builds on

Suite at I6c: **268 passed** (205 at the end of I5, 260 at I6a, 267 at I6b, +1 for the
report assertion in I6c). No new runtime dependency in any of the three commits.
