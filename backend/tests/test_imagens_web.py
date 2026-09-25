"""Imagens no Docker: sd-cli pelo runner e modelo de nuvem, com runner e API falsos (nada roda na GPU)."""
import base64
import json
import time

import httpx
import pytest

from app import config, db, downloads, imagegen, lotes, runner, workspace

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def host(p) -> str:
    return workspace.normalize(str(p))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(imagegen, "CONFIG_FILE", tmp_path / "imagens.json")
    monkeypatch.setattr(imagegen, "_home", lambda: "")
    imagegen._scan.cache_clear()
    exe = tmp_path / "sd" / "sd-cli.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    modelos = tmp_path / "modelos"
    modelos.mkdir()
    (modelos / "sd15.safetensors").write_bytes(b"x")
    (modelos / "vae-extra.safetensors").write_bytes(b"x")
    imagegen.write_config(imagegen._blank())
    imagegen.set_motor({"sd_cli": host(exe), "dirs": [host(modelos)]})
    imagegen.set_image({"out_dir": host(tmp_path / "saida"), "steps": 4})
    return tmp_path


class RunnerFalso:
    """Faz o papel do /serve do forja-runner: 'roda' o comando escrevendo o PNG no -o."""

    def __init__(self, monkeypatch, falha=False):
        self.comandos, self.parados, self.falha = [], [], falha
        self.vivo: dict[str, int] = {}
        monkeypatch.setattr(runner, "refresh", lambda force=False: {"ok": True, "shell": "powershell"})
        monkeypatch.setattr(runner, "current", lambda: {"ok": True, "shell": "powershell"})
        monkeypatch.setattr(runner, "serve_start", self.start)
        monkeypatch.setattr(runner, "servers", self.servers)
        monkeypatch.setattr(runner, "serve_log", self.log)
        monkeypatch.setattr(runner, "serve_stop", lambda n: self.parados.append(n) or {})

    def start(self, name, command, cwd):
        self.comandos.append((name, command, cwd))
        self.vivo[name] = 2  # duas consultas "vivo", depois termina
        return {"name": name, "pid": 1}

    def servers(self):
        out = []
        for n, restam in self.vivo.items():
            self.vivo[n] = restam - 1
            if restam - 1 < 0 and not self.falha:
                saida = self.comandos[-1][1].split("'-o' '", 1)[1].split("'", 1)[0]
                workspace.to_container(saida).write_bytes(PNG)
            out.append({"name": n, "alive": restam - 1 >= 0, "exit_code": None if restam - 1 >= 0 else
                        (1 if self.falha else 0)})
        return out

    def log(self, name, tail):
        return "loading...\n  |=====>   | 2/4 - 1.5s/it\r  |==========| 4/4 - 2.0it/s\nfalhou: out of memory" \
            if self.falha else "  |=====>   | 2/4 - 1.5s/it\n  |==========| 4/4 - 2.0it/s"


def test_argv_vai_com_caminhos_do_usuario_e_powershell(cfg, monkeypatch):
    RunnerFalso(monkeypatch)
    o = imagegen._opts({"model": host(cfg / "modelos" / "sd15.safetensors"), "seed": 7})
    cmd = imagegen._comando(imagegen.argv(imagegen._exe(), "um gato 'fofo'", "C:/x/out.png", o))
    assert cmd.startswith("& '") and "sd-cli.exe'" in cmd
    assert "'um gato ''fofo'''" in cmd            # aspa simples do PowerShell dobrada, sem interpolar
    assert "'-m'" in cmd and "'-s' '7'" in cmd and "'--steps' '4'" in cmd


def test_runner_gera_com_progresso_e_limpa_o_processo(cfg, monkeypatch):
    rf = RunnerFalso(monkeypatch)
    monkeypatch.setattr(imagegen.time, "sleep", lambda s: None)
    passos = []
    out = host(cfg / "saida" / "a.png")
    imagegen.generate("gato", out, {"model": host(cfg / "modelos" / "sd15.safetensors")},
                      progresso=lambda p, t, s: passos.append((p, t)))
    assert workspace.to_container(out).read_bytes() == PNG
    assert passos[-1] == (4, 4)
    assert rf.parados and rf.parados[0].startswith(imagegen.PREFIXO)  # some da lista do runner
    assert rf.comandos[0][2] == host(cfg / "sd")                      # roda na pasta do sd-cli


