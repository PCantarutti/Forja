"""Ferramentas de shell: run_command e servidores em segundo plano (serve_start/serve_status/serve_stop).

Onde executam (`target`): com o forja-runner ligado no sistema do usuário e a pasta da conversa
mapeada para um caminho de lá, o padrão é o **host** (PowerShell no Windows, bash no Linux/macOS).
Senão, bash dentro do container do backend. `target='container'` força o container (python, git,
node do Forja); `target='host'` exige o runner.

Como no Claude Desktop, a proteção é a aprovação (always_ask), não um sandbox: o shell enxerga os
discos montados (container) ou o sistema inteiro (host).
"""
from __future__ import annotations

import contextvars
import os
import shlex
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from . import config, runner, workspace
from .tools import Tool, ToolError, register, resolve_path

MAX_OUTPUT = 20_000
LOG_DIR = Path(tempfile.gettempdir()) / "forja-serve"
_LOCAL: dict[str, dict] = {}  # servidores iniciados no container: nome -> {proc, log, fh, command, cwd, started}
_local_lock = threading.Lock()  # stop/clear mexem no dict enquanto list_servers lê, e ambos rodam em thread
# Saída ao vivo: o agente define um sink por chamada e cada linha do comando vira evento na UI.
OUTPUT_SINK: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar("forja_output_sink", default=None)
# Conversa do turno atual: fica gravada no processo para a aba Instâncias separar por conversa.
CONV: contextvars.ContextVar[str] = contextvars.ContextVar("forja_conv", default="")


def exec_in(root: Path, command: str, timeout: int = 60) -> tuple[int, str]:
    """Roda `command` na pasta (host se possível, senão container) e devolve (exit code, saída). Sem ToolError."""
    host = workspace.to_host(root)
    if pick_target(None, runner.online(), host) == "host":
        try:
            r = runner.run(command, host, timeout)
        except runner.RunnerError as e:
            return -1, str(e)
        return int(r.get("exit_code", -1)), r.get("output") or ""
    try:
        p = subprocess.run(["bash", "-lc", command], cwd=root, capture_output=True, text=True, errors="replace",
                           timeout=timeout, start_new_session=True)
    except subprocess.TimeoutExpired:
        return 124, f"Timeout ({timeout}s)"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def quote(value: str, target: str = "container") -> str:
    """Um valor como argumento literal do shell, sem interpolação e sem virar outro comando.

    A aspa simples não interpola nem no PowerShell nem no bash; o que muda entre eles é só como se
    escapa a própria aspa simples (o PowerShell dobra, o POSIX fecha e reabre). Por isso o alvo
    importa: aqui o mesmo comando pode ir para o PowerShell do usuário, pelo runner, ou para o bash
    do container.
    """
    text = str(value)
    if target == "host" and (runner.current() or {}).get("shell") in ("powershell", "pwsh"):
        return "'" + text.replace("'", "''") + "'"
    return shlex.quote(text)


def quoter(root: Path):
    """A função de citação certa para os comandos que vão rodar nesta pasta (ver `quote`)."""
    alvo = pick_target(None, runner.online(), workspace.to_host(root))
    return lambda valor: quote(valor, alvo)


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return f"{text[:half]}\n\n... ({len(text) - MAX_OUTPUT} caracteres omitidos) ...\n\n{text[-half:]}"


def pick_target(requested: str | None, runner_online: bool, host_path: str | None) -> str:
    """auto: host se o runner está ligado e a pasta existe no sistema do usuário; senão container."""
    requested = (requested or "auto").strip().lower()
    if requested == "container":
        return "container"
    if requested == "host":
        if not runner_online:
            raise ToolError("forja-runner desligado: não dá para executar no sistema do usuário. Peça para ele "
                            "iniciar tools/forja-picker.cmd (Windows) ou tools/forja_runner.py, ou use target='container'.")
        if not host_path:
            raise ToolError("Esta pasta não tem caminho no sistema do usuário; use target='container'.")
        return "host"
    if requested != "auto":
        raise ToolError("target deve ser auto, host ou container.")
    return "host" if runner_online and host_path else "container"


def _where(target: str) -> str:
    return runner.describe(runner.current()) if target == "host" else "container Linux do Forja"


def _host_cwd(cwd: Path) -> str:
    host = workspace.to_host(cwd)
    if not host:
        raise ToolError("Esta pasta não tem caminho no sistema do usuário; use target='container'.")
    return host


