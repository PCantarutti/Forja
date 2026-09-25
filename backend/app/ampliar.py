"""Ampliação de imagem: ESRGAN pelo sd-cli (`-M upscale`) no runner, ou Lanczos (Pillow) aqui no container.

Mesmo desenho do desktop (forja-desktop/backend/app/ampliar.py), só a parte de imagem: vídeo precisa de
ffmpeg, que o Docker não tem. Os caminhos são os do sistema do usuário; o container lê e grava pela
montagem (`imagegen._c`). O ESRGAN que o sd.cpp roda é o RRDBNet (RealESRGAN x4plus, x4plus_anime_6B); o
x2plus entra com pixel-unshuffle e o sd.cpp recusa. A escala sai dos pesos e é medida na saída.
"""
from __future__ import annotations

import functools
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


def eh_imagem(path: str) -> bool:
    return str(path).lower().endswith(EXT_IMAGEM)


def eh_ampliador(path: str) -> bool:
    """ESRGAN (RRDBNet) pelo conteúdo: `conv_first` e os blocos `rdb` no pickle do .pth."""
    try:
        with zipfile.ZipFile(_c(path)) as z:
            pkl = next((n for n in z.namelist() if n.endswith("data.pkl")), None)
            dados = z.read(pkl) if pkl else b""
        return b"conv_first" in dados and b"rdb1" in dados
    except (OSError, ToolError, zipfile.BadZipFile, KeyError):
        return False


@functools.lru_cache(maxsize=1)
def _achados(_tick: int) -> tuple[dict, ...]:
    """Os .pth ESRGAN nas pastas de modelos (a montagem é lenta: cache de 15 s, como o `_scan`)."""
    out, vistos = [], set()  # o mesmo arquivo em duas pastas (nome e tamanho iguais) aparece uma vez só
    for pasta in imagegen.read_config()["motor"]["dirs"]:
        try:
            raiz = _c(pasta)
        except ToolError:
            continue
        if not raiz.is_dir():
            continue
        for f in sorted(raiz.rglob("*.pth")):
            host = f"{pasta.rstrip('/')}/{f.relative_to(raiz).as_posix()}"
            chave = (f.name.lower(), f.stat().st_size)
            if chave not in vistos and eh_ampliador(host):
                vistos.add(chave)
                out.append({"path": host, "name": f.stem})
    return tuple(out)


def catalogo() -> dict:
    """Mesmo formato do desktop. Sem download aqui: o web não baixa modelo (ponytail: vira download quando
    o web ganhar um downloader); o ffmpeg não conta para imagem."""
    return {"modelos": [], "no_disco": list(_achados(int(time.time() // 15))), "ffmpeg": "", "erro": ""}


def _esrgan(entrada: str, saida: str, modelo: str, repeticoes: int, job_id: str) -> None:
    exe = imagegen._exe()
    a = [exe, "-M", "upscale", "-i", entrada, "-o", saida, "--upscale-model", modelo,
         "--upscale-tile-size", str(TILE_ESRGAN), "--upscale-repeats", str(repeticoes), "--backend", imagegen._gpu(exe)]
    nome = f"{imagegen.PREFIXO}amp-{uuid.uuid4().hex[:8]}"
    try:
        runner.serve_start(nome, imagegen._comando(a), imagegen._pasta(exe))
    except runner.RunnerError as e:
        raise ToolError(str(e)) from e
    limite, info = time.monotonic() + TIMEOUT, {}
    try:
        while True:
            time.sleep(0.5)
            try:
                info = next((s for s in runner.servers() if s.get("name") == nome), {})
            except runner.RunnerError as e:
                raise ToolError(str(e)) from e
            if not info.get("alive") or (job_id and downloads.cancelled(job_id)) or time.monotonic() > limite:
                break
        log = runner.serve_log(nome, 30) if info.get("exit_code") else ""
    finally:
        try:
            runner.serve_stop(nome)
        except runner.RunnerError:
            pass
    if job_id and downloads.cancelled(job_id):
        raise ToolError("Ampliação cancelada.")
    if info.get("exit_code") != 0 or not _existe(saida):
        raise ToolError(f"O ESRGAN falhou (código {info.get('exit_code')}):\n{log}")


def ampliar_imagem(entrada: str, saida: str, fator: int, modelo: str = "", job_id: str = "") -> dict:
    """Amplia `entrada` em `fator` e grava `saida` (.png). `modelo` vazio = Lanczos. ESRGAN que passou do alvo
    (um 4× pedido como 2×) volta ao tamanho pedido por Lanczos."""
    from PIL import Image
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