def test_runner_falha_diz_o_log_e_a_dica_de_memoria(cfg, monkeypatch):
    RunnerFalso(monkeypatch, falha=True)
    monkeypatch.setattr(imagegen.time, "sleep", lambda s: None)
    with pytest.raises(imagegen.ToolError, match="sem memória"):
        imagegen.generate("gato", host(cfg / "saida" / "b.png"), {"model": host(cfg / "modelos" / "sd15.safetensors")})


def test_runner_desligado_explica(cfg, monkeypatch):
    monkeypatch.setattr(runner, "refresh", lambda force=False: None)
    with pytest.raises(imagegen.ToolError, match="forja-runner"):
        imagegen.valida("gato", imagegen._opts({"model": host(cfg / "modelos" / "sd15.safetensors")}), [])


def _api(monkeypatch, respostas):
    """Provedor OpenAI falso; `respostas` é consumida em ordem. Devolve a lista de pedidos."""
    monkeypatch.setitem(config.PROVIDERS, "nuvem", {"id": "nuvem", "name": "Nuvem", "type": "openai",
                                                    "url": "https://img.example/v1", "api_key": "k"})
    pedidos = []

    def responde(req: httpx.Request):
        pedidos.append(req)
        return respostas.pop(0)

    real = httpx.Client
    monkeypatch.setattr(imagegen.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(responde), **kw))
    return pedidos


def test_api_gera_e_tenta_sem_tamanho_quando_o_provedor_recusa(cfg, monkeypatch):
    pedidos = _api(monkeypatch, [httpx.Response(400, json={"error": {"message": "Invalid size"}}),
                                 httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})])
    imagegen.set_motor({"api_models": [{"provider": "nuvem", "model": "gpt-image-1"}]})
    out = host(cfg / "saida" / "c.png")
    imagegen.generate("gato", out, {"model": "api:nuvem:gpt-image-1", "width": 512, "height": 512})
    assert workspace.to_container(out).read_bytes() == PNG
    primeiro, segundo = (json.loads(p.content) for p in pedidos)
    assert pedidos[0].url.path.endswith("/images/generations") and primeiro["size"] == "512x512"
    assert "size" not in segundo and segundo["model"] == "gpt-image-1"
    assert pedidos[0].headers["authorization"] == "Bearer k"


def test_api_edita_com_as_referencias(cfg, monkeypatch):
    pedidos = _api(monkeypatch, [httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})])
    ref = cfg / "ref.png"
    ref.write_bytes(PNG)
    imagegen.generate("tire o relógio", host(cfg / "saida" / "d.png"), {"model": "api:nuvem:gpt-image-1"},
                      refs=[host(ref)])
    assert pedidos[0].url.path.endswith("/images/edits") and b"ref.png" in pedidos[0].content


def test_so_provedor_openai_entra_como_modelo_de_nuvem(cfg, monkeypatch):
    monkeypatch.setitem(config.PROVIDERS, "olla", {"id": "olla", "name": "Ollama", "type": "ollama", "url": "x"})
    with pytest.raises(imagegen.ToolError, match="OpenAI"):
        imagegen.set_motor({"api_models": [{"provider": "olla", "model": "x"}]})


def test_lista_tem_arquivos_e_nuvem_e_esconde_o_vae_configurado(cfg, monkeypatch):
    _api(monkeypatch, [])
    imagegen.set_motor({"api_models": [{"provider": "nuvem", "model": "gpt-image-1"}]})
    imagegen.save_image_params(host(cfg / "modelos" / "sd15.safetensors"),
                               {"vae": host(cfg / "modelos" / "vae-extra.safetensors")})
    nomes = [m["name"] for m in imagegen.modelos()]
    assert "sd15" in nomes and "vae-extra" not in nomes and any("gpt-image-1" in n for n in nomes)


