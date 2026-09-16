"""Asia/Shanghai 整点调度：作废、出题、催促。取值见 docs/tech-spec.md §五。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pycore.core.logger import get_logger
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.db.models import Application, Candidate, QuestionSet
from src.db.session import get_db_context
from src.services.candidate import CandidateService
from src.services.llm_client import (
    LlmKeyInvalidError,
    candidate_llm_context,
    candidate_llm_key_unusable,
    resolved_candidate_api_key,
)
from src.services.nudge import NudgeService
from src.services.questions import QuestionService

logger = get_logger()

# 催促窗口 10–23；00:00 只作废不发第 15 次催题（tech-spec §五）
NUDGE_HOUR_START = 10
NUDGE_HOUR_END = 23
VOID_HOUR = 0

_clock: Callable[[], datetime] | None = None
_loop_enabled = True
_loop_task: asyncio.Task[None] | None = None


def timezone_zone() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def now_beijing() -> datetime:
    if _clock is not None:
        current = _clock()
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone_zone())
        return current.astimezone(timezone_zone())
    return datetime.now(timezone_zone())


def set_clock(getter: Callable[[], datetime] | None) -> None:
    global _clock
    _clock = getter


def reset_clock() -> None:
    set_clock(None)


def set_scheduler_loop_enabled(enabled: bool) -> None:
    global _loop_enabled
    _loop_enabled = enabled


def beijing_hour_key(now: datetime) -> str:
    local = now.astimezone(timezone_zone())
    return local.strftime("%Y-%m-%dT%H")


def beijing_date_text(now: datetime) -> str:
    return now.astimezone(timezone_zone()).date().isoformat()


async def start_scheduler() -> None:
    global _loop_task
    if not _loop_enabled:
        logger.info("调度后台循环未启用")
        return
    if _loop_task is not None and not _loop_task.done():
        return
    _loop_task = asyncio.create_task(_run_loop(), name="beijing-scheduler")
    logger.info("北京时间整点调度已启动")


async def stop_scheduler() -> None:
    global _loop_task
    task = _loop_task
    _loop_task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("北京时间整点调度已停止")


async def _run_loop() -> None:
    while True:
        now = now_beijing()
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        delay = (next_hour - now).total_seconds()
        if delay < 1:
            delay = 1
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        tick_at = now_beijing().replace(minute=0, second=0, microsecond=0)
        try:
            async with get_db_context() as session:
                await run_tick(tick_at, session)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("整点调度执行失败", detail=str(exc))


async def run_tick(now: datetime | None, db: AsyncSession) -> dict[str, int]:
    current = now or now_beijing()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone_zone())
    else:
        current = current.astimezone(timezone_zone())
    hour = current.hour
    processed = 0
    candidates = await _candidates_for_schedule(db)
    for candidate in candidates:
        try:
            await _process_candidate(db, candidate, current, hour)
            processed += 1
        except Exception as exc:
            logger.error(
                "调度处理求职者失败",
                candidate_id=candidate.id,
                detail=str(exc),
            )
    await db.flush()
    logger.info("整点调度本轮结束", hour=hour, processed=processed)
    return {"processed": processed, "hour": hour}


async def _candidates_for_schedule(db: AsyncSession) -> list[Candidate]:
    has_app = exists(select(Application.id).where(Application.candidate_id == Candidate.id))
    has_set = exists(select(QuestionSet.id).where(QuestionSet.candidate_id == Candidate.id))
    result = await db.execute(
        select(Candidate).where(
            or_(has_app, has_set, Candidate.next_question_date.is_not(None))
        )
    )
    return list(result.scalars().all())


async def _process_candidate(
    db: AsyncSession,
    candidate: Candidate,
    now: datetime,
    hour: int,
) -> None:
    questions = QuestionService(db)
    if hour == VOID_HOUR:
        await questions.void_incomplete_at_midnight(candidate, now)
        return
    if hour < NUDGE_HOUR_START or hour > NUDGE_HOUR_END:
        return

    if candidate_llm_key_unusable(candidate):
        logger.info(
            "求职者 Key 不可用，跳过模型催促与出题",
            candidate_id=candidate.id,
            hour=hour,
        )
        await NudgeService(db).notify_llm_key_issue(candidate, now)
        return

    try:
        api_key = resolved_candidate_api_key(candidate)
    except Exception:
        logger.info("求职者 Key 无法解密，跳过模型催促与出题", candidate_id=candidate.id)
        await NudgeService(db).notify_llm_key_issue(candidate, now)
        return

    with candidate_llm_context(api_key, candidate.id):
        await _process_candidate_with_key(db, questions, candidate, now, hour)


async def _process_candidate_with_key(
    db: AsyncSession,
    questions: QuestionService,
    candidate: Candidate,
    now: datetime,
    hour: int,
) -> None:
    try:
        today_set = await questions.ensure_question_day(candidate, now)
        if today_set is not None and today_set.status == "completed":
            questions.apply_completed_next_date(candidate, now)
            await db.flush()
            return

        if candidate.counseling_active:
            logger.info("辅导中跳过狠催", candidate_id=candidate.id, hour=hour)
            return

        nudges = NudgeService(db)
        await nudges.send_hourly(candidate, now, today_set)
    except LlmKeyInvalidError:
        logger.info("调度遇到求职者 Key 无效", candidate_id=candidate.id, hour=hour)
        await CandidateService(db).mark_llm_key_invalid(candidate)
        await NudgeService(db).notify_llm_key_issue(candidate, now)


async def trigger_tick(db: AsyncSession, at: datetime | None = None) -> dict[str, int]:
    return await run_tick(at or now_beijing(), db)
