"""Increment I3b: one exit-code map, exhaustive taxonomy (R11, G2).

``errors.CODE_BY_ERROR`` is the single map every error path consults. The test
that every concrete ``RestverifyError`` subclass has its **own** entry is what
keeps G2 from rotting as I4-I6 add error types: a new subclass, or one that only
inherits from a mapped class, fails ``test_every_concrete_subclass_has_its_own_entry``.

Every kind is exercised end to end and asserts the invariant that matters:
``payload["error"]["exit_code"] == the process exit code == EXIT_CODE_BY_KIND[kind]``.

No timing assertions; each test spawns the fake restic at most once.
"""
import json

import pytest

from restverify import EXIT_PASS, EXIT_RESTORE_FAIL, EXIT_USAGE, errors
from restverify.cli import main


def pure_json(text: str) -> dict:
    stripped = text.lstrip()
    payload, end = json.JSONDecoder().raw_decode(stripped)
    assert stripped[end:].strip() == ""
    return payload


def captured_error(capsys):
    payload = pure_json(capsys.readouterr().out)
    assert payload["status"] == "error"
    return payload


def assert_kind(payload, kind: str, code: int) -> None:
    assert payload["error"]["kind"] == kind
    assert payload["exit_code"] == code
    assert errors.EXIT_CODE_BY_KIND[kind] == code


# ── the table itself ───────────────────────────────────────────────────────

def test_every_concrete_subclass_has_its_own_entry():
    """THE taxonomy guard (fires in the adversarial proof recorded in §5/§8)."""
    concrete = errors.error_classes()
    mapped = set(errors.CODE_BY_ERROR)
    assert concrete == mapped, (
        f"unmapped: {sorted(c.__name__ for c in concrete - mapped)}; "
        f"stale: {sorted(c.__name__ for c in mapped - concrete)}")


def test_every_kind_has_exactly_one_exit_code():
    assert set(errors.EXIT_CODE_BY_KIND) == errors.ERROR_KINDS


def test_the_map_only_uses_contract_codes():
    assert set(errors.EXIT_CODE_BY_KIND.values()) <= {EXIT_RESTORE_FAIL, EXIT_USAGE}


def test_exit_code_for_walks_the_mro_and_never_raises():
    class Inherited(errors.ResticFailed):
        pass

    assert errors.exit_code_for(errors.ResticFailed("x")) == EXIT_RESTORE_FAIL
    assert errors.exit_code_for(Inherited("x")) == EXIT_RESTORE_FAIL
    assert errors.exit_code_for(errors.ConfigError("x")) == EXIT_USAGE
    assert errors.exit_code_for(KeyboardInterrupt()) == errors.FALLBACK_EXIT_CODE


# ── all ten kinds, end to end ──────────────────────────────────────────────

def test_kind_usage_maps_to_64(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["run", "--json", "--nope"])
    assert_kind(captured_error(capsys), "usage", exc.value.code)
    assert exc.value.code == EXIT_USAGE


def test_kind_config_maps_to_64(config_path, capsys):
    config_path.write_text("not = = toml\n", encoding="utf-8")
    code = main(["run", "-r", "/srv/backup", "--json"])
    assert_kind(captured_error(capsys), "config", code)
    assert code == EXIT_USAGE


def test_kind_restic_missing_maps_to_1(monkeypatch, tmp_base, config_path, capsys):
    monkeypatch.setenv("PATH", "/definitely/not/here")
    code = main(["run", "-r", "/srv/backup", "--json"])
    assert_kind(captured_error(capsys), "restic_missing", code)
    assert code == EXIT_RESTORE_FAIL


def test_kind_no_snapshots_maps_to_1(fake_restic, tmp_base, config_path, capsys):
    fake_restic.set_mode("empty")
    code = main(["run", "-r", "/srv/backup", "--json"])
    assert_kind(captured_error(capsys), "no_snapshots", code)
    assert code == EXIT_RESTORE_FAIL


def test_kind_restic_failed_maps_to_1(fake_restic, tmp_base, config_path, capsys):
    fake_restic.set_mode("fail_restore")
    code = main(["run", "-r", "/srv/backup", "--json"])
    assert_kind(captured_error(capsys), "restic_failed", code)
    assert code == EXIT_RESTORE_FAIL


def test_kind_source_maps_to_1(fake_restic, tmp_base, config_path, capsys):
    code = main(["run", "-r", "/srv/backup", "-s", "/definitely/not/here", "--json"])
    assert_kind(captured_error(capsys), "source", code)
    assert code == EXIT_RESTORE_FAIL


def test_kind_tempdir_maps_to_1(fake_restic, tmp_path, config_path, monkeypatch, capsys):
    monkeypatch.setenv("RESTVERIFY_TMPDIR", str(tmp_path / "does-not-exist"))
    code = main(["run", "-r", "/srv/backup", "--json"])
    assert_kind(captured_error(capsys), "tempdir", code)
    assert code == EXIT_RESTORE_FAIL


def test_kind_manifest_maps_to_1(fake_restic, tmp_base, config_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise errors.ManifestError("manifest exploded", hint="re-run")

    monkeypatch.setattr("restverify.cli.manifestmod.build", boom)
    code = main(["run", "-r", "/srv/backup", "--json"])
    assert_kind(captured_error(capsys), "manifest", code)
    assert code == EXIT_RESTORE_FAIL


def test_kind_sample_maps_to_1(fake_restic, tmp_base, config_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise errors.SampleError("sample exploded", hint="re-run")

    monkeypatch.setattr("restverify.cli.samplingmod.sample_tree", boom)
    code = main(["run", "-r", "/srv/backup", "--json"])
    assert_kind(captured_error(capsys), "sample", code)
    assert code == EXIT_RESTORE_FAIL


def test_kind_interrupted_maps_to_1(fake_restic, tmp_base, config_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr("restverify.cli.resticmod.newest_snapshot", boom)
    code = main(["run", "-r", "/srv/backup", "--json"])
    captured = capsys.readouterr()
    assert_kind(pure_json(captured.out), "interrupted", code)
    assert code == EXIT_RESTORE_FAIL
    assert "interrupted" in captured.err
