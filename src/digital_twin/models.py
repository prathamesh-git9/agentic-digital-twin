from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from .email_utils import recipient_key


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
    confirmed_person_dossier_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_company_dossier_json: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    confirmed_email_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    visitor_intent: Mapped[str | None] = mapped_column(String(16), nullable=True)
    verified_corporate_domain: Mapped[str | None] = mapped_column(
        String(253), nullable=True
    )
    handoff_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    crm_stage: Mapped[str] = mapped_column(String(16), default="visited", index=True)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    tool_call_usage: Mapped[int] = mapped_column(Integer, default=0)
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

    @property
    def confirmed_person_dossier(self) -> dict[str, Any] | None:
        return _json_object(self.confirmed_person_dossier_json)

    @property
    def confirmed_company_dossier(self) -> dict[str, Any] | None:
        return _json_object(self.confirmed_company_dossier_json)

    @property
    def confirmed_email(self) -> dict[str, Any] | None:
        return _json_object(self.confirmed_email_json)


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


class OutreachDraft(Base):
    __tablename__ = "outreach_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("visits.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    person_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    recipient: Mapped[str] = mapped_column(String(320))
    recipient_status: Mapped[str] = mapped_column(String(24))
    recipient_pattern: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recipient_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recipient_why: Mapped[str] = mapped_column(Text, default="")
    recipient_source_url: Mapped[str] = mapped_column(Text, default="")
    recipient_source_kind: Mapped[str] = mapped_column(String(40), default="public_web")
    recipient_company_level: Mapped[bool] = mapped_column(Boolean, default=False)
    subject: Mapped[str] = mapped_column(String(240))
    variants_json: Mapped[str] = mapped_column(Text)
    linkedin_json: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), default="initial")
    parent_draft_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    @property
    def variants(self) -> list[dict[str, Any]]:
        value = json.loads(self.variants_json)
        return value if isinstance(value, list) else []

    @property
    def linkedin(self) -> dict[str, Any]:
        return _json_object(self.linkedin_json) or {}


class OutreachAction(Base):
    __tablename__ = "outreach_actions"
    __table_args__ = (UniqueConstraint("send_key", name="uq_outreach_send_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    draft_id: Mapped[str] = mapped_column(String(36), index=True)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)
    recipient: Mapped[str] = mapped_column(String(320))
    body_hash: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32), index=True)
    approver: Mapped[str] = mapped_column(String(120))
    transport: Mapped[str] = mapped_column(String(24))
    send_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    @property
    def metadata_value(self) -> dict[str, Any]:
        return _json_object(self.metadata_json) or {}


class Suppression(Base):
    __tablename__ = "outreach_suppressions"

    address: Mapped[str] = mapped_column(String(320), primary_key=True)
    reason: Mapped[str] = mapped_column(String(240), default="requested")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DomainPatternStat(Base):
    __tablename__ = "email_domain_pattern_stats"

    domain: Mapped[str] = mapped_column(String(253), primary_key=True)
    pattern: Mapped[str] = mapped_column(String(64), primary_key=True)
    bounce_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProofPack(Base):
    __tablename__ = "proof_packs"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    @property
    def payload(self) -> dict[str, Any]:
        return _json_object(self.payload_json) or {}


