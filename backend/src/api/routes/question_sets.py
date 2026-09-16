from datetime import datetime

from fastapi import Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pycore.api import APIRouter
from pycore.api.responses import success_response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_candidate
from src.config.settings import get_settings
from src.db.models import Candidate
from src.db.session import get_db
from src.services.questions import QuestionService
from src.services.scheduler import timezone_zone, trigger_tick
from src.utils.errors import NotFound

router = APIRouter(prefix="/api/question-sets", tags=["question_sets"])
internal_router = APIRouter(prefix="/internal/scheduler", tags=["internal"])


@router.get("/current")
async def get_current_question_set(
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = QuestionService(db)
    data = await service.current_for(candidate)
    body = success_response(data=data.model_dump(mode="json"), message="ok")
    return JSONResponse(content=jsonable_encoder(body))


@internal_router.post("/tick", include_in_schema=False)
async def trigger_scheduler_tick(
    at: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    if not get_settings().debug:
        raise NotFound("不存在该接口")
    when: datetime | None = None
    if at:
        parsed = datetime.fromisoformat(at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone_zone())
        when = parsed
    result = await trigger_tick(db, when)
    body = success_response(data=result, message="ok")
    return JSONResponse(content=jsonable_encoder(body))
