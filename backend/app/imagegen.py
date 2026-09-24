"""Geração de imagem no Forja em Docker, por dois motores.

- **runner**: o `sd-cli.exe` (stable-diffusion.cpp) roda no Windows do usuário, na GPU dele, pelo
  forja-runner (`/serve`, com log em arquivo que dá o progresso por passo). O container não enxerga
  a GPU; o runner enxerga.
- **api**: um provedor de nuvem com o endpoint OpenAI de imagens (`/images/generations` e
  `/images/edits`): OpenAI, Together, xAI, Gemini (camada de compatibilidade) e outros. O modelo
  aparece na lista como `api:<provedor>:<modelo>`.

Caminhos: tudo é guardado como o usuário vê no sistema dele (`C:/Users/...`), porque é o que o
`sd-cli` recebe. O container toca no disco pela montagem (`workspace.to_container`).

No desktop este módulo fala com o `localai` (runtime baixado, modelo na VRAM). Aqui a configuração
de imagem mora em `/data/imagens.json`; na primeira leitura ela é importada do `local.json` do
Forja Desktop, se ele estiver instalado nesta máquina, para aproveitar VAEs e modelos já ajustados.
"""
from __future__ import annotations

import asyncio
import base64
import fnmatch
import functools
import json
import mimetypes
import os
import re
import struct
import threading
import time
import uuid
from pathlib import Path

import httpx

from . import config, downloads, llm, runner, shell, uploads, workspace
from .tools import Tool, ToolError, register

CONFIG_FILE = config.DATA_DIR / "imagens.json"
PREFIXO = "forja-img-"  # nome dos processos no runner (a aba Instâncias esconde)
API = "api:"


class ModeloCarregado(ToolError):
    """Existe no desktop (LLM na VRAM). Aqui nunca é levantada; fica para as rotas serem iguais."""


# Barra de amostragem do sd.cpp: "  |=====>   | 3/8 - 11.5it/s". As barras de carregamento do modelo
# usam MB/s e ficam de fora — senão a barra da UI andaria para trás.
PROGRESS = re.compile(r"\|\s*(\d+)/(\d+) - ([\d.]+)\s*(it/s|s/it)")
TIMEOUT = 1800  # 30 min: CPU puro com modelo grande é lento mesmo
API_TIMEOUT = 300
SEM_PROJECAO = "No latent to RGB projection known"  # aviso do sd-cli, a cada passo
MAX_REFS = 10  # limite do Qwen-Image 2.1

DEFAULT_IMAGE = {
    "model": "", "vae": "", "clip_l": "", "t5xxl": "", "llm": "", "llm_vision": "", "diffusion_model": "",
    "steps": 20, "cfg": 7.0, "width": 512, "height": 512, "sampler": "euler_a", "negative": "",
    "seed": 0,      # 0 = aleatória
    "offload": False, "flash_attn": False, "vae_tiling": False,
    "te_cpu": "",   # "" (nunca), "gerar", "editar" ou "sempre": codificador de texto na CPU
    "preview": "",  # "" (automática), "none", "proj", "tae", "vae"
    "taesd": "",
    "out_dir": "",  # vazio = %APPDATA%/Forja/imagens do usuário (a mesma pasta do desktop)
    "descarte_dias": 7,
}
CAMINHOS_IMAGEM = ("model", "vae", "clip_l", "t5xxl", "llm", "llm_vision", "taesd", "diffusion_model", "out_dir")
IMAGE_PER_MODEL = ("steps", "cfg", "width", "height", "sampler", "negative", "vae", "clip_l", "t5xxl", "llm",
                   "llm_vision", "offload", "flash_attn", "vae_tiling", "te_cpu", "preview", "taesd")
COMPONENTES = ("vae", "clip_l", "t5xxl", "llm", "llm_vision", "taesd")
EXTENSOES_MODELO = (".gguf", ".safetensors", ".sft", ".ckpt")
EXTENSOES_PESO = (".gguf", ".safetensors", ".sft")

_cfg_lock = threading.Lock()
_busy = threading.Event()


# ------------------------------------------------------------------ caminhos

def _norm(p: str) -> str:
    """'c:\\Users\\x' -> 'C:/Users/x'. Caminho que não é absoluto volta como veio."""
    if not p or str(p).startswith(API):
        return str(p or "")
    try:
        return workspace.normalize(str(p))
    except workspace.WorkspaceError:
        return str(p)


def _chave(p: str) -> str:
    return _norm(p).lower()  # Windows não diferencia maiúsculas


def _c(p: str) -> Path:
    """O caminho do usuário visto de dentro do container."""
    try:
        return workspace.to_container(_norm(p))
    except workspace.WorkspaceError as e:
        raise ToolError(f"O container não enxerga {p}: monte o disco em HOST_MOUNTS (docker-compose). {e}") from e


def _existe(p: str) -> bool:
    try:
        return bool(p) and _c(p).is_file()
    except ToolError:
        return False


