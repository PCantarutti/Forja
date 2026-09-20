"""forja-runner: executa comandos e servidores de desenvolvimento NO SEU SISTEMA (fora do Docker).

O Forja roda no Docker; sem este ajudante, o `run_command` do agente roda bash dentro do container
Linux, e `npm install`/venv gravam binários Linux na sua pasta do Windows. Com o runner ligado, o
backend manda os comandos para cá: PowerShell no Windows, bash no Linux/macOS, na pasta da conversa.

    Windows:  pythonw tools\\forja_runner.py   (o forja-picker.cmd já inicia picker e runner)
    Linux:    python3 tools/forja_runner.py &
    macOS:    python3 tools/forja_runner.py &

Segurança: toda chamada exige `Authorization: Bearer <token>`. O token é gerado na primeira
execução em `config/runner-token` (a pasta config/ do repositório é montada no container, então o
backend lê o mesmo arquivo; nada para copiar). O runner escuta em 0.0.0.0 por padrão porque o
container só alcança o host por `host.docker.internal`, não por 127.0.0.1. Restrinja pelo firewall
(perfil privado) ou defina FORJA_RUNNER_BIND para o IP do adaptador do WSL.

Rotas (JSON):
    GET  /ping                    sistema, shell, versões de node/python/git, pasta home
    POST /run    {command, cwd, timeout}     executa e devolve {exit_code, output}
    POST /serve  {name, command, cwd}        inicia servidor em segundo plano (log em arquivo)
    GET  /serve                   lista servidores {name, pid, alive, command, cwd, log}
    GET  /serve/<name>/log?tail=N últimas linhas do log
    DELETE /serve/<name>          mata o servidor (árvore inteira)

Variáveis: FORJA_RUNNER_PORT (3002), FORJA_RUNNER_BIND (0.0.0.0), FORJA_RUNNER_TOKEN (senão o arquivo).
"""
from __future__ import annotations

import json
import os
import platform
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = int(os.getenv("FORJA_RUNNER_PORT", "3002"))
BIND = os.getenv("FORJA_RUNNER_BIND", "0.0.0.0")
TOKEN_FILE = Path(__file__).resolve().parent.parent / "config" / "runner-token"
SYSTEM = platform.system()  # Windows | Linux | Darwin
WINDOWS = SYSTEM == "Windows"
MAX_OUTPUT = 20_000
MAX_TIMEOUT = 3_600
LOG_DIR = Path(tempfile.gettempdir()) / "forja-serve"
PS_PREAMBLE = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; "
               "$ErrorActionPreference='Continue'; ")


def token() -> str:
    env = os.getenv("FORJA_RUNNER_TOKEN", "").strip()
    if env:
        return env
    if TOKEN_FILE.is_file():
        saved = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if saved:
            return saved
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    new = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(new + "\n", encoding="utf-8")
    return new


TOKEN = token()


def shell_name() -> str:
    if WINDOWS:
        return "pwsh" if shutil.which("pwsh") else "powershell"
    return "bash"


