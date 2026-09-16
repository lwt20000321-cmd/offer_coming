"""
FastAPI 依赖注入。基于 pycore/api/deps.py 模板扩展。
认证使用接续令牌，不使用全局 AuthMiddleware。
"""

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Candidate
from src.db.session import get_db
from src.services.candidate import CandidateService
from src.utils.errors import Unauthorized

security = HTTPBearer(auto_error=False)


async def get_current_candidate(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Candidate:
    if credentials is None or not credentials.credentials:
        raise Unauthorized()
    service = CandidateService(db)
    return await service.candidate_by_token(credentials.credentials)
