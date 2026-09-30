"""`restverify dashboard` (I14, R49) — read-only local view of the run history.

One stdlib `http.server` page, nothing else: no framework, no JavaScript, no
form, no external resource, no write path. The history database is opened
read-only via a `mode=ro` SQLite URI — a dashboard that can create or write
the store it displays would be a liability, not a convenience.

Security posture (each item is pinned by a test):
  * binds 127.0.0.1 by default; `--bind-all` is refused (exit 64, before any
    socket exists) unless paired with `--yes-i-know`;
  * the Host header must name the bound address (or localhost) — a DNS
    rebinding page cannot aim a browser at this server; with `--bind-all`
    the check is skipped because binding to all interfaces was chosen
    deliberately, which changes who can reach the page;
  * every value from the database is html.escape()d on render, so a repo
    named `<script>alert(1)</script>` renders as text;
  * responses carry `Content-Security-Policy: default-src 'none'; style-src
    'unsafe-inline'`, `X-Content-Type-Options: nosniff`, `Cache-Control:
    no-store`;
  * GET/HEAD on `/` only — other paths 404, other methods 405;
  * request logging never repeats query strings.
"""
from __future__ import annotations

import argparse
import html
import sqlite3
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__

DASH_PROG = "restverify"
ROWS_SHOWN = 50
CSP = "default-src 'none'; style-src 'unsafe-inline'"
MAX_PORT = 65535

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>restverify — run history</title>
<style>
 body {{ background:#0d1117; color:#c9d1d9; font-family:ui-monospace,SFMono-Regular,
        Menlo,Consolas,monospace; margin:2rem; }}
 h1 {{ color:#58a6ff; font-size:1.2rem; }}
 p.summary {{ color:#8b949e; }}
 table {{ border-collapse:collapse; width:100%; margin-top:1rem; }}
 th, td {{ border:1px solid #21262d; padding:.35rem .6rem; text-align:left;
          font-size:.85rem; }}
 th {{ color:#58a6ff; background:#161b22; }}
 td.st-pass {{ color:#3fb950; }} td.st-diff_mismatch {{ color:#d29922; }}
 td.st-error {{ color:#f85149; }}
 td.num {{ text-align:right; }}
</style>
</head>
<body>
<h1>restverify — run history</h1>
<p class="summary">{summary}</p>
<table>
<tr><th>started (UTC)</th><th>status</th><th>exit</th><th>repo</th>
    <th>snapshot</th><th>duration</th></tr>
{rows}
</table>
</body>
</html>
"""

_NO_HISTORY = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>restverify — run history</title></head>
<body>
<h1>restverify — run history</h1>
<p class="summary">no runs recorded yet — run `restverify run -r &lt;repo&gt;`
first, then refresh this page.</p>
</body>
</html>
"""


def parse_port(value) -> int:
    """--port: integer 0-65535 (0 = pick a random free port)."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"--port must be an integer (got {value!r})") from None
    if not 0 <= port <= MAX_PORT:
        raise ValueError(f"--port must be between 0 and {MAX_PORT} (got {port})")
    return port


def port_arg(value) -> int:
    """argparse type= for --port: a bad value is a usage problem (exit 64)."""
    try:
        return parse_port(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _duration(ms) -> str:
    if ms is None:
        return "—"
    seconds = int(ms) / 1000.0
    if seconds >= 10:
        return f"{seconds:.0f}s"
    return f"{seconds:.1f}s"


def read_rows(db_path) -> tuple[list[dict] | None, dict | None, str | None]:
    """(rows, counts, error). Read-only by URI; a missing store is NOT an
    error — it renders the no-history page. The database is never created,
    never written, and the connection never outlives this call."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None, None, None
    uri = f"file:{db_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            "SELECT started_at, status, exit_code, repo, snapshot, duration_ms "
            "FROM runs ORDER BY id DESC LIMIT ?", (ROWS_SHOWN,)).fetchall()
        counts = dict(connection.execute(
            "SELECT status, COUNT(*) FROM runs GROUP BY status").fetchall())
    except sqlite3.Error as exc:
        return None, None, str(exc)
    finally:
        connection.close()
    out = [{"started_at": r[0], "status": r[1], "exit_code": r[2], "repo": r[3],
            "snapshot": r[4], "duration_ms": r[5]} for r in rows]
    return out, counts, None


def render_page(rows: list[dict], counts) -> str:
    """The one page. Every database value passes html.escape() — no exceptions."""
    if counts:
        total = sum(counts.values())
        passed = counts.get("pass", 0)
        mismatch = counts.get("diff_mismatch", 0)
        errored = counts.get("error", 0)
        summary = (f"{html.escape(str(total))} runs: {html.escape(str(passed))} pass / "
                   f"{html.escape(str(mismatch))} mismatch / "
                   f"{html.escape(str(errored))} error — newest {ROWS_SHOWN} below")
    else:
        summary = "no runs recorded yet"
    trs = []
    for row in rows or []:
        status = html.escape(str(row["status"]))
        trs.append(
            "<tr><td>{started}</td><td class=\"st-{status}\">{status}</td>"
            "<td class=\"num\">{exit}</td><td>{repo}</td><td>{snapshot}</td>"
            "<td>{duration}</td></tr>".format(
                started=html.escape(str(row["started_at"] or "")),
                status=status,
                exit=html.escape(str(row["exit_code"])),
                repo=html.escape(str(row["repo"] or "")),
                snapshot=html.escape(str(row["snapshot"] or "—")),
                duration=_duration(row["duration_ms"]),
            ))
    return _PAGE.format(summary=summary, rows="\n".join(trs))


class DashboardHandler(BaseHTTPRequestHandler):
    """GET/HEAD / → the page; everything else is refused by status code."""

    server_version = f"restverify-dashboard/{__version__}"
    protocol_version = "HTTP/1.1"

    # Filled by make_server: the bound host, the store path, and whether the
    # bind covers all interfaces (which also widens the Host check).
    db_path = None
    bound_host = "127.0.0.1"
    allow_any_host = False

    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        bare = {self.bound_host, "localhost"} if self.bound_host != "0.0.0.0" \
            else {"localhost"}
        out = set()
        for name in bare:
            out.add(name)
            out.add(f"{name}:{port}")
        return out

    def _host_ok(self) -> bool:
        if self.allow_any_host:
            return True
        host = self.headers.get("Host", "")
        return host in self._allowed_hosts()

    def _respond(self, code: int, body: bytes,
                 extra_headers: list[tuple[str, str]] | None = None) -> None:
        self.send_response(code)
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _page_response(self) -> None:
        rows, counts, error = read_rows(self.db_path)
        if error is not None:
            self._respond(500, _NO_HISTORY.encode("utf-8"))
            print(f"{DASH_PROG}: history store could not be read: {error}",
                  file=sys.stderr, flush=True)
            return
        if rows is None:
            self._respond(200, _NO_HISTORY.encode("utf-8"))
            return
        self._respond(200, render_page(rows, counts).encode("utf-8"))

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            self._respond(403, b"forbidden host\n")
            return
        if self.path.split("?", 1)[0] != "/":
            self._respond(404, b"not found\n")
            return
        self._page_response()

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def _method_refused(self):
        if not self._host_ok():
            self._respond(403, b"forbidden host\n")
            return
        self._respond(405, b"method not allowed\n",
                      extra_headers=[("Allow", "GET, HEAD")])

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_refused

    def log_message(self, fmt, *args):
        # Rebuilt from sanitized parts on purpose: the default request line
        # echoes the full path INCLUDING the query string, and URLs can carry
        # tokens — a dashboard log that repeats them is a leak waiting to happen.
        # (fmt/args deliberately unused: they carry the unsanitized request line.)
        path = self.path.split("?", 1)[0]
        print(f'{self.address_string()} - "{self.command} {path}"',
              file=sys.stderr, flush=True)


def make_server(host: str, port: int, db_path) -> ThreadingHTTPServer:
    """Build the server WITHOUT serving: tests run serve_forever() on a thread
    and shut it down; main() calls serve() on the returned object."""
    handler = type("BoundDashboardHandler", (DashboardHandler,), {
        "db_path": db_path,
        "bound_host": host,
        "allow_any_host": host == "0.0.0.0",
    })
    server = ThreadingHTTPServer((host, port), handler)
    return server


def serve(server: ThreadingHTTPServer) -> None:
    """Drive the server until Ctrl-C, then close it cleanly. dashboard.py owns
    the serve_forever call site on purpose: this module is THE one listener in
    the shipped tree (the N4 boundary tests pin that), so the listening
    happens here and nowhere else."""
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