def shell_argv(command: str) -> list[str]:
    if WINDOWS:
        # Exit code: comandos nativos deixam $LASTEXITCODE; cmdlets que falham deixam $? falso.
        script = PS_PREAMBLE + command + "\nif ($LASTEXITCODE) { exit $LASTEXITCODE } elseif (-not $?) { exit 1 }"
        return [shell_name(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
    return ["bash", "-lc", command]


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n\n... ({len(text) - MAX_OUTPUT} caracteres omitidos) ...\n\n{text[-half:]}"


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _popen_kwargs() -> dict:
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def _version(cmd: str) -> str | None:
    """Pelo shell: no Windows, npm/npx são .cmd e não resolvem sem ele."""
    try:
        r = subprocess.run(shell_argv(cmd), capture_output=True, timeout=15, **_popen_kwargs())
        line = (r.stdout or r.stderr).decode("utf-8", "replace").strip().splitlines()
        return line[0][:60] if r.returncode == 0 and line else None
    except Exception:
        return None


_INFO: dict | None = None


def info() -> dict:
    global _INFO
    if _INFO is None:
        _INFO = {
            "ok": True, "system": SYSTEM, "release": platform.release(), "version": platform.version(),
            "machine": platform.machine(), "shell": shell_name(), "home": str(Path.home()),
            "versions": {k: _version(v) for k, v in {
                "node": "node --version", "npm": "npm --version", "python": "python --version",
                "git": "git --version"}.items()},
        }
    return _INFO


def run(command: str, cwd: str, timeout: int) -> dict:
    if not command.strip():
        return {"exit_code": 2, "output": "command vazio."}
    if not os.path.isdir(cwd or ""):
        return {"exit_code": 2, "output": f"Pasta não encontrada neste sistema: {cwd}"}
    timeout = max(1, min(int(timeout or 60), MAX_TIMEOUT))
    p = subprocess.Popen(shell_argv(command), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, **_popen_kwargs())
    try:
        raw, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        raw, _ = p.communicate()
        return {"exit_code": 124, "timeout": True,
                "output": f"Timeout: o comando passou de {timeout}s e foi encerrado.\nSaída parcial:\n{_truncate(_text(raw))}"}
    return {"exit_code": p.returncode, "output": _truncate(_text(raw))}


def _text(raw: bytes) -> str:
    """Saída do processo como texto, com quebras de linha do Windows normalizadas."""
    return raw.decode("utf-8", "replace").replace("\r\n", "\n")


# ------------------------------------------------------------------ servidores em segundo plano

_SERVERS: dict[str, dict] = {}
_servers_lock = threading.Lock()


def serve_start(name: str, command: str, cwd: str) -> dict:
    name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (name or "").strip())[:40] or "server"
    if not command.strip():
        raise ValueError("command vazio.")
    if not os.path.isdir(cwd or ""):
        raise ValueError(f"Pasta não encontrada neste sistema: {cwd}")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with _servers_lock:
        old = _SERVERS.get(name)
        if old and old["proc"].poll() is None:
            kill_tree(old["proc"])  # mesmo nome = reinicia
        log = LOG_DIR / f"{name}.log"
        fh = open(log, "wb")
        proc = subprocess.Popen(shell_argv(command), cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **_popen_kwargs())
        _SERVERS[name] = {"proc": proc, "log": str(log), "command": command, "cwd": cwd, "started": time.time()}
    return serve_info(name)


def serve_info(name: str) -> dict:
    s = _SERVERS[name]
    code = s["proc"].poll()
    return {"name": name, "pid": s["proc"].pid, "alive": code is None, "exit_code": code, "command": s["command"],
            "cwd": s["cwd"], "log": s["log"], "uptime": int(time.time() - s["started"])}


def serve_log(name: str, tail: int) -> str:
    s = _SERVERS.get(name)
    if not s:
        raise KeyError(name)
    try:
        data = Path(s["log"]).read_bytes()
    except OSError:
        return ""
    lines = data.decode("utf-8", "replace").splitlines()
    return "\n".join(lines[-max(1, min(int(tail or 50), 500)):])


def serve_stop(name: str) -> dict:
    with _servers_lock:
        s = _SERVERS.get(name)
        if not s:
            raise KeyError(name)
        kill_tree(s["proc"])
        out = serve_info(name)
        del _SERVERS[name]
    return out


# ------------------------------------------------------------------ abrir no editor / revelar

def open_path(path: str, mode: str) -> str:
    if not os.path.exists(path):
        raise ValueError(f"Caminho não existe neste sistema: {path}")
    if mode == "reveal":
        if WINDOWS:
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
        elif SYSTEM == "Darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
        return "revelado"
    # editor: VS Code se existir, senão o programa padrão do sistema
    if shutil.which("code") or (WINDOWS and shutil.which("code.cmd")):
        subprocess.Popen(shell_argv(f'code "{path}"'), **_popen_kwargs())
        return "code"
    if WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]
        return "padrão"
    subprocess.Popen(["open" if SYSTEM == "Darwin" else "xdg-open", path])
    return "padrão"


# ------------------------------------------------------------------ forja-picker (seletor de pasta)

PICKER_PORT = int(os.getenv("FORJA_PICKER_PORT", "3001"))
PICKER_FILE = Path(__file__).resolve().parent / "forja_picker.py"


def picker_online() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", PICKER_PORT), timeout=0.5):
            return True
    except OSError:
        return False


