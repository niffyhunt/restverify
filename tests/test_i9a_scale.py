"""I9a — R43 (RESTVERIFY_HOME) and R46 (metadata drift declared, not asserted).

R43: a drill/CI user must be able to relocate restverify's whole footprint
(config + state) with one variable, and the variable must beat the XDG vars —
otherwise an inherited XDG_CONFIG_HOME/XDG_STATE_HOME would leak drill rows
into a real user's layout. Absence is proven too: with the variable unset,
every path resolves exactly as before (the pre-I9 behaviour is pinned).

R46: mode/uid are recorded best-effort in the manifest and compared
like-for-like; divergence is an *Info* diff in the existing warnings array
(severity "info"), never a failure on its own, promotable only by --strict.
No new JSON keys — the schema 1 envelope is untouched by construction, and a
test pins that too.
"""
import os

import pytest

from restverify import compare, config, history, manifest, sampling


# ── R43: RESTVERIFY_HOME ────────────────────────────────────────────────────

def test_r43_home_beats_xdg(monkeypatch, tmp_path):
    """The drill override wins even when XDG vars are inherited — that leak is
    the exact failure R43 exists to prevent."""
    fake_home = tmp_path / "drillhome"
    monkeypatch.setenv(config.HOME_ENV, str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "leaked-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "leaked-state"))
    monkeypatch.delenv(history.STATE_ENV, raising=False)  # the autouse fixture sets it
    assert config.default_config_path() == \
        fake_home / ".config" / "restverify" / "config.toml"
    assert history.state_dir() == \
        fake_home / ".local" / "state" / "restverify"


def test_r43_home_relocates_the_store_write(monkeypatch, tmp_path):
    """End to end: with the variable set, a recorded row lands under the
    artificial home and nowhere else."""
    fake_home = tmp_path / "drillhome"
    monkeypatch.setenv(config.HOME_ENV, str(fake_home))
    monkeypatch.delenv(history.STATE_ENV, raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    path, _pruned = history.record(history.RunRecord(
        repo="r", status="pass", exit_code=0,
        started_at=history.iso(1), finished_at=history.iso(1),
        duration_ms=1, tool_version="t", schema=1))
    assert path == fake_home / ".local" / "state" / "restverify" / "history.db"
    assert path.exists()


def test_r43_absence_leaves_every_path_unchanged(monkeypatch):
    """With the variable unset, resolution is byte-identical to the pre-I9
    rule (RESTVERIFY_STATE/XDG/home), so no existing user layout moves."""
    monkeypatch.delenv(config.HOME_ENV, raising=False)
    monkeypatch.delenv(history.STATE_ENV, raising=False)  # the autouse fixture sets it
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg/cfg")
    monkeypatch.setenv("XDG_STATE_HOME", "/xdg/state")
    assert config.default_config_path() == \
        config.Path("/xdg/cfg") / "restverify" / "config.toml"
    assert history.state_dir() == \
        config.Path("/xdg/state") / "restverify"
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert config.default_config_path() == \
        config.Path.home() / ".config" / "restverify" / "config.toml"
    assert history.state_dir() == \
        config.Path.home() / ".local" / "state" / "restverify"


def test_r43_state_env_still_beats_the_drill_home(monkeypatch, tmp_path):
    """Explicit base > RESTVERIFY_STATE > RESTVERIFY_HOME: the documented
    precedence holds, so a drill can pin the store precisely."""
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "drillhome"))
    monkeypatch.setenv(history.STATE_ENV, str(tmp_path / "pinned"))
    assert history.state_dir() == tmp_path / "pinned"


def test_r43_explicit_base_beats_everything(monkeypatch, tmp_path):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "drillhome"))
    monkeypatch.setenv(history.STATE_ENV, str(tmp_path / "pinned"))
    assert history.state_dir(tmp_path / "explicit") == tmp_path / "explicit"


# ── R46: metadata drift, declared not asserted ─────────────────────────────

def _build_pair(tmp_path, source_mode, restored_mode, source_uid=1000,
                restored_uid=1000):
    """Two trees, one file each, mode/uid forced apart on demand.

    Returns the two manifests plus the restored-side sample built exactly the
    way the CLI builds it (the sample is a required argument of compare()).
    """
    src, rst = tmp_path / "src", tmp_path / "rst"
    src.mkdir()
    rst.mkdir()
    f1, f2 = src / "data.bin", rst / "data.bin"
    f1.write_bytes(b"identical-bytes")
    f2.write_bytes(b"identical-bytes")
    os.chmod(f1, source_mode)
    os.chmod(f2, restored_mode)

    def snap(root, uid):
        man = manifest.build(root)
        # force the uid the caller wants (chown needs privileges we may not have)
        for e in man.entries:
            e.uid = uid
        return man

    rest_man = snap(rst, restored_uid)
    rest_sample = sampling.sample_tree(rest_man, rst, ())
    return rest_man, snap(src, source_uid), rest_sample