# ------------------------------------------------------------------ run_command

def background(root: Path, args: dict) -> str:
    """Comando demorado (build, suíte de teste) vira processo em segundo plano, o mesmo mecanismo dos
    servidores: o turno não fica preso e o modelo acompanha com serve_status."""
    nome = _safe_name(str(args.get("name") or "").strip() or args["command"].split()[0])
    return serve_start(root, {**args, "name": nome}, kind="Processo")


def run_command(root: Path, args: dict) -> str:
    command = args["command"].strip()
    if not command:
        raise ToolError("command vazio.")
    if args.get("background"):
        return background(root, args)
    cwd = resolve_path(root, args.get("cwd"))
    timeout = max(1, min(int(args.get("timeout") or 60), config.SHELL_TIMEOUT_MAX))
    target = pick_target(args.get("target"), runner.online(), workspace.to_host(cwd))
    sink = OUTPUT_SINK.get()
    if target == "host":
        try:
            r = (runner.run_stream(command, _host_cwd(cwd), timeout, sink) if sink
                 else runner.run(command, _host_cwd(cwd), timeout))
        except runner.RunnerError as e:
            raise ToolError(str(e)) from e
        body = f"[{_where('host')}] exit code: {r.get('exit_code')}\n{_truncate(r.get('output') or '') or '(sem saída)'}"
        if r.get("exit_code") != 0:
            raise ToolError(body)
        return body
    # Sessão própria para matar o grupo inteiro (bash + filhos) no timeout. Saída lida linha a linha
    # para a UI mostrar ao vivo.
    p = subprocess.Popen(["bash", "-lc", command], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, text=True, errors="replace", start_new_session=True)
    timed_out = threading.Event()

    def _kill():
        timed_out.set()
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    timer = threading.Timer(timeout, _kill)
    timer.start()
    chunks: list[str] = []
    try:
        assert p.stdout
        for line in p.stdout:
            chunks.append(line)
            if sink:
                sink(line)
        p.wait()
    finally:
        timer.cancel()
    out = "".join(chunks)
    if timed_out.is_set():
        raise ToolError(f"Timeout: o comando passou de {timeout}s e foi encerrado.\nSaída parcial:\n{_truncate(out)}")
    body = f"exit code: {p.returncode}\n{_truncate(out) or '(sem saída)'}"
    if p.returncode != 0:
        raise ToolError(body)
    return body


def command_preview(root: Path, args: dict) -> dict:
    cwd = resolve_path(root, args.get("cwd"))
    host = workspace.to_host(cwd)
    try:
        target = pick_target(args.get("target"), runner.online(), host)
    except ToolError:
        target = "container"
    where = f"{host}  ·  no seu sistema" if target == "host" else (host or str(cwd))
    return {"kind": "command", "path": where, "text": args.get("command", "")}


# ------------------------------------------------------------------ servidores em segundo plano

def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (name or "").strip())[:40] or "server"


def _local_start(name: str, command: str, cwd: Path) -> dict:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with _local_lock:
        if name in _LOCAL:
            _drop_local(_LOCAL.pop(name))  # mesmo nome = reinicia
        log = LOG_DIR / f"{name}.log"
        fh = open(log, "wb")  # fechado em _drop_local: sem guardar o handle, vazava um descritor por servidor
        proc = subprocess.Popen(["bash", "-lc", command], cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        _LOCAL[name] = {"proc": proc, "log": str(log), "fh": fh, "command": command, "cwd": str(cwd),
                        "started": time.time(), "conv": CONV.get()}
    return _local_info(name)


def _drop_local(s: dict) -> None:
    """Encerra o processo do container e fecha o arquivo de log dele."""
    if s["proc"].poll() is None:
        try:
            os.killpg(s["proc"].pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        s["fh"].close()
    except (OSError, KeyError):
        pass


def close_local() -> None:
    """Encerra o que o agente subiu DENTRO do container. O que roda no host é do runner, e ele vive
    além do backend: reiniciar o backend não pode derrubar o servidor de desenvolvimento do usuário."""
    with _local_lock:
        restantes = list(_LOCAL.values())
        _LOCAL.clear()
    for s in restantes:
        _drop_local(s)


def _local_info(name: str) -> dict:
    s = _LOCAL[name]
    code = s["proc"].poll()
    return {"name": name, "pid": s["proc"].pid, "alive": code is None, "exit_code": code, "command": s["command"],
            "cwd": s["cwd"], "log": s["log"], "uptime": int(time.time() - s["started"]), "where": "container",
            "conv": s.get("conv") or ""}


def _local_log(name: str, tail: int) -> str:
    try:
        lines = Path(_LOCAL[name]["log"]).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max(1, min(int(tail or 40), 500)):])