def test_importa_a_config_do_desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(imagegen, "CONFIG_FILE", tmp_path / "imagens.json")
    base = tmp_path / "AppData" / "Roaming" / "Forja"
    (base / "runtimes" / "sd" / "vulkan").mkdir(parents=True)
    (base / "runtimes" / "sd" / "vulkan" / "sd-cli.exe").write_bytes(b"")
    modelo = str(tmp_path / "m" / "qwen.gguf").replace("/", "\\")
    (base / "local.json").write_text(json.dumps({
        "image": {"steps": 8, "vae": str(tmp_path / "v.safetensors").replace("/", "\\")},
        "image_models": {modelo: {"cfg": 2.5}}, "models_dir": str(tmp_path / "m")}), "utf-8")
    monkeypatch.setattr(imagegen, "_home", lambda: host(tmp_path))
    cfg = imagegen.read_config()
    assert cfg["motor"]["sd_cli"].endswith("runtimes/sd/vulkan/sd-cli.exe")
    assert cfg["image"]["steps"] == 8 and "\\" not in cfg["image"]["vae"]
    assert cfg["image_models"][host(modelo)] == {"cfg": 2.5}


def test_lote_gera_decide_e_apaga(cfg, monkeypatch):
    feitos = []

    def gera(prompt, out, opts, job_id="", refs=(), progresso=None, previa=None):
        feitos.append(opts["seed"])
        workspace.to_container(out).parent.mkdir(parents=True, exist_ok=True)
        workspace.to_container(out).write_bytes(PNG)
        return out

    monkeypatch.setattr(imagegen, "generate", gera)
    monkeypatch.setattr(imagegen, "valida", lambda *a: None)
    monkeypatch.setattr(lotes.mirror, "write", lambda c: None)
    with db.session() as s:
        c = db.Conversation(kind="imagem")
        s.add(c)
        s.commit()
        conv = c.id
    msg = lotes.start(conv, "gato", {}, [host(cfg / "modelos" / "sd15.safetensors")], 3, 10, "incremental")
    for _ in range(200):
        if lotes._mensagem(msg["id"])["status"] != "running":
            break
        time.sleep(0.02)
    m = lotes._mensagem(msg["id"])
    assert feitos == [10, 11, 12] and all(i["status"] == "pronta" for i in m["meta"]["images"])
    fica = m["meta"]["images"][0]["path"]
    assert lotes.servivel(fica)
    out = lotes.decidir(msg["id"], [fica])
    descartadas = [i["path"] for i in out["meta"]["images"] if i["status"] == "descartada"]
    assert len(descartadas) == 2 and all("/descartadas/" in p for p in descartadas)
    assert workspace.to_container(descartadas[0]).is_file()
    assert lotes.apagar_imagens(conv) == 3
    assert not downloads.cancelled(m["meta"]["job"])


def test_arquivo_fora_da_pasta_nao_e_servido(cfg):
    assert not lotes.servivel("C:/Windows/win.ini")


def _fim(message_id):
    for _ in range(300):
        m = lotes._mensagem(message_id)
        if m["status"] != "running":
            return m
        time.sleep(0.02)
    return m


def test_ampliar_imagem_lanczos_e_esrgan_que_nao_amplia(cfg, monkeypatch):
    """Lanczos é o Pillow aqui no container; ESRGAN vai pelo runner. O sd-cli que não carrega o modelo grava a
    própria entrada: isso vira erro, não uma "ampliação" do mesmo tamanho."""
    import zipfile
    from PIL import Image
    from app import ampliar
    rf = RunnerFalso(monkeypatch)  # grava o PNG 1×1 no -o: o "ESRGAN que não ampliou"
    monkeypatch.setattr(imagegen.time, "sleep", lambda s: None)
    monkeypatch.setattr(ampliar.time, "sleep", lambda s: None)
    monkeypatch.setattr(imagegen, "_gpu", lambda exe: "vulkan0")
    monkeypatch.setattr(lotes.mirror, "write", lambda c: None)
    with db.session() as s:
        c = db.Conversation(kind="imagem")
        s.add(c)
        s.commit()
        conv = c.id
    src = cfg / "de-fora" / "foto.jpg"
    src.parent.mkdir()
    Image.new("RGB", (5, 4)).save(src)
    m = _fim(lotes.ampliar_arquivo(conv, host(src), 4)["id"])
    item = m["meta"]["images"][0]
    assert m["status"] == "pronto" and item["path"].endswith("-foto-4x (Lanczos).png") and item["model_name"] == "Lanczos · 4×"
    assert Image.open(workspace.to_container(item["path"])).size == (20, 16)
    assert (m["meta"]["opts"]["width"], m["meta"]["opts"]["height"]) == (20, 16)

    esrgan = cfg / "modelos" / "RealESRGAN_x4plus.pth"
    with zipfile.ZipFile(esrgan, "w") as z:
        z.writestr("archive/data.pkl", b"conv_first.weight body.0.rdb1.conv1.weight")
    ampliar._achados.cache_clear()
    assert [x["name"] for x in ampliar.catalogo()["no_disco"]] == ["RealESRGAN_x4plus"]
    m = _fim(lotes.ampliar(m["id"], item["path"], 2, host(esrgan))["id"])
    assert m["status"] == "erro" and "mesmo tamanho" in m["meta"]["images"][0]["error"]
    assert "'-M' 'upscale'" in rf.comandos[-1][1] and "'--backend' 'vulkan0'" in rf.comandos[-1][1]
    with pytest.raises(lotes.ToolError, match="2× ou 4×"):
        lotes.ampliar_arquivo(conv, host(src), 3)


