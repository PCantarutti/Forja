"""Tela Voz (texto para fala) no Forja Docker. Mesmos motores do Forja Desktop:

- "f5": F5-TTS/E2-TTS (o oficial ou qualquer fine-tune, ex.: pt-br) — tts_f5.py.
- "fish": Fish Audio no formato do fish-speech (S2-pro e afins; aceita [tags] de emoção no texto) — tts_fish.py.

O que muda do desktop (arquivo que diverge de propósito, ver SYNC.md):
- O container não enxerga a GPU: o motor roda no Windows, pelo forja-runner (`/serve`), no Python que o Forja Desktop
  desta máquina instalou ({appdata}/runtimes/tts/<motor>). Instalar e atualizar é lá, em Configurações › Runtime.
- O `/serve` não tem stdin: o motor sobe com --fila, lê os pedidos de arquivos <id>.json numa pasta do Windows e anuncia
  cada um com "PEDIDO <id>" no log, que o backend lê pelo runner. Sai sozinho depois de OCIOSO segundos parado.
- Áudio de referência, saída e modelos ficam em {appdata}/tts (o que o motor no Windows lê e grava); a pasta de modelos e
  a de vozes são as do desktop (um baixa, o outro aproveita). Os cadastros (config.json) ficam no /data do container.
- Sem a pergunta de VRAM do LLM (o Docker não carrega modelo local).

Caminhos: o backend lê e grava pelo caminho do container (/host/c/...), o motor recebe o do Windows (C:/...).
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

import httpx

from . import config, db, downloads, imagegen, runner, workspace
from .tools import ToolError

CONFIG = config.DATA_DIR / "tts" / "config.json"
ARQUITETURAS = ["F5TTS_v1_Base", "F5TTS_Base", "E2TTS_Base", "F5TTS_v1_Small", "F5TTS_Small", "E2TTS_Small"]
EXT_AUDIO = (".wav", ".mp3", ".flac", ".ogg", ".m4a")
OCIOSO = 300  # segundos parado até o motor sair sozinho e devolver a VRAM
PREFIXO = "forja-tts-"
MOTORES = {
    "f5": {"nome": "F5-TTS / E2-TTS", "script": "tts_f5.py", "gb": 5},
    "fish": {"nome": "Fish Audio (S2-pro e afins)", "script": "tts_fish.py", "gb": 6},
}

_cfg = threading.Lock()
_lock = threading.Lock()  # ponytail: uma geração por vez numa trava global; fila de verdade se precisar de ordem
_rodando: dict[int, int] = {}
_motor: dict = {}  # o processo de pé no runner: nome, chave, modelo (nome), dispositivo, ultimo, ocupado


# ------------------------------------------------------------------ caminhos (Windows ⇄ container)

def _base() -> str:
    """{appdata}/tts no Windows: precisa do forja-runner (ou de WORKSPACE_HOST) para saber a pasta do usuário."""
    a = imagegen._appdata()
    if not a:
        raise ToolError("Sem o forja-runner: a tela Voz roda o motor no Windows. Ligue o runner e tente de novo.")
    return f"{a}/tts"


def _c(host: str) -> Path:
    return imagegen._c(host)


def _h(p: Path) -> str:
    h = workspace.to_host(p)
    if not h:
        raise ToolError(f"O Windows não enxerga {p}: ele precisa estar num disco montado (HOST_MOUNTS).")
    return workspace.normalize(h)


def _pasta(sub: str) -> Path:
    return _c(f"{_base()}/{sub}")


def _py(motor: str) -> str:
    return f"{imagegen._appdata()}/runtimes/tts/{motor}/venv/Scripts/python.exe"


def instalado(motor: str) -> bool:
    try:
        return bool(imagegen._appdata()) and _c(_py(motor)).is_file() and \
            _c(f"{imagegen._appdata()}/runtimes/tts/{motor}/pronto").is_file()
    except ToolError:
        return False


def estado() -> dict:
    motores = {k: {"nome": m["nome"], "instalado": instalado(k), "gb": m["gb"], "instalavel": False, "instalando": ""}
               for k, m in MOTORES.items()}
    baixando = [{k: j[k] for k in ("id", "name", "done", "total", "status", "error", "detail")}
                for j in downloads.list_jobs() if j["kind"] == "modelo" and j["name"].endswith("(voz)")]
    try:
        vozes_ = vozes()
    except ToolError:
        vozes_ = []
    return {"motores": motores, "baixando": baixando, "gpu": "do Windows", "backend": "auto", "modelos": modelos(),
            "vozes": vozes_, "arquiteturas": ARQUITETURAS,
            "carregado": {"nome": _motor["modelo"], "dispositivo": _motor["dispositivo"]} if _vivo() else None}


# ------------------------------------------------------------------ modelos e vozes

def _ler() -> dict:
    try:
        return json.loads(CONFIG.read_text("utf-8"))
    except (OSError, ValueError):
        return {"modelos": []}


def _gravar(d: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(CONFIG)


def _existe(host: str, pasta: bool = False) -> bool:
    try:
        p = _c(host)
        return p.is_dir() if pasta else p.is_file()
    except ToolError:
        return False


def _hf(v: str) -> tuple[str, str]:
    partes = v[5:].split("/")
    return "/".join(partes[:2]), "/".join(partes[2:])


def modelos() -> list[dict]:
    """Os cadastrados; `a_baixar`: o modelo ainda não está no disco (desce na primeira geração)."""
    out = []
    for m in _ler().get("modelos") or []:
        m = {"motor": "f5", **m}
        try:
            if m["motor"] == "fish":
                falta = not _existe(m["modelo"], True) and not (_pasta_repo(m["modelo"]) / ".completo").is_file()
            else:
                falta = any(m.get(c, "").startswith("hf://") and not (_pasta_repo(_hf(m[c])[0]) / _hf(m[c])[1]).is_file()
                            for c in ("ckpt", "vocab"))
        except ToolError:
            falta = True
        out.append({**m, "a_baixar": falta})
    return out


def salvar_modelo(m: dict) -> list[dict]:
    nome = str(m.get("nome") or "").strip()
    if not nome:
        raise ToolError("Dê um nome ao modelo.")
    motor = m.get("motor") or "f5"
    texto = {k: str(m.get(k) or "").strip().strip('"') for k in ("ckpt", "vocab", "numeros", "modelo")}
    if motor == "f5":
        if m.get("arquitetura") not in ARQUITETURAS:
            raise ToolError("Arquitetura desconhecida: " + ", ".join(ARQUITETURAS))
        for k in ("ckpt", "vocab"):
            v = texto[k]
            if v and not v.startswith("hf://"):
                texto[k] = v = imagegen._norm(v)
                if not _existe(v):
                    raise ToolError(f"Arquivo não encontrado: {v} (use um caminho do Windows ou hf://dono/repo/arquivo).")
        novo = {"nome": nome, "motor": motor, "arquitetura": m["arquitetura"], "ckpt": texto["ckpt"],
                "vocab": texto["vocab"], "numeros": texto["numeros"], "minusculas": bool(m.get("minusculas"))}
    elif motor == "fish":
        v = texto["modelo"]
        if re.fullmatch(r"[\w.-]+/[\w.-]+", v) and not re.match(r"^[A-Za-z]:", v):
            pass  # repositório do Hugging Face
        elif _existe(imagegen._norm(v), True):
            v = imagegen._norm(v)
        else:
            raise ToolError("Informe o repositório do Hugging Face (ex.: fishaudio/s2-pro) ou a pasta do modelo no Windows.")
        novo = {"nome": nome, "motor": motor, "modelo": v}
    else:
        raise ToolError("Motor desconhecido: " + ", ".join(MOTORES))
    with _cfg:
        d = _ler()
        d["modelos"] = [x for x in d.get("modelos") or [] if x["nome"] != nome] + [novo]
        _gravar(d)
    return modelos()


def apagar_modelo(nome: str) -> list[dict]:
    with _cfg:
        d = _ler()
        d["modelos"] = [x for x in d.get("modelos") or [] if x["nome"] != nome]
        _gravar(d)
    return modelos()


def _slug(nome: str) -> str:
    return re.sub(r"[^\w-]+", "-", nome.strip()).strip("-")[:60] or "voz"


def vozes() -> list[dict]:
    """As de {appdata}/tts/vozes: as mesmas do Forja Desktop. `caminho` é o do Windows (a tela usa na rota de arquivo)."""
    pasta, out = _pasta("vozes"), []
    for f in sorted(pasta.glob("*.json")) if pasta.is_dir() else []:
        try:
            v = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if (pasta / v.get("arquivo", "")).is_file():
            out.append({**v, "id": f.stem, "caminho": f"{_base()}/vozes/{v['arquivo']}"})
    return out


def salvar_voz(nome: str, texto: str, arquivo: str, dados: bytes) -> list[dict]:
    ext = Path(arquivo).suffix.lower()
    if ext not in EXT_AUDIO:
        raise ToolError("Envie um áudio (" + ", ".join(EXT_AUDIO) + ").")
    if len(dados) > 30_000_000:
        raise ToolError("Áudio maior que 30 MB: a referência boa tem de 5 a 15 segundos.")
    vid, pasta = _slug(nome), _pasta("vozes")
    pasta.mkdir(parents=True, exist_ok=True)
    for velho in pasta.glob(vid + ".*"):
        velho.unlink()
    (pasta / (vid + ext)).write_bytes(dados)
    (pasta / (vid + ".json")).write_text(json.dumps({"nome": nome.strip() or vid, "texto": texto.strip(),
                                                     "arquivo": vid + ext}, ensure_ascii=False), "utf-8")
    return vozes()


def _transcrita(vid: str, texto: str) -> None:
    f = _pasta("vozes") / (vid + ".json")
    try:
        v = json.loads(f.read_text("utf-8"))
        if not v.get("texto"):
            f.write_text(json.dumps({**v, "texto": texto}, ensure_ascii=False), "utf-8")
    except (OSError, ValueError):
        pass


def apagar_voz(vid: str) -> list[dict]:
    for f in _pasta("vozes").glob(_slug(vid) + ".*"):
        f.unlink()
    return vozes()


def servivel(path: str) -> Path:
    """A rota de arquivo só entrega áudio de dentro de {appdata}/tts (o caminho vem como o do Windows)."""
    f = _c(imagegen._norm(path)).resolve()
    if not f.is_relative_to(_c(_base()).resolve()) or f.suffix.lower() not in EXT_AUDIO or not f.is_file():
        raise ToolError("Arquivo não encontrado.")
    return f


# ------------------------------------------------------------------ modelos no Hugging Face

HF = "https://huggingface.co"
ORDENS = {"relevancia": "", "curtidas": "likes", "downloads": "downloads", "recentes": "lastModified"}
TAG_RUIDO = ("region:", "endpoints_compatible", "base_model:", "autotrain", "arxiv:", "doi:", "dataset:",
             "co2_eq_emissions", "model-index", "has_space", "custom_code", "text-generation-inference")
CKPT_F5 = re.compile(r"(^|/)model_(\d+|last)\.(pt|safetensors)$", re.I)
QUANTIZADO = re.compile(r"fp8|nf4|4bit|8bit|bnb|w4a16|nvfp4|int8|gguf|mlx|onnx", re.I)
FORA_FISH = (".gitattributes", ".md", ".png", ".jpg", ".jpeg", ".gif", ".webp")
BUSCA_PADRAO = ["fishaudio", "fish-speech", "s2-pro", "f5-tts", "f5 tts"]


def hf_headers() -> dict:
    t = __import__("os").environ.get("HF_TOKEN", "").strip()  # repositório gated: o token vem do .env do compose
    return {"Authorization": f"Bearer {t}"} if t else {}


def _tags(tags: list[str], limite: int = 8) -> list[str]:
    return [t for t in tags if not t.startswith(TAG_RUIDO)][:limite]


def _arvore(repo: str) -> list[tuple[str, int]]:
    r = httpx.get(f"{HF}/api/models/{repo}/tree/main", timeout=20, follow_redirects=True, headers=hf_headers(),
                  params={"recursive": "true"})
    if r.status_code in (401, 403):
        raise ToolError("Repositório restrito (gated): aceite os termos no site do Hugging Face e ponha HF_TOKEN no .env.")
    if r.status_code >= 400:
        raise ToolError(f"Hugging Face respondeu {r.status_code} para {repo}.")
    return [(f["path"], f.get("size") or (f.get("lfs") or {}).get("size") or 0) for f in r.json() if f.get("type") == "file"]


def formato(nomes: list[str]) -> str:
    """Pelo conteúdo do repositório: "fish" (o layout do S2 que o fish-speech fixado carrega), "f5" ou ""."""
    baixo = [n.lower() for n in nomes]
    if (any(n.endswith("codec.pth") for n in baixo) and any(n.endswith("tokenizer.json") for n in baixo)
            and any(n.rsplit("/", 1)[-1].startswith("model") and n.endswith(".safetensors") for n in baixo)):
        return "fish"
    return "f5" if any(CKPT_F5.search(n) for n in nomes) else ""


def _arquitetura(caminho: str) -> str:
    c = caminho.lower()
    return "E2TTS_Base" if "e2" in c else "F5TTS_v1_Base" if "v1" in c else "F5TTS_Base"


def arquivos_hf(repo: str) -> list[dict]:
    arv = _arvore(repo)
    fmt = formato([p for p, _ in arv])
    if fmt == "fish":
        uteis = [(p, s) for p, s in arv if not p.lower().endswith(FORA_FISH)]
        return [{"path": "", "size": sum(s for _, s in uteis), "quant": "", "shards": 1, "tipo": "fish", "papel": "modelo",
                 "nome": f"Modelo inteiro · {len(uteis)} arquivos"}]
    if fmt == "f5":
        return sorted(({"path": p, "size": s, "quant": "", "shards": 1, "tipo": "f5", "papel": "modelo",
                        "arquitetura": _arquitetura(f"{repo}/{p}")} for p, s in arv if CKPT_F5.search(p)),
                      key=lambda f: (f["size"], f["path"]))
    return []


def buscar_hf(q: str, sort: str = "relevancia", limite: int = 20) -> list[dict]:
    ordem = ORDENS.get(sort, "") or ("" if q.strip() else "downloads")
    vistos: dict[str, dict] = {}
    consultas = ([{"search": q.strip(), "pipeline_tag": "text-to-speech"}, {"search": q.strip()}] if q.strip()
                 else [{"search": t} for t in BUSCA_PADRAO])
    for consulta in consultas:
        r = httpx.get(f"{HF}/api/models", timeout=20, follow_redirects=True, headers=hf_headers(),
                      params={**consulta, "limit": 40, "full": "true", **({"sort": ordem, "direction": -1} if ordem else {})})
        if r.status_code >= 400:
            raise ToolError(f"Hugging Face respondeu {r.status_code}.")
        for m in r.json():
            if QUANTIZADO.search(m["id"]) or any(QUANTIZADO.fullmatch(t) for t in m.get("tags") or []):
                continue
            fmt = formato([s.get("rfilename", "") for s in m.get("siblings") or []])
            if fmt:
                vistos.setdefault(m["id"], {**m, "_fmt": fmt})
    saida = [{"id": m["id"], "author": m.get("author", ""), "downloads": m.get("downloads", 0), "likes": m.get("likes", 0),
              "updated": m.get("lastModified", ""), "gated": bool(m.get("gated")), "tags": _tags(m.get("tags") or []),
              "variante_nome": MOTORES[m["_fmt"]]["nome"]} for m in vistos.values()]
    if not ORDENS.get(sort):
        saida.sort(key=lambda m: -m["downloads"])
    return saida[:limite]


def repo_info(repo: str) -> dict:
    """A ficha da janela de busca (no desktop é o localai.repo_info, que aqui não existe)."""
    r = httpx.get(f"{HF}/api/models/{repo}", timeout=20, follow_redirects=True, headers=hf_headers())
    if r.status_code >= 400:
        raise ToolError(f"Hugging Face respondeu {r.status_code} para {repo}.")
    j = r.json()
    readme = ""
    try:
        rr = httpx.get(f"{HF}/{repo}/raw/main/README.md", timeout=20, follow_redirects=True, headers=hf_headers())
        if rr.status_code < 400:
            readme = rr.text
            if readme.startswith("---") and (fim := readme.find("\n---", 3)) > 0:
                readme = readme[fim + 4:]
    except httpx.HTTPError:
        pass
    licenca = next((t.split(":", 1)[1] for t in j.get("tags") or [] if t.startswith("license:")), "")
    return {"id": j.get("id", repo), "author": j.get("author", ""), "downloads": j.get("downloads", 0),
            "likes": j.get("likes", 0), "updated": j.get("lastModified", ""), "gated": bool(j.get("gated")),
            "tags": _tags(j.get("tags") or [], 12), "license": licenca, "params": 0, "arch": "", "ctx_train": 0,
            "capabilities": {"vision": False, "tools": False, "reasoning": False}, "files": arquivos_hf(repo),
            "readme": re.sub(r"<[^>]+>", "", readme)[:30_000]}


def _pasta_repo(repo: str) -> Path:
    return _pasta("modelos") / repo.replace("/", "--")


def _baixar_arquivos(repo: str, arquivos: list[tuple[str, int]], dest: Path, job: dict) -> None:
    """Baixa para a pasta do Windows (vista do container), mantendo as subpastas, com retomada e a barra no job."""
    from .downloads import _fetch, update
    total = sum(s for _, s in arquivos)
    update(job["id"], total=total, done=0)
    base = 0
    for caminho, tamanho in arquivos:
        alvo = dest / caminho
        if alvo.is_file() and (not tamanho or alvo.stat().st_size == tamanho):
            base += tamanho
            update(job["id"], done=base)
            continue
        update(job["id"], detail=caminho)
        _fetch(f"{HF}/{repo}/resolve/main/{caminho}?download=true", alvo, job, base, total, hf_headers())
        base += tamanho


def _arquivos_do_download(repo: str, path: str) -> tuple[str, list[tuple[str, int]]]:
    arv = _arvore(repo)
    fmt = formato([p for p, _ in arv])
    if not fmt:
        raise ToolError(f"{repo} não é um modelo que a tela Voz roda (nem Fish Audio, nem checkpoint F5).")
    if fmt == "fish":
        return fmt, [(p, s) for p, s in arv if not p.lower().endswith(FORA_FISH)]
    ckpt = next(((p, s) for p, s in arv if p == path), None)
    if not ckpt:
        raise ToolError(f"Checkpoint {path} não encontrado em {repo}.")
    pasta = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
    vocab = next(((p, s) for p, s in arv if p == f"{pasta}vocab.txt"), None) or next(((p, s) for p, s in arv if p == "vocab.txt"), None)
    return fmt, [ckpt] + ([vocab] if vocab else [])


def baixar(repo: str, path: str = "") -> dict:
    """Download pela janela de modelos (aba voz): no fim o modelo entra cadastrado na tela Voz."""
    fmt, arquivos = _arquivos_do_download(repo, path)
    dest = _pasta_repo(repo)
    job = downloads.create("modelo", f"{repo}{'/' + Path(path).name if path else ''} (voz)")

    def correr() -> None:
        try:
            _baixar_arquivos(repo, arquivos, dest, job)
            if fmt == "fish":
                (dest / ".completo").write_text(time.strftime("%Y-%m-%d %H:%M"), "utf-8")
                novo = {"nome": repo.split("/")[-1], "motor": "fish", "modelo": _h(dest)}
            else:
                vocab = next((_h(dest / p) for p, _ in arquivos if p.endswith("vocab.txt")), "")
                novo = {"nome": f"{repo.split('/')[-1]} · {Path(path).stem}", "motor": "f5", "arquitetura": _arquitetura(f"{repo}/{path}"),
                        "ckpt": _h(dest / path), "vocab": vocab, "minusculas": False, "numeros": ""}
            with _cfg:
                d = _ler()
                lista = d.get("modelos") or []
                if not any(x.get("modelo") == novo.get("modelo") and x.get("ckpt") == novo.get("ckpt") for x in lista):
                    nomes = {x["nome"] for x in lista}
                    while novo["nome"] in nomes:
                        novo["nome"] += " (2)"
                    d["modelos"] = lista + [novo]
                    _gravar(d)
            downloads.finish(job["id"], result=str(dest))
        except Exception as e:
            if not downloads.cancelled(job["id"]):
                downloads.finish(job["id"], error=str(e) if isinstance(e, ToolError) else f"{e.__class__.__name__}: {e}")
    threading.Thread(target=correr, daemon=True).start()
    return job


def _local(m: dict, mid: int, job: dict) -> dict:
    """O modelo com os arquivos no disco do Windows; repositório (Fish) ou hf:// (F5) ainda não baixados descem aqui,
    com a barra na mensagem da geração. Devolve os caminhos como o motor no Windows os vê."""
    pedidos: list[tuple[str, str, str]] = []
    if m["motor"] == "fish" and not _existe(m["modelo"], True):
        if (_pasta_repo(m["modelo"]) / ".completo").is_file():
            return {**m, "modelo": _h(_pasta_repo(m["modelo"]))}
        pedidos.append(("modelo", m["modelo"], ""))
    for campo in ("ckpt", "vocab") if m["motor"] == "f5" else ():
        v = m.get(campo, "")
        if v.startswith("hf://"):
            repo, caminho = _hf(v)
            if (_pasta_repo(repo) / caminho).is_file():
                m = {**m, campo: _h(_pasta_repo(repo) / caminho)}
            else:
                pedidos.append((campo, repo, caminho))
    if not pedidos:
        return m
    fim = threading.Event()

    def barra() -> None:
        while not fim.wait(1):
            if job.get("total"):
                gb = lambda n: f"{n / 2**30:.1f}".replace(".", ",")
                _patch(mid, fase=f"baixando o modelo · {gb(job['done'])} de {gb(job['total'])} GB",
                       progresso=job["done"] / job["total"])
    threading.Thread(target=barra, daemon=True).start()
    try:
        for campo, repo, caminho in pedidos:
            dest = _pasta_repo(repo)
            if campo == "modelo":
                _, arquivos = _arquivos_do_download(repo, "")
                _baixar_arquivos(repo, arquivos, dest, job)
                (dest / ".completo").write_text(time.strftime("%Y-%m-%d %H:%M"), "utf-8")
                m = {**m, "modelo": _h(dest)}
            else:
                tamanho = next((s for p, s in _arvore(repo) if p == caminho), 0)
                _baixar_arquivos(repo, [(caminho, tamanho)], dest, job)
                m = {**m, campo: _h(dest / caminho)}
    finally:
        fim.set()
        _patch(mid, progresso=None)
    return m


# ------------------------------------------------------------------ o motor de pé (no Windows, pelo runner)

def _vivo() -> bool:
    if not _motor.get("nome"):
        return False
    try:
        return any(s.get("name") == _motor["nome"] and s.get("alive") for s in runner.servers())
    except runner.RunnerError:
        return False


def descarregar() -> None:
    nome = _motor.get("nome")
    _motor.clear()
    if nome:
        try:
            runner.serve_stop(nome)
        except runner.RunnerError:
            pass


def liberar_gpu() -> None:
    if _motor.get("ocupado"):
        raise ToolError("A tela Voz está gerando um áudio agora. Espere terminar.")
    descarregar()


def _argv(m: dict) -> list[str]:
    if m["motor"] == "fish":
        return ["--modelo", m["modelo"]]
    return ["--arquitetura", m["arquitetura"], "--ckpt", m.get("ckpt", ""), "--vocab", m.get("vocab", "")]


def _subir(m: dict) -> str:
    """O motor de pé para o modelo `m` (caminhos do Windows); devolve o nome do processo no runner. O script vai para
    {appdata}/tts/.forja (o Windows não enxerga o código do container), como o comfy_job.py da ampliação."""
    chave = (m["motor"], json.dumps(_argv(m)))
    if _motor.get("chave") == chave and _vivo():
        return _motor["nome"]
    descarregar()
    if not instalado(m["motor"]):
        raise ToolError(f"Falta o motor {MOTORES[m['motor']]['nome']}: instale pelo Forja Desktop desta máquina, em "
                        "Configurações › Runtime › Motor de voz.")
    script = _pasta(".forja") / MOTORES[m["motor"]]["script"]
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(Path(__file__).with_name(MOTORES[m["motor"]]["script"]).read_bytes())
    fila = _pasta(f"fila-{m['motor']}")
    fila.mkdir(parents=True, exist_ok=True)
    for velho in fila.glob("*.json"):
        velho.unlink()  # pedido de um processo anterior que morreu antes de pegar
    nome = f"{PREFIXO}{m['motor']}-{uuid.uuid4().hex[:6]}"
    hf = f"{imagegen._appdata()}/runtimes/tts/hf"
    cmd = imagegen._comando([_py(m["motor"]), "-X", "utf8", "-s", _h(script), *_argv(m), "--fila", _h(fila),
                             "--ocioso", str(OCIOSO)])
    if (runner.current() or {}).get("shell") in ("powershell", "pwsh"):
        token = hf_headers().get("Authorization", "")[7:]
        cmd = f"$env:HF_HUB_CACHE='{hf}'; " + (f"$env:HF_TOKEN='{token}'; " if token else "") + cmd
    try:
        runner.serve_start(nome, cmd, f"{imagegen._appdata()}/runtimes/tts/{m['motor']}")
    except runner.RunnerError as e:
        raise ToolError(str(e)) from e
    _motor.update(nome=nome, chave=chave, modelo=m["nome"], dispositivo="", ultimo=time.time(), ocupado=True,
                  fila=fila, lidas=0)
    return nome


def _acompanhar(nome: str, mid: int, fim: tuple[str, ...], depois: str, voz: str, job_id: str, limite_s: int) -> str:
    """Lê o log do motor pelo runner até uma linha de `fim` vinda depois da linha `depois` ("" = desde o começo),
    levando FASE/PROGRESSO à mensagem. Processo morto, cancelado ou sem resposta em `limite_s` acaba a espera."""
    t0, vistas = time.monotonic(), 0
    while time.monotonic() - t0 < limite_s:
        time.sleep(0.5)
        if job_id and downloads.cancelled(job_id):
            return "ERRO Cancelado."
        try:
            linhas = runner.serve_log(nome, 500).splitlines()
            vivo = any(s.get("name") == nome and s.get("alive") for s in runner.servers())
        except runner.RunnerError as e:
            return f"ERRO {e}"
        if depois:
            i = max((k for k, l in enumerate(linhas) if l.strip() == depois), default=-1)
            if i < 0:
                if not vivo:
                    return "ERRO O motor de voz saiu antes de pegar o pedido. " + " | ".join(linhas[-6:])[-500:]
                continue
            linhas = linhas[i + 1:]
        for linha in linhas[vistas:]:
            linha = linha.strip()
            if linha.startswith("FASE "):
                _patch(mid, fase=linha[5:])
            elif linha.startswith("PROGRESSO "):
                try:
                    _patch(mid, progresso=float(linha.split()[1]))
                except (IndexError, ValueError):
                    pass
            elif linha.startswith("TRANSCRICAO ") and voz:
                _transcrita(voz, linha[12:])
            elif linha.startswith(fim):
                return linha
        vistas = len(linhas)
        if not vivo:
            return "ERRO O motor de voz saiu no meio. " + " | ".join(l for l in linhas[-6:] if l.strip())[-500:]
    return "ERRO O motor de voz não respondeu a tempo."


# ------------------------------------------------------------------ geração

def pendentes() -> list[int]:
    return [c for c, n in list(_rodando.items()) if n > 0]


def _patch(mid: int, **tts) -> None:
    with db.session() as s:
        m = s.get(db.Message, mid)
        if m:
            meta = dict(m.meta or {})
            meta["tts"] = {**meta.get("tts", {}), **tts}
            m.meta = meta
            if tts.get("estado") in ("pronto", "erro", "cancelado"):
                m.status = "pronto"
            s.commit()


PARAMS = {"f5": {"velocidade": 1.0, "passos": 32, "sem_silencio": False},
          "fish": {"temperatura": 1.0, "top_p": 0.9}}


def gerar(conv_id: int, body: dict) -> dict:
    texto = str(body.get("texto") or "").strip()
    if not texto:
        raise ToolError("Escreva o texto a falar.")
    m = next((x for x in modelos() if x["nome"] == body.get("modelo")), None)
    if not m:
        raise ToolError("Escolha um modelo (Modelos › Adicionar).")
    if not instalado(m["motor"]):
        raise ToolError(f"Falta o motor {MOTORES[m['motor']]['nome']}: instale pelo Forja Desktop desta máquina, em "
                        "Configurações › Runtime › Motor de voz.")
    v = next((x for x in vozes() if x["id"] == body.get("voz")), None)
    if not v:
        raise ToolError("Escolha uma voz de referência (Vozes › Adicionar).")
    params = {"modelo": m["nome"], "voz": v["nome"], "semente": int(body.get("semente", -1)),
              **{k: type(d)(body.get(k, d)) for k, d in PARAMS[m["motor"]].items()}}
    with db.session() as s:
        conv = s.get(db.Conversation, conv_id)
        if not conv or conv.kind != "tts":
            raise ToolError("Conversa de voz não encontrada.")
        if conv.title == "Nova conversa":
            conv.title = texto.splitlines()[0][:60]
        conv.updated_at = db._now()
        s.add(db.Message(conversation_id=conv_id, role="user", content=texto, meta={"tts": params}))
        a = db.Message(conversation_id=conv_id, role="assistant", content="", status="running",
                       meta={"tts": {**params, "estado": "na fila"}})
        s.add(a)
        s.commit()
        mid = a.id
    job = downloads.create("tts", f"Voz: {texto[:40]}")
    _rodando[conv_id] = _rodando.get(conv_id, 0) + 1
    _patch(mid, job=job["id"])
    threading.Thread(target=_rodar, args=(conv_id, mid, job, texto, m, v, params), daemon=True).start()
    return {"message_id": mid, "job": job["id"]}


def _rodar(conv_id: int, mid: int, job: dict, texto: str, m: dict, v: dict, p: dict) -> None:
    try:
        with _lock:
            if downloads.cancelled(job["id"]):
                raise ToolError("Cancelado.")
            _patch(mid, estado="gerando", fase="começando")
            m = _local(m, mid, job)
            novo = not (_motor.get("chave") == (m["motor"], json.dumps(_argv(m))) and _vivo())
            nome = _subir(m)
            _motor.update(ocupado=True)
            if novo:
                _patch(mid, fase="carregando o modelo")
                pronto = _acompanhar(nome, mid, ("PRONTO", "ERRO"), "", "", job["id"], 900)
                if not pronto.startswith("PRONTO"):
                    descarregar()
                    raise ToolError(pronto[5:])
                _motor["dispositivo"] = pronto[7:]
            saida = _pasta(f"saida-web/{conv_id}") / f"{mid}.wav"
            saida.parent.mkdir(parents=True, exist_ok=True)
            pid = f"{int(time.time() * 1000)}-{mid}"
            pedido = {**p, **{k: m[k] for k in ("minusculas", "numeros") if k in m}, "texto": texto,
                      "ref": v["caminho"], "ref_texto": v.get("texto", ""), "saida": _h(saida)}
            tmp = _motor["fila"] / f"{pid}.tmp"
            tmp.write_text(json.dumps(pedido, ensure_ascii=False), "utf-8")
            tmp.rename(tmp.with_suffix(".json"))  # inteiro de uma vez: o motor não lê pela metade
            linha = _acompanhar(nome, mid, ("OK", "ERRO"), f"PEDIDO {pid}", v["id"], job["id"], 3600)
            if downloads.cancelled(job["id"]):
                descarregar()  # o modelo sai junto; o próximo pedido carrega de novo
                raise ToolError("Cancelado.")
            if not linha.startswith("OK"):
                if "DEVICE_LOST" in linha or not _vivo():
                    descarregar()
                if "DEVICE_LOST" in linha:
                    raise ToolError("A GPU perdeu o contexto (device lost), provavelmente por falta de VRAM. O motor foi "
                                    "encerrado; feche o que estiver usando a GPU antes de tentar de novo.")
                raise ToolError(linha[5:])
            duracao, semente, segundos = linha.split()[1:4]
            _patch(mid, estado="pronto", arquivo=_h(saida), duracao=float(duracao), semente_usada=int(semente),
                   segundos=float(segundos), fase="", dispositivo=_motor.get("dispositivo", ""))
            downloads.finish(job["id"], result=_h(saida))
    except Exception as e:
        erro = str(e) if isinstance(e, ToolError) else f"{e.__class__.__name__}: {e}"
        if downloads.cancelled(job["id"]):
            erro = "Cancelado."
        _patch(mid, estado="cancelado" if erro == "Cancelado." else "erro", erro=erro, fase="")
        downloads.finish(job["id"], error=erro)
    finally:
        if _motor:
            _motor.update(ocupado=False, ultimo=time.time())
        _rodando[conv_id] = _rodando.get(conv_id, 1) - 1


def cancelar(mid: int) -> None:
    with db.session() as s:
        m = s.get(db.Message, mid)
        job = ((m.meta or {}).get("tts") or {}).get("job") if m else None
    if job:
        downloads.cancel(job)
