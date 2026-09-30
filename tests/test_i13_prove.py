"""I13 (R48) — `restverify prove`: prove a snapshot by restoring a sample.

Offline coverage uses the fake restic (gate G1): the fake serves a real
NDJSON `ls` listing (snapshot header + file nodes with sizes, no hashes —
the shape measured on restic 0.16.4) and honours `restore --include` with
Go filepath.Match semantics, including synthetic listing files whose names
carry glob metacharacters and a literal backslash, so an escaping mistake
in restore_paths fails verification exactly as it would against real restic.

The DRBG's AES-256 is pinned against the official FIPS-197 C.3 block vector
and the NIST SP 800-38A F.5.5 CTR-AES256 vectors — the same vectors that
caught the first T-table draft (a wrong 3S slot produced `a65630d4…` where
`8ea2b7ca…` was required).

Real-restic integration tests run only when a real `restic` binary AND a
prepared probe repository are available (RESTVERIFY_PROVE_REAL=1), and skip
cleanly otherwise; their assertions mirror the measured real-repo behaviour.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from restverify import EXIT_DIFF_MISMATCH, EXIT_PASS, EXIT_RESTORE_FAIL, EXIT_USAGE
from restverify import prove as P
from restverify import restic as resticmod
from restverify.cli import main

BSLASH = chr(92)  # a literal backslash, without backslash-escaping this file


# ── AES-256 / DRBG pinning (the C1 dependency, proven) ────────────────────

def test_fips197_c3_vector():
    key = bytes(range(32))
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    assert P.aes256_encrypt_block(key, pt).hex() == \
        "8ea2b7ca516745bfeafc49904b496089"


def test_fips197_round_trip():
    key = bytes(range(32))
    pt = bytes(range(16))
    assert P.aes256_decrypt_block(key, P.aes256_encrypt_block(key, pt)) == pt


def test_sp800_38a_f55_ctr_vectors():
    key = bytes.fromhex(
        "603deb1015ca71be2b73aef0857d7781"
        "1f352c073b6108d72d9810a30914dff4")
    iv = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
    blocks = ["6bc1bee22e409f96e93d7e117393172a",
              "ae2d8a571e03ac9c9eb76fac45af8e51",
              "30c81c46a35ce411e5fbc1191a0a52ef",
              "f69f2445df4f9b17ad2b417be66c3710"]
    want = ["601ec313775789a5b7a7f504bbf3d228",
            "f443e3ca4d62b59aca84e990cacaf5c5",
            "2b0930daa23de94ce87017ba2d84988d",
            "dfc9c58db67aada613c2dd08457941a6"]
    counter = int.from_bytes(iv, "big")
    for pt_hex, ct_hex in zip(blocks, want):
        ks = P.aes256_encrypt_block(key, counter.to_bytes(16, "big"))
        got = bytes(a ^ b for a, b in zip(ks, bytes.fromhex(pt_hex)))
        assert got.hex() == ct_hex
        counter += 1


def test_drbg_deterministic_and_reseeded_by_seed():
    a = P.SamplingDRBG(*P.derive_drbg("snap", None, 10)[:2])
    b = P.SamplingDRBG(*P.derive_drbg("snap", None, 10)[:2])
    c = P.SamplingDRBG(*P.derive_drbg("snap", 7, 10)[:2])
    assert a.random_bytes(64) == b.random_bytes(64)
    assert a.random_bytes(64) != c.random_bytes(64)


def test_drbg_below_is_uniform_across_halves():
    drbg = P.SamplingDRBG(*P.derive_drbg("snap", 1, 10)[:2])
    half = sum(1 for _ in range(4000) if drbg.below(100000) < 50000)
    assert 1800 < half < 2200          # ~2000 expected


def test_drbg_below_hits_boundaries():
    drbg = P.SamplingDRBG(*P.derive_drbg("snap", 2, 10)[:2])
    assert {drbg.below(2) for _ in range(100)} == {0, 1}
    assert drbg.below(1) == 0 and drbg.below(0) == 0


def test_100k_sample_runs_in_seconds():
    files = [{"path": f"f{i:06d}.bin", "size": i} for i in range(100_000)]
    started = time.monotonic()
    P.sample(files, 10, 12345, snapshot_id="abc")
    assert time.monotonic() - started < 5.0


# ── file list / sampling semantics ────────────────────────────────────────

def test_file_list_keeps_files_only_and_sorts():
    nodes = [
        {"type": "dir", "path": "/srv/data", "size": 0},
        {"type": "file", "path": "/srv/data/b.txt", "size": 2},
        {"type": "symlink", "path": "/srv/data/l", "size": 4},
        {"type": "file", "path": "/srv/data/a.txt", "size": 1},
        {"type": "file", "path": "", "size": 9},
    ]
    assert [f["path"] for f in P.file_list(nodes)] == \
        ["/srv/data/a.txt", "/srv/data/b.txt"]


def test_sample_counts_and_largest_file():
    files = [{"path": f"f{i:03d}", "size": i} for i in range(100)]
    got = P.sample(files, 10, 42, snapshot_id="s")
    assert len(got) == 10                              # ceil(100*10/100)
    assert max(files, key=lambda f: (f["size"], f["path"])) in got


def test_sample_min_one_and_all_at_100():
    files = [{"path": "a", "size": 1}, {"path": "b", "size": 2}]
    assert len(P.sample(files, 1, 1, snapshot_id="s")) == 1
    assert P.sample(files, 100, 1, snapshot_id="s") == files
    assert P.sample([], 10, 1, snapshot_id="s") == []


def test_sample_deterministic_same_seed_different_by_seed():
    files = [{"path": f"f{i:04d}", "size": i} for i in range(500)]
    a = P.sample(files, 10, 7, snapshot_id="s")
    b = P.sample(files, 10, 7, snapshot_id="s")
    c = P.sample(files, 10, 8, snapshot_id="s")
    d = P.sample(files, 10, 7, snapshot_id="t")
    assert a == b and a != c and a != d


def test_percent_arg_maps_usage_errors_to_exit_64_type():
    for bad in ("0", "101", "abc"):
        with pytest.raises(Exception) as exc:
            P.percent_arg(bad)
        assert "--sample" in str(exc.value)
    assert P.percent_arg("1") == 1 and P.percent_arg("100") == 100


# ── verification / excludes (pure functions) ──────────────────────────────

def test_verify_sample_size_mismatch_is_exit_2_fuel(tmp_path):
    (tmp_path / "ok.txt").write_bytes(b"12345")
    (tmp_path / "bad.txt").write_bytes(b"tooshort")
    sample = [{"path": "/srv/ok.txt", "size": 5},
              {"path": "/srv/bad.txt", "size": 99},
              {"path": "/srv/gone.txt", "size": 1}]
    fails = P.verify_sample(sample, tmp_path, snapshot_paths=["/srv"])
    reasons = {f["path"]: f["reason"] for f in fails}
    assert "size differs" in reasons["/srv/bad.txt"]
    assert "missing" in reasons["/srv/gone.txt"]
    bad = next(f for f in fails if f["path"] == "/srv/bad.txt")
    assert bad["expected_size"] == 99 and bad["actual_size"] == 8


def test_verify_sample_blocks_path_escape(tmp_path):
    sample = [{"path": "/srv/../../etc/passwd", "size": 1}]
    fails = P.verify_sample(sample, tmp_path, snapshot_paths=["/srv"])
    assert len(fails) == 1
    assert "escape" in fails[0]["reason"]
    assert fails[0]["actual_size"] is None


def test_filter_excludes_matches_restic_style():
    files = [{"path": "/srv/data/app.log", "size": 1},
             {"path": "/srv/data/sub/keep.txt", "size": 2},
             {"path": "/srv/cache/blob.bin", "size": 3}]
    assert [f["path"] for f, in zip(P.filter_excludes(files, ["*.log"])[0],)] == \
        ["/srv/data/sub/keep.txt", "/srv/cache/blob.bin"]
    kept, n = P.filter_excludes(files, ["keep.txt"])
    assert n == 1 and [f["path"] for f in kept] == \
        ["/srv/data/app.log", "/srv/cache/blob.bin"]
    kept, n = P.filter_excludes(files, ["cache"])
    assert n == 1 and len(kept) == 2
    kept, n = P.filter_excludes(files, [])
    assert n == 0 and len(kept) == 3


def test_filter_excludes_backslash_is_a_filename_character():
    files = [{"path": f"/srv/data/back{BSLASH}slash.txt", "size": 1},
             {"path": "/srv/data/keep.txt", "size": 2}]
    kept, n = P.filter_excludes(files, ["back" + BSLASH + "slash.txt"])
    assert n == 1 and [f["path"] for f in kept] == ["/srv/data/keep.txt"]


# ── CLI contract through the fake restic ──────────────────────────────────

def test_prove_happy_path_exit_0_counts_consistent(fake_restic, tmp_base, capsys):
    code = main(["prove", "-r", "/srv/backup", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "listing" in captured.err and "sampling" in captured.err
    assert code == EXIT_PASS
    assert payload["command"] == "prove"
    assert payload["status"] == "pass" and payload["exit_code"] == EXIT_PASS
    prov = payload["prove"]
    assert prov["files_listed"] == 5
    assert prov["files_sampled"] == 1            # ceil(5 * 10 / 100), default
    assert prov["files_verified"] == prov["files_sampled"]
    assert prov["files_failed"] == 0
    assert prov["hashes_available"] is False
    assert payload["restore"]["target_removed"] is True
    assert list(Path(tmp_base).iterdir()) == []       # nothing left behind
    verbs = fake_restic.verbs()
    assert verbs[0] == "snapshots" and "ls" in verbs
    assert "restore" in verbs
    assert "check" in verbs            # the content-integrity layer
    assert all(v in ("snapshots", "ls", "restore", "check") for v in verbs)
    prov = payload["prove"]
    assert prov["content_check"]["performed"] is True
    assert prov["content_check"]["ok"] is True


def test_prove_restores_exactly_the_sampled_paths(fake_restic, tmp_base, capsys):
    main(["prove", "-r", "/srv/backup", "--sample", "100", "--json"])
    restore_calls = [c for c in fake_restic.calls() if c.startswith("restore")]
    assert restore_calls, "prove must restore"
    argv = restore_calls[0].split()
    includes = [argv[i + 1] for i, a in enumerate(argv) if a == "--include"]
    assert set(includes) == {"/srv/data/restored.txt", "/srv/data/sub/nested.bin",
                             "/srv/data/weird[[]1].txt",
                             "/srv/data/back" + BSLASH * 2 + "slash.txt",
                             "/srv/data/plain.txt"}


def test_prove_sampling_line_shows_seed(fake_restic, tmp_base, capsys):
    main(["prove", "-r", "/srv/backup", "--seed", "7"])
    out = capsys.readouterr().out
    assert "seed --seed 7" in out
    assert "5 file(s)" in out


def test_prove_default_seed_derived_from_snapshot(fake_restic, tmp_base, capsys):
    main(["prove", "-r", "/srv/backup", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["prove"]["seed_desc"] == "derived from snapshot 9f3a2c00"


def test_prove_corruption_is_exit_2_with_failures(fake_restic, tmp_base, capsys):
    fake_restic.set_mode("corrupt")
    code = main(["prove", "-r", "/srv/backup", "--sample", "100", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert code == EXIT_DIFF_MISMATCH
    assert payload["status"] == "diff_mismatch" and payload["exit_code"] == 2
    prov = payload["prove"]
    assert prov["files_failed"] > 0
    assert prov["files_verified"] == prov["files_sampled"] - prov["files_failed"]
    assert all({"path", "reason", "expected_size", "actual_size"} <= set(f)
               for f in prov["failures"])


def test_prove_runs_read_data_subset_over_the_sample_share(fake_restic, tmp_base):
    """The content check verifies the SAME share of packs as the file sample.
    100% must become a full --read-data (restic's flag, not a fake '100%')."""
    main(["prove", "-r", "/srv/backup", "--sample", "25"])
    check_calls = [c for c in fake_restic.calls() if c.startswith("check")]
    assert check_calls == ["check --read-data-subset 25% --repo /srv/backup"]
    main(["prove", "-r", "/srv/backup", "--sample", "100"])
    check_calls = [c for c in fake_restic.calls() if c.startswith("check")]
    assert check_calls[-1] == "check --read-data --repo /srv/backup"


def test_prove_check_failure_is_exit_2_data_verdict(fake_restic, tmp_base, capsys):
    """A failed seal is a DATA verdict (exit 2), not could-not-complete: the
    check ran to completion and the data did not verify."""
    fake_restic.set_mode("check_fails")
    code = main(["prove", "-r", "hmm", "--sample", "100", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == EXIT_DIFF_MISMATCH
    assert payload["status"] == "diff_mismatch" and payload["exit_code"] == 2
    assert payload["prove"]["files_failed"] == 0      # sizes were fine
    assert payload["prove"]["content_check"]["ok"] is False
    assert "errors" in payload["prove"]["content_check"]["detail"]


def test_prove_check_is_skipped_when_nothing_is_sampled(fake_restic, tmp_base, capsys):
    fake_restic.set_mode("ls_empty")
    code = main(["prove", "-r", "/srv/backup", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_PASS
    assert payload["prove"]["content_check"]["performed"] is False
    assert "check" not in fake_restic.verbs()          # no pointless restic run


def test_prove_check_catches_what_the_size_check_cannot(fake_restic, tmp_base, capsys):
    """The operator ruling (2026-09-30): 'why can't honest scope be fixed'.
    The size check alone would pass a corruption that swaps bytes without
    changing lengths; the seal check does not. Both layers must run."""
    fake_restic.set_mode("check_fails")
    code = main(["prove", "-r", "/srv/backup", "--json"])
    assert code == EXIT_DIFF_MISMATCH                  # sizes pass, seal fails
    verbs = fake_restic.verbs()
    assert "restore" in verbs and "check" in verbs


def test_prove_history_row_written_like_run(fake_restic, tmp_base):
    main(["prove", "-r", "/srv/backup", "--json"])
    from restverify import history as historymod
    rows = historymod.read_recent(limit=10, repo="/srv/backup")
    assert rows and rows[0].status == "pass" and rows[0].snapshot == "9f3a2c00"
    assert rows[0].exit_code == EXIT_PASS and rows[0].file_count == 5


def test_prove_empty_listing_is_pass_exit_0(fake_restic, tmp_base, capsys):
    fake_restic.set_mode("ls_empty")
    code = main(["prove", "-r", "/srv/backup", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_PASS
    assert payload["prove"]["files_listed"] == 0
    assert payload["prove"]["files_sampled"] == 0
    assert payload["status"] == "pass"


def test_prove_ls_badjson_is_exit_1(fake_restic, tmp_base, capsys):
    fake_restic.set_mode("badjson")
    code = main(["prove", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "could not read as JSON" in err
    assert "next:" in err


def test_prove_wrong_password_is_exit_1_teaching(fake_restic, tmp_base, capsys):
    fake_restic.set_mode("wrong_password")
    code = main(["prove", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "password_command" in err or "password" in err


def test_prove_missing_restic_teaches(monkeypatch, tmp_base, capsys):
    monkeypatch.setenv("PATH", "/definitely/not/here")
    code = main(["prove", "-r", "/srv/backup"])
    err = capsys.readouterr().err
    assert code == EXIT_RESTORE_FAIL
    assert "restic was not found" in err


def test_prove_error_path_records_history_row(fake_restic, tmp_base):
    fake_restic.set_mode("wrong_password")
    main(["prove", "-r", "/srv/backup"])
    from restverify import history as historymod
    rows = historymod.read_recent(limit=10, repo="/srv/backup")
    assert rows and rows[0].status == "error" and rows[0].exit_code == 1


def test_prove_dry_run_touches_nothing(fake_restic, tmp_base, capsys):
    code = main(["prove", "-r", "/srv/backup", "--dry-run"])
    out = capsys.readouterr().out
    assert code == EXIT_PASS
    assert "dry run" in out
    assert fake_restic.calls() == []
    assert list(Path(tmp_base).iterdir()) == []


def test_prove_dry_run_json_states_no_restore(fake_restic, tmp_base, capsys):
    code = main(["prove", "-r", "/srv/backup", "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_PASS and payload["status"] == "dry_run"
    assert payload["dry_run"]["restores_anything"] is False
    assert payload["dry_run"]["restic_invoked"] is False


@pytest.mark.parametrize("bad", ["0", "101", "abc", "-5"])
def test_prove_bad_sample_is_exit_64(fake_restic, tmp_base, capsys, bad):
    with pytest.raises(SystemExit) as ei:
        main(["prove", "-r", "/srv/backup", "--sample", bad])
    err = capsys.readouterr().err
    assert ei.value.code == EXIT_USAGE
    assert "--sample" in err


def test_prove_help_documents_contract(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["prove", "--help"])
    assert ei.value.code == 0
    text = capsys.readouterr().out
    assert "size-only" in text or "hashes_available" in text
    assert "FIPS" not in text            # stdlib detail, not user contract
    for token in ("exit codes", "deterministic", "--seed"):
        assert token in text


def test_prove_is_a_known_command(fake_restic, tmp_base, capsys):
    from restverify import cli as climod
    assert "prove" in climod._KNOWN_COMMANDS
    assert climod._DISPATCH.get("prove") is not None


def test_prove_webhook_error_path_delivers(fake_restic, tmp_base, capsys, monkeypatch):
    from restverify import cli as climod
    calls = []
    monkeypatch.setattr(climod, "_deliver_webhook",
                        lambda args, entry, payload: calls.append(payload))
    fake_restic.set_mode("wrong_password")
    main(["prove", "-r", "/srv/backup", "--json"])
    assert calls and calls[0]["status"] == "error"


# ── real-restic integration (skip cleanly without the probe repo) ─────────

REAL = os.environ.get("RESTVERIFY_PROVE_REAL") == "1" and shutil.which("restic")
REAL_REPO = "/tmp/probe-repo"
REAL_PASSWORD = "echo drill-pass"   # throwaway local probe repo, sanitized
pytestmark_real = pytest.mark.skipif(
    not REAL, reason="real-restic probe not requested "
                     "(set RESTVERIFY_PROVE_REAL=1 with /tmp/probe-repo present)")


@pytest.mark.skipif(not REAL, reason="real-restic probe not requested "
                                     "(set RESTVERIFY_PROVE_REAL=1 with /tmp/probe-repo present)")
def test_real_restic_healthy_repo_verifies(monkeypatch, tmp_base):
    monkeypatch.setenv("RESTIC_PASSWORD_COMMAND", REAL_PASSWORD)
    snaps = resticmod.list_snapshots(REAL_REPO)
    assert snaps, "probe repo must have snapshots"
    for snap in snaps:
        files = P.file_list(resticmod.ls(REAL_REPO, snap.id))
        sample = P.sample(files, 100, 7, snapshot_id=snap.id)
        from restverify import tempstore
        with tempstore.restore_dir(REAL_REPO) as target:
            resticmod.restore_paths(REAL_REPO, snap.id,
                                    [f["path"] for f in sample], target)
            root = resticmod.restored_root(target, snap)
            failures = P.verify_sample(sample, root, snapshot_paths=snap.paths)
        assert failures == [], (snap.short_id, failures)


@pytest.mark.skipif(not REAL, reason="real-restic probe not requested "
                                     "(set RESTVERIFY_PROVE_REAL=1 with /tmp/probe-repo present)")
def test_real_restic_cli_smoke(monkeypatch, tmp_base, capsys):
    monkeypatch.setenv("RESTIC_PASSWORD_COMMAND", REAL_PASSWORD)
    code = main(["prove", "-r", REAL_REPO, "--sample", "100", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_PASS
    assert payload["prove"]["files_verified"] == payload["prove"]["files_sampled"]
    assert payload["prove"]["hashes_available"] is False