def _safetensors(f, camadas):
    import struct
    cab = json.dumps({n: {"dtype": "F16", "shape": [1], "data_offsets": [2 * i, 2 * i + 2]} for i, n in enumerate(camadas)}).encode()
    f.write_bytes(struct.pack("<Q", len(cab)) + cab + b"\0\0" * len(camadas))


def test_seedvr2_e_esrgan_antigo_no_catalogo_e_o_driver_pelo_runner(cfg, monkeypatch):
    """UltraSharp (formato antigo) e SeedVR2 aparecem com o tipo; o SeedVR2 roda o comfy_job.py pelo runner e o
    resultado sai do log (FASE vira progresso, OK vira tamanho)."""
    from PIL import Image
    from app import ampliar
    m = cfg / "modelos"
    _safetensors(m / "4x-UltraSharp.safetensors", ["model.0.weight", "model.1.sub.0.RDB1.conv1.0.weight"])
    _safetensors(m / "seedvr2_3b_fp16.safetensors", ["blocks.0.ada.txt.attn_gate"])
    _safetensors(m / "qualquer-vae.safetensors", ["decoder.up_blocks.0.upsamplers.0.upscale_conv.weight"])
    ampliar._achados.cache_clear()
    assert {x["name"]: x["tipo"] for x in ampliar.catalogo()["no_disco"]} == {"4x-UltraSharp": "esrgan", "seedvr2_3b_fp16": "seedvr2"}
    seed = host(m / "seedvr2_3b_fp16.safetensors")
    src = cfg / "a.png"
    Image.new("RGB", (5, 4)).save(src)
    monkeypatch.setattr(ampliar, "comfy_dir", lambda: "")
    with pytest.raises(lotes.ToolError, match="ComfyUI"):  # sem o portátil do desktop
        lotes._validar_ampliacao(host(src), 2, seed)
    monkeypatch.setattr(ampliar, "comfy_dir", lambda: host(cfg / "comfy"))
    comandos = []

    def roda(a, cwd, job_id, limite_s, ao_ler=None):
        comandos.append(a)
        saida = a[a.index("--saida") + 1]
        Image.new("RGB", (20, 16)).save(workspace.to_container(saida))
        log = "FASE iniciando o ComfyUI\nFASE ampliando\nPROGRESSO 0.400\nOK 20x16"
        ao_ler and ao_ler(log)
        return {"exit_code": 0}, log
    monkeypatch.setattr(ampliar, "_rodar", roda)
    fases = []
    out = host(cfg / "saida" / "a-4x.png")
    workspace.to_container(out).parent.mkdir(parents=True, exist_ok=True)
    assert ampliar.ampliar_imagem(host(src), out, 4, seed, progresso=lambda f, x: fases.append((f, x))) == {"w": 20, "h": 16}
    assert fases == [("iniciando o ComfyUI", None), ("ampliando", None), (None, 0.4)]
    a = comandos[0]
    assert a[a.index("--vae") + 1].endswith("/qualquer-vae.safetensors") and a[1:3] == ["-X", "utf8"]
    assert workspace.to_container(a[a.index("-s") + 1]).read_bytes().startswith(b'"""Uma amplia')  # o driver foi para o disco


