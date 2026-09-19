"""Testes do forja-picker sem abrir diálogo de verdade:  python -m pytest tools"""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import forja_picker as fp


@pytest.fixture
def server(monkeypatch):
    chosen = {"value": "C:/Users/pedro/app"}
    monkeypatch.setattr(fp, "pick", lambda start: chosen["value"])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), fp.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", chosen
    srv.shutdown()


def get(url, origin):
    req = urllib.request.Request(url, headers={"Origin": origin} if origin else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), e.headers


def test_forja_origin_gets_path_and_cors(server):
    base, _ = server
    status, body, headers = get(f"{base}/pick", "http://localhost:3000")
    assert status == 200 and body["path"] == "C:/Users/pedro/app" and not body["cancelled"]
    assert headers["Access-Control-Allow-Origin"] == "http://localhost:3000"


@pytest.mark.parametrize("origin", ["https://site-malicioso.com", "http://localhost:9999", None])
def test_other_origins_are_refused(server, origin):
    base, _ = server
    status, body, headers = get(f"{base}/pick", origin)
    assert status == 403 and "Access-Control-Allow-Origin" not in headers


def test_cancel(server):
    base, chosen = server
    chosen["value"] = None
    status, body, _ = get(f"{base}/pick", "http://127.0.0.1:3000")
    assert status == 200 and body["cancelled"] and body["path"] is None


def test_ping(server):
    base, _ = server
    assert get(f"{base}/ping", "http://localhost:3000")[0] == 200
