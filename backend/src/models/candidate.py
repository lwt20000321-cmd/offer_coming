from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NormalizedStatus = Literal["waiting_interview", "rejected", "other"]
QuestionSetStatus = Literal["none", "pending", "in_progress", "completed", "voided", "rest_day"]
LlmKeyStatus = Literal["missing", "saved", "invalid"]
LlmKeyProbeStatus = Literal["ok", "unreachable"]
MessageRole = Literal["user", "assistant"]
MessageType = Literal[
    "chat",
    "nudge",
    "questions",
    "review",
    "counseling",
    "jd_summary",
    "error_notice",
    "kb_notice",
    "kb_ask",
]
QuestionKind = Literal["common", "role"]


class CandidatePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    resume_filename: str
    resume_parse_ok: bool
    has_llm_api_key: bool
    llm_key_status: LlmKeyStatus
    created_at: datetime


class SessionStartPublic(BaseModel):
    session_token: str
    candidate: CandidatePublic
    conversation_id: str
    llm_key_probe_status: LlmKeyProbeStatus | None = None


class SessionCreate(BaseModel):
    email: str = Field(..., min_length=1)
    llm_api_key: str | None = None


class LlmKeyUpdate(BaseModel):
    llm_api_key: str | None = None


class LlmKeyUpdatePublic(BaseModel):
    has_llm_api_key: bool
    llm_key_status: LlmKeyStatus
    llm_key_probe_status: LlmKeyProbeStatus


class SessionRevokePublic(BaseModel):
    revoked: bool


class TodaySummaryPublic(BaseModel):
    beijing_date: str
    is_question_day: bool
    is_rest_day: bool
    question_set_status: QuestionSetStatus
    counseling_active: bool


class CandidateCurrentPublic(BaseModel):
    id: str
    email: str
    resume_filename: str
    resume_parse_ok: bool
    has_llm_api_key: bool
    llm_key_status: LlmKeyStatus
    application_count: int
    waiting_interview_count: int
    today: TodaySummaryPublic


class ConversationPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    candidate_id: str
    created_at: datetime


class MessagePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: MessageRole
    message_type: MessageType
    content: str
    created_at: datetime


class MessageListPublic(BaseModel):
    messages: list[MessagePublic]


class ApplicationPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company_name: str
    role_title: str
    jd_url: str | None
    exam_points: str
    progress_text: str
    status_text: str
    normalized_status: NormalizedStatus
    applied_at: str
    interview_at: str | None
    interview_summary: str
    deadline: str | None
    updated_at: datetime

    @field_validator("applied_at", mode="before")
    @classmethod
    def coerce_applied_at(cls, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    @field_validator("interview_summary", mode="before")
    @classmethod
    def coerce_interview_summary(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value)


class ApplicationCreate(BaseModel):
    company_name: str | None = None
    role_title: str | None = None
    jd_url: str | None = None
    applied_at: str | None = None
    interview_at: str | None = None
    status_text: str | None = None
    interview_summary: str | None = None
    progress_text: str | None = None


class ApplicationUpdate(BaseModel):
    company_name: str | None = None
    role_title: str | None = None
    jd_url: str | None = None
    applied_at: str | None = None
    interview_at: str | None = None
    status_text: str | None = None
    interview_summary: str | None = None
    progress_text: str | None = None


class ApplicationPatchPublic(ApplicationPublic):
    knowledge_review_pending: bool


class ApplicationListPublic(BaseModel):
    applications: list[ApplicationPublic]


class QuestionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: QuestionKind
    prompt: str
    answer: str | None
    target_application_id: str | None


class QuestionSetPublic(BaseModel):
    id: str | None
    beijing_date: str
    status: QuestionSetStatus
    questions: list[QuestionPublic]
    review: str | None
    voided_at: datetime | None
