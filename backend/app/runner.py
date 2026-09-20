"""Cliente do forja-runner: executa comandos e servidores no sistema do usuário (fora do Docker).

O runner (tools/forja_runner.py) roda no Windows/Linux/macOS do usuário e o backend fala com ele por
`FORJA_RUNNER_URL` (padrão host.docker.internal:3002) com o token de `config/runner-token`, que a
pasta config/ montada no container compartilha. `INFO` guarda o /ping da execução atual: o system
prompt descreve o ambiente a partir dele, e `run_command`/`serve_*` escolhem onde executar.

Tudo síncrono de propósito: as ferramentas de shell rodam em thread (tools.execute → to_thread).
"""
from __future__ import annotations

import contextvars
import time
from pathlib import Path

import httpx

from . import config

TTL = 30.0  # segundos que um /ping vale; o runner pode ligar/desligar a qualquer momento
_cache: dict = {"at": 0.0, "info": None}
INFO: contextvars.ContextVar[dict | None] = contextvars.ContextVar("forja_runner_info", default=None)


class RunnerError(Exception):
    pass


def token() -> str:
    if config.RUNNER_TOKEN:
        return config.RUNNER_TOKEN
    try:
        return Path(config.RUNNER_TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _headers() -> dict:
    return {"Authorization": f"Bearer {token()}"}


def _url(path: str) -> str:
    return config.RUNNER_URL.rstrip("/") + path


def refresh(force: bool = False) -> dict | None:
    """/ping do runner (cache de TTL). None = desligado, sem token ou URL vazia."""
    now = time.monotonic()
    if not force and now - _cache["at"] < TTL:
        return _cache["info"]
    info = None
    if config.RUNNER_URL and token():
        try:
            r = httpx.get(_url("/ping"), headers=_headers(), timeout=3)
            if r.status_code == 200:
                info = r.json()
            elif r.status_code == 401:
                info = {"ok": False, "error": "token do runner não confere (config/runner-token)"}
        except (httpx.HTTPError, ValueError):
            info = None
    elif config.RUNNER_URL and not token():
        info = {"ok": False, "error": "sem token: inicie o forja-runner uma vez para gerar config/runner-token"}
    _cache.update(at=now, info=info)
    return info


def current() -> dict | None:
    """Runner visto no início da execução atual (agent.run_agent define); fora dela, o cache."""
    info = INFO.get()
    return info if info is not None else _cache["info"]


def online() -> bool:
    info = current()
    return bool(info and info.get("ok"))


def _call(method: str, path: str, timeout: float, **kw) -> dict:
    try:
        r = httpx.request(method, _url(path), headers=_headers(), timeout=timeout, **kw)
    except httpx.HTTPError as e:
        _cache.update(at=0.0)  # força novo /ping na próxima
        raise RunnerError(f"forja-runner inacessível ({e.__class__.__name__}). Ele está rodando no seu sistema?") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("error", r.text)
        except ValueError:
            detail = r.text
        raise RunnerError(f"forja-runner respondeu {r.status_code}: {str(detail)[:300]}")
    return r.json()


def run(command: str, cwd: str, timeout: int) -> dict:
    return _call("POST", "/run", timeout + 15, json={"command": command, "cwd": cwd, "timeout": timeout})


def run_stream(command: str, cwd: str, timeout: int, sink) -> dict:
    """Como run(), mas entrega cada linha a `sink` enquanto o comando roda (saída ao vivo na UI)."""
    out: list[str] = []
    code: int | None = None
    try:
        with httpx.stream("POST", _url("/run/stream"), headers=_headers(), timeout=timeout + 15,
                          json={"command": command, "cwd": cwd, "timeout": timeout}) as r:
            if r.status_code >= 400:
                raise RunnerError(f"forja-runner respondeu {r.status_code}: {r.read().decode('utf-8', 'replace')[:300]}")
            for line in r.iter_lines():
                if line.startswith("__FORJA_EXIT__:"):
                    code = int(line.split(":", 1)[1].strip() or -1)
                    continue
                out.append(line + "\n")
                sink(line + "\n")
    except httpx.HTTPError as e:
        _cache.update(at=0.0)
        raise RunnerError(f"forja-runner inacessível ({e.__class__.__name__}). Ele está rodando no seu sistema?") from e
    return {"exit_code": code if code is not None else -1, "output": "".join(out),
            "timeout": code == 124}


def picker_start() -> dict:
    """Pede ao runner para subir o forja-picker (seletor nativo de pasta) se ele estiver desligado."""
    return _call("POST", "/picker/start", 15, json={})


def open_path(path: str, mode: str) -> dict:
    """Abre no editor (code) ou revela no gerenciador de arquivos do sistema do usuário."""
    return _call("POST", "/open", 20, json={"path": path, "mode": mode})


# terminal do usuário (sem PTY): shell lendo do stdin, saída por polling
def term_start(cwd: str) -> dict:
    return _call("POST", "/term/start", 20, json={"cwd": cwd})


def term_input(tid: str, text: str) -> dict:
    return _call("POST", f"/term/{tid}/input", 20, json={"text": text})


def term_poll(tid: str, cursor: int) -> dict:
    return _call("GET", f"/term/{tid}/poll?cursor={int(cursor)}", 30)


def term_close(tid: str) -> dict:
    return _call("DELETE", f"/term/{tid}", 20)


def serve_start(name: str, command: str, cwd: str) -> dict:
    return _call("POST", "/serve", 30, json={"name": name, "command": command, "cwd": cwd})


def servers() -> list[dict]:
    return _call("GET", "/serve", 10).get("servers", [])


def serve_log(name: str, tail: int) -> str:
    return _call("GET", f"/serve/{name}/log?tail={int(tail)}", 10).get("log", "")


def serve_stop(name: str) -> dict:
    return _call("DELETE", f"/serve/{name}", 30)


def describe(info: dict | None) -> str:
    """Uma linha para o painel e o prompt: 'Windows 11 (powershell)' ou 'desligado'."""
    if not info:
        return "desligado"
    if not info.get("ok"):
        return f"erro: {info.get('error')}"
    system = {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(info.get("system"), info.get("system"))
    release = info.get("release") or ""
    return " ".join(x for x in (system, release) if x) + f" ({info.get('shell')})"
