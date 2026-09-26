"""Celular no Forja em Docker: sem pareamento nem push (isso é do Forja Desktop, que roda na máquina do usuário).

Só o que o código compartilhado chama, sem efeito.
"""
from __future__ import annotations


def notify(ev: dict, conv_id: int, run_id: str) -> None:
    return None


def revoga(ids) -> None:
    return None


def avisa(titulo: str, texto: str, conv_id: int | None = None) -> None:
    return None


def devices() -> list[str]:
    return []


def defaults() -> dict:
    return {}
