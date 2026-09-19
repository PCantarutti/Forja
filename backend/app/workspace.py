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


def _is_windows(host: str) -> bool:
    return bool(DRIVE_RE.match(host))


def normalize(host_path: str) -> str:
    """Caminho do sistema do usuário, normalizado.

    Windows: 'c:\\Users\\x\\' -> 'C:/Users/x'   Linux/macOS: '/home/x/' -> '/home/x'
    """
    raw = (host_path or "").strip().strip('"')
    m = DRIVE_RE.match(raw)
    if m:
        prefix, rest = f"{m.group(1).upper()}:/", m.group(2)
    elif raw.startswith("/"):
        prefix, rest = "/", raw
    else:
        raise WorkspaceError("Use um caminho completo, ex.: C:/Users/voce/Projetos/app ou /home/voce/projetos/app")
    parts = [x for x in rest.replace("\\", "/").split("/") if x not in ("", ".")]
    if ".." in parts:
        raise WorkspaceError("Caminho não pode conter '..'.")
    return prefix + "/".join(parts)


def mounts() -> list[tuple[str, Path]]:
    """[(prefixo no sistema, pasta no container)], prefixo mais longo primeiro.

    HOST_MOUNTS aceita "C=/host/c" (disco do Windows), "C:/Users/x=/host/x" ou "/home/x=/host/home".
    """
    out = []
    for part in filter(None, (config.HOST_MOUNTS or "").split(",")):
        host, _, container = part.rpartition("=")
        host, container = host.strip(), container.strip()
        if not host or not container:
            continue
        if len(host) == 1 and host.isalpha():  # formato antigo: só a letra
            host = f"{host}:/"
        try:
            out.append((normalize(host), Path(container)))
        except WorkspaceError:
            continue
    return sorted(out, key=lambda m: len(m[0]), reverse=True)


def _under(host: str, prefix: str) -> str | None:
    """Resto do caminho se `host` estiver dentro de `prefix` (sem diferenciar maiúsculas no Windows)."""
    a, b = (host.lower(), prefix.lower()) if _is_windows(prefix) else (host, prefix)
    if a == b:
        return ""
    base = b if b.endswith("/") else b + "/"
    return host[len(base):] if a.startswith(base) else None


def root() -> Path:
    """Raiz da execução atual (conversa); fora de execução, a pasta padrão."""
    return CURRENT.get() or default_root()


def default_root() -> Path:
    return config.WORKSPACE_ROOT


def to_container(host_path: str) -> Path:
    host = normalize(host_path)
    for prefix, base in mounts():
        rest = _under(host, prefix)
        if rest is not None:
            return base / PurePosixPath(rest) if rest else base
    montados = ", ".join(p for p, _ in mounts()) or "nenhum"
    raise WorkspaceError(f"{host} não está dentro de uma pasta montada no container "
                         f"(HOST_MOUNTS no docker-compose.yml). Montados: {montados}.")


def to_host(path: Path) -> str | None:
    """Caminho do container -> sistema do usuário (None se não estiver sob nada montado)."""
    candidates = []
    if config.WORKSPACE_HOST:
        try:
            candidates.append((normalize(config.WORKSPACE_HOST), config.WORKSPACE_ROOT))
        except WorkspaceError:
            pass
    candidates += mounts()
    for prefix, base in sorted(candidates, key=lambda m: len(str(m[1])), reverse=True):
        try:
            rel = Path(path).relative_to(base).as_posix()
        except ValueError:
            continue
        return prefix if rel == "." else prefix.rstrip("/") + "/" + rel
    return None


def resolve(host_path: str | None) -> Path:
    """Pasta da conversa (caminho do sistema) -> diretório existente no container. Vazio = pasta padrão."""
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
    out = []
    for prefix, base in sorted(mounts()):
        if base.is_dir():
            name = prefix.rstrip("/") if _is_windows(prefix) else prefix
            out.append({"name": name, "path": prefix})
    return out


def _parent(host: str) -> str | None:
    if host.endswith(":/") or host == "/":
        return None
    parent = host.rsplit("/", 1)[0]
    parent = parent + "/" if parent.endswith(":") else (parent or "/")
    try:
        to_container(parent)  # só oferece ".." se o pai também estiver montado
    except WorkspaceError:
        return None
    return parent


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
                    if e.is_dir() and e.name not in HIDDEN and not e.name.startswith((".", "$")):
                        dirs.append(e.name)
                except OSError:
                    continue
    except PermissionError:
        raise WorkspaceError(f"Sem permissão para listar {host}") from None
    return {"path": host, "parent": _parent(host),
            "dirs": [{"name": d, "path": host.rstrip("/") + "/" + d} for d in sorted(dirs, key=str.lower)]}
