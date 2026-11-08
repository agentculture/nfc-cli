"""Web server (t14): guard, /api generated from the registry, SSE, bind policy."""

from __future__ import annotations

import http.client
import json
import stat
import threading

import pytest

from nfc_cli.nfc.errors import NfcError
from nfc_cli.web import server as web
from nfc_cli.web.guard import load_secret


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("NFC_CLI_FAKE", raising=False)
    s = web.make_server(port=0, fake="x3")
    s.poll_interval = 0.05
    th = threading.Thread(target=s.serve_forever, daemon=True)
    th.start()
    yield s
    s.shutdown()
    s.server_close()


def req(s, method, path, body=None, headers=None, auth=True):
    port = s.server_address[1]
    h = {"Host": f"127.0.0.1:{port}"}
    if auth:
        h["Authorization"] = f"Bearer {s.secret}"
    h.update(headers or {})
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = json.dumps(body).encode() if body is not None else None
    if data:
        h["Content-Type"] = "application/json"
    c.request(method, path, data, h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, raw


def apdus(s):
    return [e for e in s.factory().log if e[0] == "apdu"]


def test_secret_file_is_0600_under_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    v = load_secret()
    p = tmp_path / "nfc-cli" / "web-secret"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert load_secret() == v


def test_state(srv):
    st, raw = req(srv, "GET", "/api/state", auth=False)
    d = json.loads(raw)
    assert st == 200 and d["card"]["present"] is True and d["card"]["uid"] == "cb30c560"
    assert d["reader"]["connected"] is True


def test_post_requires_secret(srv):
    before = len(apdus(srv))
    st, _ = req(
        srv, "POST", "/api/tag/write", {"block": 4, "data": "00" * 16, "apply": True}, auth=False
    )
    assert st == 401
    st, _ = req(
        srv,
        "POST",
        "/api/tag/write",
        {"block": 4, "data": "00" * 16, "apply": True},
        headers={"Authorization": "Bearer nope"},
        auth=False,
    )
    assert st == 401
    assert len(apdus(srv)) == before


def test_foreign_origin_and_host_refused(srv):
    before = len(apdus(srv))
    body = {"block": 4, "data": "00" * 16, "apply": True}
    st, _ = req(srv, "POST", "/api/tag/write", body, headers={"Origin": "http://evil.example"})
    assert st == 403
    st, _ = req(srv, "POST", "/api/tag/write", body, headers={"Host": "evil.example"})
    assert st == 403
    st, _ = req(srv, "GET", "/api/state", headers={"Host": "evil.example"}, auth=False)
    assert st == 403
    assert len(apdus(srv)) == before


def test_post_verb_ok_and_dry_run(srv):
    st, raw = req(srv, "POST", "/api/tag/uid", {})
    d = json.loads(raw)
    assert st == 200 and d["ok"] and d["result"]["uid"] == "cb30c560"
    st, raw = req(srv, "POST", "/api/tag/write", {"block": 4, "data": "00" * 16})
    assert st == 200 and json.loads(raw)["ok"]


def test_errors_mapped(srv):
    st, raw = req(srv, "POST", "/api/tag/uid", {"bogus": 1})
    e = json.loads(raw)
    assert st == 400 and e["error"]["kind"] == "user" and e["error"]["remediation"]
    assert req(srv, "POST", "/api/tag/nope", {})[0] == 404
    assert req(srv, "POST", "/api/tag/uid", None, headers={"Content-Length": "x"})[0] in (400, 200)
    srv.factory()._busy = True
    try:
        st, raw = req(srv, "POST", "/api/tag/uid", {})
        assert st == 409 and json.loads(raw)["error"]["kind"] == "env"
    finally:
        srv.factory()._busy = False


def test_trailer_gate_over_api(srv):
    before = len(apdus(srv))
    st, raw = req(srv, "POST", "/api/ndef/format", {"apply": True})
    assert st == 400 and "include_trailers" in json.loads(raw)["error"]["message"]
    assert len(apdus(srv)) == before


def test_audit_log_written_on_protected_write(srv, tmp_path):
    st, raw = req(srv, "POST", "/api/ndef/format", {"apply": True, "include_trailers": True})
    assert st == 200, raw
    log = tmp_path / "state" / "nfc-cli"
    assert any(p.is_file() and "api" in p.read_text() for p in log.rglob("*") if p.is_file())


def test_sse_card_event_and_releases_claim(srv):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/api/events", headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type") == "text/event-stream"
    line = b""
    while b"data:" not in line:
        line += r.fp.readline()
    assert b"event: card" in line and b"cb30c560" in line
    # claim released between polls: a verb call still succeeds while the stream is open
    assert req(srv, "POST", "/api/tag/uid", {})[0] == 200
    c.close()


