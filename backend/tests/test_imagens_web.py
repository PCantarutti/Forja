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