def test_r46_mode_drift_is_info_and_never_fails(tmp_path):
    rest_man, src_man, rest_sample = _build_pair(tmp_path, 0o644, 0o600)
    comparison = compare.compare(rest_man, rest_sample, tmp_path / "src")
    meta = [w for w in comparison.warnings if w.kind == "metadata"]
    assert len(meta) == 1 and meta[0].severity == "info"
    assert "mode differs" in meta[0].detail
    assert comparison.exit_code() == compare.EXIT_PASS, \
        "R46: metadata drift alone must never produce exit 2"


def test_r46_uid_drift_is_info_too(tmp_path):
    rest_man, src_man, rest_sample = _build_pair(tmp_path, 0o644, 0o644,
                                                 source_uid=1000, restored_uid=33)
    comparison = compare.compare(rest_man, rest_sample, tmp_path / "src")
    meta = [w for w in comparison.warnings if w.kind == "metadata"]
    assert len(meta) == 1 and "owner differs" in meta[0].detail
    assert comparison.exit_code() == compare.EXIT_PASS


def test_r46_strict_promotes_metadata(tmp_path):
    rest_man, src_man, rest_sample = _build_pair(tmp_path, 0o644, 0o600)
    comparison = compare.compare(rest_man, rest_sample, tmp_path / "src", strict=True)
    assert comparison.failed, "--strict must promote the info class"
    assert comparison.exit_code() == compare.EXIT_DIFF_MISMATCH


def test_r46_no_double_report_where_data_already_differs(tmp_path):
    """A path flagged as a data diff must not also grow a metadata note."""
    src, rst = tmp_path / "src", tmp_path / "rst"
    src.mkdir()
    rst.mkdir()
    (src / "f.bin").write_bytes(b"x" * 10)
    (rst / "f.bin").write_bytes(b"y" * 20)
    os.chmod(src / "f.bin", 0o644)
    os.chmod(rst / "f.bin", 0o600)
    rest_man, src_man = manifest.build(rst), manifest.build(src)
    rest_sample = sampling.sample_tree(rest_man, rst, ())
    comparison = compare.compare(rest_man, rest_sample, tmp_path / "src")
    assert not [w for w in comparison.warnings if w.kind == "metadata"]
    assert comparison.exit_code() == compare.EXIT_DIFF_MISMATCH


def test_r46_like_for_like_is_quiet(tmp_path):
    """Both sides recording the same metadata (e.g. the same synthetic uid on
    a platform without uids) produces no warnings — the rule cannot lie."""
    rest_man, src_man, rest_sample = _build_pair(tmp_path, 0o644, 0o644,
                                                 source_uid=0, restored_uid=0)
    comparison = compare.compare(rest_man, rest_sample, tmp_path / "src")
    assert comparison.warnings == []
    assert comparison.status == "match"


def test_r46_no_new_envelope_keys(tmp_path):
    """The schema 1 envelope is untouched: the comparison block's key set is
    exactly the pre-I9 set; info diffs ride the warnings array."""
    rest_man, src_man, rest_sample = _build_pair(tmp_path, 0o644, 0o600)
    comparison = compare.compare(rest_man, rest_sample, tmp_path / "src")
    assert set(comparison.to_json()) == {
        "implemented", "enabled", "status", "reason", "source", "strict",
        "compared_files", "error_count", "warning_count", "digests",
        "errors", "warnings"}
    assert comparison.to_json()["warnings"][0]["severity"] == "info"


def test_r46_manifest_records_mode_and_uid(tmp_path):
    """Capture is best-effort and honest: real values on this platform, and
    None stays representable (never zero-faked)."""
    src = tmp_path / "src"
    src.mkdir()
    f = src / "a.txt"
    f.write_text("hello")
    os.chmod(f, 0o604)
    man = manifest.build(src)
    entry = man.entries[0]
    assert entry.mode is not None
    assert oct(entry.mode) == oct(os.stat(f).st_mode & 0o7777)
    assert isinstance(entry.uid, int)
    assert entry.to_json()["mode"] == oct(entry.mode)
    # None keeps old behaviour honest and serialises as an absent key
    entry.mode = None
    entry.uid = None
    assert "mode" not in entry.to_json() and "uid" not in entry.to_json()
