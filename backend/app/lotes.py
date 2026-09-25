"""Lotes de imagem: várias variações de um prompt, divididas entre modelos, com aprovação depois.

Mesmo desenho do desktop: um lote são duas mensagens de uma Conversation(kind="imagem") — a do
usuário com o pedido, e a do assistente com a lista de imagens, que a thread vai preenchendo. O
motor (sd-cli pelo runner, ou nuvem) é por modelo; ver imagegen.py.

Diferença do Docker: os caminhos das imagens são os do sistema do usuário (C:/Users/...), porque é
lá que o sd-cli grava; o container toca nelas pela montagem (`imagegen._c`).

As reprovadas não são apagadas na hora: vão para `<pasta>/descartadas/` e o expurgo leva as que
passarem de `image.descarte_dias` (padrão 7).
"""
from __future__ import annotations

import random
import re
import shutil
import threading
import time

from . import db, downloads, imagegen, mirror
from .imagegen import _c, _existe
from .tools import ToolError

DESCARTADAS = "descartadas"
MAX_VARIACOES = 50  # o sd-cli é sequencial; acima disso é espera, não geração
SEED_MAX = 2**31 - 1


def descartadas_dir() -> str:
    return f"{imagegen.out_dir()}/{DESCARTADAS}"


def previas_dir() -> str:
    """Prévia de cada imagem enquanto ela gera; o arquivo some quando a imagem termina."""
    return f"{imagegen.out_dir()}/.previas"


def _base(p: str) -> str:
    return str(p).replace("\\", "/").rsplit("/", 1)[-1]


def _sementes(count: int, seed: int, modo: str) -> list[int]:
    """A semente é sempre decidida aqui, nunca pelo sd.cpp: sem isso não dá para repetir a imagem."""
    if modo == "aleatoria":
        return [random.randint(1, SEED_MAX) for _ in range(count)]
    base = int(seed) or random.randint(1, SEED_MAX)
    if modo == "fixa":
        return [base] * count
    return [(base + i) % SEED_MAX or 1 for i in range(count)]


def _distribuir(models: list[str], count: int) -> list[str]:
    """Blocos contíguos, resto nos primeiros: 10 em 2 modelos = 5+5; 10 em 3 = 4+3+3."""
    if not models:
        raise ToolError("Escolha pelo menos um modelo de imagem.")
    por, resto = divmod(count, len(models))
    out: list[str] = []
    for i, m in enumerate(models):
        out += [m] * (por + (1 if i < resto else 0))
    return out


def _nome(m: str) -> str:
    if m.startswith(imagegen.API):
        return m.split(":", 2)[-1]
    return _base(m).rsplit(".", 1)[0] if m else ""


def _save(conv_id: int, **fields) -> db.Message:
    with db.session() as s:
        m = db.Message(conversation_id=conv_id, **fields)
        s.add(m)
        conv = s.get(db.Conversation, conv_id)
        if conv:
            conv.updated_at = db._now()
        s.commit()
        return m


def _patch(message_id: int, **fields) -> dict:
    """meta é JSON puro (sem MutableDict): só persiste se o dict for reatribuído inteiro."""
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m:
            raise ToolError("Lote não encontrado.")
        meta = dict(m.meta or {})
        if "meta" in fields:
            meta.update(fields.pop("meta"))
            m.meta = meta
        for k, v in fields.items():
            setattr(m, k, v)
        s.commit()
        return {**m.to_dict(), "conversation_id": m.conversation_id}


def _mensagem(message_id: int) -> dict:
    with db.session() as s:
        m = s.get(db.Message, message_id)
        if not m or not (m.meta or {}).get("images"):
            raise ToolError("Lote não encontrado.")
        return {**m.to_dict(), "conversation_id": m.conversation_id}


# ------------------------------------------------------------------ referências

EXT_REFERENCIA = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MAX_REFERENCIAS = 500


def registrar_referencia(path: str) -> str:
    """Imagem do disco do usuário para editar: usada onde está, sem cópia. Fica registrada para a
    rota de arquivo poder mostrar a miniatura (ela não serve arquivo qualquer do disco)."""
    host = imagegen._norm(path)
    if not host.lower().endswith(EXT_REFERENCIA):
        raise ToolError("Anexe uma imagem (PNG, JPG ou WebP).")
    if not _existe(host):
        raise ToolError(f"Imagem de referência não encontrada: {path}")
    if _c(host).stat().st_size > 50_000_000:
        raise ToolError("Imagem maior que 50 MB.")
    chave = imagegen._chave(host)
    data = imagegen.read_config()
    lista = [p for p in data.get("referencias") or [] if p != chave] + [chave]
    data["referencias"] = lista[-MAX_REFERENCIAS:]
    imagegen.write_config(data)
    return host


