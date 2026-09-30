import os
import tempfile

# db.py cria o SQLite no import; nos testes, num diretório temporário.
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="forja-test-"), "forja.db")  # nunca o banco real


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _disco_do_teste_montado(monkeypatch, tmp_path):
    """O disco onde o pytest roda vale como "disco do usuário" montado no mesmo caminho.

    Os testes que vieram do Forja desktop guardam `tmp_path` como pasta da conversa e esperam que
    `workspace.resolve` a devolva igual, como lá. Teste que precisa de outro mapeamento sobrescreve.
    """
    from app import config

    drive = tmp_path.drive  # "C:" no Windows, "" no Linux
    monkeypatch.setattr(config, "HOST_MOUNTS", f"{drive[0]}={drive}/" if drive else "/=/")
_TMP = tempfile.mkdtemp(prefix="forja-test-")
os.environ["DB_PATH"] = os.path.join(_TMP, "forja.db")  # nunca o banco real
# E o resto dos dados junto: sem isto, o lifespan do TestClient roda mirror.sync() e escreve o
# espelho das conversas de teste em %APPDATA%\Forja\conversas, o diretório de quem está rodando.
os.environ.setdefault("FORJA_DATA", _TMP)
# Pasta de trabalho padrão também temporária: o Maestro faz commit por tarefa, e um teste sem
# workspace próprio não pode cair num repositório git de verdade (~/Forja ou outro).
os.environ.setdefault("WORKSPACE_ROOT", os.path.join(_TMP, "ws"))
os.makedirs(os.environ["WORKSPACE_ROOT"], exist_ok=True)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _sem_titulo_do_design(monkeypatch):
    """O título do Design é uma chamada extra ao modelo: os modelos falsos dos testes não esperam por ela."""
    from app import design
    monkeypatch.setattr(design, "RETITULAR", False)
