from fastapi import Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pycore.api import APIRouter
from pycore.api.responses import success_response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import security
from src.db.session import get_db
from src.models.candidate import SessionCreate
from src.services.candidate import CandidateService
from src.utils.errors import Unauthorized

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("")
async def create_session(
    payload: SessionCreate,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = CandidateService(db)
    data = await service.continue_with_email(payload.email, payload.llm_api_key)
    message = "已接上原来的简历和任务"
    if data.llm_key_probe_status == "unreachable":
        message = "已接上原来的简历和任务。这次没连上百炼，之后若失败请到「我的key」再试。"
    body = success_response(
        data=data.model_dump(mode="json"),
        message=message,
    )
    return JSONResponse(content=jsonable_encoder(body))


@router.delete("/current")
async def revoke_current_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    if credentials is None or not credentials.credentials:
        raise Unauthorized()
    service = CandidateService(db)
    data = await service.revoke_current_session(credentials.credentials)
    body = success_response(data=data.model_dump(mode="json"), message="ok")
    return JSONResponse(content=jsonable_encoder(body))
