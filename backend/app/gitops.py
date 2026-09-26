"""Git da pasta da conversa: status, diff, commit com mensagem gerada pelo modelo, PR (gh) e worktree.

Os comandos rodam onde o run_command rodaria (sistema do usuário via runner, senão container), na
pasta da conversa. A mensagem de commit e o corpo do PR vão por arquivo (`.forja/`) para não brigar
com as aspas do PowerShell; o que sobra interpolado passa por `shell.quoter`.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import llm, shell, workspace
from .parsing import split_think
from .tools import ToolError

MAX_DIFF_CHARS = 12_000
COMMIT_PROMPT = (
    "Você escreve mensagens de commit. Dado o diff abaixo, responda SOMENTE com a mensagem, no formato "
    "Conventional Commits: primeira linha `tipo(escopo): resumo` com até 72 caracteres, em português, "
    "depois uma linha em branco e, se ajudar, até 5 tópicos curtos do que mudou. Sem markdown, sem aspas.")


def _run(root: Path, command: str, timeout: int = 60) -> tuple[int, str]:
    return shell.exec_in(root, command, timeout)


def _ok(root: Path, command: str, timeout: int = 60) -> str:
    code, out = _run(root, command, timeout)
    if code != 0:
        raise ToolError(out.strip() or f"`{command}` falhou (exit {code})")
    return out


def is_repo(root: Path) -> bool:
    code, out = _run(root, "git rev-parse --is-inside-work-tree", 20)
    return code == 0 and "true" in out


def status(root: Path) -> dict:
    """Estado resumido para a aba Alterações."""
    if not is_repo(root):
        return {"repo": False}
    branch = _run(root, "git branch --show-current", 20)[1].strip()
    code, porcelain = _run(root, "git status --porcelain=v1", 30)
    files = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        xy, path = line[:2], line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ")[-1]
        files.append({"status": "untracked" if xy == "??" else "modified" if "M" in xy else "added" if "A" in xy
                      else "deleted" if "D" in xy else "renamed" if "R" in xy else xy.strip(),
                      "path": path.strip('"'), "staged": xy[0] not in (" ", "?")})
    ahead = behind = None
    code, counts = _run(root, "git rev-list --left-right --count @{u}...HEAD", 20)
    if code == 0:
        m = re.match(r"\s*(\d+)\s+(\d+)", counts)
        if m:
            behind, ahead = int(m.group(1)), int(m.group(2))
    remote = _run(root, "git remote get-url origin", 20)[1].strip() if code is not None else ""
    has_gh = _run(root, "gh --version", 20)[0] == 0
    last = _run(root, "git log -1 --pretty=%h%x09%s", 20)[1].strip()
    return {"repo": True, "branch": branch, "files": files, "ahead": ahead, "behind": behind,
            "remote": remote if "fatal" not in remote else "", "has_gh": has_gh, "last_commit": last}


def diff(root: Path, path: str | None = None) -> str:
    """Diff do working tree (staged + unstaged) contra HEAD; untracked vira 'arquivo novo'."""
    q = shell.quoter(root)
    target = f" -- {q(path)}" if path else ""
    out = _run(root, f"git diff HEAD{target}", 60)[1]
    if path and not out.strip():
        p = root / path
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            out = f"--- /dev/null\n+++ b/{path}\n" + "".join(f"+{l}\n" for l in text.splitlines())
    return out[:200_000]


async def generate_message(root: Path, provider: str, model: str) -> str:
    _ok(root, "git add -A", 60)
    stat = _run(root, "git diff --cached --stat", 60)[1]
    patch = _run(root, "git diff --cached", 60)[1]
    if not patch.strip():
        raise ToolError("Nada para commitar: a árvore está limpa.")
    text = f"{stat}\n\n{patch[:MAX_DIFF_CHARS]}" + ("\n\n(diff truncado)" if len(patch) > MAX_DIFF_CHARS else "")
    out = ""
    async for kind, val in llm.chat_stream(provider, model, [{"role": "system", "content": COMMIT_PROMPT},
                                                             {"role": "user", "content": text}], None, 16_384,
                                           think=False):
        if kind == "content":
            out += val
    msg = split_think(out)[1].strip().strip("`").strip()
    if not msg:
        raise ToolError("O modelo não devolveu uma mensagem de commit.")
    return msg


def _write_forja_file(root: Path, name: str, text: str) -> str:
    folder = root / ".forja"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")
    return f".forja/{name}"


def commit(root: Path, message: str) -> dict:
    if not message.strip():
        raise ToolError("Mensagem de commit vazia.")
    _ok(root, "git add -A", 60)
    if not _run(root, "git diff --cached --quiet", 30)[0]:
        raise ToolError("Nada para commitar: a árvore está limpa.")
    rel = _write_forja_file(root, "commit-msg.txt", message)
    q = shell.quoter(root)
    try:
        # .forja/ pode não estar no .gitignore: não deixa o arquivo da mensagem entrar no commit.
        _run(root, f"git reset -q -- {q(rel)}", 20)
        out = _ok(root, f"git commit -F {q(rel)}", 120)
    finally:
        try:
            (root / rel).unlink()
        except OSError:
            pass
    sha = _run(root, "git rev-parse --short HEAD", 20)[1].strip()
    return {"sha": sha, "output": out.strip(), "message": message}


MAX_COMMIT_BYTES = 5_000_000  # arquivo maior que isto não entra no commit automático da tarefa


def commit_paths(root: Path, paths: list[str], message: str) -> str:
    """Commit só destes caminhos (os que uma tarefa do Maestro escreveu), sem varrer o resto da árvore:
    o usuário pode ter alterações próprias sem commit, e `git add -A` as levaria junto. Fica de fora
    `.env*` e arquivo acima de MAX_COMMIT_BYTES. Devolve o hash curto, ou '' se nada mudou."""
    candidatos = [p for p in dict.fromkeys(str(p).replace("\\", "/") for p in paths)
                  if p and not Path(p).name.startswith(".env")
                  and not ((root / p).is_file() and (root / p).stat().st_size > MAX_COMMIT_BYTES)]
    if not candidatos:
        return ""
    rastreados = set(_run(root, "git ls-files -- " + " ".join(shell.quoter(root)(p) for p in candidatos), 30)[1].splitlines())
    # Apagado e nunca rastreado não existe para o git: o `git add` falharia com "did not match".
    alvos = [p for p in candidatos if (root / p).exists() or p in rastreados]
    if not alvos:
        return ""
    alvo = " ".join(shell.quoter(root)(p) for p in alvos)
    _ok(root, f"git add -A -- {alvo}", 60)
    if _run(root, f"git diff --cached --quiet -- {alvo}", 30)[0] == 0:
        return ""
    rel = _write_forja_file(root, "commit-msg.txt", message)
    try:
        # Com caminhos, o commit leva só eles, mesmo que o usuário tenha outra coisa no índice.
        _ok(root, f"git commit -q -F {shell.quoter(root)(rel)} -- {alvo}", 120)
    finally:
        try:
            (root / rel).unlink()
        except OSError:
            pass
    return _run(root, "git rev-parse --short HEAD", 20)[1].strip()


def create_pr(root: Path, title: str, body: str) -> dict:
    st = status(root)
    if not st.get("repo"):
        raise ToolError("A pasta da conversa não é um repositório git.")
    if not st.get("has_gh"):
        raise ToolError("O GitHub CLI (gh) não está instalado ou não está no PATH onde os comandos rodam. "
                        "Instale com `winget install GitHub.cli` e faça `gh auth login`.")
    if st.get("files"):
        raise ToolError("Há alterações sem commit. Faça o commit antes de abrir o PR.")
    push = _ok(root, "git push -u origin HEAD", 180)
    rel = _write_forja_file(root, "pr-body.md", body or "")
    try:
        q = shell.quoter(root)
        cmd = (f"gh pr create --title {q(title)} --body-file {q(rel)}" if title else "gh pr create --fill")
        out = _ok(root, cmd, 120)
    finally:
        try:
            (root / rel).unlink()
        except OSError:
            pass
    url = next((w for w in out.split() if w.startswith("http")), "")
    return {"url": url, "output": (push + "\n" + out).strip()}


def worktree(root: Path, branch: str) -> dict:
    """Cria um worktree irmão da raiz do repositório numa branch nova e devolve o caminho no sistema do usuário."""
    if not is_repo(root):
        raise ToolError("A pasta da conversa não é um repositório git.")
    top = Path(_ok(root, "git rev-parse --show-toplevel", 20).strip().replace("\\", "/"))
    # `top` vem no formato de onde o git rodou (Windows ou container): traduz para o container para criar o nome.
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-") or "forja"
    name = f"{top.name}-{slug}"
    q = shell.quoter(root)
    if str(top)[1:3] == ":/":  # caminho do Windows: cria via git (que roda lá) e traduz depois
        dest_host = f"{top.parent.as_posix()}/{name}"
        _ok(root, f"git worktree add {q(dest_host)} -b {q(branch)}", 120)
        return {"path": workspace.normalize(dest_host), "branch": branch}
    dest = top.parent / name
    _ok(root, f"git worktree add {q(dest)} -b {q(branch)}", 120)
    host = workspace.to_host(dest)
    if not host:
        raise ToolError(f"Worktree criado em {dest}, mas fora das pastas montadas: não dá para usá-lo como pasta da conversa.")
    return {"path": host, "branch": branch}


# ------------------------------------------------------------------ worktree por tarefa (E7)

WT_DIR = ".forja/wt"
_ID = "-c user.name=Forja -c user.email=forja@local"


def _ignora_wt(root: Path) -> None:
    """`.forja/wt/` fora do `git status` da pasta principal, sem mexer no .gitignore do usuário."""
    git = root / ".git"
    if not git.is_dir():
        return
    exclude = git / "info" / "exclude"
    try:
        atual = exclude.read_text("utf-8") if exclude.exists() else ""
        if WT_DIR + "/" not in atual:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text(atual.rstrip("\n") + ("\n" if atual else "") + WT_DIR + "/\n", encoding="utf-8")
    except OSError:
        pass


def worktree_tarefa(root: Path, code: str) -> Path:
    """Worktree limpo, a partir do último commit, onde o Worker desta tarefa trabalha sem pisar em outro."""
    # ponytail: no Docker o git roda no host pelo runner e o merge de 3 vias lê arquivo pelo container;
    # até isso ser conferido, Worker em paralelo fica com a trava por arquivo (o maestro cai nela sozinho).
    raise ToolError("no Forja em Docker, Workers em paralelo usam a trava por arquivo, sem worktree")
    _ignora_wt(root)
    wt = root / WT_DIR / code
    remove_worktree_tarefa(root, code)  # sobra de uma tentativa que caiu no meio
    _ok(root, f"git worktree add -q -b {shell.quoter(root)('forja-wt/' + code)} {shell.quoter(root)(wt.as_posix())} HEAD", 120)
    return wt


_TRAZIDO: dict[str, dict[str, tuple[bytes | None, bytes | None]]] = {}  # worktree -> {arquivo: (antes, trazido)}


def _git_bytes(cwd: Path, *args: str) -> tuple[int, bytes]:
    """git com saída em bytes (conteúdo de arquivo não passa pelo decode do shell)."""
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=60)
    return r.returncode, r.stdout


def _ler(p: Path) -> bytes | None:
    try:
        return p.read_bytes()
    except OSError:
        return None


def traz_do_worktree(root: Path, wt: Path, da_forja: set[str] | frozenset = frozenset()) -> tuple[list[str], str]:
    """Leva o que o Worker fez no worktree para a pasta principal, sem commit (o commit continua sendo o do
    update_task, E2). Merge de 3 vias por arquivo: base = o commit de onde o worktree saiu, "nosso" = a pasta
    principal (que pode já ter o que outra tarefa em paralelo trouxe), "deles" = o worktree. Devolve
    (arquivos, conflito); com conflito nada é escrito. `da_forja`: arquivos sem commit que vieram de outra
    tarefa do Forja; sem commit por qualquer outro motivo (o usuário) recusa em vez de misturar."""
    _ok(wt, "git add -A", 60)
    if _run(wt, "git diff --cached --quiet", 30)[0] == 0:
        return [], ""
    _ok(wt, f"git {_ID} commit -q -m tarefa", 120)
    arquivos = [l.strip() for l in _ok(wt, "git diff --name-only HEAD~1 HEAD", 30).splitlines() if l.strip()]
    sujos = [l[3:].strip().strip('"') for l in _run(root, "git status --porcelain=v1 -- "
                                                    + " ".join(shell.quoter(root)(a) for a in arquivos), 30)[1].splitlines()
             if l.strip()]
    if alheios := [s for s in sujos if s not in da_forja]:
        return [], ("a pasta principal tem alterações sem commit nestes arquivos, e trazer a tarefa passaria por "
                    "cima: " + ", ".join(alheios[:10]))
    plano: list[tuple[str, bytes | None]] = []
    for a in arquivos:
        codigo, base = _git_bytes(wt, "show", f"HEAD~1:{a}")
        base = base if codigo == 0 else None
        deles, nosso = _ler(wt / a), _ler(root / a)
        if nosso == base or nosso == deles:
            plano.append((a, deles))
            continue
        if deles == base:
            continue  # o worktree não mudou este de fato
        if None in (base, deles, nosso):
            return [], f"conflito ao trazer a tarefa para a pasta principal: {a} foi criado ou apagado dos dois lados"
        tmp = wt / ".git-merge"
        tmp.mkdir(exist_ok=True)
        for nome, dado in (("o", nosso), ("b", base), ("t", deles)):
            (tmp / nome).write_bytes(dado)
        r = subprocess.run(["git", "merge-file", "-p", str(tmp / "o"), str(tmp / "b"), str(tmp / "t")],
                           capture_output=True, timeout=60)
        if r.returncode != 0:
            return [], (f"conflito ao trazer a tarefa para a pasta principal: outra tarefa mexeu nas mesmas linhas "
                        f"de {a}")
        plano.append((a, r.stdout))
    _TRAZIDO[str(wt)] = {a: (_ler(root / a), dado) for a, dado in plano}
    for a, dado in plano:
        alvo = root / a
        if dado is None:
            alvo.unlink(missing_ok=True)
        else:
            alvo.parent.mkdir(parents=True, exist_ok=True)
            alvo.write_bytes(dado)
    return [a for a, _ in plano], ""


def remove_worktree_tarefa(root: Path, code: str) -> None:
    wt = root / WT_DIR / code
    _run(root, f"git worktree remove --force {shell.quoter(root)(wt.as_posix())}", 60)
    _run(root, "git worktree prune", 30)
    _run(root, f"git branch -D {shell.quoter(root)('forja-wt/' + code)}", 30)


def desfaz_trazidos(root: Path, wt: Path) -> list[str]:
    """A tarefa trazida do worktree falhou depois (regressão): cada arquivo volta a como estava antes de
    trazer — só se ninguém mexeu nele desde então (outra tarefa em paralelo). Devolve os que ficaram."""
    ficaram = []
    for a, (antes, trazido) in _TRAZIDO.pop(str(wt), {}).items():
        alvo = root / a
        if _ler(alvo) != trazido:
            ficaram.append(a)
        elif antes is None:
            alvo.unlink(missing_ok=True)
        else:
            alvo.write_bytes(antes)
    return ficaram
