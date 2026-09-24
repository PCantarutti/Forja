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
