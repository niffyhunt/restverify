"""CLI contract tests (gate G2 + usability laws U2/U3).

These pin the surface a user meets first. They must stay green on every
increment: help text, exit codes, and the shape of failure messages.
"""
import pytest

from restverify import (EXIT_DIFF_MISMATCH, EXIT_PASS, EXIT_RESTORE_FAIL, EXIT_USAGE,
                        __version__)
from restverify.cli import main


# ── exit-code contract (PDF, normative) ─────────────────────────────────────

def test_exit_code_contract_values():
    assert (EXIT_PASS, EXIT_RESTORE_FAIL, EXIT_DIFF_MISMATCH) == (0, 1, 2)
    # usage failures are not verification outcomes, so they must not borrow 1/2
    assert EXIT_USAGE == 64


def test_top_level_help_documents_the_job_and_exit_codes(capsys):
    """U3: a user must succeed from --help alone."""
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    out = capsys.readouterr().out
    assert e.value.code == 0
    assert "verify" in out.lower()
    assert "exit codes" in out.lower()
    assert "restverify run -r" in out            # a copy-pasteable first step
    assert "examples:" in out.lower()


def test_no_arguments_prints_help_and_succeeds(capsys):
    assert main([]) == EXIT_PASS
    out = capsys.readouterr().out
    assert "usage:" in out.lower()


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


# ── every subcommand documents itself (U3) ─────────────────────────────────

@pytest.mark.parametrize("cmd", ["init", "run", "report", "cron"])
def test_subcommand_help_shows_examples(cmd, capsys):
    with pytest.raises(SystemExit) as e:
        main([cmd, "--help"])
    out = capsys.readouterr().out
    assert e.value.code == 0
    assert "examples:" in out.lower(), f"{cmd} --help must show examples"
    # no flag may ship without a one-line explanation (U3)
    assert "--json" in out


# ── teaching failures (U2): what happened / likely cause / next command ────

def test_bad_flag_teaches_instead_of_traceback(capsys):
    with pytest.raises(SystemExit) as e:
        main(["run", "--nope"])
    err = capsys.readouterr().err
    assert e.value.code == EXIT_USAGE
    assert "error:" in err
    assert "next:" in err, "usage errors must name the next command (U2c)"


def test_help_documents_the_usage_code(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "64" in out, "the -64 usage code must be documented in --help (U3)"


@pytest.mark.parametrize("cmd,ptr", [("report", "I4"), ("cron", "I5")])
def test_unimplemented_commands_are_honest(cmd, ptr, capsys):
    """Gate G6: the scaffold must not pretend to work."""
    code = main([cmd])
    err = capsys.readouterr().err
    assert code != EXIT_PASS
    assert "not implemented" in err
    assert ptr in err
    assert "docs/PHASE2-PLAN.md" in err
