from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Candidate, apply_sqlite_patches

_patched_binds: set[int] = set()


class CandidateRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _ensure_schema(self) -> None:
        bind = self.db.get_bind()
        key = id(bind)
        if key in _patched_binds:
            return
        connection = await self.db.connection()
        await connection.run_sync(apply_sqlite_patches)
        _patched_binds.add(key)

    async def get_by_id(self, candidate_id: str) -> Candidate | None:
        await self._ensure_schema()
        result = await self.db.execute(select(Candidate).where(Candidate.id == candidate_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Candidate | None:
        await self._ensure_schema()
        result = await self.db.execute(select(Candidate).where(Candidate.email == email))
        return result.scalar_one_or_none()

    async def create(self, candidate: Candidate) -> Candidate:
        await self._ensure_schema()
        self.db.add(candidate)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ValueError("这个邮箱已有记录，请直接用邮箱进入") from exc
        await self.db.refresh(candidate)
        return candidate

    async def save(self, candidate: Candidate) -> Candidate:
        await self._ensure_schema()
        await self.db.flush()
        await self.db.refresh(candidate)
        return candidate

    async def mark_llm_key_invalid(self, candidate: Candidate) -> Candidate:
        candidate.llm_key_status = "invalid"
        return await self.save(candidate)