def _visivel(pasta: str) -> bool:
    """A pasta existe vista do container? Disco fora de HOST_MOUNTS não aparece."""
    try:
        return _c(pasta).is_dir()
    except ToolError:
        return False


def _home() -> str:
    """Pasta do usuário no sistema dele: a do runner; sem ele, deduzida de WORKSPACE_HOST (C:/Users/x/...)."""
    info = runner.current() or runner.refresh()
    if info and info.get("ok") and info.get("home"):
        return _norm(info["home"])
    m = re.match(r"^([A-Za-z]:/Users/[^/]+)", _norm(config.WORKSPACE_HOST or ""))
    return m.group(1) if m else ""


def _appdata() -> str:
    return f"{_home()}/AppData/Roaming/Forja" if _home() else ""


# ------------------------------------------------------------------ configuração

def _blank() -> dict:
    return {"image": dict(DEFAULT_IMAGE), "image_models": {}, "motor": {"sd_cli": "", "dirs": []},
            "api_models": [], "sem_proj": [], "referencias": [], "kinds": {}}


def _importa_desktop(out: dict) -> dict:
    """Primeira leitura: o que o Forja Desktop desta máquina já sabe (modelos, VAEs, sd-cli)."""
    base = _appdata()
    if not base:
        return out
    try:
        data = json.loads(_c(f"{base}/local.json").read_text("utf-8"))
    except (OSError, ValueError, ToolError):
        data = {}
    img = {k: v for k, v in (data.get("image") or {}).items() if k in DEFAULT_IMAGE}
    out["image"] = {**out["image"], **{k: (_norm(v) if k in CAMINHOS_IMAGEM else v) for k, v in img.items()}}
    out["image_models"] = {_norm(k): {c: (_norm(v) if c in CAMINHOS_IMAGEM else v) for c, v in (p or {}).items()}
                           for k, p in (data.get("image_models") or {}).items()}
    pastas = [data.get("models_dir") or f"{_home()}/Forja/modelos", *(data.get("dirs") or [])]
    out["motor"]["dirs"] = list(dict.fromkeys(_norm(d) for d in pastas if d))
    escolhido = (data.get("runtime") or {}).get("sd") or ""
    for backend in dict.fromkeys([escolhido, "cuda", "vulkan", "cpu"]):
        for nome in ("sd-cli.exe", "sd.exe"):
            if backend and _existe(f"{base}/runtimes/sd/{backend}/{nome}"):
                out["motor"]["sd_cli"] = f"{base}/runtimes/sd/{backend}/{nome}"
                return out
    return out


def read_config() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        data = None
    out = _blank()
    if data is None:
        out = _importa_desktop(out)
        write_config(out)
        return out
    out.update({k: v for k, v in data.items() if k in out})
    out["image"] = {**DEFAULT_IMAGE, **(out.get("image") or {})}
    out["motor"] = {**_blank()["motor"], **(out.get("motor") or {})}
    return out


def write_config(data: dict) -> dict:
    with _cfg_lock:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(CONFIG_FILE)
    return data


def _image_valores(patch: dict) -> dict:
    """Só as chaves conhecidas, no tipo certo e com a barra normalizada."""
    out: dict = {}
    for k, default in DEFAULT_IMAGE.items():
        if k not in patch:
            continue
        try:
            out[k] = type(default)(patch[k])
        except (TypeError, ValueError):
            raise ToolError(f"Valor inválido para '{k}': {patch[k]!r}")
        if k in CAMINHOS_IMAGEM and out[k]:
            out[k] = _norm(out[k])
    return out


def set_image(patch: dict) -> dict:
    """Padrões da aba Imagens (modelo, passos, tamanho, pasta...)."""
    data = read_config()
    img = {**DEFAULT_IMAGE, **data["image"], **_image_valores(patch)}
    if img["out_dir"]:
        try:
            _c(img["out_dir"]).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ToolError(f"Não deu para usar a pasta '{img['out_dir']}': {e}")
    data["image"] = img
    write_config(data)
    return img


def set_motor(patch: dict) -> dict:
    """sd-cli, pastas de modelos e modelos de nuvem."""
    data = read_config()
    motor = dict(data["motor"])
    if "sd_cli" in patch:
        motor["sd_cli"] = _norm(str(patch["sd_cli"] or "").strip().strip('"'))
        if motor["sd_cli"] and not _existe(motor["sd_cli"]):
            raise ToolError(f"sd-cli não encontrado: {motor['sd_cli']}")
    if "dirs" in patch:
        motor["dirs"] = list(dict.fromkeys(_norm(str(d).strip().strip('"')) for d in patch["dirs"] or [] if str(d).strip()))
    data["motor"] = motor
    if "api_models" in patch:
        lista = []
        for m in patch["api_models"] or []:
            provider, model = str(m.get("provider") or ""), str(m.get("model") or "").strip()
            if not model:
                continue
            if config.PROVIDERS.get(provider, {}).get("type") != "openai":
                raise ToolError(f"'{provider}' não é um provedor OpenAI-compatível (Configurações › Provedores).")
            lista.append({"provider": provider, "model": model})
        data["api_models"] = lista
    write_config(data)
    _scan.cache_clear()
    return data