def test_static_fallback_and_meta_injection(srv, tmp_path):
    srv.dist = None
    st, raw = req(srv, "GET", "/", auth=False)
    assert st == 200 and b"not built" in raw
    d = tmp_path / "dist"
    d.mkdir()
    (d / "index.html").write_text("<html><head></head><body>hi</body></html>")
    srv.dist = d
    st, raw = req(srv, "GET", "/", auth=False)
    assert f'content="{srv.secret}"'.encode() in raw
    st, raw = req(srv, "GET", "/../../etc/passwd", auth=False)
    assert b"root:" not in raw


def test_resolve_bind(monkeypatch):
    monkeypatch.delenv("NFC_CLI_HOST", raising=False)
    assert web.resolve_bind(None) == "127.0.0.1"
    assert web.resolve_bind("localhost") == "127.0.0.1"
    assert web.resolve_bind("::1") == "::1"
    with pytest.raises(web.BindRefused) as ei:
        web.resolve_bind("0.0.0.0")
    assert ei.value.remediation
    assert isinstance(ei.value, NfcError)
    assert web.resolve_bind("0.0.0.0", bind_public=True) == "0.0.0.0"
    monkeypatch.setenv("NFC_CLI_HOST", "192.168.1.5")
    with pytest.raises(web.BindRefused):
        web.resolve_bind(None)


def test_defaults(monkeypatch):
    monkeypatch.delenv("NFC_CLI_PORT", raising=False)
    assert web.DEFAULT_PORT == 8770
    assert web._resolve_port(None) == 8770
    monkeypatch.setenv("NFC_CLI_PORT", "9001")
    assert web._resolve_port(None) == 9001


def test_refused_bind_opens_no_socket(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("socket opened")

    monkeypatch.setattr(web, "NfcHTTPServer", boom)
    with pytest.raises(web.BindRefused):
        web.serve("0.0.0.0")


def test_public_bind_does_not_embed_secret(tmp_path, monkeypatch):
    """Review finding #3: a page served on a public bind must not carry the write secret,
    or any network client could read it and defeat the guard."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    s = web.make_server(host="0.0.0.0", port=0, bind_public=True, fake="x3")
    assert s.embed_secret is False
    d = tmp_path / "dist"
    d.mkdir()
    (d / "index.html").write_text("<html><head></head><body>hi</body></html>")
    s.dist = d
    th = threading.Thread(target=s.serve_forever, daemon=True)
    th.start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
        c.request("GET", "/", None, {"Host": f"127.0.0.1:{s.server_address[1]}"})
        raw = c.getresponse().read()
        c.close()
        assert s.secret.encode() not in raw
        assert b"nfc-cli-secret" not in raw
    finally:
        s.shutdown()
        s.server_close()


def test_static_path_traversal_blocked(srv, tmp_path):
    d = tmp_path / "dist2"
    d.mkdir()
    (d / "index.html").write_text("<html><head></head><body>spa</body></html>")
    (d / "app.js").write_text("ok")
    srv.dist = d
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    for p in (
        "/../secret.txt",
        "/..%2f..%2fsecret.txt",
        "/%2e%2e/secret.txt",
        "/foo/../../secret.txt",
    ):
        st, raw = req(srv, "GET", p, auth=False)
        assert b"TOPSECRET" not in raw  # never escapes dist
    st, raw = req(srv, "GET", "/app.js", auth=False)
    assert raw == b"ok"  # legitimate asset still served
