# I9 drill — restore verification at scale (transcript + evidence)

Executed 2026-09-29 on host `vmi3403127` (this box), restic 0.16.4
(`0.16.4-2ubuntu0.24.04.3`, go1.22.2 — same package the I7 host validated).
This is the gate evidence for I9 of the V2 train
(`docs/V2-BUILD-PLAN.md`): one drill on a ≥1 GB / ≥1000-file repo, non-root
user, remote (SFTP) backend — exit 0, 0 diffs, pasted transcript.

## Environment (all provisioned for this drill)

- **restic 0.16.4** installed via apt (was absent on this box; B1's I7a rule applied).
- **`rvdrill`** — dedicated non-root user (uid/gid 1001), own home, own venv:
  the wheel under test was pip-installed from `dist/` into
  `/home/rvdrill/rv-venv` (B2 path), **not** run from the root-owned checkout.
- **SFTP backend**: the local sshd is the remote. Key-only auth proven first:
  `runuser -u rvdrill -- ssh -o BatchMode=yes localhost 'echo SFTP-AUTH-OK as $(whoami)'`
  → `SFTP-AUTH-OK as rvdrill`. Repository location:
  `sftp://rvdrill@localhost/home/rvdrill/drill-repo` — opaque to restverify (N3 held).
- **Password**: `RESTIC_PASSWORD_FILE`-style `password_command = "cat …"`
  in the config; never stored by restverify (R2/R24).
- **Corpus** (deterministic generator, seed 0x1d9 — same seed, same bytes, no RNG):
  1253 files, **1.02 GiB logical** (1003 MB on disk): 1000 × ~1 MB blobs with
  size variety, 250 × ~10 KB unicode text files, 1 unicode-named file
  (`unicode — daten — 007.txt`), 1 symlink, 1 sparse file (50 MB logical,
  ~1 block on disk), 1 empty directory (exercises the structure-warning class).

## The gate run (pasted)

```
$ runuser -u rvdrill -- env HOME=/home/rvdrill \
    /home/rvdrill/rv-venv/bin/restverify run -r drill-sftp
✓ restored snapshot 75aa2528: 1252 files / 1.0 GiB in 11s; 0 diff(s)
  manifest : 1252 files, 1.0 GiB, 2 dirs, max depth 1, 1 symlink(s) (recorded-not-followed), 0 tempstore file(s) ignored
  sample   : sha256:db0d1837efc713e4a594f3d25269685b701a8123a9dd5eb9d5026000ffe2d78d (180 of 1252 files; ...)
  compare  : match vs /home/rvdrill/corpus (0 error(s), 0 warning(s); strict off)
EXIT=0
real 0m12.024s
```

Backup leg (restic itself): `processed 1252 files, 1.023 GiB in 0:11,
snapshot 75aa2528 saved`.

## R43 — `RESTVERIFY_HOME` in anger

One variable relocates **both** config and state; the store lands at
`<home>/.local/state/restverify/history.db` (0700 dir) under the artificial
home, and a `report` from the relocated home shows the drill trend:

```
=== drill rerun: ONLY RESTVERIFY_HOME set (no HOME) ===
✓ restored snapshot 75aa2528: 1252 files / 1.0 GiB in 9s; 0 diff(s)
EXIT=0
=== report from the relocated home ===
restverify report — 2 recent run(s)
  trend    : 1 pass, 0 diff, 1 error  (50% pass)
```

(The `1 error` row is the drill's own first attempt *before* `restverify
init` had written the config — restverify recorded the failure with a
teaching error instead of hiding it. Failures-included history, working.)

Negative proof: with `RESTVERIFY_HOME` pointing at a fresh dir, `report`
does not read the real home — it teaches instead:

```
"exit_code": 1,
"hint": "run your first verification (`restverify run -r <repo>`), then re-run report",
```

## R46 — metadata drift, declared not asserted (field proof)

`chown root:root /home/rvdrill/corpus/text-0000.txt` (uid 1001 restored vs
uid 0 source), then:

```
=== default: info warning, exit 0 ===
default-exit=0 (uid drift present)
✓ restored snapshot 75aa2528: 1252 files / 1.0 GiB in 8s; 0 diff(s)
  compare  : match vs /home/rvdrill/corpus (0 error(s), 1 warning(s); strict off)
    ! text-0000.txt: owner differs: restored uid 1001, source uid 0 (informational; --strict promotes) (warning)

=== --strict: promoted, exit 2 ===
✗ restored snapshot 75aa2528: 1252 files / 1.0 GiB in 7s; 0 diff(s) (+1 warning(s) promoted by --strict)
  compare  : mismatch vs /home/rvdrill/corpus (0 error(s), 1 warning(s); strict on)
strict-exit=2
```

Byte tamper at scale (`printf 'tampered-by-i9-drill' >> blob-0500.bin`):

```
tamper-exit=2
✗ restored snapshot 75aa2528: 1252 files / 1.0 GiB in 7s; 2 diff(s)
  compare  : mismatch vs /home/rvdrill/corpus (2 error(s), 1 warning(s); strict off)
    - blob-0500.bin: restored 1048076 B, source 1048096 B
    - (totals): restored 1098770852 B total, source 1098770872 B total
    ! text-0000.txt: owner differs: restored uid 1001, source uid 0 (informational; --strict promotes) (warning)
```

Worth reading twice: the tampered file was **not** in the 180-file sample —
the sample digest is identical on both sides — and the size/entry-set
comparison caught it anyway and named the exact path. That is the bounded
run's documented price being paid correctly. Tree restored to pristine
afterwards: `clean-exit=0`, `✓ … 0 diff(s)`.

## Fake-vs-real reconciliation (the I7b-style list)

Corrections required: **none** — the fake did not diverge from real restic
0.16.4 on anything this drill exercised. Assumptions *confirmed*:

1. A repository that does not exist exits 1 with restic's own text
   (`Fatal: unable to open config file: stat drill-sftp/config: …`) — the
   teaching error passes the real message through and the hint
   (`restic -r <repo> snapshots` by hand) is accurate.
2. restic **restores empty directories**, so a symmetric empty dir is quiet
   in the compare (the corpus `empty-dir` produced no warning) — matches the
   fake's model of the world.
3. Unicode filenames survive the full round trip (backup → SFTP → restore →
   manifest → compare) untouched.
4. The SFTP backend is invisible above `restic.py` — repo location stayed an
   opaque string end to end (N3 evidence at scale).

## Drill-method note (process finding, not a code finding)

Take 1 of the R43 field proof silently used the **0.1.0 wheel** (predates
I9a) because the drill ran the installed artefact while the feature lived
only at HEAD. Rule going forward, recorded here: **rebuild and reinstall the
wheel from HEAD before every drill** — a drill that proves yesterday's
artefact is worse than no drill, because it looks like a pass. (This is also
the argument for I10's CI drill: CI installs what the commit just built.)

## Cleanup (operator-callable)

Everything drill-specific lives under `/home/rvdrill/` (user, keypair, venv,
corpus, repo, drillhome) plus the `restic` apt package. Remove with:

```bash
userdel -r rvdrill          # removes the whole tree and the user
apt-get remove -y restic    # if the box should not keep restic
```

The drill is re-runnable from this document top to bottom.
