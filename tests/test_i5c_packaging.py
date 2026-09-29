"""I5c - the packaging surface, asserted without running a build (R28).

Why no build here: `python -m build` takes seconds, which would break the trend
guard for a unit test. The build itself is proved in the increment's packaging
evidence (shell output pasted into the I5c briefing); what these tests pin is the
metadata a build reads, so a commit that quietly adds a runtime dependency, drops
the console script, or unlists the release steps fails fast.
"""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
RELEASE_DOC = ROOT / "docs" / "RELEASE.md"


def _project() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def test_runtime_dependencies_stay_empty():
    """The whole point of the tool: no runtime dependency (C1 position also relies
    on this - sqlite3 and tomllib are stdlib)."""
    assert _project()["project"]["dependencies"] == []


def test_console_script_is_declared():
    """pipx puts this entry point on PATH, so it is part of the contract."""
    assert _project()["project"]["scripts"] == {"restverify": "restverify.cli:main"}


def test_dev_extras_pin_the_tools_the_checklist_names():
    """The release checklist runs pytest and build; both must be pinned."""
    dev = _project()["project"]["optional-dependencies"]["dev"]
    assert any(dep.startswith("pytest==") for dep in dev), dev
    assert any(dep.startswith("build==") for dep in dev), dev


def test_wheel_target_ships_only_the_package():
    """tests/ must never be importable from an install."""
    targets = _project()["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert targets["packages"] == ["src/restverify"]


def test_release_doc_names_every_checklist_step():
    """R28's doc is only useful if a reader can follow it end to end."""
    text = RELEASE_DOC.read_text(encoding="utf-8")
    for step in ("Version bump", "Changelog", "Build", "Install", "Smoke test",
                 "Tag", "Publish"):
        assert step in text, f"docs/RELEASE.md is missing the '{step}' step"
    assert "pipx install" in text
    assert "git tag -s" in text


# ── I8: signed release train ────────────────────────────────────────────────

def test_sdist_is_an_explicit_allowlist():
    """The 0.1.0 sdist shipped five untracked test transcripts
    (rv_testrun.txt, testsuite_*.txt) because the sdist target included
    everything. I8 replaces that with an allowlist, so a forgotten file
    simply does not ship (docs/VERIFICATION-0.1.0.md, finding 2)."""
    sdist = _project()["tool"]["hatch"]["build"]["targets"]["sdist"]
    include = sdist["include"]
    assert isinstance(include, list) and len(include) >= 7
    for expected in ("pyproject.toml", "README.md", "CHANGELOG.md", "docs/",
                     "src/", "tests/"):
        assert expected in include, include
    # the transcripts the verification found must never be listed
    for stray in ("rv_testrun.txt", "testsuite_final.txt", "testsuite_full.txt",
                  "testsuite_run2.txt", "testsuite_run3.txt"):
        assert stray not in include


def test_release_doc_names_the_signing_surface():
    """I8: the checklist must execute the signing, not aspire to it — key
    material, tag verification, artefact signatures, publisher auth, and
    per-artefact provenance."""
    text = RELEASE_DOC.read_text(encoding="utf-8")
    assert "Key material" in text, "the key-material step is missing"
    assert "git tag -v" in text, "the tag must be verified, not just signed"
    assert "gpg --detach-sign" in text and "gpg --verify" in text, \
        "artefact signing + round-trip verification are R28's core"
    assert "trusted publishing" in text.lower(), \
        "publisher authentication (OIDC) is part of the R28 posture"
    assert "C5F735E977D4D45C1663AA40E4CE56B6CEF87373" in text, \
        "the release key fingerprint must be pinned in the doc"
    assert "build host and platform" in text, \
        "per-artefact provenance (Windows-leg evidence) is recorded"
    assert "testsuite_" in text, \
        "the sdist-transcript exclusion must stay visible to the operator"


def test_release_key_ships_in_repo():
    """A downloader must be able to verify artefacts without a keyserver:
    the armored public key is committed."""
    key = ROOT / "docs" / "release-key.asc"
    text = key.read_text(encoding="utf-8")
    assert text.lstrip().startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")
    assert "-----END PGP PUBLIC KEY BLOCK-----" in text
    assert len(text) > 500, "an armored RSA-3072 public key is never this small"


def test_changelog_records_the_i8_train():
    """The signing remediation is itself a change worth a changelog line."""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "I8" in changelog and "signed" in changelog.lower()
