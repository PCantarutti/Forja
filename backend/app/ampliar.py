"""Ampliação de imagem: ESRGAN pelo sd-cli (`-M upscale`) no runner, SeedVR2 pelo ComfyUI portátil (também no
runner), ou Lanczos (Pillow) aqui no container.

Mesmo desenho do desktop (forja-desktop/backend/app/ampliar.py e comfy.py), só a parte de imagem: vídeo precisa
de ffmpeg, que o Docker não tem. Os caminhos são os do sistema do usuário; o container lê e grava pela montagem
(`imagegen._c`). O ESRGAN que o sd.cpp roda é o RRDBNet, nos dois jeitos de nomear as camadas; o x2plus entra
com pixel-unshuffle e o sd.cpp recusa. A escala sai dos pesos e é medida na saída. O ComfyUI não é baixado
aqui (o web não baixa runtime): é o que o Forja Desktop instalou em `%APPDATA%/Forja/runtimes/comfy`.
"""
from __future__ import annotations

import functools
import json
import re
import struct
import time
import uuid
import zipfile
from pathlib import Path

from . import downloads, imagegen, runner
from .imagegen import _c, _existe
from .tools import ToolError

EXT_IMAGEM = (".png", ".jpg", ".jpeg", ".webp")
TILE_ESRGAN = 256  # o mesmo do desktop (medido no Arc B580)
TIMEOUT = 600
TIMEOUT_SEEDVR2 = 3900  # o comfy_job.py desiste em 1 h de trabalho + 5 min de subida
VAE_SEEDVR2 = "seedvr2_ema_vae_fp16.safetensors"
ESRGAN_MB = 200  # ponytail: ESRGAN/UltraSharp têm < 200 MB; acima disso só o SeedVR2 tem o cabeçalho lido (montagem lenta)


def eh_imagem(path: str) -> bool:
    return str(path).lower().endswith(EXT_IMAGEM)


def _nomes(path: str) -> list[str] | bytes:
    """Nomes das camadas: o pickle do .pth (bytes, sem desserializar) ou o cabeçalho JSON do .safetensors."""
    f = _c(path)
    if f.suffix.lower() == ".pth":
        with zipfile.ZipFile(f) as z:
            pkl = next((n for n in z.namelist() if n.endswith("data.pkl")), None)
            return z.read(pkl) if pkl else b""
    with open(f, "rb") as arq:
        n = struct.unpack("<Q", arq.read(8))[0]
        return list(json.loads(arq.read(n)))


def eh_ampliador(path: str) -> bool:
    """ESRGAN (RRDBNet) pelo conteúdo, no formato novo (`conv_first`, `rdb1`) ou no antigo (`model.0`, `RDB1`,
    do UltraSharp e dos da comunidade)."""
    try:
        nomes = _nomes(path)
    except (OSError, ToolError, ValueError, zipfile.BadZipFile, KeyError, struct.error):
        return False
    if isinstance(nomes, bytes):
        return (b"conv_first" in nomes and b"rdb1" in nomes) or (b"model.0.weight" in nomes and b"RDB1" in nomes)
    return ((any(n.startswith("conv_first") for n in nomes) and any(".rdb1." in n for n in nomes))
            or ("model.0.weight" in nomes and any(".RDB1." in n for n in nomes)))


def eh_seedvr2(path: str) -> bool:
    """O DiT do SeedVR2 pelo cabeçalho: blocos com modulação por texto (`blocks.N.ada.txt`)."""
    if not str(path).lower().endswith(".safetensors"):
        return False
    try:
        return any(".ada.txt." in n for n in _nomes(path))
    except (OSError, ToolError, ValueError, struct.error):
        return False


def comfy_dir() -> str:
    """O ComfyUI portátil que o Forja Desktop desta máquina instalou (vazio = não tem)."""
    base = imagegen._appdata()
    pasta = f"{base}/runtimes/comfy" if base else ""
    return pasta if pasta and _existe(f"{pasta}/python_embeded/python.exe") else ""


