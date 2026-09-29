# I11 — `run --sandbox` (R45): what shipped + the gate drill transcript

Executed 2026-09-29 on host `vmi3403127`, real restic 0.16.4 and real Docker
29.1.3. This is the fourth and last increment of the V2 train
(`docs/V2-BUILD-PLAN.md`): the restore can now run inside a disposable
container, per the operator's Plan 1 (docs/V2-PLAN-ADDENDUM.md, Upgrade 4).

## What shipped

| File | Role |
|---|---|
| `src/restverify/sandbox.py` | the sandbox: create → copy restic in → start → exec restore as the invoking uid → purge on every exit; orphan sweep via the `restverify.owner=<pid>` label |
| `src/restverify/restic.py` | seam additions only: `find_binary`, `pid_alive`, `spawn_process` — restic.py stays the only process-spawning module |
| `src/restverify/cli.py` | `run --sandbox` (run-only flag), the additive `sandbox` JSON block, the `sandbox :` note under the R22 line |
| `tests/test_i11_sandbox.py` | 9 tests: no-spawn source scan, lifecycle order + mounts vs a fake runtime, purge-on-failure, no new exit code, orphan-sweep selectivity, flag wiring, additive payload, offline end-to-end |

## How the isolation works

- The container is created **without network** for local repos (the repo is a
  read-only bind-mount at `/restverify-repo`); a remote repo (sftp://,
  rclone:) resolves inside the container with the operator's own credentials
  (`~/.ssh` bind-mounted read-only) and therefore keeps the default network —
  documented, deliberate.
- The **host restic binary** is `docker cp`'d into the container
  (`/usr/local/bin/restic`); no image build, no registry, nothing persists.
- The restore target is the normal **tempstore dir** (0700, ownership marker)
  bind-mounted read-write; the exec runs as the **invoking uid/gid**, so the
  restored tree is owned by the caller and the host-side manifest/compare
  walk is byte-for-byte the code every native run uses. The isolation
  boundary protects the host from the restored *data*.
- **Purge on every exit**: the container is `rm --force --volumes`'d in a
  `finally` plus an exception handler; a `kill -9` (no Python runs) is
  covered by `sweep_orphans`, which every sandboxed run calls first — it
  removes only `restverify-sandbox-*` containers whose owner pid is gone.
- **Password** (R2/R24): the user's own `password_command` rides
  `RESTIC_PASSWORD_COMMAND` into the exec. Because it executes *inside* the
  container, a command that reads a host file needs the documented override
  `RESTVERIFY_SANDBOX_PASSFILE=<host file>` — mounted read-only at
  `/restverify-pass` and used as `cat /restverify-pass` in-container.
  restverify never reads the file.
- **No new exit code**: runtime failures surface as `ResticFailed` → exit 1,
  kind `restic_failed`, with a teaching hint. One documented runtime (docker;
  `RESTVERIFY_SANDBOX_RUNTIME`/`_IMAGE` overridable); never sudo.

## Gate (approved plan wording)

> sandboxed run on a real repo exits 0 with an unchanged `schema: 1` payload
> and **zero orphan containers** after a SIGKILL mid-restore (kill -9 sweep
> pattern)

### Gate 1 — sandboxed restore on a real repo (exit 0, schema 1)

Fixture: 40 random 1 MiB blobs + canary (later grown by 50 × 10 MiB), restic
repo at `/tmp/i11-drill/repo`, run with `RESTVERIFY_HOME` relocated.

```
$ python -m restverify run -r /tmp/i11-drill/repo -s /tmp/i11-drill/fixture --sandbox --json
EXIT=0
schema: 1 | status: pass | exit_code: 0
manifest files: 41 | total: 41943061 | compare errors: 0
sandbox block: {'runtime': '/usr/bin/docker', 'image': 'ubuntu:24.04',
                'container': 'restverify-sandbox-1849828-1790695943', 'purged': True}
GATE1: PASS — real sandboxed restore via docker, schema 1 intact, exit 0
=== orphan check ===
ZERO ORPHANS
```

### Gate 2 — SIGKILL mid-restore → orphan → sweep

Fixture grown to 540 MiB so the restore has a window; `kill -9` fired the
moment the container appeared:

```
=== launch run --sandbox, then kill -9 mid-restore ===
killed pid 1850310 (caught container: restverify-sandbox-1850310-1790696008)
=== orphan present after kill -9? ===
restverify-sandbox-1850310-1790696008	Up 1 second
```

The next sandboxed invocation swept it first and completed clean:

```
=== orphan before: ===
restverify-sandbox-1850310-1790696008
=== new sandboxed run (must sweep the orphan first) ===
  manifest : 91 files, 540.0 MiB, 1 dirs, max depth 0, ...
  sandbox  : restored via /usr/bin/docker container restverify-sandbox-1850734-1790696036 (ubuntu:24.04); purged=yes
  compare  : match vs /tmp/i11-drill/fixture (0 error(s), 0 warning(s); strict off)
EXIT=0
=== orphans after: ===
ZERO ORPHANS — the kill -9 sweep works
=== R22 line ===
✓ restored snapshot c3171bf7: 91 files / 540.0 MiB in 5s; 0 diff(s)
```

## Fake-vs-real corrections (measured on restic 0.16.4 + Docker 29.1.3)

1. **restic's `restore` takes a repository lock** — the first real run failed
   with `unable to create lock in backend … read-only file system` because
   the repo mount is ro. Fixed with restic's own `--no-lock` flag on the
   in-container restore. The fake never exposed this because the fake never
   locks.
2. **`docker exec` on a created-but-never-started container fails** — the
   lifecycle is create → cp → **start** → exec (an earlier draft assumed
   exec would auto-start; it does not on this runtime).
3. **Restored files would have been root-owned** without `--user
   uid:gid` on the exec — the host walk would still have worked, but the
   tree would not match a native restore's ownership. Fixed before it could
   matter.
4. **The password command runs in-container** — `cat $RUNNER_TEMP/...` cannot
   read a host path, hence the explicit `RESTVERIFY_SANDBOX_PASSFILE`
   override (mounted ro at `/restverify-pass`).

All four were found by running the real thing, not by reading code — the
same lesson as I7/I9/I10.

## Contracts held

- Suite: **347 passed** (338 + 9). `test_n5_only_restic_py_spawns_processes`
  passes **unmodified** — sandbox.py contains no spawn call (asserted twice:
  by the I6 walker over the shipped tree and by a dedicated source scan).
- Exit codes unchanged; `schema: 1` envelope grows only the additive
  `sandbox` block; the R22 line keeps its shape with the sandbox note on its
  own line.
- N-negatives intact: no backup/forget/prune anywhere; no listener; no
  platform branching (the sandbox is POSIX/Linux by design, Windows runs
  natively per Ruling 1 — stated in the module docstring).

Suite after the drill: 347 passed. Requirement **R45 shipped**; PHASE2-PLAN
row minted. This closes the V2 train: I8 ✓ I9 ✓ I10 ✓ I11 ✓.
