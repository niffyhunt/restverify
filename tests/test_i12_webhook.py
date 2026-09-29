"""I12 (R47) — `run --report-webhook`: the envelope leaves the machine.

Every delivery is a POST of the exact --json envelope to an operator-provided
https:// URL. Best-effort by contract: a delivery failure is one stderr line
and never changes the exit code. These tests prove that contract on a local
HTTPS server in a thread (real TLS, self-signed test cert trusted via the
injectable-context seam — the same seam a production reverse-proxy setup
would use for a private CA).

Test-infrastructure rules hold: no sleep, no wall-clock reads, no elapsed-time
assertions. The timeout test synchronizes on a threading.Event held by the
handler and releases it in teardown; assertions are on outcomes, not durations.
"""
import http.server
import json
import ssl
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from restverify import webhook as wh

CERT_DIR = Path(__file__).resolve().parent / "certs"
CERT = CERT_DIR / "testcert.pem"
KEY = CERT_DIR / "testkey.pem"


# ── the local HTTPS receiver ──────────────────────────────────────────────

class _Receiver(http.server.BaseHTTPRequestHandler):
    """Records requests; behavior driven by the class attributes each test sets."""

    server_version = "TestReceiver/1"
    protocol_version = "HTTP/1.1"

    hold_event = None          # set -> handler blocks until the Event fires
    status = 200
    seen = []                  # (method, path, headers-dict, body-bytes)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        _Receiver.seen.append((self.command, self.path, dict(self.headers), body))
        if _Receiver.hold_event is not None:
            # Simulate a slow receiver without sleeping: block until the test
            # releases the Event (teardown guarantees release).
            _Receiver.hold_event.wait(timeout=10)
        if 300 <= _Receiver.status < 400 and _Receiver.status in (301, 302, 307, 308):
            self.send_response(_Receiver.status)
            self.send_header("Location", "https://elsewhere.example/hook")
            self.end_headers()
            self.wfile.write(b"")
            return
        self.send_response(_Receiver.status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # quiet: nothing from the server reaches output
        pass


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    """One HTTPS server per test on an ephemeral port; torn down by shutdown()."""
    _Receiver.seen = []
    _Receiver.hold_event = None
    _Receiver.status = 200
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERT, KEY)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    srv = server   # a distinct name: `server = server` inside a class body
                   # makes `server` class-local and unbound for the f-string

    class Handle:
        url = f"https://127.0.0.1:{srv.server_address[1]}/hook"
        seen = _Receiver.seen
        server = srv

        @staticmethod
        def set_status(code):
            _Receiver.status = code

    yield Handle
    if _Receiver.hold_event is not None:
        _Receiver.hold_event.set()      # release a blocked handler, if any
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def trusted():
    """An SSL context that trusts the test cert, passed through deliver()'s
    injection seam — the production code path, not a mock."""
    return ssl.create_default_context(cafile=str(CERT))


def _payload(status="pass", exit_code=0):
    return {"tool": "restverify", "schema": 1, "version": "test", "command": "run",
            "status": status, "exit_code": exit_code}


# ── delivery on every outcome ─────────────────────────────────────────────

@pytest.mark.parametrize("status,code", [("pass", 0), ("diff_mismatch", 2),
                                         ("error", 1)])
def test_pass_mismatch_error_each_deliver_the_envelope(receiver, trusted, status, code):
    ok, line = wh.deliver(receiver.url, wh.envelope_bytes(_payload(status, code)),
                          10, ssl_context=trusted)
    assert ok, line
    method, path, headers, body = receiver.seen[0]
    assert method == "POST" and path == "/hook"
    assert headers["Content-Type"] == "application/json"
    assert headers["User-Agent"] == f"restverify/{wh.__version__}"
    assert json.loads(body.decode("utf-8")) == _payload(status, code)


def test_body_is_byte_identical_to_the_json_output(receiver, trusted):
    payload = _payload()
    exact = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    ok, _ = wh.deliver(receiver.url, wh.envelope_bytes(payload), 10, ssl_context=trusted)
    assert ok
    assert receiver.seen[0][3].decode("utf-8") == exact


# ── the failures are one line, and never touch the exit code ──────────────

def test_unreachable_url_reports_and_gives_up(receiver):
    dead = "https://127.0.0.1:1/hook"          # nothing listens there
    ok, line = wh.deliver(dead, wh.envelope_bytes(_payload()), 2)
    assert ok is False
    assert "failed" in line and dead in line


