from fastapi import Depends, File, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pycore.api import APIRouter
from pycore.api.responses import success_response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_candidate
from src.db.models import Candidate
from src.db.session import get_db
from src.services.knowledge_store import KnowledgeStore
from src.utils.errors import ValidationFailed

router = APIRouter(prefix="/api/knowledge-items", tags=["knowledge-items"])


@router.get("")
async def list_knowledge_items(
    application_id: str | None = Query(default=None),
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = KnowledgeStore(db)
    items = await service.list_items(candidate, application_id)
    body = success_response(
        data={"items": [item.model_dump(mode="json") for item in items]},
        message="ok",
    )
    return JSONResponse(content=jsonable_encoder(body))


@router.post("", status_code=201)
async def upload_knowledge_item(
    file: UploadFile | None = File(None),
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    if file is None:
        raise ValidationFailed("请选择要上传的面经文件。")
    content = await file.read()
    service = KnowledgeStore(db)
    item = await service.ingest_upload_file(candidate, file.filename, content)
    body = success_response(data=item.model_dump(mode="json"), message="ok")
    return JSONResponse(status_code=201, content=jsonable_encoder(body))


@router.get("/{knowledge_item_id}")
async def get_knowledge_item(
    knowledge_item_id: str,
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = KnowledgeStore(db)
    item = await service.get_item(candidate, knowledge_item_id)
    body = success_response(data=item.model_dump(mode="json"), message="ok")
    return JSONResponse(content=jsonable_encoder(body))


@router.delete("/{knowledge_item_id}")
async def delete_knowledge_item(
    knowledge_item_id: str,
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = KnowledgeStore(db)
    deleted_id = await service.delete_item(candidate, knowledge_item_id)
    body = success_response(data={"id": deleted_id, "deleted": True}, message="ok")
    return JSONResponse(content=jsonable_encoder(body))
