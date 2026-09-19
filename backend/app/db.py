from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, ForeignKey, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from . import config


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="Nova conversa")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now)
    messages: Mapped[list["Message"]] = relationship(
        cascade="all, delete-orphan", order_by="Message.id", back_populates="conversation")


class Message(Base):
    """role: user | assistant | tool | event (event = aviso da UI, nunca vai para o modelo)."""
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    thinking: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{id,name,arguments}]
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ok | erro | rejeitada | cancelada
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # args, preview, warning kind...
    created_at: Mapped[datetime] = mapped_column(default=_now)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "id", "role", "content", "thinking", "tool_calls", "tool_call_id", "name", "status", "meta")}


class ModelSetting(Base):
    __tablename__ = "model_settings"
    model: Mapped[str] = mapped_column(String(300), primary_key=True)
    tool_mode: Mapped[str] = mapped_column(String(10), default="auto")  # native | text | auto


Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{config.DB_PATH}", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)


def session() -> Session:
    return Session(engine, expire_on_commit=False)


def get_tool_mode(model: str) -> str:
    with session() as s:
        ms = s.get(ModelSetting, model)
        return ms.tool_mode if ms else "auto"