@functools.lru_cache(maxsize=1)
def _achados(_tick: int) -> tuple[dict, ...]:
    """Os ESRGAN (.pth/.safetensors) e SeedVR2 nas pastas de modelos (a montagem é lenta: cache de 15 s)."""
    out, vistos = [], set()  # o mesmo arquivo em duas pastas (nome e tamanho iguais) aparece uma vez só
    for pasta in imagegen.read_config()["motor"]["dirs"]:
        try:
            raiz = _c(pasta)
        except ToolError:
            continue
        if not raiz.is_dir():
            continue
        for f in sorted(raiz.rglob("*")):
            if f.suffix.lower() not in (".pth", ".safetensors") or f.name.lower() == VAE_SEEDVR2:
                continue
            try:
                tam = f.stat().st_size
            except OSError:
                continue
            host = f"{pasta.rstrip('/')}/{f.relative_to(raiz).as_posix()}"
            chave = (f.name.lower(), tam)
            if chave in vistos:
                continue
            tipo = ("esrgan" if tam < ESRGAN_MB << 20 and eh_ampliador(host)
                    else "seedvr2" if f.name.lower().startswith("seedvr2") and eh_seedvr2(host) else "")
            if tipo:
                vistos.add(chave)
                out.append({"path": host, "name": f.stem, "tipo": tipo})
    return tuple(out)


