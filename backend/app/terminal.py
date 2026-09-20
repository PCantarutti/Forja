"""Terminal do usuário na UI: um shell por sessão, no sistema do usuário (runner) ou no container.

Sem PTY: é um shell lendo comandos do stdin e escrevendo no stdout (PowerShell `-Command -` ou
bash). Programas interativos de tela cheia não funcionam; comandos comuns, sim. A UI mostra o
prompt por conta própria e faz polling da saída (`poll` espera até 20 s por novidade).
"""
from __future__ import annotations

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
        self.cond = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        assert self.proc.stdout
        while True:
            chunk = self.proc.stdout.read1(4096) if hasattr(self.proc.stdout, "read1") else self.proc.stdout.read(1)
            if not chunk:
                break
            with self.cond:
                self.buf = (self.buf + chunk.decode("utf-8", "replace"))[-MAX_BUFFER:]
                self.cond.notify_all()
        with self.cond:
            self.cond.notify_all()

    def write(self, text: str) -> None:
        if self.proc.poll() is not None or not self.proc.stdin:
            raise ToolError("O shell deste terminal já encerrou. Abra outro.")
        self.proc.stdin.write((text.rstrip("\n") + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def poll(self, cursor: int) -> dict:
        deadline = time.monotonic() + POLL_WAIT
        with self.cond:
            while len(self.buf) <= cursor and self.proc.poll() is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(left)
            text = self.buf[cursor:] if cursor < len(self.buf) else ""
            return {"text": text, "cursor": len(self.buf), "alive": self.proc.poll() is None}

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


SESSIONS: dict[str, dict] = {}  # id -> {"where": host|container, "local": LocalTerm|None, "remote": id|None, "cwd": str}


def start(root: Path) -> dict:
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