def test_dat_hat_pelo_comfyui_do_desktop(cfg, monkeypatch):
    """DAT/HAT/SwinIR (e o RealESRGAN x2plus) vão pelo mesmo driver, com --modo spandrel e sem VAE."""
    from PIL import Image
    from app import ampliar
    m = cfg / "modelos"
    _safetensors(m / "4x-UltraSharpV2.safetensors", ["before_RG.1.weight", "conv_after_body.weight"])
    ampliar._achados.cache_clear()
    dat = host(m / "4x-UltraSharpV2.safetensors")
    assert ampliar.tipo_local(dat) == "spandrel"
    assert {x["name"]: x["tipo"] for x in ampliar.catalogo()["no_disco"]}["4x-UltraSharpV2"] == "spandrel"
    monkeypatch.setattr(ampliar, "comfy_dir", lambda: host(cfg / "comfy"))
    src = cfg / "b.png"
    Image.new("RGB", (5, 4)).save(src)
    comandos = []

    def roda(a, cwd, job_id, limite_s, ao_ler=None):
        comandos.append(a)
        Image.new("RGB", (20, 16)).save(workspace.to_container(a[a.index("--saida") + 1]))
        return {"exit_code": 0}, "FASE ampliando\nOK 20x16"
    monkeypatch.setattr(ampliar, "_rodar", roda)
    out = host(cfg / "saida" / "b-4x.png")
    workspace.to_container(out).parent.mkdir(parents=True, exist_ok=True)
    assert ampliar.ampliar_imagem(host(src), out, 4, dat) == {"w": 20, "h": 16}
    assert comandos[0][comandos[0].index("--modo") + 1] == "spandrel" and "--vae" not in comandos[0]


def test_redesenhar_com_checkpoint_sdxl_pelo_comfyui(cfg, monkeypatch):
    """Um checkpoint SDXL completo (qualquer nome) vira "redesenhar": vai pelo driver com prompt, força e bloco de 1024;
    o prompt e a força ficam na ampliação, e sem prompt vale o da geração."""
    from PIL import Image
    from app import ampliar
    m = cfg / "modelos"
    _safetensors(m / "meu-modelo.safetensors", ["model.diffusion_model.input_blocks.0.0.weight",
                                                  "first_stage_model.encoder.conv_in.weight", "conditioner.embedders.0.x"])
    ampliar._achados.cache_clear()
    ck = host(m / "meu-modelo.safetensors")
    assert ampliar.tipo_checkpoint(ck) == "sdxl"
    assert {x["name"]: x["tipo"] for x in ampliar.catalogo()["no_disco"]}["meu-modelo"] == "redesenhar"
    monkeypatch.setattr(ampliar, "comfy_dir", lambda: host(cfg / "comfy"))
    src = cfg / "c.png"
    Image.new("RGB", (5, 4)).save(src)
    comandos = []

    def roda(a, cwd, job_id, limite_s, ao_ler=None):
        comandos.append(a)
        Image.new("RGB", (10, 8)).save(workspace.to_container(a[a.index("--saida") + 1]))
        return {"exit_code": 0}, "FASE redesenhando o bloco 1 de 1\nOK 10x8"
    monkeypatch.setattr(ampliar, "_rodar", roda)
    out = host(cfg / "saida" / "c-2x.png")
    workspace.to_container(out).parent.mkdir(parents=True, exist_ok=True)
    assert ampliar.ampliar_imagem(host(src), out, 2, ck, prompt="a cup", forca=0.5) == {"w": 10, "h": 8}
    a = comandos[0]
    assert [a[a.index(k) + 1] for k in ("--modo", "--prompt", "--forca", "--bloco")] == ["redesenhar", "a cup", "0.50", "1024"]
    assert lotes._redesenho(ck, " a cup ", None) == {"prompt": "a cup", "forca": ampliar.FORCA_PADRAO}
    assert lotes._redesenho(host(src), "x", 0.5) == {}  # não é checkpoint: nada de prompt
    with pytest.raises(lotes.ToolError, match="Força"):
        lotes._redesenho(ck, "x", 0.95)
    assert lotes.prompt_da_imagem("foto.png", {"ampliacao": {"origem": "x"}}) == ""  # nome de arquivo não é prompt
    assert lotes.prompt_da_imagem("a cat", None) == "a cat"
