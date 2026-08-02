from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Visit(Base):
    __tablename__ = "visits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ip_hash: Mapped[str] = mapped_column(String(64), index=True)
    visitor_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    visitor_company: Mapped[str | None] = mapped_column(String(120), nullable=True)
    research_status: Mapped[str] = mapped_column(String(24), default="idle")
    research_opted_out: Mapped[bool] = mapped_column(default=False)
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_candidate_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    @property
    def confirmed_candidate(self) -> dict[str, Any] | None:
        if not self.confirmed_candidate_json:
            return None
        value = json.loads(self.confirmed_candidate_json)
        return value if isinstance(value, dict) else None


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("visits.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    sources_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    @property
    def sources(self) -> list[str]:
        value = json.loads(self.sources_json)
        return value if isinstance(value, list) else []


class Database:
    def __init__(self, url: str) -> None:
        kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **kwargs)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine) as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def create_visit(self, ip_hash: str) -> Visit:
        with self.session() as db:
            visit = Visit(id=str(uuid4()), ip_hash=ip_hash)
            db.add(visit)
            db.flush()
            db.expunge(visit)
            return visit

    def get_visit(self, session_id: str) -> Visit | None:
        with self.session() as db:
            visit = db.get(Visit, session_id)
            if visit is not None:
                db.expunge(visit)
            return visit

    def update_visit(self, session_id: str, **values: Any) -> Visit | None:
        with self.session() as db:
            visit = db.get(Visit, session_id)
            if visit is None:
                return None
            for key, value in values.items():
                setattr(visit, key, value)
            visit.last_seen_at = utc_now()
            db.flush()
            db.expunge(visit)
            return visit

    def add_message(
        self, session_id: str, role: str, content: str, sources: list[str] | None = None
    ) -> None:
        with self.session() as db:
            db.add(
                Message(
                    session_id=session_id,
                    role=role,
                    content=content,
                    sources_json=json.dumps(sources or []),
                )
            )
            visit = db.get(Visit, session_id)
            if visit is not None:
                visit.message_count += 1
                visit.last_seen_at = utc_now()

    def recent_messages(self, session_id: str, limit: int = 10) -> list[Message]:
        with self.session() as db:
            rows = list(
                db.scalars(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.id.desc())
                    .limit(limit)
                )
            )
            for row in rows:
                db.expunge(row)
            return list(reversed(rows))

    def list_visits(self, limit: int = 200) -> list[Visit]:
        with self.session() as db:
            rows = list(
                db.scalars(select(Visit).order_by(Visit.created_at.desc()).limit(limit))
            )
            for row in rows:
                db.expunge(row)
            return rows

    def questions_for(self, session_id: str, limit: int = 20) -> list[str]:
        with self.session() as db:
            return list(
                db.scalars(
                    select(Message.content)
                    .where(Message.session_id == session_id, Message.role == "user")
                    .order_by(Message.id.desc())
                    .limit(limit)
                )
            )

    def delete_visit(self, session_id: str) -> None:
        with self.session() as db:
            for message in db.scalars(
                select(Message).where(Message.session_id == session_id)
            ):
                db.delete(message)
            visit = db.get(Visit, session_id)
            if visit is not None:
                db.delete(visit)