def ensure_picker() -> dict:
    """Sobe o forja-picker (diálogo nativo de pasta) se ele não estiver escutando. Chamado no início e por /picker/start."""
    if picker_online():
        return {"online": True, "started": False}
    if not PICKER_FILE.is_file():
        return {"online": False, "started": False, "error": f"não achei {PICKER_FILE}"}
    exe = shutil.which("pythonw") if WINDOWS else None
    subprocess.Popen([exe or sys.executable, str(PICKER_FILE)], cwd=str(PICKER_FILE.parent), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_popen_kwargs())
    for _ in range(20):  # até ~4 s para ele abrir a porta
        time.sleep(0.2)
        if picker_online():
            return {"online": True, "started": True}
    return {"online": False, "started": True, "error": "iniciei o forja-picker, mas ele ainda não respondeu"}


# ------------------------------------------------------------------ terminal do usuário (sem PTY)

_TERMS: dict[str, dict] = {}
MAX_TERM_BUFFER = 400_000
TERM_POLL_WAIT = 20.0


def term_start(cwd: str) -> dict:
    if not os.path.isdir(cwd or ""):
        raise ValueError(f"Pasta não encontrada neste sistema: {cwd}")
    if WINDOWS:
        argv = [shell_name(), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"]
    else:
        argv = ["bash", "-l"]
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            **_popen_kwargs())
    tid = secrets.token_hex(6)
    t = {"proc": proc, "buf": "", "cond": threading.Condition(), "cwd": cwd}
    _TERMS[tid] = t
    if WINDOWS:  # saída em UTF-8 e sem barra de progresso quebrando o texto
        proc.stdin.write((PS_PREAMBLE + "$ProgressPreference='SilentlyContinue'\n").encode("utf-8"))
        proc.stdin.flush()

    def reader():
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(1)
            if not chunk:
                break
            with t["cond"]:
                t["buf"] = (t["buf"] + chunk.decode("utf-8", "replace").replace("\r\n", "\n"))[-MAX_TERM_BUFFER:]
                t["cond"].notify_all()
        with t["cond"]:
            t["cond"].notify_all()

    threading.Thread(target=reader, daemon=True).start()
    return {"id": tid, "shell": shell_name(), "cwd": cwd}


def term_input(tid: str, text: str) -> None:
    t = _TERMS.get(tid)
    if not t:
        raise KeyError(tid)
    if t["proc"].poll() is not None:
        raise ValueError("o shell deste terminal já encerrou")
    t["proc"].stdin.write((text.rstrip("\n") + "\n").encode("utf-8"))
    t["proc"].stdin.flush()


def term_poll(tid: str, cursor: int) -> dict:
    t = _TERMS.get(tid)
    if not t:
        raise KeyError(tid)
    deadline = time.monotonic() + TERM_POLL_WAIT
    with t["cond"]:
        while len(t["buf"]) <= cursor and t["proc"].poll() is None:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            t["cond"].wait(left)
        text = t["buf"][cursor:] if cursor < len(t["buf"]) else ""
        return {"text": text, "cursor": len(t["buf"]), "alive": t["proc"].poll() is None}


