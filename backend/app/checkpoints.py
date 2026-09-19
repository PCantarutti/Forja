"""Checkpoints: desfazer as alterações de arquivo feitas pelo agente.

Antes da PRIMEIRA escrita num arquivo dentro de um turno, o conteúdo anterior é salvo (ou o fato
de que ele não existia). Restaurar um turno volta os arquivos ao estado de antes dele; restaurar
"a partir de" um turno desfaz também os turnos seguintes, na ordem inversa, para não deixar
estados misturados. Só write_file/edit_file são rastreados: run_command, MCP e navegador não
(o README avisa).
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from . import config, db, workspace

TRACKED = {"write_file", "edit_file"}


def record(conv_id: int, turn_id: int, path: Path) -> None:
    path = Path(path)
    with db.session() as s:
        exists = s.scalar(select(db.Checkpoint.id).where(
            db.Checkpoint.conversation_id == conv_id, db.Checkpoint.turn_id == turn_id,
            db.Checkpoint.path == str(path)))
        if exists:
            return  # já temos o estado de antes deste turno
        existed = path.is_file()
        content = path.read_bytes() if existed and path.stat().st_size <= config.MAX_FILE_BYTES else None
        if existed and content is None:
            return  # grande demais para guardar; não finge que dá para desfazer
        s.add(db.Checkpoint(conversation_id=conv_id, turn_id=turn_id, path=str(path),
                            existed=existed, content=content))
        s.commit()


def summary(conv_id: int) -> dict[int, list[str]]:
    """{turn_id: [arquivos]} para a UI (caminhos do Windows quando possível)."""
    out: dict[int, list[str]] = {}
    with db.session() as s:
        for cp in s.scalars(select(db.Checkpoint).where(db.Checkpoint.conversation_id == conv_id)
                            .order_by(db.Checkpoint.id)):
            out.setdefault(cp.turn_id, []).append(workspace.to_host(Path(cp.path)) or cp.path)
    return out


def restore_from(conv_id: int, turn_id: int) -> list[str]:
    """Desfaz os turnos >= turn_id (mais novos primeiro). Devolve os arquivos restaurados."""
    restored: list[str] = []
    with db.session() as s:
        rows = list(s.scalars(select(db.Checkpoint).where(
            db.Checkpoint.conversation_id == conv_id, db.Checkpoint.turn_id >= turn_id)
            .order_by(db.Checkpoint.turn_id.desc(), db.Checkpoint.id.desc())))
        for cp in rows:
            p = Path(cp.path)
            if cp.existed:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(cp.content or b"")
            elif p.is_file():
                p.unlink()
            label = workspace.to_host(p) or cp.path
            if label not in restored:
                restored.append(label)
            s.delete(cp)
        s.commit()
    return restored


def forget_from(conv_id: int, turn_id: int) -> None:
    """Descarta checkpoints de turnos apagados sem restaurar (o usuário escolheu manter os arquivos)."""
    with db.session() as s:
        s.query(db.Checkpoint).filter(db.Checkpoint.conversation_id == conv_id,
                                      db.Checkpoint.turn_id >= turn_id).delete()
        s.commit()
