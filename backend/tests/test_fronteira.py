"""A API recusa requisição vinda de outra página.

O compose publica a interface em 0.0.0.0:7001 e o nginx repassa /api para cá. Sem esta checagem,
uma página que o usuário visite no navegador dispara POST contra esse endereço e alcança /api/term,
que é execução de shell, e /api/open, que abre arquivo — o navegador manda a requisição, ele só não
deixa a página ler a resposta.

Ver o mesmo no repo Desktop (cb83369, 4dc3ebe), onde há também um token por execução.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app, mesma_origem


@pytest.fixture
def cliente():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("origin", [
    "https://evil.example",
    "http://localhost:5173",   # outro servidor da mesma máquina não é o Forja
    "http://127.0.0.1:9999",   # nem outra porta
    "null",                    # iframe sandbox / file://
])
def test_origem_de_fora_e_recusada(cliente, origin):
    r = cliente.get("/api/config", headers={"Origin": origin, "Host": "localhost:7001"})
    assert r.status_code == 403


def test_a_propria_interface_passa(cliente):
    r = cliente.get("/api/config", headers={"Origin": "http://localhost:7001", "Host": "localhost:7001"})
    assert r.status_code == 200


def test_de_outra_maquina_da_rede_tambem_passa(cliente):
    """Abrir o Forja de outro computador é uso legítimo aqui: o compose publica em 0.0.0.0."""
    r = cliente.get("/api/config", headers={"Origin": "http://192.168.0.10:7001", "Host": "192.168.0.10:7001"})
    assert r.status_code == 200


def test_sem_origin_passa(cliente):
    """curl e o próprio runner não mandam Origin, e não é esse o vetor."""
    assert cliente.get("/api/config").status_code == 200


def test_host_vazio_nao_libera():
    assert not mesma_origem("http://qualquer", "")
