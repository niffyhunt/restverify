"""Increment I6b — the V2 security sweep as tests (R25 + the dependency claim).

Two things are asserted here, and they are different kinds of claim:
  1. The dependency/telemetry facts, asserted with this file's own AST walk, so a
     defect in `scripts/security_sweep.py` cannot hide behind a green suite.
  2. The sweep script itself runs, reports the five group verdicts, and fails
     loudly (naming the group and the module) when a scratch tree imports
     something forbidden. Its stdout is the evidence pasted into the I6 report.

The scratch trees come from `--root`, so the script is exercised without touching
src/ and without network access or any tool beyond Python.
"""
import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "restverify"
SCRIPT = REPO / "scripts" / "security_sweep.py"
SHIPPED = sorted(SRC.rglob("*.py"))

# Amended in I12 (R47, operator-approved spec): webhook.py is the ONE module
# allowed outbound network (urllib.request) — the network-modules row of the
# sweep now reports webhook.py's imports as EXPECTED rather than forbidden.
NETWORK_MODULES = ["socket", "ssl", "urllib", "urllib2", "urllib3", "http", "requests",
                   "aiohttp", "httpx", "ftplib", "smtplib", "telnetlib", "xmlrpc"]
EXPECTED_NETWORK = {"webhook.py": {"urllib.request", "urllib.error", "urllib.parse"}}
TELEMETRY_WORDS = ["analytics", "telemetry", "sentry", "datadog", "newrelic", "honeycomb",
                   "lightstep", "opentelemetry", "prometheus", "statsd", "mixpanel",
                   "amplitude", "segment", "posthog", "matomo", "plausible"]
TELEMETRY_HOSTS = ["sentry.io", "datadoghq.com", "newrelic.com", "honeycomb.io",
                   "lightstep.com", "grafana.net", "prometheus.io", "mixpanel.com",
                   "amplitude.com", "segment.io", "posthog.com", "matomo.org",
                   "plausible.io"]


def _imports():
    """module -> imported names, from this file's own walk (dynamic imports included)."""
    out = {}
    for path in SHIPPED:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add("." * node.level + (node.module or ""))
            elif isinstance(node, ast.Call):
                func = node.func
                label = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if label in ("import_module", "__import__") and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        names.add(first.value)
        out[path.name] = {n for n in names if n}
    return out


def _resolve(name):
    if not name or name.startswith("."):
        return "local"
    root = name.split(".")[0]
    if root == "restverify":
        return "local"
    return "stdlib" if root in sys.stdlib_module_names else "third-party"


def _sweep(root):
    """Run the sweep against `root`; returns (exit code, stdout+stderr)."""
    proc = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)],
                          capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout + proc.stderr


def _scratch(tmp_path, body):
    """A throwaway package directory containing one module with `body`."""
    package = tmp_path / "src" / "restverify"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "scratch.py").write_text(body, encoding="utf-8")
    return package



# ── the facts, asserted independently of the sweep script ─────────────────

def test_every_import_resolves_to_stdlib_or_local():
    """The "zero runtime dependencies" claim, made executable. sys.stdlib_module_names
    is the oracle, so a vendored or typo'd third-party import cannot pass as stdlib."""
    classified = {}
    for module, names in _imports().items():
        for name in names:
            if _resolve(name) == "third-party":
                classified.setdefault(module, []).append(name)
    assert classified == {}, f"third-party imports in shipped code: {classified}"


def test_no_network_module_is_imported():
    hits = {}
    for module, names in _imports().items():
        found = sorted(n for n in names if n.split(".")[0] in NETWORK_MODULES)
        expected = EXPECTED_NETWORK.get(module, set())
        extra = sorted(set(found) - expected)
        missing = sorted(expected - set(found))
        if extra or missing:
            hits[module] = {"unexpected": extra, "missing": missing}
    assert hits == {}, f"network module imported outside the I12 boundary: {hits}"