OUT_PADRAO = "Forja/imagens"


def out_dir() -> str:
    """Pasta das imagens, como o usuário a vê."""
    escolhida = read_config()["image"].get("out_dir")
    if escolhida:
        return escolhida
    if _appdata():
        return f"{_appdata()}/imagens"  # a mesma do desktop: as duas versões mostram a mesma galeria
    raise ToolError("Defina a pasta das imagens nos ajustes da aba Imagens.")


# ------------------------------------------------------------------ GGUF (cabeçalho)
# Mesma leitura do localai.py do desktop, só o necessário para imagem: arquitetura e se é LLM.

_FIXED = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_UNPACK = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


def _string(f) -> str:
    return f.read(struct.unpack("<Q", f.read(8))[0]).decode("utf-8", "replace")


def _value(f, vtype: int):
    if vtype == 8:
        return _string(f)
    if vtype == 9:
        itype, count = struct.unpack("<IQ", f.read(12))
        return [_value(f, itype) for _ in range(count)]
    fmt = _UNPACK.get(vtype)
    if not fmt:
        raise ValueError(f"tipo GGUF desconhecido: {vtype}")
    return struct.unpack(fmt, f.read(_FIXED[vtype]))[0]


@functools.lru_cache(maxsize=64)
def _gguf_kv(path: str, _stamp: tuple) -> dict:
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError("não é um arquivo GGUF")
        f.read(4)
        _, n_kv = struct.unpack("<QQ", f.read(16))
        kv = {}
        for _ in range(n_kv):
            key = _string(f)
            value = _value(f, struct.unpack("<I", f.read(4))[0])
            if not (isinstance(value, list) and len(value) > 64):  # listas de tokens não interessam
                kv[key] = value
    return kv


def gguf_info(path: str) -> dict:
    """{"arch", "llm"} do .gguf (caminho do usuário). Vazio quando não dá para ler."""
    try:
        f = _c(path)
        st = f.stat()
        kv = _gguf_kv(str(f), (st.st_size, int(st.st_mtime)))
    except (OSError, ValueError, struct.error, ToolError):
        return {"arch": "", "llm": False}
    arch = str(kv.get("general.architecture") or "")
    return {"arch": arch, "llm": bool(kv.get(f"{arch}.block_count") and kv.get(f"{arch}.attention.head_count"))}


# ------------------------------------------------------------------ requisitos por arquitetura
# Copiado do localai.py do desktop: valores e links dos docs do sd.cpp.

_SDDOC = "https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/"
REQUISITOS = {
    "qwen_image21": {
        "nome": "Qwen-Image 2.1", "doc": _SDDOC + "qwen_image_2.1.md",
        "precisa": {"vae": ("qwen_image_2.1_vae_bf16.safetensors (o VAE do Qwen-Image 1.0 não serve)",
                            "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/tree/main/vae"),
                    "llm": ("Qwen3-VL-8B-Instruct, GGUF (ex.: Q4_K_M) ou safetensors",
                            "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/tree/main")},
        "edita": {"llm_vision": ("mmproj-Qwen3VL-8B-Instruct-F16.gguf (só se o codificador for GGUF)",
                                 "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/tree/main")},
        "sem_proj": True,
        "sugere": {"sampler": "euler", "cfg": 6.0, "width": 1024, "height": 1024, "steps": 20,
                   "offload": True, "flash_attn": True, "vae_tiling": True}},
    "qwen_image": {
        "nome": "Qwen-Image", "doc": _SDDOC + "qwen_image.md",
        "precisa": {"vae": ("qwen_image_vae.safetensors",
                            "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/tree/main/split_files/vae"),
                    "llm": ("Qwen2.5-VL-7B-Instruct, GGUF",
                            "https://huggingface.co/mradermacher/Qwen2.5-VL-7B-Instruct-GGUF/tree/main")},
        "sugere": {"sampler": "euler", "cfg": 2.5, "width": 1024, "height": 1024, "steps": 20,
                   "offload": True, "flash_attn": True, "vae_tiling": True}},
    "flux": {
        "nome": "Flux", "doc": _SDDOC + "flux.md",
        "precisa": {"vae": ("ae.safetensors", "https://huggingface.co/black-forest-labs/FLUX.1-schnell/tree/main"),
                    "clip_l": ("clip_l.safetensors", "https://huggingface.co/comfyanonymous/flux_text_encoders/tree/main"),
                    "t5xxl": ("t5xxl_fp16.safetensors (ou fp8)",
                              "https://huggingface.co/comfyanonymous/flux_text_encoders/tree/main")},
        "sugere": {"sampler": "euler", "cfg": 1.0, "width": 1024, "height": 1024, "steps": 20,
                   "offload": True, "flash_attn": True, "vae_tiling": True}},
}
PADROES = {
    "qwen_image21": {"vae": ["*qwen*image*2.1*vae*"], "llm": ["*qwen3*vl*8b*"], "llm_vision": ["mmproj*qwen3*vl*8b*"]},
    "qwen_image": {"vae": ["*qwen*image*vae*"], "llm": ["*qwen2.5*vl*7b*"], "llm_vision": ["mmproj*qwen2.5*vl*7b*"]},
    "flux": {"vae": ["ae.safetensors", "*flux*vae*", "*flux*ae.safetensors"], "clip_l": ["clip_l*"], "t5xxl": ["t5xxl*"],
             "taesd": ["taef1*"]},
}
ROTULO_ARQUIVO = {"vae": "VAE", "llm": "Codificador LLM", "llm_vision": "Visão do LLM (mmproj)",
                  "clip_l": "clip_l", "t5xxl": "t5xxl", "taesd": "TAESD"}
