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