def term_close(tid: str) -> None:
    t = _TERMS.pop(tid, None)
    if t:
        kill_tree(t["proc"])


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    def _stream_run(self, body: dict) -> None:
        """POST /run/stream: saída linha a linha enquanto roda; a última linha é __FORJA_EXIT__:<código>."""
        command, cwd = str(body.get("command", "")), str(body.get("cwd", ""))
        timeout = max(1, min(int(body.get("timeout") or 60), MAX_TIMEOUT))
        if not command.strip() or not os.path.isdir(cwd or ""):
            return self._send(400, {"error": "command vazio ou pasta não encontrada neste sistema"})
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        p = subprocess.Popen(shell_argv(command), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, **_popen_kwargs())
        timed_out = threading.Event()

        def _kill():
            timed_out.set()
            kill_tree(p)

        timer = threading.Timer(timeout, _kill)
        timer.start()
        try:
            for raw in p.stdout:
                try:
                    self.wfile.write(raw.replace(b"\r\n", b"\n"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionError):
                    kill_tree(p)  # o backend desistiu: mata o comando
                    break
            p.wait()
        finally:
            timer.cancel()
        code = 124 if timed_out.is_set() else p.returncode
        try:
            self.wfile.write(f"__FORJA_EXIT__:{code}\n".encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionError):
            pass

    def _send(self, code: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self) -> bool:
        return secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {TOKEN}")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        if not self._auth():
            return self._send(401, {"error": "token inválido"})
        url = urlparse(self.path)
        parts = url.path.strip("/").split("/")
        if url.path == "/ping":
            return self._send(200, info())
        if url.path == "/serve":
            return self._send(200, {"servers": [serve_info(n) for n in list(_SERVERS)]})
        if len(parts) == 3 and parts[0] == "serve" and parts[2] == "log":
            tail = (parse_qs(url.query).get("tail") or ["50"])[0]
            try:
                return self._send(200, {"name": parts[1], "log": serve_log(parts[1], int(tail))})
            except KeyError:
                return self._send(404, {"error": f"servidor '{parts[1]}' não existe"})
        if len(parts) == 3 and parts[0] == "term" and parts[2] == "poll":
            cursor = int((parse_qs(url.query).get("cursor") or ["0"])[0] or 0)
            try:
                return self._send(200, term_poll(parts[1], cursor))
            except KeyError:
                return self._send(404, {"error": "terminal não existe"})
        self._send(404, {"error": "rota desconhecida"})

    def do_POST(self):
        if not self._auth():
            return self._send(401, {"error": "token inválido"})
        try:
            body = self._body()
        except ValueError:
            return self._send(400, {"error": "JSON inválido"})
        if self.path == "/run":
            return self._send(200, run(str(body.get("command", "")), str(body.get("cwd", "")), body.get("timeout") or 60))
        if self.path == "/run/stream":
            return self._stream_run(body)
        if self.path == "/open":
            try:
                return self._send(200, {"ok": True, "opened_with": open_path(str(body.get("path", "")), str(body.get("mode", "editor")))})
            except (ValueError, OSError) as e:
                return self._send(400, {"error": str(e)})
        if self.path == "/picker/start":
            return self._send(200, ensure_picker())
        if self.path == "/term/start":
            try:
                return self._send(200, term_start(str(body.get("cwd", ""))))
            except (ValueError, OSError) as e:
                return self._send(400, {"error": str(e)})
        parts = self.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "term" and parts[2] == "input":
            try:
                term_input(parts[1], str(body.get("text", "")))
                return self._send(200, {"ok": True})
            except KeyError:
                return self._send(404, {"error": "terminal não existe"})
            except (ValueError, OSError) as e:
                return self._send(400, {"error": str(e)})
        if self.path == "/serve":
            try:
                return self._send(200, serve_start(str(body.get("name", "")), str(body.get("command", "")),
                                                   str(body.get("cwd", ""))))
            except (ValueError, OSError) as e:
                return self._send(400, {"error": str(e)})
        self._send(404, {"error": "rota desconhecida"})

    def do_DELETE(self):
        if not self._auth():
            return self._send(401, {"error": "token inválido"})
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "serve":
            try:
                return self._send(200, serve_stop(parts[1]))
            except KeyError:
                return self._send(404, {"error": f"servidor '{parts[1]}' não existe"})
        if len(parts) == 2 and parts[0] == "term":
            term_close(parts[1])
            return self._send(200, {"ok": True})
        self._send(404, {"error": "rota desconhecida"})

    def log_message(self, *args):  # silencioso (roda com pythonw, sem console)
        pass


class Server(ThreadingHTTPServer):
    # No Windows, SO_REUSEADDR deixa dois processos escutarem a mesma porta e as conexões caem no
    # runner antigo. Melhor falhar na hora ("porta em uso") do que servir código velho em silêncio.
    allow_reuse_address = False


def main() -> None:
    server = Server((BIND, PORT), Handler)
    print(f"forja-runner ({SYSTEM}, {shell_name()}) ouvindo em http://{BIND}:{PORT}; token em {TOKEN_FILE}")
    threading.Thread(target=ensure_picker, daemon=True).start()  # o picker sobe junto, se não estiver rodando
    try:
        server.serve_forever()
    finally:
        for name in list(_SERVERS):
            kill_tree(_SERVERS[name]["proc"])
        for tid in list(_TERMS):
            term_close(tid)


if __name__ == "__main__":
    main()