def _url_hint(target: str) -> str:
    if target == "host":
        return ("Servidor roda no sistema do usuário: no navegador integrado use http://host.docker.internal:PORTA; "
                "o usuário abre http://localhost:PORTA.")
    return "Servidor roda no container: no navegador integrado use http://localhost:PORTA."


def serve_start(root: Path, args: dict, kind: str = "Servidor") -> str:
    name = _safe_name(str(args.get("name") or "server"))
    command = str(args["command"]).strip()
    if not command:
        raise ToolError("command vazio.")
    cwd = resolve_path(root, args.get("cwd"))
    target = pick_target(args.get("target"), runner.online(), workspace.to_host(cwd))
    try:
        if target == "host":
            info = runner.serve_start(name, command, _host_cwd(cwd))
            time.sleep(2.5)  # dá tempo de o servidor imprimir a porta
            log = runner.serve_log(name, 30)
            alive = any(s["name"] == name and s["alive"] for s in runner.servers())
        else:
            info = _local_start(name, command, cwd)
            time.sleep(2.5)
            log = _local_log(name, 30)
            alive = _local_info(name)["alive"]
    except runner.RunnerError as e:
        raise ToolError(str(e)) from e
    status = "rodando" if alive else ("JÁ ENCERROU (veja o log: provável erro)" if kind == "Servidor"
                                      else "JÁ TERMINOU (o log abaixo é o resultado)")
    dica = f"{_url_hint(target)}\n" if kind == "Servidor" else ""
    return (f"{kind} '{name}' iniciado em {_where(target)} (pid {info.get('pid')}), {status}.\n{dica}"
            f"Use serve_status(name='{name}') para acompanhar e serve_stop para encerrar.\n--- log ---\n{log or '(vazio ainda)'}")


def list_servers() -> list[dict]:
    """Servidores vivos ou recém-encerrados, no sistema do usuário (runner) e no container."""
    entries: list[dict] = []
    if runner.online():
        try:
            entries += [{**s, "where": "host"} for s in runner.servers()]
        except runner.RunnerError as e:
            entries.append({"name": "(runner)", "alive": False, "error": str(e), "where": "host", "command": ""})
    with _local_lock:  # stop_server e clear_finished mexem no dict; ler fora dava KeyError
        entries += [_local_info(n) for n in list(_LOCAL)]
    return entries


def server_log(name: str, tail: int = 40) -> str:
    name = _safe_name(name)
    if name in _LOCAL:
        return _local_log(name, tail)
    if runner.online():
        return runner.serve_log(name, tail)
    raise ToolError(f"Servidor '{name}' não existe.")


def stop_server(name: str) -> str:
    """Encerra pelo nome, onde estiver. Devolve onde estava."""
    name = _safe_name(name)
    with _local_lock:
        s = _LOCAL.pop(name, None)
    if s:
        _drop_local(s)
        return "container"
    if runner.online():
        runner.serve_stop(name)
        return "host"
    raise ToolError(f"Servidor '{name}' não existe. Veja serve_status.")


WAIT_MAX = 120  # teto da espera do serve_status, para o turno nunca ficar preso


def _wait_end(name: str, seconds: int) -> None:
    """Segura a chamada até o processo sair ou o tempo acabar.

    Sem isto, acompanhar um processo em background custa uma inferência por consulta — o modelo
    pergunta, comenta, pergunta de novo. Com a espera, é um serve_status só.
    """
    limite = time.monotonic() + max(1, min(seconds, WAIT_MAX))
    while time.monotonic() < limite:
        vivo = next((s.get("alive") for s in list_servers() if s.get("name") == name), None)
        if not vivo:
            return
        time.sleep(1)


def clear_finished() -> int:
    """Tira da lista os processos que já terminaram. Só a lista: nada é encerrado aqui."""
    with _local_lock:
        mortos = [n for n, s in list(_LOCAL.items()) if s["proc"].poll() is not None]
        for n in mortos:
            s = _LOCAL.pop(n, None)
            if s:
                _drop_local(s)
    return len(mortos)


