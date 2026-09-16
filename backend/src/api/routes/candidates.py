from fastapi import Depends, File, Form, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pycore.api import APIRouter
from pycore.api.responses import success_response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_candidate
from src.db.models import Candidate
from src.db.session import get_db
from src.models.candidate import LlmKeyUpdate
from src.services.candidate import CandidateService

router = APIRouter(prefix="/api/candidates", tags=["candidates"])


@router.post("", status_code=201)
async def create_candidate(
    email: str | None = Form(None),
    resume: UploadFile | None = File(None),
    llm_api_key: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    filename = resume.filename if resume is not None else None
    content = await resume.read() if resume is not None else b""
    service = CandidateService(db)
    data = await service.start_with_resume(
        email_raw=email,
        filename=filename,
        content=content,
        llm_api_key=llm_api_key,
    )
    message = "已进入小凹"
    if data.llm_key_probe_status == "unreachable":
        message = "已进入小凹。这次没连上百炼，之后若失败请到「我的key」再试。"
    body = success_response(data=data.model_dump(mode="json"), message=message)
    return JSONResponse(status_code=201, content=jsonable_encoder(body))


@router.get("/current")
async def get_current_candidate_summary(
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = CandidateService(db)
    data = await service.current_summary(candidate)
    body = success_response(data=data.model_dump(mode="json"), message="ok")
    return JSONResponse(content=jsonable_encoder(body))


@router.post("/current/resume")
async def replace_current_resume(
    resume: UploadFile | None = File(None),
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    filename = resume.filename if resume is not None else None
    content = await resume.read() if resume is not None else b""
    service = CandidateService(db)
    data = await service.replace_resume(
        candidate, filename=filename, content=content
    )
    body = success_response(
        data=data.model_dump(mode="json"),
        message=f"已换成 {data.resume_filename}",
    )
    return JSONResponse(content=jsonable_encoder(body))


@router.put("/current/llm-key")
async def update_current_llm_key(
    payload: LlmKeyUpdate,
    candidate: Candidate = Depends(get_current_candidate),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    service = CandidateService(db)
    data = await service.update_llm_key(candidate, payload.llm_api_key)
    message = "已保存新的百炼 Key"
    if data.llm_key_probe_status == "unreachable":
        message = "已保存新的百炼 Key。这次没连上百炼，之后若失败请到「我的key」再试。"
    body = success_response(data=data.model_dump(mode="json"), message=message)
    return JSONResponse(content=jsonable_encoder(body))
