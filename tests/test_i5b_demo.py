"""I5b — the demo line and the first-run next command (R22/R1).

The demo line's field ORDER is pinned by I2's assertions and kept by ruling R1;
this module adds the end-to-end shape assertion (a real run against the fake
restic and a planted tree, so the line is produced the way a user sees it) plus
the `init` next-command beat that I5b adds.

Tolerance, stated so the regex is not mistaken for the contract: the line must
carry the mark, the snapshot short id, the file count, a human byte total, an
elapsed-seconds value and (when comparing) a diff count, in that order. The
elapsed value is wall-clock, so its digits are not asserted.
"""
import io
import re
import sys

import pytest

from restverify import EXIT_PASS, EXIT_USAGE
from restverify.cli import main

DEMO_LINE = re.compile(
    r"^[✓✗] restored snapshot [0-9a-f]{8}: \d+ files / "
    r"[\d.]+ (?:B|KiB|MiB|GiB|TiB) in [\d.]+s"
    r"(?:; \d+ diff\(s\))?$"
)


class _FakeStdin(io.StringIO):
    """A tty-looking stream so `init`'s interactive path is exercised for real."""

    def isatty(self):                     # noqa: D102 - tiny stub
        return True


# ── the demo line ───────────────────────────────────────────────────────────

def test_demo_line_shape_end_to_end(fake_restic, tmp_base, source_tree, capsys):
    """R22/R1: the line a user sees matches the shape within tolerance."""
    assert main(["run", "-r", "/srv/backup", "-s", str(source_tree)]) == EXIT_PASS
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert DEMO_LINE.match(lines[0]), f"demo line shape drifted: {lines[0]!r}"


def test_demo_line_reports_diffs_with_the_same_shape(fake_restic, tmp_base,
                                                     source_tree, capsys):
    """A mismatch keeps the shape and flips the mark, with the diff count."""
    victim = source_tree / "restored.txt"
    victim.write_bytes(b"tampered")
    assert main(["run", "-r", "/srv/backup", "-s", str(source_tree)]) != EXIT_PASS
    first = capsys.readouterr().out.splitlines()[0]
    assert DEMO_LINE.match(first), first
    assert first.startswith("✗")


def test_dry_run_still_prints_its_pass_line(fake_restic, tmp_base, capsys):
    """Carried from I1 and deliberately unchanged by I5b."""
    assert main(["run", "-r", "/srv/backup", "--dry-run"]) == EXIT_PASS
    out = capsys.readouterr().out
    assert "✓ PASS (dry run)" in out


# ── init's next-command beat ────────────────────────────────────────────────

def test_init_prints_the_cron_next_step(config_path, fake_restic, capsys):
    assert main(["init", "-r", "/srv/backup", "--name", "backup"]) == EXIT_PASS
    out = capsys.readouterr().out
    assert "next: restverify run -r backup" in out
    assert "restverify cron -r backup" in out


def test_init_interactive_prints_the_cron_next_step(config_path, fake_restic,
                                                    capsys, monkeypatch):
    """The interactive path teaches the same next step, from typed input."""
    monkeypatch.setattr(sys, "stdin", _FakeStdin("/srv/backup\n"))
    assert main(["init"]) == EXIT_PASS
    out = capsys.readouterr().out
    assert "added 'backup'" in out
    assert "restverify cron -r backup" in out


def test_init_interactive_eof_is_not_a_crash(config_path, capsys, monkeypatch):
    """U4: closing stdin mid-prompt exits 64 with a sentence, not a traceback."""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(""))
    assert main(["init"]) == EXIT_USAGE
    assert "nothing added" in capsys.readouterr().err


def test_init_without_repo_still_teaches_non_interactively(config_path, capsys):
    """Carried: no --repo and no tty exits 64 naming the next command (U2/U4)."""
    assert main(["init"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "nothing to save" in err
    assert "init -r" in err
