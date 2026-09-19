"""Pasta de trabalho por conversa (como no Claude Desktop).

Os discos do Windows são montados no container em `/host/<letra>` (HOST_MOUNTS no compose). Cada
conversa guarda o caminho do Windows escolhido (ex.: `C:/Users/pedro/Dev/app`); durante a execução,
`CURRENT` aponta para o caminho equivalente no container e toda ferramenta de arquivo usa essa raiz.

As ferramentas de arquivo ficam confinadas à pasta escolhida (resolve_path). O run_command roda no
container e, como no Claude Desktop, é protegido pela aprovação, não por sandbox: ele enxerga os
discos montados.
"""
from __future__ import annotations

import contextvars
import os
import re
from pathlib import Path, PurePosixPath

from . import config

CURRENT: contextvars.ContextVar[Path | None] = contextvars.ContextVar("forja_workspace", default=None)

DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/]*(.*)$")


class WorkspaceError(ValueError):
    pass


def mounts() -> dict[str, Path]:
    """{"C": Path("/host/c")} a partir de HOST_MOUNTS="C=/host/c,D=/host/d"."""
    out = {}
    for part in filter(None, (config.HOST_MOUNTS or "").split(",")):
        letter, _, path = part.partition("=")
        if letter.strip() and path.strip():
            out[letter.strip().upper()] = Path(path.strip())
    return out


def root() -> Path:
    """Raiz da execução atual (conversa); fora de execução, a pasta padrão."""
    return CURRENT.get() or default_root()


def default_root() -> Path:
    return config.WORKSPACE_ROOT


def normalize(host_path: str) -> str:
    """'c:\\Users\\x\\' -> 'C:/Users/x'."""
    m = DRIVE_RE.match((host_path or "").strip().strip('"'))
    if not m:
        raise WorkspaceError("Use um caminho do Windows completo, ex.: C:/Users/pedro/Projetos/app")
    rest = m.group(2).replace("\\", "/").strip("/")
    parts = [p for p in rest.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise WorkspaceError("Caminho não pode conter '..'.")
    return f"{m.group(1).upper()}:/" + "/".join(parts)


def to_container(host_path: str) -> Path:
    host = normalize(host_path)
    letter, rest = host[0], host[3:]
    base = mounts().get(letter)
    if base is None:
        raise WorkspaceError(f"O disco {letter}: não está montado no container. "
                             f"Adicione em HOST_MOUNTS (docker-compose.yml). Montados: {', '.join(mounts()) or 'nenhum'}.")
    return base / PurePosixPath(rest) if rest else base


def to_host(path: Path) -> str | None:
    """Caminho do container -> Windows (None se não estiver sob um disco montado)."""
    for letter, base in mounts().items():
        try:
            rel = Path(path).relative_to(base)
        except ValueError:
            continue
        rel_s = rel.as_posix()
        return f"{letter}:/" + ("" if rel_s == "." else rel_s)
    return None


def resolve(host_path: str | None) -> Path:
    """Pasta da conversa (Windows) -> diretório existente no container. Vazio = pasta padrão."""
    if not host_path:
        return default_root()
    p = to_container(host_path)
    if not p.is_dir():
        raise WorkspaceError(f"A pasta não existe (ou o container não a enxerga): {normalize(host_path)}")
    return p


def label(host_path: str | None) -> str:
    if not host_path:
        return config.WORKSPACE_HOST or "/workspace"
    return normalize(host_path)


# ------------------------------------------------------------------ navegação (seletor de pasta)

HIDDEN = {"$Recycle.Bin", "System Volume Information", "$WinREAgent", "Recovery", "Config.Msi"}


def roots() -> list[dict]:
    return [{"name": f"{letter}:", "path": f"{letter}:/"} for letter, base in sorted(mounts().items())
            if base.is_dir()]


def list_dirs(host_path: str) -> dict:
    host = normalize(host_path)
    p = to_container(host)
    if not p.is_dir():
        raise WorkspaceError(f"Pasta não encontrada: {host}")
    dirs = []
    try:
        with os.scandir(p) as it:
            for e in it:
                try:
                    if e.is_dir() and e.name not in HIDDEN and not e.name.startswith("."):
                        dirs.append(e.name)
                except OSError:
                    continue
    except PermissionError:
        raise WorkspaceError(f"Sem permissão para listar {host}") from None
    parent = None if len(host) <= 3 else host.rsplit("/", 1)[0] or host[:3]
    if parent and len(parent) == 2:
        parent += "/"
    return {"path": host, "parent": parent,
            "dirs": [{"name": d, "path": (host.rstrip("/") + "/" + d)} for d in sorted(dirs, key=str.lower)]}
