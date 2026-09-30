"""Increment I6a — the negative requirements as executable assertions.

N3 (no cloud SDK, no credential handling), N4 (no web framework, no listener),
N5 (no notification SDK, no webhook call), N7 (no Windows code, no GUI).

Why this file exists: through I5, N3-N7 were prose claims in audit documents. A
claim in a document cannot fail a build; an assertion can. Every test below walks
the *real* shipped modules under `src/restverify/`, so adding a forbidden import
turns it red. The I6 briefing carries one adversarial proof per group (scratch
module added -> red, scratch removed -> green), because a green absence test that
cannot go red is worthless.

Test infrastructure (named here per the PHASE2-PLAN "Test infrastructure" rule):
this module is deliberately self-contained. The index below — `SHIPPED`,
`_trees()`, `_imports()`, `_dotted_name()`, `_env_read_names()`, `_literal_strings()`,
`_spawn_calls()`, `_listener_calls()`, `_platform_branches()` — is duplicated on purpose in
`scripts/security_sweep.py` (I6b) instead of being shared: one broken walker would
otherwise silently disable every assertion at once.

Scope discipline: an absence test is only meaningful if it is narrower than the
truth it protects. Two examples baked into the patterns below:
  * `restic.py` legitimately contains the teaching URL `https://restic.net`, so N3
    forbids *provider* URLs (amazonaws/googleapis/azure/oracle) and N5 forbids URLs
    appearing in *subprocess arguments*, not URLs in general.
  * `restic.py` does a bulk `dict(os.environ)` copy, so N3 forbids *named* reads of
    AWS_/AZURE_/GOOGLE_/GCP_ variables, not any contact with the environment.
"""
import ast
import functools
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "restverify"
SHIPPED = sorted(SRC.rglob("*.py"))          # rglob: a new subpackage is covered too


# ── the source index ──────────────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def _trees():
    """module name -> parsed AST. Parsed once; the shipped files are read-only."""
    return {p.name: ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            for p in SHIPPED}


def _dotted_name(node):
    """`os.environ.get` for an attribute chain, `foo` for a bare name, else ''."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


@functools.lru_cache(maxsize=None)
def _imports():
    """module name -> every module name it imports, dynamic imports included.

    A constant argument to `importlib.import_module("socket")` is an import too; a
    plain grep would miss it, which is why this walks the AST.
    """
    out = {}
    for name, tree in _trees().items():
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.add("." * (node.level or 0) + (node.module or ""))
            elif isinstance(node, ast.Call):
                func = node.func
                label = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if label in ("import_module", "__import__") and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        found.add(first.value)
        out[name] = {i for i in found if i}
    return out


def _find(forbidden):
    """{module: [matching import names]} for every forbidden module or prefix."""
    hits = {}
    for name, names in _imports().items():
        matched = sorted(n for n in names
                         if any(n == f or n.startswith(f + ".") for f in forbidden))
        if matched:
            hits[name] = matched
    return hits



def _env_read_names(tree):
    """Named environment lookups: os.environ['X'], os.environ.get('X'), os.getenv('X')."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _dotted_name(node.value) in ("os.environ", "environ"):
            index = node.slice
            if isinstance(index, ast.Constant) and isinstance(index.value, str):
                names.add(index.value)
        elif isinstance(node, ast.Call):
            dotted = _dotted_name(node.func)
            if dotted in ("os.environ.get", "environ.get", "os.getenv", "getenv",
                          "os.environ.pop", "os.environ.setdefault") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.add(first.value)
    return names


def _literal_strings(node):
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value]


def _spawn_calls(tree):
    """Calls that can spawn a process, i.e. the subprocess/os.exec* family."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            dotted = _dotted_name(node.func)
            if dotted.startswith("subprocess.") or dotted in (
                    "os.system", "os.popen", "os.spawnv", "os.spawnl",
                    "os.execv", "os.execvp"):
                out.append(node)
    return out


def _listener_calls(tree):
    """Listening call sites: socket.bind/listen, ssl.wrap_socket, *.serve_forever,
    http.server / socketserver server constructors."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            dotted = _dotted_name(node.func)
            if not dotted:
                continue
            head, _, tail = dotted.rpartition(".")
            if tail in ("serve_forever", "wrap_socket"):
                out.add(dotted)
            elif tail in ("bind", "listen") and "sock" in head.lower():
                out.add(dotted)
            elif dotted in ("http.server.HTTPServer", "http.server.ThreadingHTTPServer",
                            "socketserver.TCPServer", "socketserver.ThreadingTCPServer",
                            "socketserver.UDPServer"):
                out.add(dotted)
    return out


