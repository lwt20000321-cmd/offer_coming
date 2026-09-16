"""
数据库模型。基于 pycore/integrations/db/models.py 模板扩展。
"""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类。"""

    pass


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    resume_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    resume_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    resume_text: Mapped[str] = mapped_column(Text, default="")
    resume_parse_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    llm_api_key_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_key_status: Mapped[str] = mapped_column(
        String(16), default="missing", server_default="missing", nullable=False
    )
    llm_key_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    counseling_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    next_question_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def has_llm_api_key(self) -> bool:
        return bool(self.llm_api_key_ciphertext)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("candidates.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("candidates.id"), unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("conversations.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("candidates.id"), nullable=False, index=True
    )
    company_name: Mapped[str] = mapped_column(String(255), default="")
    role_title: Mapped[str] = mapped_column(String(255), default="")
    jd_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    jd_text: Mapped[str] = mapped_column(Text, default="")
    exam_points: Mapped[str] = mapped_column(Text, default="")
    progress_text: Mapped[str] = mapped_column(Text, default="")
    status_text: Mapped[str] = mapped_column(Text, default="")
    normalized_status: Mapped[str] = mapped_column(String(32), default="other", nullable=False)
    deadline: Mapped[str | None] = mapped_column(String(10), nullable=True)
    applied_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    interview_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interview_summary: Mapped[str] = mapped_column(
        Text, default="", server_default="", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class QuestionSet(Base):
    __tablename__ = "question_sets"
    __table_args__ = (
        UniqueConstraint("candidate_id", "beijing_date", name="uq_question_set_candidate_date"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("candidates.id"), nullable=False, index=True
    )
    beijing_date: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    review: Mapped[str | None] = mapped_column(Text, nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    question_set_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("question_sets.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_application_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("applications.id"), nullable=True
    )


class NudgeLog(Base):
    __tablename__ = "nudge_logs"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "beijing_hour_key",
            "channel",
            name="uq_nudge_candidate_hour_channel",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("candidates.id"), nullable=False, index=True
    )
    beijing_hour_key: Mapped[str] = mapped_column(String(16), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    tone_level: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("candidates.id"), nullable=False, index=True
    )
    application_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("applications.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), default="")
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class KnowledgeSegment(Base):
    __tablename__ = "knowledge_segments"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    knowledge_item_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("knowledge_items.id"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class KnowledgeSearchRun(Base):
    __tablename__ = "knowledge_search_runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    application_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LlmCallLog(Base):
    __tablename__ = "llm_call_logs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("candidates.id"), nullable=False, index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("conversations.id"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def apply_sqlite_patches(connection) -> None:
    """为已有 SQLite 补 applications / candidates 新列。"""
    app_rows = connection.execute(text("PRAGMA table_info(applications)")).fetchall()
    app_names = {row[1] for row in app_rows}
    if app_names:
        if "applied_at" not in app_names:
            connection.execute(text("ALTER TABLE applications ADD COLUMN applied_at DATE"))
        if "interview_at" not in app_names:
            connection.execute(text("ALTER TABLE applications ADD COLUMN interview_at VARCHAR(64)"))
        if "interview_summary" not in app_names:
            connection.execute(
                text("ALTER TABLE applications ADD COLUMN interview_summary TEXT DEFAULT ''")
            )
    cand_rows = connection.execute(text("PRAGMA table_info(candidates)")).fetchall()
    cand_names = {row[1] for row in cand_rows}
    if cand_names:
        if "llm_api_key_ciphertext" not in cand_names:
            connection.execute(
                text("ALTER TABLE candidates ADD COLUMN llm_api_key_ciphertext TEXT")
            )
        if "llm_key_status" not in cand_names:
            connection.execute(
                text(
                    "ALTER TABLE candidates ADD COLUMN llm_key_status VARCHAR(16) "
                    "NOT NULL DEFAULT 'missing'"
                )
            )
        if "llm_key_updated_at" not in cand_names:
            connection.execute(
                text("ALTER TABLE candidates ADD COLUMN llm_key_updated_at DATETIME")
            )
