# Release checklist

Feeds **R28** ("Pinned, signed releases"). Seven steps, in order. Nothing here is
automated yet; each step is a command a human runs and reads the output of.

## 1. Version bump

`pyproject.toml` → `[project] version`. Update it in one commit with the changelog
entry so the tag and the artefact agree. The dev suffix (`0.1.0.dev0`) is dropped
for the first real release.

## 2. Changelog

Add the entry to the release notes with three headings, always in this shape:

- what shipped (requirement ids),
- what changed in behaviour a user will notice,
- what is still open (B1/C1/U7 style honest limits).

## 3. Build

```bash
cd <checkout>
python -m build            # requires the dev extra: pip install -e '.[dev]'
```

Expect `dist/restverify-<version>-py3-none-any.whl` and the sdist. A wheel that
contains `tests/`, `__pycache__/`, or a `history.db` is a failed build — inspect it:

```bash
python -m zipfile -l dist/restverify-<version>-py3-none-any.whl
```

## 4. Install

```bash
pipx install ./dist/restverify-<version>-py3-none-any.whl
pipx list
```

`pipx` is the supported install path (B2 ruling). One venv per app, no
site-packages pollution, and the console script lands on PATH.

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
git push --follow-tags
```

Signed tags only (`-s`). An unsigned tag fails R28's intent.

## 7. Publish

Publish the wheel and sdist to the index (or attach both to the signed tag if
publishing is deliberately deferred). Record the published hash in the release
notes so a downloader can verify the artefact they got is the one that was built.

## After the release

Re-open the loop on the honest limits: B1 (no real restic exercised), C1 (PyYAML
ruling), U7 (install-to-value not measured). A release that hides those is worse
than a release note that names them.