def serve_status(root: Path, args: dict) -> str:
    name = args.get("name")
    tail = int(args.get("tail") or 40)
    if name and int(args.get("wait") or 0) > 0:
        _wait_end(_safe_name(str(name)), int(args["wait"]))
    entries = list_servers()
    if not entries:
        return "Nenhum servidor iniciado por serve_start nesta sessão."
    lines = []
    for s in entries:
        state = "rodando" if s.get("alive") else f"parado (exit {s.get('exit_code')})"
        lines.append(f"{'●' if s.get('alive') else '○'} {s['name']} [{s.get('where')}] pid {s.get('pid')} {state}: "
                     f"{s.get('command') or s.get('error', '')}")
    if name:
        try:
            log = server_log(str(name), tail)
        except (runner.RunnerError, ToolError) as e:
            log = str(e)
        lines += [f"--- log de {_safe_name(str(name))} (últimas {tail} linhas) ---", log or "(vazio)"]
    return "\n".join(lines)


def serve_stop(root: Path, args: dict) -> str:
    name = _safe_name(str(args["name"]))
    try:
        where = stop_server(name)
    except runner.RunnerError as e:
        raise ToolError(str(e)) from e
    return f"Servidor '{name}' ({_where('host') if where == 'host' else 'container'}) encerrado."


def serve_preview(root: Path, args: dict) -> dict:
    pv = command_preview(root, args)
    pv["path"] = f"servidor '{_safe_name(str(args.get('name') or 'server'))}'  ·  " + pv["path"]
    return pv


# ------------------------------------------------------------------ registro

TARGET = {"type": "string", "description": "auto (padrão: sistema do usuário se o forja-runner estiver ligado, "
                                           "senão container) | host | container"}

register(Tool(
    "run_command",
    "Executa um comando de shell na pasta da conversa e devolve a saída (veja o bloco Ambiente: sistema do "
    "usuário via forja-runner, ou bash no container). Use para testes, scripts, git, instalar pacotes. "
    "NÃO use para servidores (fica preso até o timeout): use serve_start. Comando demorado: background=true. "
    "Sem stdin.",
    {"type": "object", "properties": {
        "command": {"type": "string", "description": "Comando (PowerShell no Windows, bash no Linux/macOS/container)"},
        "cwd": {"type": "string", "description": "Subpasta da pasta de trabalho. Padrão: '.'"},
        "timeout": {"type": "integer", "description": f"Segundos (padrão 60, máx {config.SHELL_TIMEOUT_MAX})"},
        "background": {"type": "boolean",
                       "description": "Roda em segundo plano e devolve na hora: use para o que passa de ~1 min "
                                      "(build, suíte de teste longa). Espere o fim com "
                                      "serve_status(name=..., wait=60)."},
        "name": {"type": "string", "description": "Apelido do processo em background, ex.: build, testes"},
        "target": TARGET},
     "required": ["command"]},
    run_command, mutating=True, preview=command_preview, always_ask=True))
register(Tool(
    "serve_start",
    "Inicia um servidor de desenvolvimento em segundo plano (ex.: npm run dev, uvicorn, php artisan serve) e "
    "devolve as primeiras linhas do log. Mesmo nome reinicia.",
    {"type": "object", "properties": {
        "name": {"type": "string", "description": "Apelido curto, ex.: vite, api"},
        "command": {"type": "string"},
        "cwd": {"type": "string", "description": "Subpasta da pasta de trabalho. Padrão: '.'"},
        "target": TARGET},
     "required": ["name", "command"]},
    serve_start, mutating=True, preview=serve_preview, always_ask=True))
register(Tool(
    "serve_status",
    "Lista os processos iniciados por serve_start ou por run_command(background) e, com name, as últimas "
    "linhas do log. Com `wait`, espera o processo terminar antes de responder — é assim que se acompanha "
    "algo demorado sem ficar consultando de novo.",
    {"type": "object", "properties": {
        "name": {"type": "string", "description": "Apelido para ver o log"},
        "tail": {"type": "integer", "description": "Linhas do log (padrão 40)"},
        "wait": {"type": "integer",
                 "description": f"Espera até N segundos (máx {WAIT_MAX}) o processo terminar. Precisa de name."}},
     "required": []},
    serve_status, poll=True))
register(Tool(
    "serve_stop", "Encerra um servidor iniciado por serve_start.",
    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    serve_stop, mutating=True))