def eh_referencia(path: str) -> bool:
    return imagegen._chave(path) in (imagegen.read_config().get("referencias") or [])


def servivel(path: str) -> bool:
    """A rota de arquivo só serve o que é do Forja: a pasta de imagens ou uma referência registrada."""
    chave = imagegen._chave(path)
    try:
        pasta = imagegen._chave(imagegen.out_dir()).rstrip("/") + "/"
    except ToolError:
        pasta = None
    return bool((pasta and chave.startswith(pasta)) or eh_referencia(path))


# ------------------------------------------------------------------ geração

def start(conv_id: int, prompt: str, opts: dict | None = None, models: list[str] | None = None,
          count: int = 1, seed: int = 0, seed_mode: str = "incremental", confirm: bool = False,
          refs: list[str] | None = None) -> dict:
    """Enfileira o lote e devolve a mensagem do assistente já criada (a thread preenche o resto)."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ToolError("Descreva a imagem (prompt vazio).")
    count = max(1, min(int(count or 1), MAX_VARIACOES))
    opts = {k: v for k, v in (opts or {}).items() if v not in (None, "")}
    escolhidos = _distribuir(list(models or []), count)
    refs = [imagegen._norm(str(r)) for r in (refs or [])]
    for m in dict.fromkeys(escolhidos):  # motor, modelo e arquivos conferidos ANTES de enfileirar
        imagegen.valida(prompt, imagegen._opts({**opts, "model": m}), refs)

    sementes = _sementes(count, seed, seed_mode)
    pasta = imagegen.out_dir()
    marca = time.strftime("%Y%m%d-%H%M%S")
    imagens = [{"path": f"{pasta}/{marca}-{i:02d}-s{s}.png", "seed": s, "model": m,
                "model_name": _nome(m), "status": "pendente", "error": ""}
               for i, (m, s) in enumerate(zip(escolhidos, sementes))]

    with db.session() as s:
        conv = s.get(db.Conversation, conv_id)
        if not conv:
            raise ToolError("Conversa não encontrada.")
        if conv.title == "Nova conversa":
            conv.title = prompt.splitlines()[0][:60] or "Nova conversa"
        s.commit()

    _save(conv_id, role="user", content=prompt,
          meta={"opts": opts, "models": list(dict.fromkeys(escolhidos)), "count": count,
                "seed": seed, "seed_mode": seed_mode, "refs": refs})
    job = downloads.create("lote", prompt[:60])
    downloads.update(job["id"], done=0, total=count)
    msg = _save(conv_id, role="assistant", content="", status="running",
                meta={"job": job["id"], "count": count, "seed_mode": seed_mode,
                      "opts": opts, "images": imagens})

    threading.Thread(target=_trabalhar, args=(conv_id, msg.id, prompt, opts, job["id"], refs), daemon=True).start()
    return msg.to_dict()


def _trabalhar(conv_id: int, message_id: int, prompt: str, opts: dict, job_id: str,
               refs: list[str] | None = None) -> None:
    imagens = list(_mensagem(message_id)["meta"]["images"])
    imagegen.set_image_busy(True)
    erro = ""
    feitas, alvo = 0, sum(i["status"] == "pendente" for i in imagens)
    try:
        for i, item in enumerate(imagens):
            if item["status"] != "pendente":  # "Continuar": o que já saiu fica como está
                continue
            if downloads.cancelled(job_id):
                for resto in imagens[i:]:
                    if resto["status"] == "pendente":
                        resto["status"] = "cancelada"
                _patch(message_id, meta={"images": imagens})
                break
            item["status"] = "gerando"
            item["progress"] = 0.0
            item["com_previa"] = imagegen.modo_previa(imagegen._opts({**opts, "model": item["model"]})) is not None
            _patch(message_id, meta={"images": imagens})
            previa = f"{previas_dir()}/{_base(item['path'])}"
            try:
                _c(previa).parent.mkdir(parents=True, exist_ok=True)
            except (OSError, ToolError):
                pass

            def progresso(passo: int, total: int, s_passo: float = 0.0, item=item, previa=previa) -> None:
                if _existe(previa):
                    item["preview"] = previa
                item["progress"] = round(passo / total, 3) if total else 0.0
                item["s_passo"] = round(s_passo, 2)
                item["restante"] = round(max(0, total - passo) * s_passo)
                _patch(message_id, meta={"images": imagens})

            try:
                imagegen.generate(prompt, item["path"], {**opts, "model": item["model"], "seed": item["seed"]},
                                  job_id, refs or [], progresso, previa)
                item["status"] = "pronta"
            except Exception as e:
                cancelada = downloads.cancelled(job_id)
                item["status"] = "cancelada" if cancelada else "erro"
                item["error"] = "" if cancelada else str(e)
                if not cancelada:
                    erro = erro or str(e)
            item.pop("preview", None)
            item.pop("com_previa", None)
            try:
                _c(previa).unlink(missing_ok=True)
            except (OSError, ToolError):
                pass
            feitas += 1
            downloads.update(job_id, done=feitas, total=alvo)
            _patch(message_id, meta={"images": imagens})
    finally:
        imagegen.set_image_busy(False)

    pronta = any(i["status"] in ("pronta", "mantida", "descartada") for i in imagens)
    cancelado = any(i["status"] == "cancelada" for i in imagens)
    status = "pronto" if pronta else ("cancelado" if cancelado else "erro")
    downloads.finish(job_id, error="" if pronta else erro)
    mirror.write(conv_id)
    limpar_descartadas()
    # o status sai por último de propósito: é o sinal de "acabou" para quem espera o lote
    _patch(message_id, status=status, meta={"images": imagens})


# ------------------------------------------------------------------ ampliação (só imagem: vídeo precisa de ffmpeg)

def _validar_ampliacao(path: str, fator: int, modelo: str) -> None:
    from . import ampliar as amp
    if int(fator) not in (2, 4):
        raise ToolError("Amplie em 2× ou 4×.")
    if not amp.eh_imagem(path):
        raise ToolError("Amplie uma imagem PNG, JPG ou WebP.")
    if not _existe(path):
        raise ToolError("Esse arquivo não existe (ou não está acessível).")
    if modelo and amp.eh_seedvr2(modelo):
        if not amp.comfy_dir():
            raise ToolError("Falta o ComfyUI (motor do SeedVR2): instale pelo Forja Desktop, em Imagens › Ampliar › Baixar o que falta.")
        return
    if modelo and not amp.eh_ampliador(modelo):
        raise ToolError("Esse arquivo não é um modelo de ampliação (ESRGAN ou SeedVR2).")


def _nova_ampliacao(conv_id: int, origem: str, saida: str, prompt: str, opts: dict, seed: int,
                    fator: int, modelo: str) -> dict:
    """A tomada nova (pedido + resposta) e a thread que amplia. `opts`: largura e altura da origem."""
    nome = _nome(modelo) if modelo else "Lanczos"
    amp_meta = {"origem": origem, "fator": int(fator), "modelo": modelo, "suavizar": False}
    opts = {**opts, "width": int(opts.get("width") or 0) * int(fator), "height": int(opts.get("height") or 0) * int(fator),
            "ampliacao": amp_meta}
    imagens = [{"path": saida, "seed": seed, "model": modelo, "model_name": f"{nome} · {fator}×",
                "status": "pendente", "error": ""}]
    _save(conv_id, role="user", content=prompt, meta={"refs": [], "models": [modelo], "ampliacao": amp_meta})
    job = downloads.create("lote", f"ampliar {_base(origem)}")
    nova = _save(conv_id, role="assistant", content="", status="running",
                 meta={"job": job["id"], "count": 1, "seed_mode": "fixa", "opts": opts, "images": imagens})
    threading.Thread(target=_ampliar_trabalho, args=(conv_id, nova.id, job["id"]), daemon=True).start()
    return nova.to_dict()


def ampliar(message_id: int, path: str, fator: int, modelo: str = "") -> dict:
    """Amplia uma imagem pronta do lote: vira um lote à parte na mesma conversa."""
    msg = _mensagem(message_id)
    item = next((i for i in msg["meta"]["images"] if i["path"] == path), None)
    if not item:
        raise ToolError("Essa imagem não está pronta (ou o arquivo sumiu).")
    _validar_ampliacao(path, fator, modelo)
    with db.session() as s:
        pedido = (s.query(db.Message).filter(db.Message.conversation_id == msg["conversation_id"], db.Message.role == "user",
                                             db.Message.id < message_id).order_by(db.Message.id.desc()).first())
        prompt = pedido.content if pedido else ""
    saida = f"{path.rsplit('.', 1)[0]}-{fator}x.png"
    return _nova_ampliacao(msg["conversation_id"], path, saida, prompt, dict(msg["meta"].get("opts") or {}),
                           item["seed"], fator, modelo)


def ampliar_arquivo(conv_id: int, path: str, fator: int, modelo: str = "") -> dict:
    """Uma imagem qualquer (enviada pelo navegador ou do disco): o resultado vai para a pasta de imagens."""
    from PIL import Image
    path = imagegen._norm(path)
    _validar_ampliacao(path, fator, modelo)
    try:
        with Image.open(_c(path)) as im:
            w, h = im.size
    except OSError:
        raise ToolError(f"Não consegui ler {_base(path)} como imagem.") from None
    _c(imagegen.out_dir()).mkdir(parents=True, exist_ok=True)
    nome = re.sub(r"^[0-9a-f]{16}-", "", _base(path))  # a enviada chega em referencias/ com o sha na frente
    saida = f"{imagegen.out_dir()}/{time.strftime('%Y%m%d-%H%M%S')}-{nome.rsplit('.', 1)[0]}-{fator}x.png"
    return _nova_ampliacao(conv_id, path, saida, nome, {"width": w, "height": h}, 0, fator, modelo)


def _ampliar_trabalho(conv_id: int, message_id: int, job_id: str) -> None:
    from . import ampliar as amp
    meta = _mensagem(message_id)["meta"]
    imagens = list(meta["images"])
    a = meta["opts"]["ampliacao"]
    item = imagens[0]
    imagegen.set_image_busy(True)
    try:
        item.update(status="gerando", progress=0.0)
        _patch(message_id, meta={"images": imagens})
        fases = {"iniciando o ComfyUI": 0.05, "ampliando": 0.3}  # só o SeedVR2 avisa: leva minutos

        def fase(texto: str) -> None:
            item.update(fase=texto, progress=fases.get(texto, item.get("progress", 0.0)))
            _patch(message_id, meta={"images": imagens})
        amp.ampliar_imagem(a["origem"], item["path"], a["fator"], a["modelo"], job_id, fase)
        item["status"] = "pronta"
    except Exception as e:
        cancelada = downloads.cancelled(job_id)
        item["status"] = "cancelada" if cancelada else "erro"
        item["error"] = "" if cancelada else str(e)
    finally:
        imagegen.set_image_busy(False)
        item.pop("progress", None)
        item.pop("fase", None)
    pronta = item["status"] == "pronta"
    downloads.finish(job_id, error="" if pronta else item["error"])
    mirror.write(conv_id)
    _patch(message_id, status="pronto" if pronta else ("cancelado" if item["status"] == "cancelada" else "erro"),
           meta={"images": imagens})


A_REFAZER = ("interrompida", "pendente", "cancelada", "erro")


def reap() -> int:
    """Na subida do backend nenhum lote está rodando: os que ficaram "running" são de uma queda."""
    try:
        shutil.rmtree(_c(previas_dir()), ignore_errors=True)
    except ToolError:
        pass
    with db.session() as s:
        presos = s.query(db.Message).filter(db.Message.role == "assistant", db.Message.status == "running").all()
        n = 0
        for m in presos:
            imagens = [dict(i) for i in (m.meta or {}).get("images") or []]
            if not imagens:
                continue
            for i in imagens:
                if i["status"] in ("gerando", "pendente"):
                    i["status"] = "pronta" if _existe(i["path"]) else "interrompida"
                    i.pop("progress", None)
                    i.pop("preview", None)
                    i.pop("com_previa", None)
            m.meta = {**m.meta, "images": imagens}
            m.status = "interrompido" if any(i["status"] == "interrompida" for i in imagens) else "pronto"
            n += 1
        s.commit()
        return n


def continuar(message_id: int, confirm: bool = False) -> dict:
    """Gera o que faltou do lote, com as mesmas sementes e ajustes. As prontas ficam como estão."""
    msg = _mensagem(message_id)
    if msg["status"] == "running":
        raise ToolError("O lote ainda está rodando.")
    imagens = list(msg["meta"]["images"])
    if not any(i["status"] in A_REFAZER for i in imagens):
        raise ToolError("Nada a continuar: todas as imagens deste lote já saíram.")
    if (msg["meta"].get("opts") or {}).get("ampliacao"):  # é uma ampliação: refaz a ampliação
        for i in imagens:
            i.update(status="pendente", error="")
        job = downloads.create("lote", f"ampliar {_base(imagens[0]['path'])}")
        _patch(message_id, status="running", meta={"job": job["id"], "images": imagens})
        threading.Thread(target=_ampliar_trabalho, args=(msg["conversation_id"], message_id, job["id"]), daemon=True).start()
        return {"ok": True}
    with db.session() as s:
        pedido = (s.query(db.Message)
                  .filter(db.Message.conversation_id == msg["conversation_id"], db.Message.role == "user",
                          db.Message.id < message_id)
                  .order_by(db.Message.id.desc()).first())
        if not pedido:
            raise ToolError("Pedido do lote não encontrado.")
        prompt, refs = pedido.content, list((pedido.meta or {}).get("refs") or [])
    for i in imagens:
        if i["status"] in A_REFAZER:
            i.update(status="pendente", error="")
    faltam = sum(i["status"] == "pendente" for i in imagens)
    job = downloads.create("lote", prompt[:60])
    downloads.update(job["id"], done=0, total=faltam)
    _patch(message_id, status="running", meta={"job": job["id"], "images": imagens})
    opts = msg["meta"].get("opts") or {}
    threading.Thread(target=_trabalhar, args=(msg["conversation_id"], message_id, prompt, opts, job["id"], refs),
                     daemon=True).start()
    return {"ok": True}


def cancelar(message_id: int) -> dict:
    meta = _mensagem(message_id)["meta"]
    downloads.cancel(meta.get("job") or "")
    return {"ok": True}


# ------------------------------------------------------------------ aprovação

def _mover(origem: str, destino: str) -> None:
    _c(destino).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(_c(origem)), str(_c(destino)))


def decidir(message_id: int, keep: list[str]) -> dict:
    """As aprovadas ficam onde estão; o resto vai para descartadas/ (some sozinho no expurgo)."""
    m = _mensagem(message_id)
    imagens = [dict(i) for i in m["meta"]["images"]]
    manter = {imagegen._chave(p) for p in (keep or [])}
    for item in imagens:
        if item["status"] not in ("pronta", "mantida", "descartada"):
            continue
        if imagegen._chave(item["path"]) in manter:
            if item["status"] == "descartada":  # desfazer: volta para a pasta de saída
                alvo = f"{imagegen.out_dir()}/{_base(item['path'])}"
                if _existe(item["path"]):
                    _mover(item["path"], alvo)
                item["path"] = alvo
            item["status"] = "mantida"
        elif item["status"] != "descartada":
            alvo = f"{descartadas_dir()}/{_base(item['path'])}"
            if _existe(item["path"]):
                _mover(item["path"], alvo)
            item["path"] = alvo
            item["status"] = "descartada"
    out = _patch(message_id, meta={"images": imagens})
    mirror.write(out["conversation_id"])
    return out


def imagens_da_conversa(conv_id: int) -> list[str]:
    """Os arquivos que os lotes desta conversa geraram e ainda existem (inclusive em descartadas/).
    Só o que está dentro da pasta de imagens: referência anexada do disco nunca entra."""
    try:
        pasta = imagegen._chave(imagegen.out_dir()).rstrip("/") + "/"
    except ToolError:
        return []
    with db.session() as s:
        msgs = s.query(db.Message).filter(db.Message.conversation_id == conv_id, db.Message.role == "assistant").all()
        caminhos = [i["path"] for m in msgs for i in (m.meta or {}).get("images") or [] if i.get("path")]
    achados: list[str] = []
    for c in caminhos:
        for f in (c, f"{descartadas_dir()}/{_base(c)}"):
            if imagegen._chave(f).startswith(pasta) and _existe(f) and f not in achados:
                achados.append(f)
                break
    return achados


def apagar_imagens(conv_id: int) -> int:
    """Apagar a conversa leva as imagens dela junto (a tela avisa antes, com a contagem)."""
    n = 0
    for f in imagens_da_conversa(conv_id):
        try:
            _c(f).unlink()
            n += 1
        except (OSError, ToolError):
            pass
    return n


def limpar_descartadas(dias: int | None = None) -> int:
    """Expurgo por idade: roda na subida do backend e no fim de cada lote. `dias<=0` leva tudo."""
    try:
        pasta = _c(descartadas_dir())
    except ToolError:
        return 0
    if not pasta.is_dir():
        return 0
    if dias is None:
        dias = int(imagegen.read_config()["image"].get("descarte_dias") or 0)
        if dias <= 0:
            return 0
    limite = time.time() - dias * 86400 if dias > 0 else time.time() + 1
    apagados = 0
    for f in pasta.glob("*.png"):
        try:
            if f.stat().st_mtime < limite:
                f.unlink()
                apagados += 1
        except OSError:
            pass
    return apagados
