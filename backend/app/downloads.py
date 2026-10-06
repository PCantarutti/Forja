"""Trabalhos com progresso (hoje: lotes de imagem).

No desktop este módulo também baixa runtimes e modelos; no Docker não há IA local para baixar, então
fica só o registro em memória, com a mesma API (create/update/finish/cancel/cancelled), para
`lotes.py` e `imagegen.py` continuarem parecidos com os do desktop.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path

import httpx

_JOBS: dict[str, dict] = {}
_lock = threading.Lock()
KEEP_DONE = 300     # segundos que um job concluído continua na lista
KEEP_FAILED = 7200  # erro fica muito mais tempo: some antes de a pessoa ler é pior que poluir a tela


def _new(kind: str, name: str) -> dict:
    job = {"id": uuid.uuid4().hex[:8], "kind": kind, "name": name, "done": 0, "total": 0,
           "status": "running", "error": "", "detail": "", "result": None, "started": time.time(),
           "finished": 0.0}
    with _lock:
        _JOBS[job["id"]] = job
    return job


def create(kind: str, name: str) -> dict:
    """Job controlado por outro módulo (ex.: imagegen), que chama update/finish."""
    return _new(kind, name)


def update(job_id: str, done: int | None = None, total: int | None = None, detail: str | None = None) -> None:
    with _lock:
        job = _JOBS.get(job_id)
        if not job:
            return
        if done is not None:
            job["done"] = done
        if total is not None:
            job["total"] = total
        if detail is not None:
            job["detail"] = detail


def finish(job_id: str, error: str = "", result=None) -> None:
    with _lock:
        job = _JOBS.get(job_id)
        if not job:
            return
        if job["status"] == "running":
            job["status"] = "erro" if error else "pronto"
        job["error"] = error
        job["result"] = result
        job["finished"] = time.time()


def cancelled(job_id: str) -> bool:
    with _lock:
        job = _JOBS.get(job_id)
        return bool(job and job["status"] == "cancelado")


def cancel(job_id: str) -> None:
    with _lock:
        job = _JOBS.get(job_id)
        if job and job["status"] == "running":
            job["status"] = "cancelado"
            job["finished"] = time.time()


def dismiss(job_id: str) -> None:
    """Tira o job da lista. Serve para o erro que já foi lido: ele fica 2h, mas some quando se quer."""
    with _lock:
        job = _JOBS.get(job_id)
        if job and job["status"] != "running":
            del _JOBS[job_id]


def list_jobs() -> list[dict]:
    now = time.time()
    with _lock:
        for jid, job in list(_JOBS.items()):
            limite = KEEP_FAILED if job["status"] == "erro" else KEEP_DONE
            if job["finished"] and now - job["finished"] > limite:
                del _JOBS[jid]
        return sorted((dict(j) for j in _JOBS.values()), key=lambda j: j["started"])




# ------------------------------------------------------------------ download (modelos da tela Voz)
# O mesmo _fetch do desktop: a tela Voz baixa modelo do Hugging Face direto para a pasta do Windows (montada no
# container), com retomada pelo .part.

def _fetch(url: str, dest: Path, job: dict, base: int, total_all: int, headers: dict | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    ja_tem = part.stat().st_size if part.exists() else 0
    cabecalho = dict(headers or {})
    if ja_tem:
        cabecalho["Range"] = f"bytes={ja_tem}-"
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(30, read=120),
                          headers=cabecalho) as r:
            if r.status_code == 416:  # já estava inteiro
                raise _Pronto()
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code} em {url}")
            retomou = r.status_code == 206
            if ja_tem and not retomou:
                ja_tem = 0  # servidor ignorou o Range: recomeça
            size = int(r.headers.get("content-length") or 0) + (ja_tem if retomou else 0)
            update(job["id"], total=total_all or (base + size), detail=dest.name + (" (retomando)" if retomou else ""))
            _espaco(dest.parent, size - ja_tem)
            got = ja_tem
            update(job["id"], done=base + got)
            with open(part, "ab" if retomou else "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    if cancelled(job["id"]):
                        raise _Cancelled()
                    f.write(chunk)
                    got += len(chunk)
                    update(job["id"], done=base + got)
        if cancelled(job["id"]):
            raise _Cancelled()
    except _Pronto:
        pass
    except _Cancelled:
        part.unlink(missing_ok=True)
        raise
    os.replace(part, dest)


class _Pronto(Exception):
    """O servidor disse que o arquivo já está inteiro no .part."""


class _Cancelled(Exception):
    pass


def _espaco(pasta: Path, precisa: int) -> None:
    if precisa <= 0:
        return
    try:
        livre = shutil.disk_usage(pasta).free
    except OSError:
        return
    if livre < precisa + (500 << 20):
        raise RuntimeError(f"Espaço insuficiente em {pasta}: faltam {(precisa + (500 << 20) - livre) / 2 ** 30:.1f} GB")
