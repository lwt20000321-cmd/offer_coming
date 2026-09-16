from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Question, QuestionSet


class QuestionSetRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_for_date(self, candidate_id: str, beijing_date: str) -> QuestionSet | None:
        result = await self.db.execute(
            select(QuestionSet).where(
                QuestionSet.candidate_id == candidate_id,
                QuestionSet.beijing_date == beijing_date,
            )
        )
        return result.scalar_one_or_none()

    async def list_questions(self, question_set_id: str) -> list[Question]:
        result = await self.db.execute(
            select(Question)
            .where(Question.question_set_id == question_set_id)
            .order_by(Question.seq.asc())
        )
        return list(result.scalars().all())