def test_timeout_reports_and_the_event_releases(receiver, trusted):
    event = threading.Event()
    _Receiver.hold_event = event
    try:
        ok, line = wh.deliver(receiver.url, wh.envelope_bytes(_payload()), 0.2,
                              ssl_context=trusted)
        assert ok is False
        assert "timed out" in line
    finally:
        event.set()


def test_redirect_is_refused(receiver, trusted):
    receiver.set_status(302)
    ok, line = wh.deliver(receiver.url, wh.envelope_bytes(_payload()), 10,
                          ssl_context=trusted)
    assert ok is False and "redirect" in line


def test_non_2xx_is_logged_not_fatal(receiver, trusted):
    receiver.set_status(503)
    ok, line = wh.deliver(receiver.url, wh.envelope_bytes(_payload()), 10,
                          ssl_context=trusted)
    assert ok is False and "503" in line


# ── parse-time validation: exit 64 territory ──────────────────────────────

@pytest.mark.parametrize("bad", ["http://collector.example/hook",
                                 "ftp://collector.example/hook",
                                 "https:///no-host", "https://", "",
                                 "collector.example/hook"])
def test_bad_urls_are_refused_before_any_network(bad):
    with pytest.raises(ValueError):
        wh.parse_url(bad)


@pytest.mark.parametrize("bad", ["0", "-1", "61", "abc", "inf", "nan", ""])
def test_bad_timeouts_are_refused(bad):
    with pytest.raises(ValueError):
        wh.parse_timeout(bad)


@pytest.mark.parametrize("good", ["1", "0.5", "60", 10])
def test_good_timeouts_pass(good):
    assert wh.parse_timeout(good) == float(good)


def test_cli_rejects_http_url_with_exit_64(capsys):
    """Parse-time validation: the bad URL dies in argparse's type= hook as a
    usage error (SystemExit 64) BEFORE any command runs, so no network attempt
    and no delivery line can exist."""
    from restverify import EXIT_USAGE
    from restverify.cli import main
    with pytest.raises(SystemExit) as ei:
        main(["run", "-r", "/srv/backup",
              "--report-webhook", "http://collector.example/hook", "--json"])
    assert ei.value.code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "https://" in err                      # the teaching message
    assert "restverify: webhook" not in err       # never a delivery line


# ── secrets never reach stderr ────────────────────────────────────────────

def test_stderr_redacts_query_and_userinfo(receiver):
    secret_url = "https://user:sup3rsecret@127.0.0.1:1/hook?token=hunter2&x=1"
    ok, line = wh.deliver(secret_url, wh.envelope_bytes(_payload()), 2)
    assert ok is False
    assert "sup3rsecret" not in line and "hunter2" not in line
    assert wh.redact(secret_url) == "https://127.0.0.1:1/hook"


def test_credentials_are_never_transmitted(receiver, trusted):
    secret_url = f"https://user:sup3rsecret@{receiver.url.split('://', 1)[1]}"
    ok, _ = wh.deliver(secret_url, wh.envelope_bytes(_payload()), 10,
                       ssl_context=trusted)
    assert ok
    _method, _path, headers, _body = receiver.seen[0]
    assert "Authorization" not in headers
    assert "sup3rsecret" not in _body.decode("utf-8")


# ── the boundary, made executable ─────────────────────────────────────────

def test_outbound_sockets_live_only_in_webhook_py():
    """I12 boundary: webhook.py is the only module that may open an outbound
    connection (urllib.request / http.client / socket). restic.py keeps the
    subprocess monopoly (N5 unchanged)."""
    from pathlib import Path as _P
    src = _P(__file__).resolve().parents[1] / "src" / "restverify"
    allowed = {"webhook.py"}
    tokens = ("import socket", "import ssl", "urllib.request", "urllib.error",
              "http.client", "urllib.parse")
    offenders = {}
    for path in sorted(src.glob("*.py")):
        body = path.read_text(encoding="utf-8")
        found = [t for t in tokens if t in body]
        if found and path.name not in allowed:
            offenders[path.name] = found
    assert offenders == {}, f"outbound-network imports outside webhook.py: {offenders}"


def test_parser_help_lists_both_flags(capsys):
    from restverify.cli import build_parser
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--help"])
    text = capsys.readouterr().out
    assert "--report-webhook" in text and "--webhook-timeout" in text
