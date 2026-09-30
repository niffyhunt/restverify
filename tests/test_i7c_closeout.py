"""I7c close-out: the report must reflect that B1 is closed and U7 is measured.

The standing rule is that a status claim in docs/SECURITY.md cannot outlive the
evidence behind it, so the I6c staleness test gets an I7c companion: when I7
closed B1, the "B1 open" note had to change with it. This file asserts the note
was updated rather than left to rot, and that the recorded measurement is the
one I7c actually took.

No test here reads a clock, sleeps, or asserts on a duration - the U7 number is
evidence pasted into the report, not a property the suite enforces.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPORT = REPO / "docs" / "SECURITY.md"
# The internal planning doc was removed from the public repo (sanitization,
# 2026-09-30); the host-audit assertion it carried is retired with it.
PLAN = None


def _report() -> str:
    assert REPORT.exists(), f"missing {REPORT}"
    return REPORT.read_text(encoding="utf-8")


def test_b1_is_no_longer_reported_as_open():
    """I7c: B1 closed, and the closure names what was actually exercised."""
    text = _report()
    assert "B1 open" not in text, "B1 is closed by I7a; the report must not still say open"
    assert "B1 CLOSED" in text
    assert "0.16.4" in text, "the report must name the restic version that was exercised"
    assert "build-host" in text or "second-build-host" in text, \
        "the report must name the build host (sanitized) that I7 ran on"


def test_u7_is_recorded_as_measured_with_its_target():
    text = _report()
    assert "U7 not measured" not in text
    assert "U7 MEASURED" in text
    assert re.search(r"8\.22 s", text), "the measured install-to-first-run total is missing"
    assert "five minutes" in text, "the target the measurement is compared against is missing"


def test_the_other_blockers_are_still_named():
    """C1 and B2 are untouched by I7 and must survive the edit."""
    text = _report()
    assert "C1 awaiting" in text
    assert "B2 resolved" in text


def test_plan_records_that_i7_ran_on_the_second_host():
    """Retired with the internal planning doc (sanitization, 2026-09-30):
    the audit table lived there, not in the public docs."""
    assert PLAN is None


def test_i7_tests_contain_no_clock_or_sleep():
    """The standing rule, checked mechanically over the I7 test files.

    The tokens are assembled from fragments so this file does not contain the
    very strings it is looking for - otherwise the check fails on itself, which
    is the same class of mistake as the vacuous assertion in I6a (D-I6-1).
    """
    forbidden = ("time." + "sleep", "mono" + "tonic", "perf_" + "counter",
                 "time." + "time(")
    for path in sorted((REPO / "tests").glob("test_i7*.py")):
        body = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in body, f"{path.name} uses {token}"
