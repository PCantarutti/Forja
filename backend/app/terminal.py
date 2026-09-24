"""Terminal do usuário na UI: um shell por sessão, no sistema do usuário (runner) ou no container.

Sem PTY: é um shell lendo comandos do stdin e escrevendo no stdout (PowerShell `-Command -` ou
bash). Programas interativos de tela cheia não funcionam; comandos comuns, sim. A UI mostra o
prompt por conta própria e faz polling da saída (`poll` espera até 20 s por novidade).
"""
from __future__ import annotations

import codecs
import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import runner, shell, workspace
from .tools import ToolError

POLL_WAIT = 20.0
MAX_BUFFER = 400_000


class LocalTerm:
    """bash no container lendo do stdin; um thread copia o stdout para o buffer."""

    def __init__(self, cwd: Path):
        self.proc = subprocess.Popen(["bash", "-l"], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        self.buf = ""
        # Total já escrito desde o início, não o tamanho do buffer: é ele que vira o cursor do
        # cliente. Com `len(buf)` o cursor empacava em MAX_BUFFER assim que o buffer saturava e
        # `len(buf) <= cursor` virava sempre verdade — o terminal congelava de vez.
        self.written = 0
        self.lock = threading.Lock()  # dois POST de input não podem intercalar no stdin do shell
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.cond = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        assert self.proc.stdout
        while True:
            chunk = self.proc.stdout.read1(4096) if hasattr(self.proc.stdout, "read1") else self.proc.stdout.read(1)
            if not chunk:
                break
            # Decodificador incremental: caractere multibyte partido entre duas leituras do pipe
            # virava U+FFFD com o decode por chunk.
            texto = self.decoder.decode(chunk)
            if not texto:
                continue
            with self.cond:
                self.written += len(texto)
                self.buf = (self.buf + texto)[-MAX_BUFFER:]
                self.cond.notify_all()
        with self.cond:
            self.cond.notify_all()

    def write(self, text: str) -> None:
        if self.proc.poll() is not None or not self.proc.stdin:
            raise ToolError("O shell deste terminal já encerrou. Abra outro.")
        with self.lock:
            self.proc.stdin.write((text.rstrip(chr(10)) + chr(10)).encode("utf-8"))
            self.proc.stdin.flush()

    def poll(self, cursor: int) -> dict:
        """Saída a partir de `cursor`, que é posição absoluta no total já escrito.

        Cliente que ficou para trás do buffer recebe o que sobrou dele, não o vazio: perde-se o
        miolo de uma saída enorme, mas o terminal continua vivo — que é o ponto.
        """
        deadline = time.monotonic() + POLL_WAIT
        with self.cond:
            while self.written <= cursor and self.proc.poll() is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(left)
            comeco = self.written - len(self.buf)  # posição absoluta do 1º char ainda no buffer
            text = self.buf[max(0, cursor - comeco):] if cursor < self.written else ""
            return {"text": text, "cursor": self.written, "alive": self.proc.poll() is None}

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(self.proc.pid, signal.SIGKILL)
                else:  # testes rodando no Windows
                    self.proc.kill()
            except (ProcessLookupError, PermissionError):
                pass


SESSIONS: dict[str, dict] = {}  # id -> {"where": host|container, "local": LocalTerm|None, "remote": id|None, "cwd": str}


def _reap() -> None:
    """Tira da lista os shells do container que já morreram. A UI fecha o dela, recarregar não."""
    for tid in [t for t, s in list(SESSIONS.items()) if s["local"] and s["local"].proc.poll() is not None]:
        SESSIONS.pop(tid, None)


def close_local() -> None:
    """Encerra os shells do container. Os do host são do runner e vivem além do backend: reiniciar
    o backend não pode derrubar o terminal que o usuário deixou aberto."""
    for tid, s in [(t, s) for t, s in list(SESSIONS.items()) if s["local"]]:
        SESSIONS.pop(tid, None)
        s["local"].close()


def start(root: Path) -> dict:
    _reap()
    host = workspace.to_host(root)
    target = shell.pick_target(None, runner.online(), host)
    tid = uuid.uuid4().hex[:12]
    if target == "host":
        try:
            r = runner.term_start(host)
        except runner.RunnerError as e:
            raise ToolError(str(e)) from e
        SESSIONS[tid] = {"where": "host", "remote": r["id"], "local": None, "cwd": host, "shell": r.get("shell", "")}
    else:
        SESSIONS[tid] = {"where": "container", "remote": None, "local": LocalTerm(root), "cwd": str(root), "shell": "bash"}
    s = SESSIONS[tid]
    return {"id": tid, "where": s["where"], "cwd": s["cwd"], "shell": s["shell"]}


def _get(tid: str) -> dict:
    s = SESSIONS.get(tid)
    if not s:
        raise ToolError("Terminal não existe (o backend pode ter reiniciado). Abra outro.")
    return s


def send(tid: str, text: str) -> None:
    s = _get(tid)
    if s["local"]:
        s["local"].write(text)
    else:
        try:
            runner.term_input(s["remote"], text)
        except runner.RunnerError as e:
            raise ToolError(str(e)) from e


def poll(tid: str, cursor: int) -> dict:
    s = _get(tid)
    if s["local"]:
        return s["local"].poll(cursor)
    try:
        return runner.term_poll(s["remote"], cursor)
    except runner.RunnerError as e:
        raise ToolError(str(e)) from e


def close(tid: str) -> None:
    s = SESSIONS.pop(tid, None)
    if not s:
        return
    if s["local"]:
        s["local"].close()
    else:
        try:
            runner.term_close(s["remote"])
        except runner.RunnerError:
            pass


# ------------------------------------------------------------------ terminal do agente
# Porte do tool-terminal do DeepSeek Harness: sessão de shell que sobrevive entre chamadas, para o
# que precisa de estado (cd, variável, venv ativado, REPL lendo stdin). Mesmo shell do terminal do
# usuário, sem PTY: programa de tela cheia (vim, top) não funciona.
# ponytail: sempre bash no container, mesmo com o runner online; terminal do agente no host via
# runner.term_* quando fizer falta.
AGENTE: dict[str, dict] = {}  # id -> {conv, name, lido}
MAX_AGENTE = 8
OCIOSO = 2.0          # segundos sem saída nova = o comando provavelmente terminou (inferido)
ESPERA_PADRAO, ESPERA_MAX = 30, 120
MAX_SAIDA = 20_000


def _meus() -> dict[str, dict]:
    from .shell import CONV

    return {t: a for t, a in AGENTE.items() if a["conv"] == CONV.get() and t in SESSIONS}


def _sessao(tid: str) -> tuple[LocalTerm, dict]:
    a = _meus().get(str(tid or ""))
    if not a:
        raise ToolError(f"Terminal '{tid}' não existe nesta conversa. Veja terminal_list ou abra com terminal_open.")
    return SESSIONS[tid]["local"], a


def _texto_desde(t: LocalTerm, desde: int) -> str:
    with t.cond:
        comeco = t.written - len(t.buf)
        texto = t.buf[max(0, desde - comeco):] if desde < t.written else ""
    if len(texto) > MAX_SAIDA:
        texto = f"... ({len(texto) - MAX_SAIDA} caracteres antes omitidos)\n" + texto[-MAX_SAIDA:]
    return texto


def _espera(t: LocalTerm, desde: int, segundos: float) -> str:
    """Espera o shell ficar quieto (OCIOSO s sem saída nova), sair ou o tempo acabar."""
    fim = time.monotonic() + max(0.0, segundos)
    visto, mudou = t.written, time.monotonic()
    while True:
        with t.cond:
            t.cond.wait(0.25)
        agora = time.monotonic()
        if t.proc.poll() is not None:
            return "encerrado"
        if t.written != visto:
            visto, mudou = t.written, agora
        elif agora - mudou >= OCIOSO:
            return "ocioso"
        if agora >= fim:
            return "tempo"


def _fim(estado: str, a: dict) -> str:
    return {"encerrado": "[o shell deste terminal encerrou]",
            "ocioso": "[terminal quieto: o comando parece ter terminado — é inferência, não prova]",
            "tempo": "[ainda produzindo saída; continue com terminal_read]"}.get(estado, "")


def terminal_open(root: Path, args: dict) -> str:
    from .shell import CONV
    from .tools import resolve_path

    if len(_meus()) >= MAX_AGENTE:
        raise ToolError(f"Já há {MAX_AGENTE} terminais abertos nesta conversa. Feche um com terminal_close.")
    cwd = resolve_path(root, args.get("cwd"))
    _reap()
    tid = "t" + uuid.uuid4().hex[:6]
    t = LocalTerm(cwd)
    SESSIONS[tid] = {"where": "container", "remote": None, "local": t, "cwd": str(cwd), "shell": "bash"}
    AGENTE[tid] = {"conv": CONV.get(), "name": str(args.get("name") or tid)[:40], "lido": t.written}
    return f"Terminal '{tid}' aberto (bash, no container) em {cwd}. Mande comandos com terminal_send(id='{tid}')."


def terminal_send(root: Path, args: dict) -> str:
    t, a = _sessao(args.get("id"))
    desde = t.written
    texto = str(args.get("command") or "")
    if args.get("submit") is False:
        with t.lock:  # sem Enter: caractere de controle ou entrada de REPL pela metade
            t.proc.stdin.write(texto.encode("utf-8"))
            t.proc.stdin.flush()
    else:
        t.write(texto)
    estado = _espera(t, desde, min(float(args.get("wait") or ESPERA_PADRAO), ESPERA_MAX))
    a["lido"] = t.written
    return f"{_texto_desde(t, desde).rstrip() or '(sem saída)'}\n{_fim(estado, a)}"


def terminal_read(root: Path, args: dict) -> str:
    t, a = _sessao(args.get("id"))
    if (w := float(args.get("wait") or 0)) > 0 and t.written == a["lido"]:
        _espera(t, a["lido"], min(w, ESPERA_MAX))
    texto = _texto_desde(t, a["lido"])
    a["lido"] = t.written
    vivo = "" if t.proc.poll() is None else "\n[o shell deste terminal encerrou]"
    return (texto.rstrip() or "(nada novo desde a última leitura)") + vivo


def terminal_close(root: Path, args: dict) -> str:
    _sessao(args.get("id"))
    close(str(args["id"]))
    AGENTE.pop(str(args["id"]), None)
    return f"Terminal '{args['id']}' encerrado, com os processos dele."


def terminal_list(root: Path, args: dict) -> str:
    linhas = [f"- {tid} ({a['name']}): {'vivo' if SESSIONS[tid]['local'].proc.poll() is None else 'encerrado'}"
              for tid, a in _meus().items()]
    return "\n".join(linhas) or "Nenhum terminal aberto nesta conversa."


def _preview(root: Path, args: dict) -> dict:
    return {"kind": "command", "path": f"terminal '{args.get('id')}'", "text": str(args.get("command") or "")}


def _registra() -> None:
    from .tools import Tool, _obj, register

    ident = {"id": {"type": "string", "description": "Id do terminal (de terminal_open ou terminal_list)"}}
    register(Tool(
        "terminal_open",
        "Abre um terminal persistente (bash no container, na pasta da conversa): cd, variáveis, venv ativado e "
        "REPL continuam valendo entre chamadas. Use só quando precisar desse estado ou de entrada interativa; "
        "para comando único use run_command. Não é PTY: programa de tela cheia (vim, top) não funciona.",
        _obj({"name": {"type": "string", "description": "Apelido, ex.: main, repl"},
              "cwd": {"type": "string", "description": "Subpasta inicial. Padrão: '.'"}}, []),
        terminal_open, timeout=None))
    register(Tool(
        "terminal_send",
        "Escreve no terminal (com Enter, por padrão) e espera ele ficar quieto, sair ou o tempo acabar. "
        "'Terminal quieto' é inferência: não prova que o comando terminou. submit=false manda sem Enter.",
        _obj({**ident, "command": {"type": "string", "description": "Texto a enviar (comando ou entrada do REPL)"},
              "submit": {"type": "boolean", "description": "Enter depois do texto. Padrão: true"},
              "wait": {"type": "integer", "description": f"Espera até N segundos (padrão {ESPERA_PADRAO}, máx {ESPERA_MAX})"}},
             ["id", "command"]),
        terminal_send, mutating=True, preview=_preview, always_ask=True, timeout=None))
    register(Tool(
        "terminal_read",
        "Lê o que o terminal escreveu desde a última leitura, sem mandar nada. `wait` espera saída nova.",
        _obj({**ident, "wait": {"type": "integer", "description": f"Espera até N segundos (máx {ESPERA_MAX})"}},
             ["id"]),
        terminal_read, poll=True, timeout=None))
    register(Tool("terminal_close", "Fecha um terminal e encerra os processos dele.", _obj(ident, ["id"]),
                  terminal_close))
    register(Tool("terminal_list", "Lista os terminais abertos por você nesta conversa.", _obj({}, []),
                  terminal_list, poll=True))


_registra()