BUSCA_PROFUNDIDADE, BUSCA_ANCESTRAIS, BUSCA_TETO = 3, 3, 20000


def requisitos(path: str) -> dict | None:
    if not str(path).lower().endswith(".gguf"):
        return None
    return REQUISITOS.get(gguf_info(path)["arch"])


def faltando(path: str, o: dict, editar: bool = False) -> list[str]:
    """Chaves obrigatórias sem arquivo: vazias ou apontando para caminho que não existe."""
    req = requisitos(path) or {"precisa": {}}
    chaves = list(req["precisa"])
    llm_ = str(o.get("llm") or "").lower()
    if editar and (not llm_ or llm_.endswith(".gguf")):
        chaves += list(req.get("edita") or {})
    return [k for k in chaves if not _existe(o.get(k) or "")]


def achar_arquivos(path: str) -> dict[str, list[str]]:
    """Candidatos a VAE/codificador/mmproj do modelo, procurando pelo nome perto dele e nas pastas."""
    padroes = PADROES.get(gguf_info(path)["arch"]) if str(path).lower().endswith(".gguf") else None
    if not padroes:
        return {}
    pasta = _norm(path).rsplit("/", 1)[0]
    acima = [pasta.rsplit("/", i)[0] for i in range(1, BUSCA_ANCESTRAIS + 1) if pasta.count("/") >= i]
    raizes = list(dict.fromkeys([pasta, *acima, *read_config()["motor"]["dirs"]]))
    achados: dict[str, list[str]] = {k: [] for k in padroes}
    vistos: set[str] = set()
    olhados = 0
    for raiz in raizes:
        try:
            base_c = _c(raiz)
        except ToolError:
            continue
        base = len(base_c.parts)
        for atual, subpastas, arquivos in os.walk(base_c):
            if len(Path(atual).parts) - base >= BUSCA_PROFUNDIDADE:
                subpastas[:] = []
            for nome in arquivos:
                olhados += 1
                if olhados > BUSCA_TETO:
                    return achados
                baixo = nome.lower()
                if not baixo.endswith(EXTENSOES_PESO):
                    continue
                rel = Path(atual, nome).relative_to(base_c).as_posix()
                completo = f"{raiz.rstrip('/')}/{rel}"
                if _chave(completo) in vistos or _chave(completo) == _chave(path):
                    continue
                for k, globs in padroes.items():
                    if baixo.startswith("mmproj") != (k == "llm_vision"):
                        continue
                    if any(fnmatch.fnmatch(baixo, g) for g in globs):
                        achados[k].append(completo)
                        vistos.add(_chave(completo))
                        break
    return achados


def image_params(path: str) -> dict:
    cfg = read_config()
    salvo = (cfg.get("image_models") or {}).get(_norm(path)) or {}
    return {**{k: cfg["image"][k] for k in IMAGE_PER_MODEL}, **salvo}


def save_image_params(path: str, patch: dict) -> dict:
    """Guarda só o que sai do padrão geral da aba Imagens."""
    data = read_config()
    base = data["image"]
    limpo = {k: v for k, v in _image_valores(patch).items() if k in IMAGE_PER_MODEL}
    modelos = dict(data.get("image_models") or {})
    fora = {k: v for k, v in {**(modelos.get(_norm(path)) or {}), **limpo}.items() if v != base[k]}
    modelos[_norm(path)] = fora
    data["image_models"] = modelos
    write_config(data)
    _scan.cache_clear()
    return image_params(path)


def sem_proj(path: str) -> bool:
    return bool((requisitos(path) or {}).get("sem_proj")) or _chave(path) in (read_config().get("sem_proj") or [])


def marcar_sem_proj(path: str) -> None:
    if sem_proj(path):
        return
    data = read_config()
    data["sem_proj"] = [*(data.get("sem_proj") or []), _chave(path)]
    write_config(data)


def _acompanhantes(cfg: dict) -> set[str]:
    """Arquivos já configurados como VAE/codificador de algum modelo: não são modelos de imagem."""
    ajustes = [cfg.get("image") or {}, *(cfg.get("image_models") or {}).values()]
    return {_chave(a[k]) for a in ajustes for k in COMPONENTES if a.get(k)}