def catalogo() -> dict:
    """Mesmo formato do desktop. Sem download aqui: o web não baixa modelo nem runtime (o Forja Desktop baixa)."""
    pasta = comfy_dir()
    return {"modelos": [], "no_disco": list(_achados(int(time.time() // 15))), "ffmpeg": "", "erro": "",
            "comfy": {"instalado": pasta, "gpu": "", "mb": 0, "versao": ""}}


def _rodar(a: list[str], cwd: str, job_id: str, limite_s: int, ao_ler=None) -> tuple[dict, str]:
    """Roda `a` no runner até sair (ou cancelar, ou estourar `limite_s`) e devolve (info, cauda do log).
    `ao_ler(log)` a cada meio segundo, com as últimas linhas."""
    nome = f"{imagegen.PREFIXO}amp-{uuid.uuid4().hex[:8]}"
    try:
        runner.serve_start(nome, imagegen._comando(a), cwd)
    except runner.RunnerError as e:
        raise ToolError(str(e)) from e
    limite, info, log = time.monotonic() + limite_s, {}, ""
    try:
        while True:
            time.sleep(0.5)
            try:
                info = next((s for s in runner.servers() if s.get("name") == nome), {})
                if ao_ler:
                    log = runner.serve_log(nome, 30)
                    ao_ler(log)
            except runner.RunnerError as e:
                raise ToolError(str(e)) from e
            if not info.get("alive") or (job_id and downloads.cancelled(job_id)) or time.monotonic() > limite:
                break
        log = runner.serve_log(nome, 30) if (info.get("exit_code") or ao_ler) else log
    finally:
        try:
            runner.serve_stop(nome)  # mata o que ainda estiver vivo (no SeedVR2, o driver e o ComfyUI)
        except runner.RunnerError:
            pass
    if job_id and downloads.cancelled(job_id):
        raise ToolError("Ampliação cancelada.")
    return info, log


def _esrgan(entrada: str, saida: str, modelo: str, repeticoes: int, job_id: str) -> None:
    exe = imagegen._exe()
    a = [exe, "-M", "upscale", "-i", entrada, "-o", saida, "--upscale-model", modelo,
         "--upscale-tile-size", str(TILE_ESRGAN), "--upscale-repeats", str(repeticoes), "--backend", imagegen._gpu(exe)]
    info, log = _rodar(a, imagegen._pasta(exe), job_id, TIMEOUT)
    if info.get("exit_code") != 0 or not _existe(saida):
        raise ToolError(f"O ESRGAN falhou (código {info.get('exit_code')}):\n{log}")


def _seedvr2(entrada: str, saida: str, fator: int, modelo: str, job_id: str, progresso=None) -> dict:
    """comfy_job.py (o mesmo do desktop) no Python do portátil, pelo runner. O arquivo vai para a pasta de
    imagens, que o sistema do usuário enxerga; as fases e o resultado saem pelo log do runner."""
    pasta = comfy_dir()
    if not pasta:
        raise ToolError("Falta o ComfyUI (motor do SeedVR2): instale pelo Forja Desktop, em Imagens › Ampliar › Baixar o que falta.")
    vae = f"{modelo.rsplit('/', 1)[0]}/{VAE_SEEDVR2}"
    if not _existe(vae):
        raise ToolError(f"Falta o VAE do SeedVR2 ({VAE_SEEDVR2}) ao lado do modelo.")
    job = f"{imagegen.out_dir()}/.forja/comfy_job.py"
    _c(job).parent.mkdir(parents=True, exist_ok=True)
    _c(job).write_bytes(Path(__file__).with_name("comfy_job.py").read_bytes())
    vistas: set[str] = set()

    def ao_ler(log: str) -> None:
        for linha in log.splitlines():
            if linha.startswith("FASE ") and linha not in vistas and progresso:
                vistas.add(linha)
                progresso(linha[5:].strip())
    a = [f"{pasta}/python_embeded/python.exe", "-X", "utf8", "-s", job, "--comfy", pasta, "--modelo", modelo, "--vae", vae,
         "--entrada", entrada, "--saida", saida, "--fator", str(int(fator))]
    info, log = _rodar(a, pasta, job_id, TIMEOUT_SEEDVR2, ao_ler)
    fim = next((l.strip() for l in reversed(log.splitlines()) if l.startswith(("OK ", "ERRO "))), "")
    if not fim.startswith("OK ") or not _existe(saida):
        raise ToolError(fim[5:] if fim.startswith("ERRO ") else f"O ComfyUI saiu sem resultado (código {info.get('exit_code')}).")
    w, h = (int(x) for x in re.findall(r"\d+", fim)[:2])
    return {"w": w, "h": h}


def ampliar_imagem(entrada: str, saida: str, fator: int, modelo: str = "", job_id: str = "", progresso=None) -> dict:
    """Amplia `entrada` em `fator` e grava `saida` (.png). `modelo` vazio = Lanczos; SeedVR2 pelo ComfyUI;
    ESRGAN pelo sd-cli. ESRGAN que passou do alvo (um 4× pedido como 2×) volta ao tamanho pedido por Lanczos."""
    from PIL import Image
    if modelo and eh_seedvr2(modelo):
        return _seedvr2(entrada, saida, fator, modelo, job_id, progresso)
    with Image.open(_c(entrada)) as im:
        w, h = im.size
        alvo = (w * int(fator), h * int(fator))
        if not modelo:
            im.convert("RGBA" if "A" in im.getbands() else "RGB").resize(alvo, Image.LANCZOS).save(_c(saida))
            return {"w": alvo[0], "h": alvo[1]}
    _esrgan(entrada, saida, modelo, 1, job_id)
    with Image.open(_c(saida)) as out:
        escala = round(out.width / w)
    if escala < 2:  # o sd-cli que não carrega o modelo grava a própria entrada e diz "success"
        _c(saida).unlink(missing_ok=True)
        raise ToolError(f"O sd.cpp não conseguiu rodar {Path(modelo).name} (saiu do mesmo tamanho). "
                        "Use um RRDBNet 4× como RealESRGAN_x4plus ou x4plus_anime_6B.")
    if escala < fator:
        _esrgan(entrada, saida, modelo, 2, job_id)
    with Image.open(_c(saida)) as out:
        certo = out.size == alvo
        if not certo:
            out = out.resize(alvo, Image.LANCZOS)  # já carregada: o arquivo fecha antes de ser regravado
    if not certo:
        out.save(_c(saida))
    return {"w": alvo[0], "h": alvo[1]}
