from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Application,
    KnowledgeItem,
    KnowledgeSearchRun,
    KnowledgeSegment,
)
from src.utils.crypto import new_id, utc_now


class KnowledgeRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_by_candidate(
        self,
        candidate_id: str,
        application_id: str | None = None,
    ) -> list[KnowledgeItem]:
        stmt = (
            select(KnowledgeItem)
            .where(KnowledgeItem.candidate_id == candidate_id)
            .order_by(KnowledgeItem.created_at.desc())
        )
        if application_id:
            stmt = stmt.where(KnowledgeItem.application_id == application_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, item_id: str) -> KnowledgeItem | None:
        result = await self.db.execute(
            select(KnowledgeItem).where(KnowledgeItem.id == item_id)
        )
        return result.scalar_one_or_none()

    async def list_segments(self, knowledge_item_id: str) -> list[KnowledgeSegment]:
        result = await self.db.execute(
            select(KnowledgeSegment)
            .where(KnowledgeSegment.knowledge_item_id == knowledge_item_id)
            .order_by(KnowledgeSegment.ordinal.asc())
        )
        return list(result.scalars().all())

    async def applications_by_ids(self, ids: list[str]) -> dict[str, Application]:
        if not ids:
            return {}
        result = await self.db.execute(select(Application).where(Application.id.in_(ids)))
        return {row.id: row for row in result.scalars().all()}

    async def delete_item(self, item: KnowledgeItem) -> None:
        await self.db.execute(
            delete(KnowledgeSegment).where(
                KnowledgeSegment.knowledge_item_id == item.id
            )
        )
        await self.db.delete(item)
        await self.db.flush()

    async def create_item(
        self,
        *,
        candidate_id: str,
        title: str,
        body: str,
        source_type: str,
        application_id: str | None = None,
        source_url: str | None = None,
    ) -> KnowledgeItem:
        now = utc_now()
        row = KnowledgeItem(
            id=new_id("k"),
            candidate_id=candidate_id,
            application_id=application_id,
            title=title,
            source_type=source_type,
            source_url=source_url,
            body=body,
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def create_segment(
        self,
        *,
        knowledge_item_id: str,
        ordinal: int,
        text: str,
        status: str = "active",
    ) -> KnowledgeSegment:
        row = KnowledgeSegment(
            id=new_id("s"),
            knowledge_item_id=knowledge_item_id,
            ordinal=ordinal,
            text=text,
            status=status,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def create_search_run(
        self,
        *,
        application_id: str,
        status: str = "running",
        error_text: str | None = None,
    ) -> KnowledgeSearchRun:
        row = KnowledgeSearchRun(
            id=new_id("ks"),
            application_id=application_id,
            status=status,
            error_text=error_text,
            created_at=utc_now(),
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def update_search_run(
        self,
        run: KnowledgeSearchRun,
        *,
        status: str,
        error_text: str | None = None,
    ) -> KnowledgeSearchRun:
        run.status = status
        run.error_text = error_text
        await self.db.flush()
        return run

    async def list_search_runs(self, application_id: str) -> list[KnowledgeSearchRun]:
        result = await self.db.execute(
            select(KnowledgeSearchRun)
            .where(KnowledgeSearchRun.application_id == application_id)
            .order_by(KnowledgeSearchRun.created_at.desc())
        )
        return list(result.scalars().all())

    async def mark_pending_delete(self, segment_ids: list[str]) -> int:
        if not segment_ids:
            return 0
        result = await self.db.execute(
            select(KnowledgeSegment).where(KnowledgeSegment.id.in_(segment_ids))
        )
        rows = list(result.scalars().all())
        for row in rows:
            if row.status != "deleted":
                row.status = "pending_delete"
        await self.db.flush()
        return len(rows)

    async def apply_keep_all(self, item_ids: list[str]) -> None:
        if not item_ids:
            return
        result = await self.db.execute(
            select(KnowledgeSegment).where(
                KnowledgeSegment.knowledge_item_id.in_(item_ids),
                KnowledgeSegment.status == "pending_delete",
            )
        )
        for row in result.scalars().all():
            row.status = "active"
        await self.db.flush()

    async def delete_by_ids(self, item_ids: list[str]) -> list[str]:
        deleted: list[str] = []
        for item_id in item_ids:
            row = await self.get_by_id(item_id)
            if row is None:
                continue
            await self.delete_item(row)
            deleted.append(item_id)
        return deleted