SHARD = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)


@functools.lru_cache(maxsize=1)
def _scan(_tick: int) -> tuple[dict, ...]:
    """Modelos de difusão nas pastas. A montagem do Windows é lenta: cache de 15 s (o `_tick`)."""
    cfg = read_config()
    comp = _acompanhantes(cfg)
    achados: dict[str, dict] = {}
    for pasta in cfg["motor"]["dirs"]:
        try:
            raiz = _c(pasta)
        except ToolError:
            continue
        if not raiz.is_dir():
            continue
        for f in sorted(raiz.rglob("*")):
            baixo = f.name.lower()
            if f.suffix.lower() not in EXTENSOES_MODELO or baixo.startswith("mmproj") or not f.is_file():
                continue
            m = SHARD.search(f.name)
            if m and m.group(1) != "00001":
                continue
            host = f"{pasta.rstrip('/')}/{f.relative_to(raiz).as_posix()}"
            if _chave(host) in comp or (f.suffix.lower() == ".gguf" and gguf_info(host)["llm"]):
                continue  # VAE/codificador configurado, ou um LLM (o .gguf do chat)
            try:
                st = f.stat()
            except OSError:
                continue
            achados[_chave(host)] = {"path": host, "name": f.stem, "size": st.st_size, "mtime": st.st_mtime,
                                     "shards": int(m.group(2)) if m else 1, "folder": pasta, "kind": "image",
                                     "motor": "runner"}
    return tuple(sorted(achados.values(), key=lambda x: x["name"].lower()))