def _json_object(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    decoded = json.loads(value)
    return decoded if isinstance(decoded, dict) else None


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
        self._migrate_visit_columns()
        self._migrate_outreach_draft_columns()

    def _migrate_visit_columns(self) -> None:
        """Add v2 nullable/defaulted columns for existing SQLite deployments."""
        if self.engine.dialect.name != "sqlite":
            return
        columns = {
            column["name"] for column in inspect(self.engine).get_columns("visits")
        }
        additions = {
            "confirmed_person_dossier_json": "TEXT",
            "confirmed_company_dossier_json": "TEXT",
            "confirmed_email_json": "TEXT",
            "visitor_intent": "VARCHAR(16)",
            "verified_corporate_domain": "VARCHAR(253)",
            "handoff_notified": "BOOLEAN NOT NULL DEFAULT 0",
            "crm_stage": "VARCHAR(16) NOT NULL DEFAULT 'visited'",
            "tool_call_usage": "INTEGER NOT NULL DEFAULT 0",
        }
        missing = {name: ddl for name, ddl in additions.items() if name not in columns}
        if not missing:
            return
        with self.engine.begin() as connection:
            for name, ddl in missing.items():
                connection.exec_driver_sql(  # noqa: S608 - fixed internal DDL allowlist
                    f"ALTER TABLE visits ADD COLUMN {name} {ddl}"
                )

    def _migrate_outreach_draft_columns(self) -> None:
        if self.engine.dialect.name != "sqlite":
            return
        columns = {
            column["name"]
            for column in inspect(self.engine).get_columns("outreach_drafts")
        }
        additions = {
            "recipient_pattern": "VARCHAR(64)",
            "person_key": "VARCHAR(64) NOT NULL DEFAULT ''",
            "recipient_score": "INTEGER",
            "recipient_why": "TEXT NOT NULL DEFAULT ''",
            "recipient_source_url": "TEXT NOT NULL DEFAULT ''",
            "recipient_source_kind": "VARCHAR(40) NOT NULL DEFAULT 'public_web'",
            "recipient_company_level": "BOOLEAN NOT NULL DEFAULT 0",
        }
        missing = {name: ddl for name, ddl in additions.items() if name not in columns}
        if not missing:
            return
        with self.engine.begin() as connection:
            for name, ddl in missing.items():
                connection.exec_driver_sql(  # noqa: S608 - fixed internal DDL allowlist
                    f"ALTER TABLE outreach_drafts ADD COLUMN {name} {ddl}"
                )

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

    def consume_tool_call(self, session_id: str, limit: int) -> tuple[bool, int]:
        """Atomically reserve one call from a session's separate tool budget."""
        if limit <= 0:
            return False, 0
        with self.session() as db:
            reserved = db.execute(
                update(Visit)
                .where(
                    Visit.id == session_id,
                    Visit.tool_call_usage < limit,
                )
                .values(tool_call_usage=Visit.tool_call_usage + 1)
            )
            if reserved.rowcount != 1:
                return False, 0
            used = db.scalar(select(Visit.tool_call_usage).where(Visit.id == session_id))
            return True, max(0, limit - int(used or 0))

    def tool_budget_remaining(self, session_id: str, limit: int) -> int:
        with self.session() as db:
            visit = db.get(Visit, session_id)
            if visit is None:
                return 0
            return max(0, limit - int(visit.tool_call_usage or 0))

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

    def messages_for(self, session_id: str, limit: int = 200) -> list[Message]:
        with self.session() as db:
            rows = list(
                db.scalars(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.id.asc())
                    .limit(limit)
                )
            )
            for row in rows:
                db.expunge(row)
            return rows

    def create_outreach_draft(
        self,
        *,
        session_id: str,
        candidate_id: str,
        person_key: str = "",
        recipient: str,
        recipient_status: str,
        recipient_pattern: str | None = None,
        recipient_score: int | None = None,
        recipient_why: str = "",
        recipient_source_url: str = "",
        recipient_source_kind: str = "public_web",
        recipient_company_level: bool = False,
        subject: str,
        variants: list[dict[str, Any]],
        linkedin: dict[str, Any],
        kind: str = "initial",
        parent_draft_id: str | None = None,
    ) -> OutreachDraft:
        with self.session() as db:
            draft = OutreachDraft(
                id=str(uuid4()),
                session_id=session_id,
                candidate_id=candidate_id,
                person_key=person_key,
                recipient=recipient,
                recipient_status=recipient_status,
                recipient_pattern=recipient_pattern,
                recipient_score=recipient_score,
                recipient_why=recipient_why,
                recipient_source_url=recipient_source_url,
                recipient_source_kind=recipient_source_kind,
                recipient_company_level=recipient_company_level,
                subject=subject,
                variants_json=json.dumps(variants),
                linkedin_json=json.dumps(linkedin),
                kind=kind,
                parent_draft_id=parent_draft_id,
            )
            db.add(draft)
            db.flush()
            db.expunge(draft)
            return draft

    def get_outreach_draft(self, draft_id: str) -> OutreachDraft | None:
        with self.session() as db:
            draft = db.get(OutreachDraft, draft_id)
            if draft is not None:
                db.expunge(draft)
            return draft

    def latest_outreach_draft(self, session_id: str) -> OutreachDraft | None:
        with self.session() as db:
            draft = db.scalar(
                select(OutreachDraft)
                .where(OutreachDraft.session_id == session_id)
                .order_by(OutreachDraft.created_at.desc())
                .limit(1)
            )
            if draft is not None:
                db.expunge(draft)
            return draft

    def latest_outreach_draft_for(
        self, session_id: str, candidate_id: str
    ) -> OutreachDraft | None:
        with self.session() as db:
            draft = db.scalar(
                select(OutreachDraft)
                .where(
                    OutreachDraft.session_id == session_id,
                    OutreachDraft.candidate_id == candidate_id,
                )
                .order_by(OutreachDraft.created_at.desc())
                .limit(1)
            )
            if draft is not None:
                db.expunge(draft)
            return draft

    def outreach_drafts_for(self, session_id: str) -> list[OutreachDraft]:
        with self.session() as db:
            rows = list(
                db.scalars(
                    select(OutreachDraft)
                    .where(OutreachDraft.session_id == session_id)
                    .order_by(OutreachDraft.created_at.asc())
                )
            )
            for row in rows:
                db.expunge(row)
            return rows

    def record_outreach_action(
        self,
        *,
        session_id: str,
        draft_id: str,
        candidate_id: str,
        recipient: str,
        body_hash: str,
        action: str,
        approver: str,
        transport: str,
        send_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[OutreachAction | None, bool]:
        try:
            with self.session() as db:
                row = OutreachAction(
                    id=str(uuid4()),
                    session_id=session_id,
                    draft_id=draft_id,
                    candidate_id=candidate_id,
                    recipient=recipient,
                    body_hash=body_hash,
                    action=action,
                    approver=approver,
                    transport=transport,
                    send_key=send_key,
                    metadata_json=json.dumps(metadata or {}),
                )
                db.add(row)
                db.flush()
                db.expunge(row)
                return row, True
        except IntegrityError:
            return None, False

    def outreach_actions(self, limit: int = 500) -> list[OutreachAction]:
        with self.session() as db:
            rows = list(
                db.scalars(
                    select(OutreachAction)
                    .order_by(OutreachAction.created_at.desc())
                    .limit(limit)
                )
            )
            for row in rows:
                db.expunge(row)
            return rows

    def outreach_actions_for(
        self,
        *,
        session_id: str | None = None,
        candidate_id: str | None = None,
        action_prefix: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[OutreachAction]:
        statement = select(OutreachAction)
        if session_id is not None:
            statement = statement.where(OutreachAction.session_id == session_id)
        if candidate_id is not None:
            statement = statement.where(OutreachAction.candidate_id == candidate_id)
        if action_prefix is not None:
            statement = statement.where(OutreachAction.action.startswith(action_prefix))
        if since is not None:
            statement = statement.where(OutreachAction.created_at >= since)
        with self.session() as db:
            rows = list(
                db.scalars(
                    statement.order_by(OutreachAction.created_at.desc()).limit(limit)
                )
            )
            for row in rows:
                db.expunge(row)
            return rows

    def suppress(self, address: str, reason: str) -> None:
        normalized = recipient_key(address)
        with self.session() as db:
            existing = db.get(Suppression, normalized)
            if existing is None:
                db.add(Suppression(address=normalized, reason=reason[:240]))

    def is_suppressed(self, address: str) -> bool:
        with self.session() as db:
            return db.get(Suppression, recipient_key(address)) is not None

    def record_pattern_bounce(self, domain: str, pattern: str) -> int:
        normalized_domain = domain.casefold().strip(".")
        with self.session() as db:
            key = {"domain": normalized_domain, "pattern": pattern}
            row = db.get(DomainPatternStat, key)
            if row is None:
                row = DomainPatternStat(
                    domain=normalized_domain, pattern=pattern, bounce_count=1
                )
                db.add(row)
            else:
                row.bounce_count += 1
                row.updated_at = utc_now()
            db.flush()
            return row.bounce_count

    def pattern_bounce_counts(self, domain: str) -> dict[str, int]:
        normalized_domain = domain.casefold().strip(".")
        with self.session() as db:
            rows = db.scalars(
                select(DomainPatternStat).where(
                    DomainPatternStat.domain == normalized_domain
                )
            )
            return {row.pattern: row.bounce_count for row in rows}

    def create_proof_pack(
        self,
        *,
        token: str,
        session_id: str,
        payload: dict[str, Any],
        expires_at: datetime,
    ) -> ProofPack:
        with self.session() as db:
            row = ProofPack(
                token=token,
                session_id=session_id,
                payload_json=json.dumps(payload),
                expires_at=expires_at,
            )
            db.add(row)
            db.flush()
            db.expunge(row)
            return row

    def get_proof_pack(self, token: str) -> ProofPack | None:
        with self.session() as db:
            row = db.get(ProofPack, token)
            if row is None or _as_utc(row.expires_at) <= utc_now():
                return None
            db.expunge(row)
            return row

    def delete_visit(self, session_id: str) -> None:
        with self.session() as db:
            for message in db.scalars(
                select(Message).where(Message.session_id == session_id)
            ):
                db.delete(message)
            for draft in db.scalars(
                select(OutreachDraft).where(OutreachDraft.session_id == session_id)
            ):
                db.delete(draft)
            for proof_pack in db.scalars(
                select(ProofPack).where(ProofPack.session_id == session_id)
            ):
                db.delete(proof_pack)
            visit = db.get(Visit, session_id)
            if visit is not None:
                db.delete(visit)

    def purge_research_artifacts(self, session_id: str) -> None:
        """Delete mutable research/drafts while retaining append-only delivery audit."""
        with self.session() as db:
            for draft in db.scalars(
                select(OutreachDraft).where(OutreachDraft.session_id == session_id)
            ):
                db.delete(draft)
            for proof_pack in db.scalars(
                select(ProofPack).where(ProofPack.session_id == session_id)
            ):
                db.delete(proof_pack)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
