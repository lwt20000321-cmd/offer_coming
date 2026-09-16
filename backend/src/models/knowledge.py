from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

KnowledgeSourceType = Literal["search", "upload", "paste"]
KnowledgeSegmentStatus = Literal["active", "pending_delete", "deleted"]

# API-012：excerpt 为正文前 200 字。
KNOWLEDGE_EXCERPT_CHARS = 200


class KnowledgeSegmentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ordinal: int
    text: str
    status: KnowledgeSegmentStatus


class KnowledgeItemListPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    source_type: KnowledgeSourceType
    source_url: str | None
    application_id: str | None
    company_name: str
    role_title: str
    created_at: datetime
    excerpt: str


class KnowledgeItemPublic(KnowledgeItemListPublic):
    body: str
    segments: list[KnowledgeSegmentPublic]
