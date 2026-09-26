"""Sandbox no Forja em Docker: o isolamento dos comandos é o do forja-runner (compose), não o do backend.

Só o que o código compartilhado consulta.
"""
from __future__ import annotations

import contextvars

MODOS_ISOLADO = ("desligado",)
MOTORES = ("auto",)
AUTONOMO: contextvars.ContextVar = contextvars.ContextVar("sandbox_autonomo", default=lambda: False)


def modo_isolado() -> str:
    return "desligado"


def motor_ativo() -> str:
    return ""


def nota_para_o_modelo() -> str:
    return ""
