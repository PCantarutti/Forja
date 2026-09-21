"""Testes do forja-runner com um servidor real em porta livre:  python -m pytest tools"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import forja_runner as fr


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), fr.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    for name in list(fr._SERVERS):
        fr.serve_stop(name)
    srv.shutdown()


def call(url, method="GET", body=None, token=fr.TOKEN):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_ping_reports_system_and_shell(server):
    status, body = call(f"{server}/ping")
    assert status == 200 and body["ok"] and body["system"] == fr.SYSTEM and body["shell"] == fr.shell_name()
    assert "python" in body["versions"]


def test_wrong_token_is_refused(server):
    status, body = call(f"{server}/ping", token="errado")
    assert status == 401 and "token" in body["error"]


def test_run_returns_output_and_exit_code(server, tmp_path):
    status, body = call(f"{server}/run", "POST", {"command": "echo oi", "cwd": str(tmp_path), "timeout": 30})
    assert status == 200 and body["exit_code"] == 0 and "oi" in body["output"]
    status, body = call(f"{server}/run", "POST", {"command": "exit 3", "cwd": str(tmp_path)})
    assert body["exit_code"] == 3
    status, body = call(f"{server}/run", "POST", {"command": "echo x", "cwd": str(tmp_path / "nao-existe")})
    assert body["exit_code"] == 2 and "não encontrada" in body["output"]


def test_run_timeout_kills(server, tmp_path):
    cmd = 'python -c "import time; time.sleep(30)"'
    t0 = time.time()
    status, body = call(f"{server}/run", "POST", {"command": cmd, "cwd": str(tmp_path), "timeout": 1})
    assert body["exit_code"] == 124 and body.get("timeout") and time.time() - t0 < 15


def test_serve_start_log_and_stop(server, tmp_path):
    cmd = f'"{sys.executable}" -c "import time; print(\'pronto na porta 5555\', flush=True); time.sleep(60)"'
    status, body = call(f"{server}/serve", "POST", {"name": "meu app!", "command": cmd, "cwd": str(tmp_path)})
    assert status == 200 and body["name"] == "meu-app-" and body["alive"]
    time.sleep(1.5)
    status, body = call(f"{server}/serve/meu-app-/log?tail=5")
    assert "5555" in body["log"]
    status, body = call(f"{server}/serve")
    assert [s["name"] for s in body["servers"]] == ["meu-app-"]
    status, body = call(f"{server}/serve/meu-app-", "DELETE")
    assert status == 200
    status, body = call(f"{server}/serve/meu-app-", "DELETE")
    assert status == 404


def test_token_file_is_created_once(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "TOKEN_FILE", tmp_path / "config" / "runner-token")
    monkeypatch.delenv("FORJA_RUNNER_TOKEN", raising=False)
    a = fr.token()
    assert (tmp_path / "config" / "runner-token").read_text().strip() == a and len(a) > 30
    assert fr.token() == a
    assert os.path.isdir(tmp_path / "config")


def test_run_stream_emits_lines_and_exit(server, tmp_path):
    req = urllib.request.Request(f"{server}/run/stream", data=json.dumps(
        {"command": "echo um; echo dois; exit 5", "cwd": str(tmp_path), "timeout": 30}).encode(), method="POST",
        headers={"Authorization": f"Bearer {fr.TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        lines = r.read().decode("utf-8").splitlines()
    assert lines[:2] == ["um", "dois"] and lines[-1] == "__FORJA_EXIT__:5"


def test_terminal_session_roundtrip(server, tmp_path):
    status, body = call(f"{server}/term/start", "POST", {"cwd": str(tmp_path)})
    assert status == 200 and body["id"] and body["shell"] == fr.shell_name()
    tid = body["id"]
    status, _ = call(f"{server}/term/{tid}/input", "POST", {"text": "echo ola-terminal"})
    assert status == 200
    text, cursor = "", 0
    for _ in range(20):
        status, body = call(f"{server}/term/{tid}/poll?cursor={cursor}")
        text += body["text"]
        cursor = body["cursor"]
        if "ola-terminal" in text:
            break
    assert "ola-terminal" in text and body["alive"]
    status, _ = call(f"{server}/term/{tid}", "DELETE")
    assert status == 200
    status, body = call(f"{server}/term/{tid}/poll?cursor=0")
    assert status == 404


def test_terminal_survives_a_full_buffer(monkeypatch, tmp_path):
    """O cursor é posição absoluta, não índice no buffer.

    Enquanto era `len(buf)`, ele empacava em MAX_TERM_BUFFER assim que a saída passava do teto:
    `len(buf) <= cursor` virava sempre verdade e o terminal devolvia vazio para sempre. Um log de
    build matava o terminal, sem mensagem nenhuma.
    """
    monkeypatch.setattr(fr, "TERM_POLL_WAIT", 0.05)
    info = fr.term_start(str(tmp_path))
    tid = info["id"]
    try:
        t = fr._TERMS[tid]
        with t["cond"]:  # simula a saída já ter estourado o buffer
            t["written"] = fr.MAX_TERM_BUFFER + 5_000
            t["buf"] = "x" * fr.MAX_TERM_BUFFER
        cursor = fr.term_poll(tid, 0)["cursor"]
        assert cursor == fr.MAX_TERM_BUFFER + 5_000
        with t["cond"]:
            t["written"] += len("depois-do-estouro")
            t["buf"] = (t["buf"] + "depois-do-estouro")[-fr.MAX_TERM_BUFFER:]
        assert fr.term_poll(tid, cursor)["text"] == "depois-do-estouro"
    finally:
        fr.term_close(tid)


def test_open_rejects_missing_path(server):
    status, body = call(f"{server}/open", "POST", {"path": "C:/nao/existe/x.txt", "mode": "editor"})
    assert status == 400 and "não existe" in body["error"]
