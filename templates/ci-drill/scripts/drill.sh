#!/usr/bin/env bash
# restverify restore-drill script — I10 (R44), template file.
#
# The drill that the composite action (`.github/actions/restore-drill`) runs,
# shipped as a standalone file so anyone can reuse the exact drill outside
# GitHub Actions: cron, systemd, a laptop, any CI.
#
# usage: drill.sh <repo> <fixture> <happy|tamper>
#
# What it asserts (the whole point of the drill):
#   happy  — a healthy fixture verifies; restverify must exit 0.
#   tamper — one flipped byte in the fixture; restverify must exit 2 (the
#            R11 diff-mismatch code). Exit 0 here would be a silent drill,
#            which is worse than a failed one.
# Any other exit code fails exactly as-is: this script never rewrites a
# code, it only asserts the two codes the drill depends on.
#
# Exit-code contract (R11): 0 pass / 1 run could not complete / 2 diff
# mismatch / 64 usage-config. Cron/CI automate on these; so does this drill.
#
# The drill-method rule (docs/I9-DRILL.md) applies to whoever runs this: a
# drill must exercise the artefact under test, not a stale install. In CI
# the action satisfies this by installing the released wheel it just
# verified; locally, rebuild + reinstall before drilling.
#
# Shape pinned by tests/test_i10_ci.py.

set -u
REPO="${1:?usage: drill.sh <repo> <fixture> <happy|tamper>}"
FIXTURE="${2:?usage: drill.sh <repo> <fixture> <happy|tamper>}"
MODE="${3:?usage: drill.sh <repo> <fixture> <happy|tamper>}"

case "$MODE" in
  happy) EXPECTED=0 ;;
  tamper) EXPECTED=2 ;;
  *) echo "drill.sh: mode must be 'happy' or 'tamper', got '$MODE'" >&2; exit 64 ;;
esac

# The drill never stores a password: it points restverify at the same
# password_command the fixture was backed up with (R2/R24 posture). The pass
# file location follows the action's layout by default; override with
# DRILL_PASS_FILE when reusing the drill outside GitHub Actions.
PASS_FILE="${DRILL_PASS_FILE:-${RUNNER_TEMP:-/tmp}/drill-pass}"
export RESTIC_PASSWORD_COMMAND="cat '$PASS_FILE'"

if [ "$MODE" = tamper ]; then
  printf 'tampered-by-drill\n' >> "$FIXTURE/canary.txt"
  echo "drill: flipped one byte in $FIXTURE/canary.txt (tamper leg)"
fi

echo "drill: restverify run -r $REPO (expecting exit $EXPECTED)"
set +e
restverify run -r "$REPO"
CODE=$?
set -e

if [ "$CODE" -ne "$EXPECTED" ]; then
  echo "drill: FAIL — expected exit $EXPECTED, got $CODE" >&2
  exit 1
fi
if [ "$MODE" = happy ]; then
  echo "drill: PASS — healthy fixture exited 0 (verification works)"
else
  echo "drill: PASS — tampered fixture exited 2 (the drill detects drift)"
fi

# ── R43 proof ────────────────────────────────────────────────────────────────
# The drill runs with RESTVERIFY_HOME set (see the workflow template): the
# state store must land inside the artificial home, never in the runner's
# real one. Asserted on the happy leg only; cheap and load-bearing.
if [ "$MODE" = happy ] && [ -n "${RESTVERIFY_HOME:-}" ]; then
  STORE="$RESTVERIFY_HOME/.local/state/restverify/history.db"
  if [ ! -f "$STORE" ]; then
    echo "drill: FAIL — history store missing at $STORE (R43 layout broken)" >&2
    exit 1
  fi
  echo "drill: R43 proof — history store at $STORE (inside RESTVERIFY_HOME)"
fi

exit 0
