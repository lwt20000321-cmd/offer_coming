"""整点催促文案、对话写入、SMTP 调用与 nudge_logs 幂等。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pycore.core.logger import get_logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.db.models import (
    Application,
    Candidate,
    Conversation,
    Message,
    NudgeLog,
    Question,
    QuestionSet,
)
from src.services.llm_client import (
    LlmClient,
    LlmKeyInvalidError,
    resolved_candidate_api_key,
    should_skip_llm_http,
)
from src.services.mail import (
    LLM_KEY_INVALID_EMAIL_BODY,
    llm_key_invalid_dedup_key,
    send_llm_key_invalid_email,
    send_nudge_email,
)
from src.services.prompt_context import render_prompt
from src.services.questions import _log_llm_call
from src.utils.crypto import new_id

logger = get_logger()

CHANNEL_CHAT = "chat"
CHANNEL_EMAIL = "email"
last_used_fallback = False
last_degrade_log = ""

_DEFAULT_NUDGE_PROMPT = (
    "你是小凹。根据 tone_level 写一段中文催促："
    "1 温柔哄，2 清楚催，3 严厉，4 最狠骂醒。"
    "出题日把待办里的题干原样附上。"
    "不要写投递进度表、deadline 列表、岗位优先级、考查点或题目分配。"
    "休息日禁止催作答或提及已作废题。"
    "不要在 counseling_active 时生成催促。只输出给用户看的正文。"
)

_TONE_TEMPLATES = {
    1: (
        "现在 {hour}:00 啦，不着急，我们慢慢把今天该做的做完。\n{todo}"
    ),
    2: (
        "现在 {hour}:00。今天该做的还在，按下面的题往下写：\n{todo}"
    ),
    3: (
        "已经 {hour}:00 了。别再找理由，先把这些题收口：\n{todo}"
    ),
    4: (
        "都 {hour}:00 了还没搞定？别装忙了。立刻写，别再拖：\n{todo}"
    ),
}

_REST_TONE_TEMPLATES = {
    1: (
        "现在 {hour}:00，今天是休息整理日，先不催你答题。{todo}"
    ),
    2: (
        "现在 {hour}:00。休息日不催作答。{todo}"
    ),
    3: (
        "已经 {hour}:00。今天不提昨天作废的题，也不催答题。{todo}"
    ),
    4: (
        "都 {hour}:00 了。今天仍是休息日：不许拿已作废的题来应付，也不催作答。{todo}"
    ),
}


def tone_level_for_hour(hour: int) -> int:
    # 10–12=1，13–17=2，18–21=3，22–23=4（tech-spec §五）
    if 10 <= hour <= 12:
        return 1
    if 13 <= hour <= 17:
        return 2
    if 18 <= hour <= 21:
        return 3
    if 22 <= hour <= 23:
        return 4
    raise ValueError("不在催促窗口内，没有语气档")


class NudgeService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def send_hourly(
        self,
        candidate: Candidate,
        now: datetime,
        today_set: QuestionSet | None,
    ) -> None:
        from src.services.scheduler import beijing_hour_key

        hour = now.hour
        tone_level = tone_level_for_hour(hour)
        hour_key = beijing_hour_key(now)
        if await self._log_exists(candidate.id, hour_key, CHANNEL_CHAT):
            logger.info("本整点催促已写入，跳过重跑", candidate_id=candidate.id, hour_key=hour_key)
            return

        rest_day = today_set is not None and today_set.status == "rest_day"
        todo = await self._todo_text(candidate, today_set, rest_day=rest_day)
        if todo is None:
            logger.info("本整点没有可催待办", candidate_id=candidate.id)
            return

        text = await self._compose_text(
            tone_level,
            hour,
            todo,
            rest_day=rest_day,
            candidate=candidate,
        )
        subject = f"小凹催促（语气档 {tone_level}）"
        sent_ok, error_text = send_nudge_email(
            to_email=candidate.email,
            subject=subject,
            body=text,
        )
        chat_content = text
        if not sent_ok:
            reason = error_text or "邮件发不出去。"
            if "邮件" not in reason:
                reason = f"邮件没发出去：{reason}"
            chat_content = f"{text}\n\n{reason}"
            logger.info("对话催促已保留，邮件未发出", candidate_id=candidate.id, detail=reason)

        await self._insert_chat(candidate, now, chat_content, message_type="nudge")
        await self._write_log(
            candidate.id,
            hour_key,
            CHANNEL_CHAT,
            tone_level,
            sent_ok=True,
            error_text=None,
        )
        await self._write_log(
            candidate.id,
            hour_key,
            CHANNEL_EMAIL,
            tone_level,
            sent_ok=sent_ok,
            error_text=None if sent_ok else error_text,
        )

    async def notify_llm_key_issue(self, candidate: Candidate, now: datetime) -> None:
        from src.services.scheduler import beijing_date_text, beijing_hour_key

        hour_key = beijing_hour_key(now)
        date_key = llm_key_invalid_dedup_key(beijing_date_text(now))
        if not await self._log_exists(candidate.id, hour_key, CHANNEL_CHAT):
            await self._insert_chat(
                candidate,
                now,
                LLM_KEY_INVALID_EMAIL_BODY,
                message_type="error_notice",
            )
            await self._write_log(
                candidate.id,
                hour_key,
                CHANNEL_CHAT,
                0,
                sent_ok=True,
                error_text=None,
            )
        existing_email = await self._get_log(candidate.id, date_key, CHANNEL_EMAIL)
        if existing_email is not None and existing_email.sent_ok:
            logger.info(
                "Key 失效模板邮件当日已成功，跳过",
                candidate_id=candidate.id,
                date_key=date_key,
            )
            return
        sent_ok, error_text = send_llm_key_invalid_email(to_email=candidate.email)
        if existing_email is None:
            await self._write_log(
                candidate.id,
                date_key,
                CHANNEL_EMAIL,
                0,
                sent_ok=sent_ok,
                error_text=None if sent_ok else error_text,
            )
            return
        existing_email.sent_ok = sent_ok
        existing_email.error_text = None if sent_ok else error_text
        await self.db.flush()

    async def _todo_text(
        self,
        candidate: Candidate,
        today_set: QuestionSet | None,
        *,
        rest_day: bool,
    ) -> str | None:
        apps = await self._applications(candidate.id)
        if not apps and (today_set is None or rest_day):
            return None
        if rest_day:
            return "有空再看投递就好。"
        if today_set is not None and today_set.status in {"pending", "in_progress"}:
            questions = await self._questions(today_set.id)
            unfinished = [q for q in questions if not (q.answer or "").strip()]
            if unfinished:
                return "\n".join(f"{item.seq}. {item.prompt}" for item in unfinished)
        if apps:
            return "今天把该跟的投递跟一下。"
        return None

    async def _compose_text(
        self,
        tone_level: int,
        hour: int,
        todo: str,
        *,
        rest_day: bool,
        candidate: Candidate,
    ) -> str:
        global last_used_fallback, last_degrade_log
        last_used_fallback = False
        last_degrade_log = ""
        if _llm_configured():
            generated = await self._llm_text(
                tone_level,
                hour,
                todo,
                rest_day=rest_day,
                candidate=candidate,
            )
            if generated:
                return generated
            last_used_fallback = True
            last_degrade_log = "催促模型失败，已降级为规则模板"
            logger.warning(last_degrade_log, candidate_id=candidate.id)
        else:
            last_used_fallback = True
            last_degrade_log = "未走百炼 HTTP，已降级为规则模板催促"
            logger.info(last_degrade_log, candidate_id=candidate.id)
        table = _REST_TONE_TEMPLATES if rest_day else _TONE_TEMPLATES
        return table[tone_level].format(hour=hour, todo=todo)

    async def _llm_text(
        self,
        tone_level: int,
        hour: int,
        todo: str,
        *,
        rest_day: bool,
        candidate: Candidate,
    ) -> str | None:
        settings = get_settings()
        api_key = resolved_candidate_api_key(candidate)
        system_prompt = await render_prompt(
            "nudge.md",
            self.db,
            candidate,
            fallback=_DEFAULT_NUDGE_PROMPT,
        )
        user = (
            f"tone_level={tone_level}\nhour={hour}\nrest_day={rest_day}\n待办清单：\n{todo}"
        )
        try:
            await _log_llm_call(
                self.db,
                candidate_id=candidate.id,
                role="nudge",
                model=settings.llm_nudge_model,
                purpose="hourly_nudge",
            )
            data = await LlmClient().chat_completions(
                api_key=api_key,
                candidate_id=candidate.id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user},
                ],
                model=settings.llm_nudge_model,
                temperature=settings.llm_nudge_temperature,
            )
        except LlmKeyInvalidError:
            raise
        except Exception as exc:
            logger.warning("催促文案模型调用失败，改用规则模板", detail=type(exc).__name__)
            return None
        content = _choice_text(data).strip()
        return content or None

    async def _insert_chat(
        self,
        candidate: Candidate,
        now: datetime,
        content: str,
        *,
        message_type: str = "nudge",
    ) -> None:
        conversation = await self._conversation(candidate.id)
        if conversation is None:
            logger.error("催促时找不到对话", candidate_id=candidate.id)
            return
        message = Message(
            id=new_id("m"),
            conversation_id=conversation.id,
            role="assistant",
            message_type=message_type,
            content=content,
            created_at=now.astimezone(UTC),
        )
        self.db.add(message)
        await self.db.flush()

    async def _write_log(
        self,
        candidate_id: str,
        hour_key: str,
        channel: str,
        tone_level: int,
        *,
        sent_ok: bool,
        error_text: str | None,
    ) -> None:
        log = NudgeLog(
            id=new_id("ng"),
            candidate_id=candidate_id,
            beijing_hour_key=hour_key,
            channel=channel,
            tone_level=tone_level,
            sent_ok=sent_ok,
            error_text=error_text,
        )
        if await self._log_exists(candidate_id, hour_key, channel):
            return
        try:
            async with self.db.begin_nested():
                self.db.add(log)
                await self.db.flush()
        except IntegrityError:
            logger.info("nudge_logs 已存在，不重复写入", hour_key=hour_key, channel=channel)

    async def _log_exists(self, candidate_id: str, hour_key: str, channel: str) -> bool:
        return await self._get_log(candidate_id, hour_key, channel) is not None

    async def _get_log(
        self, candidate_id: str, hour_key: str, channel: str
    ) -> NudgeLog | None:
        result = await self.db.execute(
            select(NudgeLog).where(
                NudgeLog.candidate_id == candidate_id,
                NudgeLog.beijing_hour_key == hour_key,
                NudgeLog.channel == channel,
            )
        )
        return result.scalar_one_or_none()

    async def _applications(self, candidate_id: str) -> list[Application]:
        result = await self.db.execute(
            select(Application)
            .where(Application.candidate_id == candidate_id)
            .order_by(Application.created_at.asc())
        )
        return list(result.scalars().all())

    async def _questions(self, question_set_id: str) -> list[Question]:
        result = await self.db.execute(
            select(Question)
            .where(Question.question_set_id == question_set_id)
            .order_by(Question.seq.asc())
        )
        return list(result.scalars().all())

    async def _conversation(self, candidate_id: str) -> Conversation | None:
        result = await self.db.execute(
            select(Conversation).where(Conversation.candidate_id == candidate_id)
        )
        return result.scalar_one_or_none()


def _llm_configured() -> bool:
    """是否调用百炼 HTTP。禁止把运营 llm_api_key 当作开关。"""
    if should_skip_llm_http():
        return False
    return True


def _choice_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")
