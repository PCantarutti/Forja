"""Valor interpolado num comando de shell não pode virar outro comando.

O `gitops` monta strings de comando com título de PR, nome de branch e caminho de arquivo dentro.
Aqui o comando pode ir para o PowerShell do usuário (pelo runner) ou para o bash do container, e a
aspa dupla do PowerShell avalia `$(...)` e crase — então `x$(rm -rf ~)` num título executava.

Ver a mesma correção no repo Desktop, commit a2d2aa7 (lá a citação mora em native.py, porque lá só
existe um shell possível).
"""
import shlex
import subprocess

import pytest

from app import gitops, runner, shell

PERIGOSOS = [
    "x$(rm -rf tudo)",
    "titulo com 'aspa simples'",
    'titulo com "aspa dupla"',
    "a; echo invadiu",
    "a && echo invadiu",
    "a`necho invadiu",
    "/pasta com espaco/arq.txt",
]


@pytest.mark.parametrize("valor", PERIGOSOS)
def test_quote_do_container_devolve_o_valor_intacto(valor):
    """O bash do container é o alvo padrão; o teste roda o bash de verdade."""
    argv = ["bash", "-lc", "printf %s " + shell.quote(valor)]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert r.stdout == valor


@pytest.mark.parametrize("valor", PERIGOSOS)
def test_quote_do_host_windows_usa_a_regra_do_powershell(valor, monkeypatch):
    monkeypatch.setattr(runner, "current", lambda: {"shell": "powershell"})
    citado = shell.quote(valor, "host")
    assert citado.startswith("'") and citado.endswith("'")
    assert citado[1:-1].replace("''", "'") == valor  # a aspa simples é escapada dobrando


def test_quote_do_host_unix_segue_a_regra_posix(monkeypatch):
    monkeypatch.setattr(runner, "current", lambda: {"shell": "bash"})
    assert shell.quote("a; b", "host") == shlex.quote("a; b")


@pytest.mark.parametrize("valor", PERIGOSOS)
def test_gitops_cita_o_caminho_do_diff(valor, monkeypatch, tmp_path):
    """`git diff HEAD -- <caminho>` é o ponto do gitops onde a UI escolhe o valor."""
    vistos = []

    def falso_run(root, cmd, timeout=60):
        vistos.append(cmd)
        return 0, ""

    monkeypatch.setattr(gitops, "_run", falso_run)
    gitops.diff(tmp_path, valor)
    assert vistos and shell.quote(valor) in vistos[0]