def test_no_telemetry_symbol_or_vendor_url():
    """R25 zero telemetry. Prose mentions are review material, not violations, which
    is exactly why this asserts identifiers and URLs instead of the raw text."""
    words = {w.lower() for w in TELEMETRY_WORDS}
    symbols, urls = {}, {}
    for path in SHIPPED:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0].lower() in words:
                        symbols.setdefault(path.name, []).append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0].lower() in words:
                    symbols.setdefault(path.name, []).append(node.module)
            elif isinstance(node, ast.Attribute):
                if node.attr.lower() in words:
                    symbols.setdefault(path.name, []).append(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if any(host in node.value.lower() for host in TELEMETRY_HOSTS):
                    urls.setdefault(path.name, []).append(node.value[:60])
    assert symbols == {}, f"telemetry symbol in shipped code: {symbols}"
    assert urls == {}, f"telemetry vendor URL in shipped code: {urls}"


# ── the sweep script: the five verdicts, and that it can fail ─────────────

def test_sweep_reports_the_five_group_verdicts_and_passes():
    code, out = _sweep(SRC)
    assert code == 0, out
    for label in ("R25 zero telemetry", "N3 no cloud SDK / no credentials",
                  "N4 no web framework / no listener",
                  "N5 no notification SDK / no webhook", "N7 no Windows code / no GUI"):
        line = next((l for l in out.splitlines() if l.startswith(f"  {label}")), None)
        assert line is not None, f"the sweep has no verdict row for {label!r}:\n{out}"
        assert line.rstrip().endswith("PASS"), f"{label} is not PASS: {line!r}"
    assert "third-party     0" in out
    assert "RESULT: PASS" in out
    assert "grep subset of AST: yes" in out


def test_sweep_names_the_group_and_the_module_when_a_scratch_imports_socket(tmp_path):
    root = _scratch(tmp_path, "import socket\n")
    code, out = _sweep(root)
    assert code == 1, out
    assert "network modules" in out and "FAIL" in out
    assert "scratch.py" in out
    assert "RESULT: FAIL" in out


def test_sweep_fails_a_forbidden_import_by_name(tmp_path):
    """An N-group failure, not just the network row: the group and the module are named."""
    root = _scratch(tmp_path, "import requests\n")
    code, out = _sweep(root)
    assert code == 1, out
    assert "N5 no notification SDK / no webhook" in out
    assert "FAIL" in out and "scratch.py" in out


def test_asyncio_is_flagged_for_review_but_not_forbidden(tmp_path):
    """The prompt's ruling: asyncio is not a network module, so it is reviewed, not
    forbidden. The sweep must name it and still exit 0 - and must still fail if a real
    network module appears beside it."""
    code, out = _sweep(_scratch(tmp_path, "import asyncio\n"))
    assert code == 0, out
    review = [l for l in out.splitlines()
              if l.strip().startswith("asyncio") and "not a network module" in l]
    assert review and "imported" in review[0], out
    assert "RESULT: PASS" in out
    code2, out2 = _sweep(_scratch(tmp_path / "second", "import asyncio\nimport socket\n"))
    assert code2 == 1 and "socket" in out2, out2



# ── the report (docs/SECURITY.md) ─────────────────────────────────────────

REPORT = REPO / "docs" / "SECURITY.md"


def test_the_security_report_exists_and_is_not_stale():
    """I6c: the report exists, names the groups and the blockers, and — the useful
    half — embeds the *current* sweep verdicts, so it cannot drift away from the
    tree it claims to describe."""
    assert REPORT.exists(), f"missing {REPORT}"
    text = REPORT.read_text(encoding="utf-8")
    for group in ("R25 zero telemetry", "N3 no cloud SDK / no credentials",
                  "N4 no web framework / no listener",
                  "N5 no notification SDK / no webhook", "N7 no Windows code / no GUI"):
        assert group in text, f"the report does not name {group!r}"
    for blocker in ("B1", "C1", "B2", "U7"):
        assert blocker in text, f"the report does not name blocker {blocker}"
    assert "coming soon" not in text.lower(), "the report must not promise future work"
    assert REPORT.read_text(encoding="utf-8").count("3148d6b") >= 1, "I6a hash missing"
    assert text.count("759dec6") >= 1, "I6b hash missing"
    code, out = _sweep(SRC)
    assert code == 0, out
    for line in out.splitlines():
        if line.lstrip().startswith(("R25", "N3", "N4", "N5", "N7")) and "PASS" in line:
            assert line in text, f"the report's verdicts are stale, missing: {line!r}"
    assert "RESULT: PASS — every group clean" in text

