from fastapi import Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pycore.api import APIRouter
from pycore.api.responses import success_response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_candidate
from src.db.models import Candidate
from src.db.session import get_db
from src.models.candidate import ApplicationCreate, ApplicationUpdate
from src.services.candidate import CandidateService

router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.get("")
async def list_applications(
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = CandidateService(db)
    applications = await service.list_applications(candidate)
    body = success_response(
        data={"applications": [item.model_dump(mode="json") for item in applications]},
        message="ok",
    )
    return JSONResponse(content=jsonable_encoder(body))


@router.post("", status_code=201)
async def create_application(
    payload: ApplicationCreate,
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = CandidateService(db)
    application = await service.create_application(candidate, payload)
    body = success_response(data=application.model_dump(mode="json"), message="ok")
    return JSONResponse(status_code=201, content=jsonable_encoder(body))


@router.patch("/{application_id}")
async def update_application(
    application_id: str,
    payload: ApplicationUpdate,
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = CandidateService(db)
    application = await service.update_application(candidate, application_id, payload)
    body = success_response(data=application.model_dump(mode="json"), message="ok")
    return JSONResponse(content=jsonable_encoder(body))


@router.delete("/{application_id}")
async def delete_application(
    application_id: str,
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = CandidateService(db)
    deleted_id = await service.delete_application(candidate, application_id)
    body = success_response(data={"id": deleted_id, "deleted": True}, message="ok")
    return JSONResponse(content=jsonable_encoder(body))
