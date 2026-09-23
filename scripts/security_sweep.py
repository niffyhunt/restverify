#!/usr/bin/env python3
"""V2 security sweep for restverify's shipped modules (increment I6b).

What it prints, in order:
  1. IMPORTS      — every import in every module under src/restverify/
  2. RESOLUTION   — each import resolved to stdlib / third-party / local
  3. WORD MENTIONS— the raw-text grep pass, for review (prose is not a violation)
  4. VERDICTS     — PASS/FAIL for R25, N3, N4, N5, N7 plus third-party and network
  5. CROSS-CHECK  — the grep pass' import hits must be a subset of the AST walk's

Exit code: 0 when every verdict passes, 1 otherwise.  Module order is sorted, so
the output is stable across runs.

Why the AST decides and the grep only cross-checks: `importlib.import_module("socket")`
is an import that a `^import` grep never sees, while a grep for a vendor or module
name hits English.  Both are real: `cli.py`'s help text says "last path segment"
(R25's word list contains "segment") and `manifest.py`'s comments say
"fifo/socket/device" (a file kind, not networking).  Those are printed under WORD
MENTIONS with file:line so a reviewer can see they are prose, and they do not fail
the sweep.

This script is not shipped and not packaged: pyproject's wheel target ships only
`src/restverify`.  It is the generator for the I6 report's sweep section, and it
also runs against a scratch tree (`--root`) so the tests can prove it fails loudly.
"""
import argparse
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

FORBIDDEN = {
    "N3": ["boto3", "botocore", "google.cloud", "azure", "azure.identity", "google.auth",
           "oci", "aliyun", "minio", "s3fs", "gcsfs", "libcloud"],
    "N4": ["flask", "django", "fastapi", "starlette", "aiohttp", "tornado", "bottle",
           "pyramid", "uvicorn", "gunicorn", "waitress", "werkzeug",
           "http.server", "socketserver", "ssl"],
    "N5": ["smtplib", "email", "sendgrid", "mailgun", "twilio", "slack_sdk", "slack",
           "discord", "telegram", "requests"],
    "N7": ["tkinter", "tkinter.ttk", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "kivy",
           "win32api", "win32con", "winreg", "pywin32", "msvcrt", "winsound"],
}

NETWORK_MODULES = ["socket", "ssl", "urllib", "urllib2", "urllib3", "http", "requests",
                   "aiohttp", "httpx", "ftplib", "smtplib", "telnetlib", "xmlrpc", "asyncio"]
REVIEW_ONLY = {"asyncio": "not a network module, but suspicious in a single-threaded CLI"}

TELEMETRY_WORDS = ["analytics", "telemetry", "sentry", "datadog", "newrelic", "honeycomb",
                   "lightstep", "opentelemetry", "prometheus", "statsd", "mixpanel",
                   "amplitude", "segment", "posthog", "matomo", "plausible"]
TELEMETRY_HOSTS = ["sentry.io", "datadoghq.com", "newrelic.com", "honeycomb.io",
                   "lightstep.com", "grafana.net", "prometheus.io", "mixpanel.com",
                   "amplitude.com", "segment.io", "posthog.com", "matomo.org",
                   "plausible.io"]

_GREP_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*))", re.MULTILINE)
_WORD_RE = re.compile(r"\b(" + "|".join(TELEMETRY_WORDS + NETWORK_MODULES) + r")\b",
                      re.IGNORECASE)



def modules(root):
    return sorted(Path(root).rglob("*.py"))


def dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def parse(root):
    """name -> (path, tree). A module that will not parse is itself a finding."""
    out = {}
    for path in modules(root):
        out[path.name] = (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return out


def imports_of(tree):
    """Every module name the tree imports, including constant dynamic imports."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add("." * (node.level or 0) + (node.module or ""))
        elif isinstance(node, ast.Call):
            func = node.func
            label = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if label in ("import_module", "__import__") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.add(first.value)
    return {name for name in found if name}


def resolve(name):
    if not name or name.startswith("."):
        return "local"
    root = name.split(".")[0]
    if root == "restverify":
        return "local"
    return "stdlib" if root in sys.stdlib_module_names else "third-party"


def _first_component(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def telemetry_identifiers(tree):
    """Telemetry vendor names used as identifiers: imports, call bases, attributes."""
    words = {word.lower() for word in TELEMETRY_WORDS}
    hits = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits.update(a.name for a in node.names if a.name.split(".")[0].lower() in words)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0].lower() in words:
                hits.add(node.module)
        elif isinstance(node, ast.Attribute):
            if node.attr.lower() in words:
                hits.add(node.attr)
        elif isinstance(node, ast.Call):
            base = _first_component(node.func)
            if base and base.lower() in words:
                hits.add(base)
    return hits


def telemetry_urls(tree):
    urls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(host in node.value.lower() for host in TELEMETRY_HOSTS):
                urls.add(node.value.strip()[:80])
    return urls


def matches(forbidden, names):
    """Names that are, or are a submodule of, a forbidden module."""
    return sorted(n for n in names
                  if any(n == f or n.startswith(f + ".") for f in forbidden))



# ── the non-import halves of the groups (env reads, calls, literals) ──────

CREDENTIAL_ENV_PREFIXES = ("AWS_", "AZURE_", "GOOGLE_", "GCP_")
CREDENTIAL_TOKEN_RE = re.compile(r"access_key|secret_key|client_secret", re.IGNORECASE)
PROVIDER_URL_RE = re.compile(
    r"https?://[^/\s\"']*(amazonaws\.com|googleapis\.com|azure\.com|cloud\.oracle\.com)",
    re.IGNORECASE)
WINDOWS_STRING_RES = (re.compile(r"\bwin32\b"), re.compile(r"\bHKEY_"),
                      re.compile(r"\bC:\\{1,2}[A-Za-z]"))
_SPAWN_LABELS = ("subprocess.run", "subprocess.Popen", "subprocess.call",
                 "subprocess.check_call", "subprocess.check_output", "os.system", "os.popen")


def env_read_names(tree):
    """Named environment lookups only: `dict(os.environ)` is not a credential read."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and dotted(node.value) in ("os.environ", "environ"):
            index = node.slice
            if isinstance(index, ast.Constant) and isinstance(index.value, str):
                names.add(index.value)
        elif isinstance(node, ast.Call):
            label = dotted(node.func)
            if label in ("os.environ.get", "environ.get", "os.getenv", "getenv",
                         "os.environ.pop", "os.environ.setdefault") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.add(first.value)
    return names


def listener_calls(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            label = dotted(node.func)
            if not label:
                continue
            head, _, tail = label.rpartition(".")
            if tail in ("serve_forever", "wrap_socket"):
                out.add(label)
            elif tail in ("bind", "listen") and "sock" in head.lower():
                out.add(label)
            elif label in ("http.server.HTTPServer", "http.server.ThreadingHTTPServer",
                           "socketserver.TCPServer", "socketserver.ThreadingTCPServer",
                           "socketserver.UDPServer"):
                out.add(label)
    return out


def spawn_calls(tree):
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and dotted(node.func) in _SPAWN_LABELS]


def strings_in(node):
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value]


def literal_hits(root):
    """Raw-text pass: credential tokens, provider URLs, Windows strings, word mentions."""
    tokens, urls, windows, words = {}, {}, {}, {}
    for path in modules(root):
        body = path.read_text(encoding="utf-8")
        found = sorted(set(CREDENTIAL_TOKEN_RE.findall(body))
                       | {m.group(0) for m in PROVIDER_URL_RE.finditer(body)})
        if found:
            tokens[path.name] = found
        for line_no, line in enumerate(body.splitlines(), 1):
            for match in _WORD_RE.finditer(line):
                words.setdefault(path.name, []).append(
                    (line_no, match.group(0), line.strip()[:74]))
            for win in (m.group(0) for rx in WINDOWS_STRING_RES for m in rx.finditer(line)):
                windows.setdefault(path.name, []).append((line_no, win))
    for name, (_, tree) in parse(root).items():
        if telemetry_urls(tree):
            urls[name] = sorted(telemetry_urls(tree))
    return tokens, urls, windows, words


# ── the report ────────────────────────────────────────────────────────────

