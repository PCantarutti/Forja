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
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
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
