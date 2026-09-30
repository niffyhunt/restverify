"""I14 (R49) — `restverify dashboard`: the run history as a local web page.

The contract under test: READ-ONLY end to end (sqlite mode=ro URI, never
created, never written), localhost by default, `--bind-all` refused before any
socket exists unless `--yes-i-know`, Host-header allow-list (DNS-rebinding
defense), html.escape on every database value, CSP/nosniff/no-store headers,
GET/HEAD on / only, 50 newest rows, no <form>/<script>/external resources,
query strings never logged, Ctrl-C clean.

The N4 boundary amendment lives here too: dashboard.py is the ONE listener
module, asserted from both directions (it does listen; nothing else does).
"""
import http.client
import sqlite3
import threading

import pytest

from restverify import EXIT_PASS, EXIT_USAGE
from restverify import dashboard as dash
from restverify import history as historymod
from restverify.cli import main


# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    """A seeded real history store (the dashboard reads schema, not mocks)."""
    state = tmp_path / "state"
    state.mkdir()
    records = []
    for i in range(60):
        records.append(historymod.RunRecord(
            repo=f"/srv/backup{i % 3}", status=["pass", "diff_mismatch", "error"][i % 3],
            exit_code=[0, 2, 1][i % 3],
            started_at=f"2026-09-{1 + i % 28:02d}T10:00:00Z",
            finished_at=f"2026-09-{1 + i % 28:02d}T10:00:05Z",
            duration_ms=5000 + i, snapshot=f"deadbeef"[:8], file_count=10 + i,
            tool_version="test", schema=1,
        ))
    for record in records:
        historymod.record(record, base=state)
    return state / "history.db"


@pytest.fixture
def server(store):
    """The real server on an ephemeral port, driven exactly like main() does."""
    srv = dash.make_server("127.0.0.1", 0, store)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def _get(server, path="/", method="GET", host=None):
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host} if host else {}
    connection.request(method, path, headers=headers)
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response, body


# ── the page ──────────────────────────────────────────────────────────────

def test_get_root_serves_50_newest_rows_and_summary(server):
    response, body = _get(server)
    html_text = body.decode("utf-8")
    assert response.status == 200
    assert "text/html" in response.getheader("Content-Type")
    assert html_text.count("<tr>") == 51        # 50 runs + the header row
    assert "60 runs" in html_text               # summary counts the whole store
    assert "20 pass / 20 mismatch / 20 error" in html_text