def build_report(root):
    root = Path(root)
    parsed = parse(root)
    per_module = {name: imports_of(tree) for name, (_, tree) in parsed.items()}
    all_names = sorted({i for names in per_module.values() for i in names})
    by_origin = defaultdict(list)
    for name in all_names:
        by_origin[resolve(name)].append(name)

    tokens, t_urls, windows, words = literal_hits(root)
    verdicts = []
    for group, forbidden in FORBIDDEN.items():
        bad = {}
        for name, names in per_module.items():
            hit = matches(forbidden, names)
            if hit:
                bad[name] = hit
            tree = parsed[name][1]
            if group == "N3":
                creds = sorted(n for n in env_read_names(tree)
                               if n.startswith(CREDENTIAL_ENV_PREFIXES))
                if creds:
                    bad.setdefault(name, []).extend(f"env:{c}" for c in creds)
            if group == "N4":
                listening = sorted(listener_calls(tree))
                if listening:
                    bad.setdefault(name, []).extend(listening)
            if group == "N5":
                for call in spawn_calls(tree):
                    urls = sorted(s for s in strings_in(call)
                                  if s.startswith(("http://", "https://")))
                    if urls:
                        bad.setdefault(name, []).extend(urls)
            if group == "N7":
                for line_no, text in windows.get(name, []):
                    bad.setdefault(name, []).append(f"line {line_no}: {text}")
        label = {"N3": "N3 no cloud SDK / no credentials",
                 "N4": "N4 no web framework / no listener",
                 "N5": "N5 no notification SDK / no webhook",
                 "N7": "N7 no Windows code / no GUI"}[group]
        if group == "N3" and tokens:
            bad.setdefault("literal pass", []).extend(
                f"{mod}:{hit}" for mod, hits in tokens.items() for hit in hits)
        verdicts.append((label, bad or None))

    third_party = sorted(by_origin["third-party"])
    network = [n for n in all_names
               if n.split(".")[0] in NETWORK_MODULES and n.split(".")[0] not in REVIEW_ONLY]
    network_named = [f"{m}: {n}"
                     for n in network
                     for m in sorted(mod for mod, names in per_module.items() if n in names)]
    third_party_named = [f"{m}: {n}"
                         for n in third_party
                         for m in sorted(mod for mod, names in per_module.items() if n in names)]
    reviews = [(module, "imported" if module in all_names else "absent", reason)
               for module, reason in REVIEW_ONLY.items()]
    telemetry = []
    for name, (_, tree) in parsed.items():
        for symbol in sorted(telemetry_identifiers(tree)):
            telemetry.append(f"{name}: {symbol}")
    telemetry.extend(f"{mod}: {url}" for mod, urls in t_urls.items() for url in urls)
    verdicts.insert(0, ("R25 zero telemetry", telemetry or None))
    verdicts.append(("third-party imports", third_party_named or None))
    verdicts.append(("network modules", network_named or None))

    grep_names = set()
    for name, (path, _) in parsed.items():
        for single, both in _GREP_IMPORT_RE.findall(path.read_text(encoding="utf-8")):
            grep_names.add(single or both)
    orphan = sorted({n.split(".")[0] for n in grep_names}
                    - {n.split(".")[0] for n in all_names})
    return {"root": root, "parsed": parsed, "per_module": per_module,
            "by_origin": by_origin, "verdicts": verdicts, "reviews": reviews,
            "grep_count": len(grep_names), "ast_count": len(all_names),
            "orphan": orphan, "words": words, "tokens": tokens, "windows": windows}


def print_report(data):
    root, parsed = data["root"], data["parsed"]
    print("restverify V2 security sweep (I6b)")
    print(f"  root            {root}")
    print(f"  modules         {len(parsed)}")
    print(f"  python          {sys.version.split()[0]}")
    print(f"  third-party     {len(data['by_origin']['third-party'])}")
    print()
    print("IMPORTS")
    for name in sorted(data["per_module"]):
        names = sorted(data["per_module"][name])
        print(f"  {name:<16} {', '.join(names) if names else '(none)'}")
    print()
    print("RESOLUTION")
    print(f"  {'import':<28} {'origin':<12} modules")
    for origin in ("stdlib", "local", "third-party"):
        for name in data["by_origin"][origin]:
            users = sorted(m for m, names in data["per_module"].items() if name in names)
            print(f"  {name:<28} {origin:<12} {', '.join(users)}")
    print()
    print("WORD MENTIONS (raw grep pass, for review — prose is not a violation)")
    for name in sorted(data["words"]):
        for line_no, word, text in data["words"][name]:
            print(f"  {name}:{line_no}: {word!r} in: {text}")
    print()
    print("VERDICTS")
    for label, failures in data["verdicts"]:
        verdict = "PASS" if not failures else "FAIL"
        detail = ""
        if isinstance(failures, dict):
            detail = "  " + "; ".join(
                f"{module}: {', '.join(str(i) for i in hits)}"
                for module, hits in failures.items())
        elif failures:
            detail = "  " + "; ".join(str(f) for f in failures)
        elif label.startswith(("third-party", "network")):
            detail = "  (none)"
        print(f"  {label:<36} {verdict}{detail}")
    print()
    print("REVIEW NOTES (flagged by the sweep, not forbidden by any requirement)")
    for module, status, reason in data["reviews"]:
        print(f"  {module:<10} {status:<9} {reason}")
    print()
    print("CROSS-CHECK")
    print(f"  grep found {data['grep_count']} import statements; AST walk found "
          f"{data['ast_count']} distinct modules")
    print(f"  grep roots the AST walk did not see: {data['orphan'] or 'none'}")
    print(f"  grep subset of AST: {'yes' if not data['orphan'] else 'NO'}")
    print()
    failed = [label for label, failures in data["verdicts"] if failures]
    print(f"RESULT: {'FAIL — ' + ', '.join(failed) if failed else 'PASS — every group clean'}")
    return 1 if failed or data["orphan"] else 0


def main(argv=None):
    here = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="restverify V2 security sweep")
    parser.add_argument("--root", default=str(here / "src" / "restverify"),
                        help="the package directory to sweep (default: src/restverify)")
    args = parser.parse_args(argv)
    return print_report(build_report(args.root))


if __name__ == "__main__":
    sys.exit(main())
