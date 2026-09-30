# Release checklist

Feeds **R28** ("Pinned, signed releases"). Nine steps, in order. Nothing here
is automated yet; each step is a command a human runs and reads the output of.
Since I8, the artefacts and the tag are signed — an unsigned tag or unsigned
artefacts fail R28's intent and must not ship.

## 0. Key material

One dedicated signing key per product, sign-only, no expiry (rotation is a
decision on compromise or operator change, not a calendar event — see
docs/SECURITY.md posture). restverify's release key:

- fingerprint `C5F735E977D4D45C1663AA40E4CE56B6CEF87373`
- identity `restverify release signing <contact@wraithwall.online>`
- RSA3072, `scSC` (sign/certify) capability only — no encryption subkey to
  misuse, no authentication use
- no passphrase on this box (isolated build host; `%no-protection` at
  generation). If the key ever leaves this host, regenerate, not copy.
- the public key ships in-repo as `docs/release-key.asc` so a downloader can
  verify artefacts without a keyserver; a revocation certificate exists at
  generation time (GnuPG stores it under `~/.gnupg/openpgp-revocs.d/`).

Point git at it once per clone: `git config user.signingkey <fingerprint>`.

## 1. Version bump

`pyproject.toml` → `[project] version`. Update it in one commit with the changelog
entry so the tag and the artefact agree. The dev suffix (`0.1.0.dev0`) is dropped
for the first real release.

## 2. Changelog

Add the entry to the release notes with three headings, always in this shape:

- what shipped (requirement ids),
- what changed in behaviour a user will notice,
- what is still open (deferred items, named plainly).

## 3. Build

```bash
cd <checkout>
TESTING=1 .venv/bin/python -m pytest      # suite green before artefacts exist
python -m build                            # requires the dev extra: pip install -e '.[dev]'
```

Expect `dist/restverify-<version>-py3-none-any.whl` and the sdist. A wheel that
contains `tests/`, `__pycache__/`, or a `history.db` is a failed build — inspect it:

```bash
python -m zipfile -l dist/restverify-<version>-py3-none-any.whl
tar -tzf dist/restverify-<version>.tar.gz   # no rv_testrun.txt / testsuite_*.txt:
                                            # the sdist is an explicit allowlist
                                            # (pyproject [tool.hatch.build.targets.sdist])
```

Record the **build host and platform per artefact** in the release notes. The
operator's flow is deliberate: the Linux artefacts prove the Linux leg, the
Windows artefacts prove the Windows leg (R19) — line-ending differences
between them are evidence of provenance, not a defect. Cut a given
artefact only after the suite passed on the platform it was built on.

## 4. Install

```bash
python3 -m pip install ./dist/restverify-<version>-py3-none-any.whl
```

`pip` is the primary install path (R20: "pipx/pip"); `pipx install` is the
equivalent for one-venv-per-app setups. Either way the console script lands
on PATH and no runtime dependency exists.

## 5. Smoke test

```bash
restverify --version
restverify --help
restverify cron -r /srv/backup        # prints a line; must not install anything
restverify run -r <repo> --dry-run    # must print the PASS line and write nothing
```

If any of those fails, stop: the release is not shippable. Do not "fix forward"
on a tagged artefact - bump the version and rebuild.

## 6. Tag

```bash
git tag -s v<version> -m "restverify <version>"
git tag -v v<version>                  # paste the 'Good signature' output
git push --follow-tags
```

Signed tags only (`-s`). An unsigned tag fails R28's intent. The signature is
verified with the release key before anything is published; keep the pasted
`gpg:` output in the release notes.

## 7. Sign the artefacts

```bash
gpg --detach-sign --armor dist/restverify-<version>-py3-none-any.whl
gpg --detach-sign --armor dist/restverify-<version>.tar.gz
gpg --verify dist/restverify-<version>-py3-none-any.whl.asc \
             dist/restverify-<version>-py3-none-any.whl     # round-trip both
gpg --verify dist/restverify-<version>.tar.gz.asc dist/restverify-<version>.tar.gz
```

Publish the `.asc` files next to the artefacts (GitHub release attachments;
PyPI does not host signatures) and record the sha256 of each artefact in the
release notes so a downloader can verify both the bytes and the signature.

## 8. Publish

Publish the wheel and sdist to the index (or attach both to the signed tag if
publishing is deliberately deferred). Record the published hash in the release
notes so a downloader can verify the artefact they got is the one that was built.
The index account moves to **trusted publishing** (OIDC from the tagged GitHub
workflow) as soon as the repo is hosted there; the long-lived API token is then
retired. Trusted publishing authenticates the publisher; the signatures from
step 7 authenticate the bytes — the release needs both.

## After the release

Re-open the loop on the open items — C1 (PyYAML
ruling) is the last one; `init --json` closed at I15 (0.3.0). B1 closed at
I7a and U7 was
measured at I7c (8.22 s); a release note that re-lists closed blockers is as
wrong as one that hides open ones. A release that hides those is worse than a
release note that names them.
