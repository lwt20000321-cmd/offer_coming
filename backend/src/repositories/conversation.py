from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Conversation, Message


class ConversationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, conversation_id: str) -> Conversation | None:
        result = await self.db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def get_by_candidate_id(self, candidate_id: str) -> Conversation | None:
        result = await self.db.execute(
            select(Conversation).where(Conversation.candidate_id == candidate_id)
        )
        return result.scalar_one_or_none()

    async def create(self, conversation: Conversation) -> Conversation:
        self.db.add(conversation)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ValueError("对话已存在") from exc
        await self.db.refresh(conversation)
        return conversation


class MessageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_for_conversation(
        self,
        conversation_id: str,
        *,
        after_id: str | None,
        limit: int,
    ) -> list[Message]:
        if not after_id:
            return await self.list_recent_for_conversation(
                conversation_id, limit=limit
            )
        stmt = select(Message).where(Message.conversation_id == conversation_id)
        after = await self.db.execute(select(Message).where(Message.id == after_id))
        after_row = after.scalar_one_or_none()
        if after_row is not None:
            stmt = stmt.where(Message.created_at > after_row.created_at)
        stmt = stmt.order_by(Message.created_at.asc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_recent_for_conversation(
        self, conversation_id: str, *, limit: int
    ) -> list[Message]:
        if limit <= 0:
            return []
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

    async def add(self, message: Message) -> Message:
        self.db.add(message)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ValueError("消息写入冲突") from exc
        await self.db.refresh(message)
        return message