def _platform_branches(tree):
    """Compare nodes that branch on Windows (`os.name == 'nt'`, `sys.platform == 'win32'`,
    `platform.system() == 'Windows'`). Note: the first version of this helper looked for
    the substring `os.name` in `ast.dump(node)` — which never appears there (the dump is
    `attr='name'` inside `Attribute(value=Name(id='os'))`), so it silently passed on a
    module that branched on Windows. The adversarial proof caught it; this is the fix."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            dotted = {_dotted_name(s) for s in sides if isinstance(s, ast.Attribute)}
            literals = {c.value.lower() for c in sides
                        if isinstance(c, ast.Constant) and isinstance(c.value, str)}
            if dotted & {"os.name", "platform.system", "sys.platform"} \
                    and literals & {"nt", "windows", "win32", "dos", "ce"}:
                out.append(ast.dump(node)[:70])
    return out


# ── guard against a vacuous file ──────────────────────────────────────────

def test_the_source_index_actually_sees_the_shipped_modules():
    """If the glob or the walker broke, every absence test below would pass
    vacuously. This test fails loudly instead. 11 modules at I5c."""
    assert len(SHIPPED) >= 11, f"only {len(SHIPPED)} shipped modules found under {SRC}"
    with_imports = [n for n, names in _imports().items() if names]
    assert len(with_imports) >= 10, f"the AST walk found imports in only {with_imports}"
    assert any("subprocess" in names for names in _imports().values()), \
        "the walker cannot see restic.py's subprocess import"


# ── N3: no cloud-provider SDK, no credential handling ─────────────────────

_CLOUD_SDKS = ["boto3", "botocore", "google.cloud", "azure", "azure.identity",
               "google.auth", "oci", "aliyun", "minio", "s3fs", "gcsfs", "libcloud"]
_CREDENTIAL_ENV_PREFIXES = ("AWS_", "AZURE_", "GOOGLE_", "GCP_")
_CREDENTIAL_TOKEN_RE = re.compile(r"access_key|secret_key|client_secret", re.IGNORECASE)
_PROVIDER_URL_RE = re.compile(
    r"https?://[^/\s\"']*(amazonaws\.com|googleapis\.com|azure\.com|cloud\.oracle\.com)",
    re.IGNORECASE)


@pytest.mark.parametrize("forbidden", _CLOUD_SDKS)
def test_n3_no_cloud_sdk_import(forbidden):
    hits = _find([forbidden])
    assert hits == {}, f"N3: cloud SDK imported: {hits}"


def test_n3_no_cloud_credential_env_reads():
    """AST half: a *named* read of an AWS_/AZURE_/GOOGLE_/GCP_ variable.
    `dict(os.environ)` in restic.py is a bulk copy, not a credential read, so it
    is correctly not a hit."""
    reads = {name: sorted(n for n in _env_read_names(tree)
                          if n.startswith(_CREDENTIAL_ENV_PREFIXES))
             for name, tree in _trees().items()}
    assert {k: v for k, v in reads.items() if v} == {}, \
        f"N3: cloud credential read from the environment: {reads}"
    # grep half: the same name appearing as a literal anywhere is the same smell.
    literals = {name: sorted(s for s in _literal_strings(tree)
                             if s.startswith(_CREDENTIAL_ENV_PREFIXES))
                for name, tree in _trees().items()}
    assert {k: v for k, v in literals.items() if v} == {}, \
        f"N3: cloud credential env name in a literal: {literals}"


def test_n3_no_credential_tokens_or_provider_urls():
    hits = {}
    for path in SHIPPED:
        body = path.read_text(encoding="utf-8")
        found = sorted(set(_CREDENTIAL_TOKEN_RE.findall(body))
                       | {m.group(0) for m in _PROVIDER_URL_RE.finditer(body)})
        if found:
            hits[path.name] = found
    assert hits == {}, f"N3: credential token or provider URL in shipped source: {hits}"


# ── N4: no web framework, no listener ─────────────────────────────────────

_WEB_FRAMEWORKS = ["flask", "django", "fastapi", "starlette", "aiohttp", "tornado",
                   "bottle", "pyramid", "uvicorn", "gunicorn", "waitress", "werkzeug"]
_SERVER_MODULES = ("http.server", "socketserver", "ssl")


@pytest.mark.parametrize("forbidden", _WEB_FRAMEWORKS)
def test_n4_no_web_framework_import(forbidden):
    hits = _find([forbidden])
    assert hits == {}, f"N4: web framework imported: {hits}"


def test_n4_no_listener_call_or_server_module():
    """Amended in I14 (R49, operator-approved spec): dashboard.py is the ONE
    listener module — a local, read-only history page on the stdlib
    http.server (no framework, no JS, mode=ro store, Host-header check). It
    may own exactly http.server as its server import and
    server.serve_forever as its single listening call site; EVERY other
    module with either is still a failure."""
    allowed_calls = {"dashboard.py": {"server.serve_forever"}}
    allowed_imports = {"dashboard.py": {"http.server"}}
    calls = {name: sorted(_listener_calls(tree)) for name, tree in _trees().items()}
    offenders = {name: sorted(set(v) - allowed_calls.get(name, set()))
                 for name, v in calls.items()}
    offenders = {name: v for name, v in offenders.items() if v}
    assert offenders == {}, f"N4: something listens for connections: {offenders}"
    servers = {name: sorted(i for i in names
                            if i in _SERVER_MODULES
                            or any(i.startswith(m + ".") for m in _SERVER_MODULES))
               for name, names in _imports().items()}
    server_offenders = {name: sorted(set(v) - allowed_imports.get(name, set()))
                        for name, v in servers.items()}
    server_offenders = {name: v for name, v in server_offenders.items() if v}
    assert server_offenders == {}, f"N4: server/ssl module imported: {server_offenders}"


# ── N5: no notification SDK, no webhook call ──────────────────────────────

_NOTIFICATION_SDKS = ["smtplib", "email", "sendgrid", "mailgun", "twilio",
                      "slack_sdk", "slack", "discord", "telegram", "requests"]


@pytest.mark.parametrize("forbidden", _NOTIFICATION_SDKS)
def test_n5_no_notification_sdk_import(forbidden):
    """smtplib and email are stdlib and still forbidden: N5 is about the tool not
    talking to anyone, not about where the module comes from."""
    hits = _find([forbidden])
    assert hits == {}, f"N5: notification module imported: {hits}"


def test_n5_no_url_reaches_a_spawned_process():
    hits = {}
    for name, tree in _trees().items():
        for call in _spawn_calls(tree):
            urls = sorted(s for s in _literal_strings(call)
                          if s.startswith(("http://", "https://")))
            if urls:
                hits.setdefault(name, []).extend(urls)
    assert hits == {}, f"N5: a URL was passed to a spawned process: {hits}"


def test_n5_only_restic_py_spawns_processes():
    """The boundary, made executable: restverify signals through exit codes, so
    exactly one module (restic.py) may ever spawn a process."""
    spawners = sorted(name for name, tree in _trees().items() if _spawn_calls(tree))
    assert spawners == ["restic.py"], \
        f"the boundary says restic.py is the only spawner, found: {spawners}"


# ── N7: no Windows-specific code, no GUI ──────────────────────────────────

_GUI_TOOLKITS = ["tkinter", "tkinter.ttk", "PyQt5", "PyQt6", "PySide2", "PySide6",
                 "wx", "kivy"]
_WINDOWS_APIS = ["win32api", "win32con", "winreg", "pywin32", "msvcrt", "winsound"]
_WINDOWS_STRINGS = (re.compile(r"\bwin32\b"), re.compile(r"\bHKEY_"),
                    re.compile(r"\bC:\\{1,2}[A-Za-z]"))   # C:\Users and "C:\\Users"


@pytest.mark.parametrize("forbidden", _GUI_TOOLKITS)
def test_n7_no_gui_toolkit_import(forbidden):
    hits = _find([forbidden])
    assert hits == {}, f"N7: GUI toolkit imported: {hits}"


@pytest.mark.parametrize("forbidden", _WINDOWS_APIS)
def test_n7_no_windows_api_import(forbidden):
    hits = _find([forbidden])
    assert hits == {}, f"N7: Windows API imported: {hits}"


def test_n7_no_windows_strings_or_drive_paths():
    hits = {}
    for path in SHIPPED:
        body = path.read_text(encoding="utf-8")
        found = sorted({m.group(0) for rx in _WINDOWS_STRINGS for m in rx.finditer(body)})
        if found:
            hits[path.name] = found
    assert hits == {}, f"N7: Windows-only string in shipped source: {hits}"
    # A platform branch is Windows-specific code even without those strings.
    branches = {name: sorted(_platform_branches(tree))
                for name, tree in _trees().items()}
    assert {k: v for k, v in branches.items() if v} == {}, \
        f"N7: platform branch for Windows: {branches}"
