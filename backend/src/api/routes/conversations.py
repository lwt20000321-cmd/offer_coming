from pathlib import Path

from fastapi import Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pycore.api import APIRouter
from pycore.api.responses import success_response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_candidate
from src.config.settings import get_settings
from src.db.models import Candidate
from src.db.session import get_db
from src.services.agent import AgentService, try_acquire_conversation
from src.services.candidate import CandidateService
from src.utils.errors import Conflict, ValidationFailed

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

# API-006 MessageCreate.content：0–8000 字；至少有非空 content 或 file
_MESSAGE_CONTENT_MAX_CHARS = 8000


class MessageCreate(BaseModel):
    content: str | None = None


@router.get("/current")
async def get_current_conversation(
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = CandidateService(db)
    data = await service.get_or_create_conversation(candidate)
    body = success_response(data=data.model_dump(mode="json"), message="ok")
    return JSONResponse(content=jsonable_encoder(body))


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    after_id: str | None = Query(None),
    limit: int | None = Query(None, ge=1),
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = CandidateService(db)
    messages = await service.list_messages(
        candidate,
        conversation_id,
        after_id=after_id,
        limit=limit,
    )
    body = success_response(
        data={"messages": [item.model_dump(mode="json") for item in messages]},
        message="ok",
    )
    return JSONResponse(content=jsonable_encoder(body))


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    request: Request,
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    content, upload_filename, upload_bytes = await _read_message_payload(request)
    agent = AgentService(db)
    conversation = await agent.require_conversation(conversation_id, candidate)
    if not try_acquire_conversation(conversation.id):
        raise Conflict("小凹还在回复这一句，请等它说完再发。")
    return StreamingResponse(
        agent.stream_reply(
            candidate,
            conversation,
            content,
            upload_filename=upload_filename,
            upload_bytes=upload_bytes,
        ),
        media_type="text/event-stream",
    )


async def _read_message_payload(request: Request) -> tuple[str, str | None, bytes | None]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        raw = str(form.get("content") or "")
        if len(raw) > _MESSAGE_CONTENT_MAX_CHARS:
            raise ValidationFailed("缩短后再发")
        upload = form.get("file")
        filename: str | None = None
        data: bytes | None = None
        if upload is not None and hasattr(upload, "read"):
            filename = getattr(upload, "filename", None) or "experience.txt"
            data = await upload.read()
            settings = get_settings()
            if not data:
                raise ValidationFailed("请发送可用的面经文件。")
            if len(data) > settings.knowledge_max_bytes:
                raise ValidationFailed("面经文件过大，请换一份更小的文件。")
            suffix = Path(filename).suffix.lower()
            allowed = {
                item.lower() if item.startswith(".") else f".{item.lower()}"
                for item in settings.knowledge_allowed_extensions
            }
            if suffix not in allowed:
                raise ValidationFailed("面经格式不支持，请上传 pdf、docx 或 txt 文件。")
        if not raw.strip() and not data:
            raise ValidationFailed("请写出内容")
        return raw.strip(), filename, data
    try:
        payload = MessageCreate.model_validate(await request.json())
    except Exception as exc:
        raise ValidationFailed("请写出内容") from exc
    raw = payload.content if payload.content is not None else ""
    content = raw.strip()
    if not content:
        raise ValidationFailed("请写出内容")
    if len(raw) > _MESSAGE_CONTENT_MAX_CHARS:
        raise ValidationFailed("缩短后再发")
    return content, None, None
