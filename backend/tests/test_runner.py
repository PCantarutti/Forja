"""forja-runner (lado do backend): escolha do alvo, bloco Ambiente e cliente, sem runner de verdade."""
import pytest

from app import agent, runner, shell, workspace
from app.tools import REGISTRY, ToolError

WIN = {"ok": True, "system": "Windows", "release": "11", "shell": "powershell",
       "versions": {"node": "v22.1.0", "python": "Python 3.13.1", "git": None}}


def test_pick_target_auto_prefers_host_only_when_possible():
    assert shell.pick_target(None, True, "C:/x") == "host"
    assert shell.pick_target("auto", True, None) == "container"
    assert shell.pick_target("auto", False, "C:/x") == "container"
    assert shell.pick_target("container", True, "C:/x") == "container"
    with pytest.raises(ToolError, match="desligado"):
        shell.pick_target("host", False, "C:/x")
    with pytest.raises(ToolError, match="target deve ser"):
        shell.pick_target("windows", True, "C:/x")


def test_serve_tools_registered():
    assert REGISTRY["serve_start"].always_ask and REGISTRY["serve_start"].mutating
    assert not REGISTRY["serve_status"].mutating and REGISTRY["serve_stop"].mutating
    assert "target" in REGISTRY["run_command"].parameters["properties"]


def test_describe():
    assert runner.describe(None) == "desligado"
    assert runner.describe({"ok": False, "error": "token"}) == "erro: token"
    assert runner.describe(WIN) == "Windows 11 (powershell)"


def _with(info, host, fn):
    """Simula o runner visto pela execução: INFO=None cai no cache, então o cache também é definido."""
    t1 = runner.INFO.set(info)
    t2 = workspace.CURRENT.set(None)
    saved = dict(runner._cache)
    runner._cache.update(at=1e12, info=info)
    try:
        return fn()
    finally:
        runner.INFO.reset(t1)
        workspace.CURRENT.reset(t2)
        runner._cache.update(saved)


def test_environment_block_with_runner(monkeypatch):
    monkeypatch.setattr(workspace, "to_host", lambda p: "C:/Users/pedro/Dev/app")
    text = "\n".join(_with(WIN, "C:/x", lambda: agent.environment_block(["run_command", "serve_start"])))
    assert "C:/Users/pedro/Dev/app no sistema do usuário" in text
    assert "Windows 11 (powershell), ligado via forja-runner" in text
    assert "node v22.1.0" in text and "git" not in text.split("Instalado:")[1].split(".")[0]
    assert "';'" in text and "host.docker.internal:PORTA" in text and "target='container'" in text


def test_environment_block_without_runner(monkeypatch):
    monkeypatch.setattr(workspace, "to_host", lambda p: "C:/Users/pedro/Dev/app")
    text = "\n".join(_with(None, "C:/x", lambda: agent.environment_block(["run_command"])))
    assert "desligado" in text and "binários Linux" in text and "http://localhost:PORTA" in text
    text = "\n".join(_with({"ok": False, "error": "token"}, "C:/x", lambda: agent.environment_block(["run_command"])))
    assert "falhou (token)" in text


def test_environment_block_without_shell_tools_is_short(monkeypatch):
    monkeypatch.setattr(workspace, "to_host", lambda p: None)
    lines = _with(WIN, None, lambda: agent.environment_block(["read_file"]))
    assert len(lines) == 2 and "container do Forja" in lines[1]


def test_run_command_uses_runner_when_online(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(workspace, "to_host", lambda p: "C:/Users/pedro/Dev/app")
    monkeypatch.setattr(runner, "run", lambda c, cwd, t: calls.update(command=c, cwd=cwd) or {"exit_code": 0, "output": "ok!"})
    out = _with(WIN, "C:/x", lambda: shell.run_command(tmp_path, {"command": "npm test"}))
    assert calls == {"command": "npm test", "cwd": "C:/Users/pedro/Dev/app"} and "Windows 11" in out and "ok!" in out
    monkeypatch.setattr(runner, "run", lambda c, cwd, t: {"exit_code": 1, "output": "falhou"})
    with pytest.raises(ToolError, match="falhou"):
        _with(WIN, "C:/x", lambda: shell.run_command(tmp_path, {"command": "npm test"}))


def test_command_preview_says_where(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "to_host", lambda p: "C:/Users/pedro/Dev/app")
    pv = _with(WIN, "C:/x", lambda: shell.command_preview(tmp_path, {"command": "ls"}))
    assert pv["path"].endswith("no seu sistema") and pv["path"].startswith("C:/Users/pedro/Dev/app")
    pv = _with(None, "C:/x", lambda: shell.command_preview(tmp_path, {"command": "ls"}))
    assert pv["path"] == "C:/Users/pedro/Dev/app"