def modelos() -> list[dict]:
    """Os da aba Imagens: arquivos locais (runner) e os de nuvem (api), com os ajustes de cada um."""
    cfg = read_config()
    out = []
    for m in _scan(int(time.time() // 15)):
        p = image_params(m["path"])
        out.append({**m, "params": p, "req": requisitos(m["path"]), "previa_auto": previa_automatica(m["path"], p),
                    "falta": faltando(m["path"], p), "falta_edicao": faltando(m["path"], p, editar=True)})
    nomes = {p["id"]: p["name"] for p in config.PROVIDERS.values()} if isinstance(config.PROVIDERS, dict) else {}
    for a in cfg.get("api_models") or []:
        path = f"{API}{a['provider']}:{a['model']}"
        out.append({"path": path, "name": f"{a['model']} · {nomes.get(a['provider'], a['provider'])}", "size": 0,
                    "mtime": 0, "shards": 1, "folder": "nuvem", "kind": "image", "motor": "api",
                    "params": image_params(path), "req": None, "falta": [], "falta_edicao": []})
    return out


# ------------------------------------------------------------------ opções e argv do sd-cli

def _opts(patch: dict | None = None) -> dict:
    """Padrão da aba Imagens + ajustes daquele modelo + o que veio na chamada."""
    base = read_config()["image"]
    alvo = (patch or {}).get("model") or base.get("model", "")
    do_modelo = image_params(alvo) if alvo else {}
    return {**base, **do_modelo, **{k: v for k, v in (patch or {}).items() if v not in (None, "")}}


def _flag_modelo(path: str) -> str:
    """GGUF com arquitetura (flux, qwen_image...) é só o unet: vai em --diffusion-model."""
    # ponytail: heurística pelo metadado, igual ao desktop
    if path.lower().endswith(".gguf") and gguf_info(path)["arch"]:
        return "--diffusion-model"
    return "-m"


def _confere_arquivos(model: str, o: dict, refs: list[str]) -> None:
    """Barra antes de rodar: sem VAE/codificador o sd-cli falha com erro que ninguém entende."""
    req = requisitos(model) or {}
    if refs and not req.get("edita"):
        editam = ", ".join(r["nome"] for r in REQUISITOS.values() if r.get("edita"))
        raise ToolError(f"{Path(model).stem} não edita imagem (só gera). Edição funciona com: {editam}.")
    if len(refs) > MAX_REFS:
        raise ToolError(f"No máximo {MAX_REFS} imagens de referência (máscaras incluídas); vieram {len(refs)}.")
    for r in refs:
        if not _existe(r):
            raise ToolError(f"Imagem de referência não encontrada: {r}\nEla foi movida, renomeada ou apagada: "
                            "anexe de novo (Reanexar, na miniatura).")
    falta = faltando(model, o, editar=bool(refs))
    if falta:
        arquivos = {**req.get("precisa", {}), **req.get("edita", {})}
        itens = "\n".join(f"- {ROTULO_ARQUIVO[k]}: {arquivos[k][0]} — {arquivos[k][1]}" for k in falta)
        raise ToolError(f"{req.get('nome')} precisa de arquivos que não estão configurados (ou não existem):\n"
                        f"{itens}\nInforme os caminhos em Imagens › Motor e modelos. Guia: {req.get('doc')}")


def previa_automatica(model: str, o: dict) -> str:
    if o.get("taesd"):
        return "tae"
    return "vae" if sem_proj(model) else "proj"


def modo_previa(o: dict) -> str | None:
    """O --preview que vai para o sd-cli, ou None (modelo de nuvem não tem prévia)."""
    alvo = str(o.get("model") or o.get("diffusion_model") or "")
    if alvo.startswith(API):
        return None
    modo = o.get("preview") or previa_automatica(alvo, o)
    if modo == "none" or (modo == "tae" and not o.get("taesd")):
        return None
    return modo


def argv(exe: str, prompt: str, out: str, o: dict, refs: list[str] | tuple = ()) -> list[str]:
    """Argumentos do sd-cli, com os caminhos do sistema do usuário (é lá que ele roda)."""
    a = [exe, "-p", prompt, "-o", str(out),
         "--steps", str(int(o["steps"])), "--cfg-scale", str(float(o["cfg"])),
         "-W", str(int(o["width"])), "-H", str(int(o["height"])), "--sampling-method", str(o["sampler"])]
    if o.get("diffusion_model"):
        a += ["--diffusion-model", str(o["diffusion_model"])]
    elif o.get("model"):
        _confere_arquivos(str(o["model"]), o, list(refs))
        a += [_flag_modelo(str(o["model"])), str(o["model"])]
    else:
        raise ToolError("Escolha um modelo de imagem nos ajustes da aba Imagens.")
    for key, flag in (("vae", "--vae"), ("clip_l", "--clip_l"), ("t5xxl", "--t5xxl"), ("llm", "--llm")):
        if o.get(key):
            a += [flag, str(o[key])]
    if refs:
        for r in refs:
            a += ["-r", str(r)]
        if o.get("llm_vision"):
            a += ["--llm_vision", str(o["llm_vision"])]
    if o.get("offload"):
        a += ["--offload-to-cpu"]
    if o.get("flash_attn"):
        a += ["--diffusion-fa"]
    if o.get("vae_tiling"):
        a += ["--vae-tiling"]
    if o.get("te_cpu") in ("sempre", "editar" if refs else "gerar"):
        a += ["--backend", f"{_gpu(exe)},te=cpu"]
    modo = modo_previa(o) if o.get("_preview") else None
    if modo:
        a += ["--preview", modo, "--preview-path", str(o["_preview"])]
        if modo == "tae":
            a += ["--taesd", str(o["taesd"]), "--taesd-preview-only"]
    if o.get("negative"):
        a += ["-n", str(o["negative"])]
    a += ["-s", str(int(o["seed"])) if int(o.get("seed") or 0) else "-1"]
    return a


def escolhe_gpu(listagem: str) -> str:
    """Nome da GPU dedicada na saída do `sd-cli --list-devices` (a que não é memória unificada)."""
    nomes = [m.group(1).lower() for m in re.finditer(r"^((?:vulkan|cuda)\d+)\t", listagem, re.M | re.I)]
    integradas = {f"vulkan{n}" for n in re.findall(r"^ggml_vulkan: (\d+) = .*\| uma: 1", listagem, re.M)}
    dedicadas = [n for n in nomes if n not in integradas]
    return (dedicadas or nomes or ["cpu"])[0]


def _comando(a: list[str]) -> str:
    """A linha de comando para o shell do runner (PowerShell no Windows: `& 'exe' 'arg'...`)."""
    texto = " ".join(shell.quote(x, "host") for x in a)
    return f"& {texto}" if (runner.current() or {}).get("shell") in ("powershell", "pwsh") else texto


def _pasta(p: str) -> str:
    return _norm(p).rsplit("/", 1)[0]


@functools.lru_cache(maxsize=4)  # ponytail: GPU trocada só vale depois de reiniciar o backend
def _gpu(exe: str) -> str:
    try:
        r = runner.run(_comando([exe, "--list-devices"]), _pasta(exe), 60)
    except runner.RunnerError:
        return "cpu"
    return escolhe_gpu(r.get("output") or "")


def _exe() -> str:
    """O sd-cli do usuário. Precisa do runner: é ele que executa no Windows."""
    exe = read_config()["motor"]["sd_cli"]
    if not exe:
        raise ToolError("Informe o caminho do sd-cli.exe em Imagens › Motor e modelos (ou use um modelo de nuvem).")
    if not _existe(exe):
        raise ToolError(f"sd-cli não encontrado: {exe}")
    if not (runner.refresh() or {}).get("ok"):
        raise ToolError("O forja-runner está desligado: ele é quem roda o sd-cli na sua GPU. Inicie "
                        "tools/forja-picker.cmd (ou tools/forja_runner.py) e tente de novo.")
    return exe


def valida(prompt: str, o: dict, refs: list[str]) -> None:
    """Confere tudo antes de enfileirar: motor, modelo, arquivos, prompt."""
    if not prompt.strip():
        raise ToolError("Descreva a imagem (prompt vazio).")
    model = str(o.get("model") or "")
    if model.startswith(API):
        _api_de(model)
        if len(refs) > MAX_REFS:
            raise ToolError(f"No máximo {MAX_REFS} imagens de referência.")
        return
    argv(_exe(), prompt, "x.png", o, refs)


def set_image_busy(v: bool) -> None:
    (_busy.set if v else _busy.clear)()


def image_busy() -> bool:
    return _busy.is_set()


# ------------------------------------------------------------------ motores

def generate(prompt: str, out: str, opts: dict | None = None, job_id: str = "",
             refs: list[str] | tuple = (), progresso=None, previa: str | None = None) -> str:
    """Gera até o fim (bloqueante: quem chama usa thread) e devolve `out` (caminho do usuário).

    `progresso(passo, total, s_passo)` a cada passo da amostragem (só no runner). `previa`: onde o
    sd-cli grava a prévia de cada passo, se o modelo tiver o modo de prévia ligado."""
    if not prompt.strip():
        raise ToolError("Descreva a imagem (prompt vazio).")
    o = {**_opts(opts), "_preview": previa} if previa else _opts(opts)
    _c(out).parent.mkdir(parents=True, exist_ok=True)
    if str(o.get("model") or "").startswith(API):
        return _gerar_api(prompt, out, o, list(refs), job_id)
    return _gerar_runner(prompt, out, o, list(refs), job_id, progresso)


def _gerar_runner(prompt: str, out: str, o: dict, refs: list[str], job_id: str, progresso) -> str:
    exe = _exe()
    nome = f"{PREFIXO}{uuid.uuid4().hex[:10]}"
    try:
        runner.serve_start(nome, _comando(argv(exe, prompt, out, o, refs)), _pasta(exe))
    except runner.RunnerError as e:
        raise ToolError(str(e)) from e
    limite = time.monotonic() + TIMEOUT
    visto = (-1, -1)
    log, info = "", {}
    try:
        while True:
            time.sleep(0.5)
            try:
                info = next((s for s in runner.servers() if s.get("name") == nome), {})
                log = runner.serve_log(nome, 60)
            except runner.RunnerError as e:
                raise ToolError(str(e)) from e
            passos = [m for m in PROGRESS.finditer(log) if int(m.group(2)) == int(o["steps"])]
            if passos and (int(passos[-1].group(1)), int(passos[-1].group(2))) != visto:
                m = passos[-1]
                visto = (int(m.group(1)), int(m.group(2)))
                if job_id:
                    downloads.update(job_id, done=visto[0], total=visto[1])
                if progresso:
                    v = float(m.group(3))
                    progresso(visto[0], visto[1], v if m.group(4) == "s/it" else (1 / v if v else 0.0))
            if not info.get("alive"):
                break
            if (job_id and downloads.cancelled(job_id)) or time.monotonic() > limite:
                break
    finally:
        try:
            runner.serve_stop(nome)  # mata se ainda estiver rodando e tira da lista do runner
        except runner.RunnerError:
            pass
    if SEM_PROJECAO in log and modo_previa(o) == "proj":
        marcar_sem_proj(str(o.get("model") or o.get("diffusion_model")))
    if job_id and downloads.cancelled(job_id):
        raise ToolError("Geração cancelada.")
    if info.get("alive"):
        raise ToolError(f"sd passou de {TIMEOUT // 60} min e foi encerrado.")
    if info.get("exit_code") != 0 or not _existe(out):
        cauda = "\n".join(log.splitlines()[-12:])
        dica = ""
        if "DeviceLost" in cauda or "OutOfDeviceMemory" in cauda or "out of memory" in cauda.lower():
            dica = ("A GPU ficou sem memória. Nos ajustes deste modelo ligue \"Pesos na RAM\", \"Flash "
                    "attention\" e \"VAE em blocos\", ou diminua a resolução.\n\n")
        raise ToolError(f"{dica}sd falhou (código {info.get('exit_code')}):\n{cauda}")
    return out


def _api_de(model: str) -> tuple[str, str]:
    try:
        _, provider, nome = model.split(":", 2)
    except ValueError:
        raise ToolError(f"Modelo de nuvem inválido: {model}") from None
    if provider not in config.PROVIDERS:
        raise ToolError(f"Provedor '{provider}' não existe mais (Configurações › Provedores).")
    return provider, nome


def _gerar_api(prompt: str, out: str, o: dict, refs: list[str], job_id: str) -> str:
    """Endpoint OpenAI de imagens. Negativo, semente e passos não existem nele: ficam de fora."""
    provider, nome = _api_de(str(o["model"]))
    url, hdr = llm.base_url(provider), llm.headers(provider)
    tamanho = f"{int(o['width'])}x{int(o['height'])}"

    def pede(com_tamanho: bool) -> httpx.Response:
        campos = {"model": nome, "prompt": prompt, "n": 1, **({"size": tamanho} if com_tamanho else {})}
        with httpx.Client(timeout=API_TIMEOUT, headers=hdr) as c:
            if not refs:
                return c.post(f"{url}/images/generations", json=campos)
            chave = "image[]" if len(refs) > 1 else "image"
            arquivos = [(chave, (Path(r).name, _c(r).read_bytes(), mimetypes.guess_type(r)[0] or "image/png"))
                        for r in refs]
            return c.post(f"{url}/images/edits", data={k: str(v) for k, v in campos.items()}, files=arquivos)

    if job_id and downloads.cancelled(job_id):
        raise ToolError("Geração cancelada.")
    try:
        r = pede(True)
        # Cada provedor aceita tamanhos diferentes (gpt-image: 1024x1024, 1536x1024...): sem o tamanho,
        # vale o padrão dele, melhor que falhar.
        if r.status_code == 400 and "size" in r.text.lower():
            r = pede(False)
        if r.status_code >= 400:
            raise ToolError(f"{provider} respondeu HTTP {r.status_code}: {r.text[:500]}")
        item = (r.json().get("data") or [{}])[0]
        if item.get("b64_json"):
            dados = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            dados = httpx.get(item["url"], timeout=API_TIMEOUT).content
        else:
            raise ToolError(f"{provider} não devolveu imagem: {r.text[:300]}")
    except httpx.HTTPError as e:
        raise ToolError(f"{provider} inacessível: {e}") from e
    _c(out).write_bytes(dados)
    return out


# ------------------------------------------------------------------ estado para a tela

def estado() -> dict:
    cfg = read_config()
    info = runner.refresh() or {}
    try:
        pasta = out_dir()
    except ToolError:
        pasta = ""
    motor = cfg["motor"]
    sd_ok = bool(motor["sd_cli"]) and _existe(motor["sd_cli"])
    lista = modelos()
    return {
        "image": cfg["image"], "image_models": lista, "image_dir": pasta, "image_busy": image_busy(),
        # os campos que a tela do desktop lê do /api/local: aqui não há LLM local nem runtime baixado
        "runtimes": {"sd": {"installed": bool(lista) or sd_ok}}, "server": {"running": False},
        "motor": {**motor, "sd_ok": sd_ok, "runner": bool(info.get("ok")), "runner_label": runner.describe(info),
                  "invisiveis": [d for d in motor["dirs"] if not _visivel(d)],
                  "api_models": cfg.get("api_models") or [],
                  "providers": [{"id": p["id"], "name": p["name"]} for p in config.PROVIDERS.values()
                                if p.get("type") == "openai"]},
    }


# ------------------------------------------------------------------ ferramenta do agente

def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


def _disponivel() -> bool:
    try:
        cfg = read_config()
    except Exception:
        return False
    return bool(cfg["image"].get("model") and (cfg.get("api_models") or cfg["motor"]["sd_cli"]))


async def image_generate(root: Path, args: dict) -> dict:
    set_image_busy(True)
    prompt = str(args.get("prompt") or "")
    opts = {k: args.get(k) for k in ("negative", "steps", "width", "height", "seed")}
    refs = [workspace.to_host((root / r).resolve()) or str((root / r).resolve()) for r in (args.get("refs") or [])]
    out = f"{out_dir()}/.agente/{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}.png"
    try:
        await asyncio.to_thread(generate, prompt, out, opts, "", refs)
    finally:
        set_image_busy(False)
    dados = _c(out).read_bytes()
    _c(out).unlink(missing_ok=True)
    att = uploads.save("imagem.png", dados, "image/png", root)
    return {"text": f"Imagem gerada em {att['path']} ({len(dados) // 1024} KB).", "attachments": [att]}


def _preview(_root: Path, args: dict) -> dict:
    return {"kind": "new", "path": "imagem.png", "text": str(args.get("prompt") or "")}


register(Tool(
    "image_generate",
    "Gera uma imagem a partir de uma descrição, com o modelo padrão da aba Imagens (stable-diffusion.cpp "
    "na GPU do usuário, pelo forja-runner, ou um modelo de nuvem). A imagem é salva na pasta de trabalho "
    "e aparece no chat. Use quando o usuário pedir uma imagem, ilustração ou arte.",
    _obj({"prompt": {"type": "string", "description": "Descrição da imagem, em inglês funciona melhor"},
          "negative": {"type": "string", "description": "O que evitar na imagem"},
          "steps": {"type": "integer", "description": "Passos de amostragem (padrão: o da aba Imagens)"},
          "width": {"type": "integer"}, "height": {"type": "integer"},
          "seed": {"type": "integer", "description": "Semente para repetir a mesma imagem"},
          "refs": {"type": "array", "items": {"type": "string"},
                   "description": "Imagens a editar (caminhos na pasta de trabalho). Com isso o prompt "
                                  "descreve a edição. Só em modelos que editam (Qwen-Image 2.1, ou um "
                                  "modelo de nuvem com edição). Até 10."}},
         ["prompt"]),
    image_generate, mutating=True, preview=_preview, timeout=None,  # geração longa, com progresso próprio
    available=_disponivel))