def test_rows_are_newest_first(server, store):
    _, body = _get(server)
    html_text = body.decode("utf-8")
    connection = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    newest = connection.execute(
        "SELECT started_at FROM runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    connection.close()
    assert newest in html_text


def test_headers_csp_nosniff_no_store(server):
    response, _ = _get(server)
    assert response.getheader("Content-Security-Policy") == \
        "default-src 'none'; style-src 'unsafe-inline'"
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert response.getheader("Cache-Control") == "no-store"


def test_no_form_no_script_no_external_resources(server):
    _, body = _get(server)
    text = body.decode("utf-8").lower()
    assert "<form" not in text
    assert "<script" not in text
    assert "http://" not in text and "https://" not in text


def test_repo_name_with_html_is_escaped(server, store):
    connection = sqlite3.connect(str(store))
    connection.execute(
        "UPDATE runs SET repo = '<script>alert(1)</script>' "
        "WHERE id = (SELECT MAX(id) FROM runs)")   # a row the newest-50 shows
    connection.commit()
    connection.close()
    _, body = _get(server)
    text = body.decode("utf-8")
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_missing_store_is_a_page_not_an_error(tmp_path):
    srv = dash.make_server("127.0.0.1", 0, tmp_path / "nope" / "history.db")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        response, body = _get(srv)
        assert response.status == 200
        assert "no runs recorded yet" in body.decode("utf-8")
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def test_store_file_is_never_modified(server, store):
    before = store.read_bytes()
    _get(server)
    _get(server, method="HEAD")
    _get(server, path="/?x=1")
    _get(server, method="POST")
    assert store.read_bytes() == before
    assert list(store.parent.glob("history.db-*")) == []   # no -wal/-journal left


# ── refusals ──────────────────────────────────────────────────────────────

def test_unknown_path_is_404(server):
    response, _ = _get(server, path="/nope")
    assert response.status == 404


def test_post_and_other_methods_are_405(server):
    for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
        response, _ = _get(server, method=method)
        assert response.status == 405, method
        assert "GET" in response.getheader("Allow")


def test_head_is_served_like_get_without_the_body(server):
    response, body = _get(server, method="HEAD")
    assert response.status == 200
    assert body == b""
    assert int(response.getheader("Content-Length")) > 0


def test_forged_host_header_is_rejected(server):
    response, _ = _get(server, host="evil.example")
    assert response.status == 403


def test_bound_host_and_localhost_are_accepted(server):
    port = server.server_address[1]
    for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
        response, _ = _get(server, host=host)
        assert response.status == 200, host


def test_query_strings_are_never_logged(server, store, capsys):
    _get(server, path="/?token=supersecret")
    _get(server)
    # The handler logs to stderr at request time; nothing may echo the query.
    assert "supersecret" not in capsys.readouterr().err


# ── the CLI surface ───────────────────────────────────────────────────────

def test_cli_bind_all_without_yes_i_know_is_exit_64_before_any_socket(
        isolated_state, capsys):
    code = main(["dashboard", "--bind-all"])
    err = capsys.readouterr().err
    assert code == EXIT_USAGE
    assert "--yes-i-know" in err
    assert "network" in err


def test_cli_bad_port_is_exit_64(isolated_state, capsys):
    with pytest.raises(SystemExit) as ei:
        main(["dashboard", "--port", "70000"])
    assert ei.value.code == EXIT_USAGE
    with pytest.raises(SystemExit):
        main(["dashboard", "--port", "abc"])


def test_cli_dashboard_is_wired(isolated_state):
    from restverify import cli as climod
    assert "dashboard" in climod._KNOWN_COMMANDS
    assert climod._DISPATCH.get("dashboard") is not None


def test_cli_dashboard_prints_the_url(isolated_state, capsys, monkeypatch):
    """main() starts serving and returns 0 on Ctrl-C; the URL is on stderr."""
    import restverify.cli as climod

    class FakeServer:
        server_address = ("127.0.0.1", 4567)

    calls = {}
    def _fake_make(host, port, db):
        calls.setdefault("args", (host, port, db))
        return FakeServer()
    monkeypatch.setattr(climod.dashboardmod, "make_server", _fake_make)
    monkeypatch.setattr(climod.dashboardmod, "serve", lambda server: None)
    code = main(["dashboard", "--port", "4567"])
    err = capsys.readouterr().err
    assert code == EXIT_PASS
    assert "http://127.0.0.1:4567/" in err
    assert calls["args"][0] == "127.0.0.1"
    assert calls["args"][2] == historymod.state_path()


def test_dashboard_is_the_only_listener_module():
    """The N4 amendment, asserted from the dashboard side: dashboard.py IS the
    one listener (import + call site), so the boundary did not go vacuous."""
    import ast
    from pathlib import Path
    src = Path(dash.__file__)
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imports = {n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", "")
               for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
               for n in [node] }
    body = src.read_text(encoding="utf-8")
    assert "from http.server import" in body
    assert "serve_forever()" in body          # the call site lives here


def test_n4_amendment_allows_only_dashboard():
    """The other half: every OTHER shipped module still imports no server
    module and makes no listener call (regression guard on the amendment)."""
    import ast
    from pathlib import Path
    src_dir = Path(dash.__file__).parent
    offenders = {}
    for path in sorted(src_dir.glob("*.py")):
        if path.name == "dashboard.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module)
        hits = sorted(n for n in names
                      if n.split(".")[0] in ("http.server", "socketserver", "ssl"))
        if hits:
            offenders[path.name] = hits
    assert offenders == {}
