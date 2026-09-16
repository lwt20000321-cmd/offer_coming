from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Application, KnowledgeItem, apply_sqlite_patches

_patched_binds: set[int] = set()


class ApplicationRepository:
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

    async def list_by_candidate(self, candidate_id: str) -> list[Application]:
        await self._ensure_schema()
        result = await self.db.execute(
            select(Application)
            .where(Application.candidate_id == candidate_id)
            .order_by(Application.updated_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, application_id: str) -> Application | None:
        await self._ensure_schema()
        result = await self.db.execute(
            select(Application).where(Application.id == application_id)
        )
        return result.scalar_one_or_none()

    async def find_by_jd_url(
        self,
        candidate_id: str,
        jd_url: str,
        *,
        exclude_id: str | None = None,
    ) -> Application | None:
        await self._ensure_schema()
        stmt = select(Application).where(
            Application.candidate_id == candidate_id,
            Application.jd_url == jd_url,
        )
        if exclude_id:
            stmt = stmt.where(Application.id != exclude_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def count_related_knowledge(self, application_id: str) -> int:
        await self._ensure_schema()
        result = await self.db.execute(
            select(func.count())
            .select_from(KnowledgeItem)
            .where(KnowledgeItem.application_id == application_id)
        )
        return int(result.scalar_one())

    async def create(self, application: Application) -> Application:
        await self._ensure_schema()
        self.db.add(application)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ValueError("这个投递链接已经在表里") from exc
        await self.db.refresh(application)
        return application

    async def save(self, application: Application) -> Application:
        await self._ensure_schema()
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ValueError("这个投递链接已经在表里") from exc
        await self.db.refresh(application)
        return application

    async def delete(self, application: Application) -> None:
        await self._ensure_schema()
        await self.db.delete(application)
        await self.db.flush()

    async def count_by_candidate(self, candidate_id: str) -> int:
        await self._ensure_schema()
        result = await self.db.execute(
            select(func.count()).select_from(Application).where(
                Application.candidate_id == candidate_id
            )
        )
        return int(result.scalar_one())

    async def count_waiting_interview(self, candidate_id: str) -> int:
        await self._ensure_schema()
        result = await self.db.execute(
            select(func.count()).select_from(Application).where(
                Application.candidate_id == candidate_id,
                Application.normalized_status == "waiting_interview",
            )
        )
        return int(result.scalar_one())
