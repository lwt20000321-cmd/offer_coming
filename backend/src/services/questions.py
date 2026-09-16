"""出题日生成五题、作废与当日题集读取。JSON 失败降级见 tech-spec §四。"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from pycore.core.logger import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.db.models import (
    Application,
    Candidate,
    Conversation,
    LlmCallLog,
    Message,
    Question,
    QuestionSet,
)
from src.models.candidate import QuestionPublic, QuestionSetPublic, QuestionSetStatus
from src.services.knowledge_store import KnowledgeStore
from src.services.llm_client import (
    LlmClient,
    LlmKeyInvalidError,
    resolved_candidate_api_key,
    should_skip_llm_http,
)
from src.services.prompt_context import format_knowledge_override, render_prompt
from src.utils.crypto import new_id, utc_now

logger = get_logger()

QUESTION_COUNT = 5
FORMAT_ATTEMPTS = 2
COMPLETE_GAP_DAYS = 1
VOID_REST_DAYS = 2
COMMON_KIND = "common"
ROLE_KIND = "role"
WAITING_STATUS = "waiting_interview"
REJECTED_STATUS = "rejected"
_ANSWER_SPLIT_RE = re.compile(r"第[一二三四五12345]题[:：]?")
_ANSWER_MARKERS = (
    "第一题",
    "第二题",
    "第三题",
    "第四题",
    "第五题",
    "第1题",
    "第2题",
    "第3题",
    "第4题",
    "第5题",
)
_FIVE_QUESTION_ASK = (
    "今日五题",
    "每日五题",
    "今天练什么",
    "今天的五题",
)
_FIVE_QUESTION_RETRY = (
    "截断",
    "被截",
    "少一题",
    "缺一题",
)


def looks_like_question_answers(text: str) -> bool:
    return any(marker in text for marker in _ANSWER_MARKERS)


def looks_like_five_question_ask(text: str) -> bool:
    blob = (text or "").strip()
    if not blob:
        return False
    if any(marker in blob for marker in _FIVE_QUESTION_ASK):
        return True
    if any(marker in blob for marker in _FIVE_QUESTION_RETRY):
        return True
    if "五题" in blob and any(marker in blob for marker in ("完整", "再给", "再发", "重新")):
        return True
    return False


def format_daily_questions_text(questions: Sequence[Any], *, heading: str = "今日五题：") -> str:
    lines = [heading, ""]
    for index, item in enumerate(questions, start=1):
        if isinstance(item, dict):
            seq = item.get("seq") or index
            prompt = str(item.get("prompt") or "").strip()
        else:
            seq = getattr(item, "seq", None) or index
            prompt = str(getattr(item, "prompt", "") or "").strip()
        if prompt:
            lines.append(f"{seq}. {prompt}")
    return "\n".join(lines).strip()


def split_answer_texts(text: str, count: int) -> list[str]:
    parts = _ANSWER_SPLIT_RE.split(text)
    answers = [item.strip() for item in parts if item.strip()]
    if len(answers) >= count:
        return answers[:count]
    if answers:
        return answers
    stripped = (text or "").strip()
    return [stripped] if stripped else []


def parse_user_answers(text: str, questions: list[Any]) -> list[dict[str, str]]:
    answers = split_answer_texts(text, len(questions) or QUESTION_COUNT)
    items: list[dict[str, str]] = []
    for question, answer in zip(questions, answers, strict=False):
        if isinstance(question, dict):
            qid = question.get("id")
        else:
            qid = getattr(question, "id", None)
        if qid:
            items.append({"question_id": str(qid), "answer": answer})
    return items

last_used_fallback = False
last_degrade_log = ""

_KNOWLEDGE_CITE_CHARS = 80
_DEFAULT_QUESTION_PROMPT = (
    "你是面试陪伴出题助手。必须只输出 JSON，合计五道题："
    "默认 3 道 common（简历深挖），2 道 role（针对等待面试岗位）；"
    "可改为 2+3 或 4+1，但必须同时包含两种题型。"
    "prompt 只写面试官会问的题干，不要写考查点、分配或优先级。"
    "已挂岗位 id 不得出现。有考查点必须用来出题。有 knowledge_excerpts 时必须引用其中可核对事实，"
    "不得只出与面经无关的简历套话。格式："
    '{"questions":['
    '{"seq":1,"kind":"common","prompt":"...","target_application_id":null},'
    '{"seq":2,"kind":"common","prompt":"...","target_application_id":null},'
    '{"seq":3,"kind":"common","prompt":"...","target_application_id":null},'
    '{"seq":4,"kind":"role","prompt":"...","target_application_id":"a_xx"},'
    '{"seq":5,"kind":"role","prompt":"...","target_application_id":"a_xx"}]}'
)


class QuestionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def current_for(self, candidate: Candidate) -> QuestionSetPublic:
        from src.services.scheduler import beijing_date_text, now_beijing

        today = beijing_date_text(now_beijing())
        question_set = await self.get_for_date(candidate.id, today)
        if question_set is None:
            return QuestionSetPublic(
                id=None,
                beijing_date=today,
                status="none",
                questions=[],
                review=None,
                voided_at=None,
            )
        items = await self.list_questions(question_set.id)
        return QuestionSetPublic(
            id=question_set.id,
            beijing_date=question_set.beijing_date,
            status=cast(QuestionSetStatus, question_set.status),
            questions=[QuestionPublic.model_validate(item) for item in items],
            review=question_set.review,
            voided_at=question_set.voided_at,
        )

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

    async def list_applications(self, candidate_id: str) -> list[Application]:
        result = await self.db.execute(
            select(Application)
            .where(Application.candidate_id == candidate_id)
            .order_by(Application.created_at.asc())
        )
        return list(result.scalars().all())

    async def waiting_applications(self, candidate_id: str) -> list[Application]:
        apps = await self.list_applications(candidate_id)
        waiting = [item for item in apps if item.normalized_status == WAITING_STATUS]
        return sort_waiting_by_urgency(waiting)

    def is_question_day(
        self,
        candidate: Candidate,
        today: date,
        waiting: list[Application],
        today_set: QuestionSet | None,
    ) -> bool:
        if not waiting:
            return False
        if today_set is not None and today_set.status == "rest_day":
            return False
        next_q = candidate.next_question_date
        if next_q is not None and next_q > today:
            return False
        return True

    async def ensure_question_day(self, candidate: Candidate, now: datetime) -> QuestionSet | None:
        today = now.date()
        today_text = today.isoformat()
        today_set = await self.get_for_date(candidate.id, today_text)
        waiting = await self.waiting_applications(candidate.id)
        if today_set is not None:
            return today_set
        if not self.is_question_day(candidate, today, waiting, today_set):
            return None
        created = await self.generate_for_candidate(candidate, now, waiting)
        return created

    async def generate_for_candidate(
        self,
        candidate: Candidate,
        now: datetime,
        waiting: list[Application],
    ) -> QuestionSet:
        global last_used_fallback, last_degrade_log
        last_used_fallback = False
        last_degrade_log = ""
        rejected_ids = await self._rejected_ids(candidate.id)
        specs = await self._llm_or_template(candidate, waiting, rejected_ids)
        created_at = now.astimezone(UTC)
        question_set = QuestionSet(
            id=new_id("qs"),
            candidate_id=candidate.id,
            beijing_date=now.date().isoformat(),
            status="in_progress",
            review=None,
            voided_at=None,
            created_at=created_at,
        )
        self.db.add(question_set)
        await self.db.flush()
        stored: list[Question] = []
        for spec in specs:
            row = Question(
                id=new_id("q"),
                question_set_id=question_set.id,
                seq=int(spec["seq"]),
                kind=str(spec["kind"]),
                prompt=str(spec["prompt"]),
                answer=None,
                target_application_id=spec.get("target_application_id"),
            )
            self.db.add(row)
            stored.append(row)
        await self.db.flush()
        if candidate.next_question_date is None:
            candidate.next_question_date = now.date()
        await self._insert_questions_message(candidate, now, stored)
        logger.info(
            "已写入当日五题",
            candidate_id=candidate.id,
            question_set_id=question_set.id,
            degraded=last_used_fallback,
        )
        return question_set

    async def void_incomplete_at_midnight(self, candidate: Candidate, now: datetime) -> None:
        yesterday = (now.date() - timedelta(days=1)).isoformat()
        today = now.date().isoformat()
        previous = await self.get_for_date(candidate.id, yesterday)
        if previous is not None and previous.status in {"pending", "in_progress"}:
            previous.status = "voided"
            previous.voided_at = now.astimezone(UTC)
            question_day = date.fromisoformat(previous.beijing_date)
            candidate.next_question_date = question_day + timedelta(days=VOID_REST_DAYS)
            logger.info(
                "已作废未完成题集",
                candidate_id=candidate.id,
                question_set_id=previous.id,
                next_question_date=candidate.next_question_date.isoformat(),
            )
        today_set = await self.get_for_date(candidate.id, today)
        should_rest = (
            previous is not None
            and previous.status == "voided"
            and candidate.next_question_date is not None
            and candidate.next_question_date > now.date()
        )
        if should_rest and today_set is None:
            rest = QuestionSet(
                id=new_id("qs"),
                candidate_id=candidate.id,
                beijing_date=today,
                status="rest_day",
                review=None,
                voided_at=None,
                created_at=now.astimezone(UTC),
            )
            self.db.add(rest)
            logger.info("已写入休息日题集", candidate_id=candidate.id, beijing_date=today)
        await self.db.flush()

    def apply_completed_next_date(self, candidate: Candidate, now: datetime) -> None:
        nxt = now.date() + timedelta(days=COMPLETE_GAP_DAYS)
        candidate.next_question_date = nxt
        logger.info(
            "题集已完成，下次出题日已设为完成日加一天",
            candidate_id=candidate.id,
            next_question_date=nxt.isoformat(),
        )

    async def complete_with_review(
        self,
        candidate: Candidate,
        question_set: QuestionSet,
        review: str,
        now: datetime,
    ) -> None:
        question_set.review = review
        question_set.status = "completed"
        self.apply_completed_next_date(candidate, now)
        candidate.updated_at = utc_now()
        await self.append_review_summaries(candidate, question_set, review)
        await self.db.flush()
        logger.info("已保存点评并追加面试总结", question_set_id=question_set.id)

    async def append_review_summaries(
        self,
        candidate: Candidate,
        question_set: QuestionSet,
        review: str,
    ) -> None:
        text = (review or "").strip()
        if not text:
            return
        questions = await self.list_questions(question_set.id)
        related_ids = {
            item.target_application_id
            for item in questions
            if item.target_application_id
        }
        apps = await self.list_applications(candidate.id)
        if related_ids:
            related = [item for item in apps if item.id in related_ids]
        else:
            related = [item for item in apps if item.normalized_status == WAITING_STATUS]
        for row in related:
            existing = (row.interview_summary or "").strip()
            if existing:
                row.interview_summary = f"{existing}\n{text}"
            else:
                row.interview_summary = text
            row.updated_at = utc_now()
        await self.db.flush()

    async def _rejected_ids(self, candidate_id: str) -> set[str]:
        apps = await self.list_applications(candidate_id)
        return {item.id for item in apps if item.normalized_status == REJECTED_STATUS}

    async def _llm_or_template(
        self,
        candidate: Candidate,
        waiting: list[Application],
        rejected_ids: set[str],
    ) -> list[dict[str, Any]]:
        global last_used_fallback, last_degrade_log
        excerpts = await KnowledgeStore(self.db).excerpts_for_waiting(candidate, waiting)
        if _llm_configured():
            parsed = await self._try_llm_questions(
                candidate, waiting, rejected_ids, excerpts
            )
            if parsed is not None:
                return parsed
            last_used_fallback = True
            last_degrade_log = "出题 JSON 校验失败，已降级为规则模板"
            logger.warning(
                last_degrade_log,
                candidate_id=candidate.id,
            )
            return _fallback_template(waiting, excerpts)
        last_used_fallback = True
        last_degrade_log = "未走百炼 HTTP，已降级为规则模板出题"
        logger.info(last_degrade_log, candidate_id=candidate.id)
        return _happy_template(waiting, excerpts)

    async def _try_llm_questions(
        self,
        candidate: Candidate,
        waiting: list[Application],
        rejected_ids: set[str],
        excerpts: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        payload = _build_llm_user_payload(candidate, waiting, excerpts)
        system_prompt = await render_prompt(
            "questions.md",
            self.db,
            candidate,
            fallback=_DEFAULT_QUESTION_PROMPT,
            knowledge_override=format_knowledge_override(excerpts),
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload},
        ]
        settings = get_settings()
        api_key = resolved_candidate_api_key(candidate)
        for attempt in range(FORMAT_ATTEMPTS):
            try:
                await _log_llm_call(
                    self.db,
                    candidate_id=candidate.id,
                    role="interview",
                    model=settings.llm_interview_model,
                    purpose="questions",
                )
                data = await LlmClient().chat_completions(
                    api_key=api_key,
                    candidate_id=candidate.id,
                    messages=messages,
                    model=settings.llm_interview_model,
                    temperature=settings.llm_interview_temperature,
                )
            except LlmKeyInvalidError:
                raise
            except Exception as exc:
                logger.warning(
                    "出题模型调用失败",
                    candidate_id=candidate.id,
                    attempt=attempt + 1,
                    detail=type(exc).__name__,
                )
                continue
            content = _choice_text(data)
            parsed = _validate_question_payload(content, waiting, rejected_ids)
            if parsed is not None:
                return parsed
            logger.warning(
                "出题 JSON 校验未通过，准备重试",
                candidate_id=candidate.id,
                attempt=attempt + 1,
            )
        return None

    async def _insert_questions_message(
        self,
        candidate: Candidate,
        now: datetime,
        questions: list[Question],
    ) -> None:
        conversation = await self._conversation(candidate.id)
        if conversation is None:
            logger.error("出题时找不到对话", candidate_id=candidate.id)
            return
        message = Message(
            id=new_id("m"),
            conversation_id=conversation.id,
            role="assistant",
            message_type="questions",
            content=format_daily_questions_text(questions),
            created_at=now.astimezone(UTC),
        )
        self.db.add(message)
        await self.db.flush()

    async def _conversation(self, candidate_id: str) -> Conversation | None:
        result = await self.db.execute(
            select(Conversation).where(Conversation.candidate_id == candidate_id)
        )
        return result.scalar_one_or_none()


def sort_waiting_by_urgency(waiting: list[Application]) -> list[Application]:
    """有 interview_at 的按时刻升序更急；时间为空不按 deadline 加急。"""
    return sorted(waiting, key=_urgency_key)


def _urgency_key(item: Application) -> tuple[int, str, str]:
    interview_at = (item.interview_at or "").strip()
    if interview_at:
        return (0, interview_at, _applied_or_created(item))
    return (1, "", _applied_or_created(item))


def _applied_or_created(item: Application) -> str:
    if item.applied_at is not None:
        return item.applied_at.isoformat()
    created = item.created_at
    if created is None:
        return ""
    return created.isoformat()


async def _log_llm_call(
    db: AsyncSession,
    *,
    candidate_id: str,
    role: str,
    model: str,
    purpose: str,
) -> None:
    result = await db.execute(
        select(Conversation.id).where(Conversation.candidate_id == candidate_id)
    )
    conversation_id = result.scalar_one_or_none()
    db.add(
        LlmCallLog(
            id=new_id("l"),
            candidate_id=candidate_id,
            conversation_id=conversation_id,
            role=role,
            model=model,
            purpose=purpose,
        )
    )
    await db.flush()
    logger.info("已记录模型调用", role=role, model=model, purpose=purpose)


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


def _extract_json_text(raw: str) -> str:
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped


def _validate_question_payload(
    raw: str,
    waiting: list[Application],
    rejected_ids: set[str],
) -> list[dict[str, Any]] | None:
    waiting_ids = {item.id for item in waiting}
    try:
        data = json.loads(_extract_json_text(raw))
    except json.JSONDecodeError:
        return None
    items = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != QUESTION_COUNT:
        return None
    kinds: list[str] = []
    normalized: list[dict[str, Any]] = []
    seqs: list[int] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        seq_raw = item.get("seq")
        if seq_raw is None:
            return None
        try:
            seq = int(seq_raw)
        except (TypeError, ValueError):
            return None
        kind = str(item.get("kind") or "")
        prompt = str(item.get("prompt") or "").strip()
        target = item.get("target_application_id")
        if target == "":
            target = None
        if not prompt or kind not in {COMMON_KIND, ROLE_KIND}:
            return None
        if kind == COMMON_KIND and target is not None:
            return None
        if kind == ROLE_KIND:
            if not isinstance(target, str) or target in rejected_ids or target not in waiting_ids:
                return None
        seqs.append(seq)
        kinds.append(kind)
        normalized.append(
            {
                "seq": seq,
                "kind": kind,
                "prompt": prompt,
                "target_application_id": target,
            }
        )
    if sorted(seqs) != list(range(1, QUESTION_COUNT + 1)):
        return None
    if COMMON_KIND not in kinds or ROLE_KIND not in kinds:
        return None
    normalized.sort(key=lambda row: int(row["seq"]))
    return normalized


def _role_targets(waiting: list[Application]) -> list[Application]:
    if not waiting:
        raise ValueError("没有等待面试岗位，不能出题")
    if len(waiting) == 1:
        return [waiting[0], waiting[0]]
    return [waiting[0], waiting[1]]


def _happy_template(
    waiting: list[Application],
    excerpts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    roles = _role_targets(waiting)
    first, second = roles[0], roles[1]
    first_cite = _first_citation(excerpts, application_id=first.id)
    second_cite = _first_citation(excerpts, application_id=second.id) or first_cite
    common_one = "请把简历里某段项目经历讲深一层：你做了什么、难点是什么、结果如何衡量？"
    common_two = "再选一段不同的简历经历，说明其中的冲突、你如何权衡，以及学到了什么。"
    common_three = "用简历里一次协作或推进受阻的经历，讲清你怎么沟通、怎么收口。"
    if first_cite:
        common_one = (
            f"结合已收录面经中的「{first_cite}」，把简历里相关经历讲深一层："
            "你做了什么、难点是什么、结果如何衡量？"
        )
        common_two = f"对照面经材料「{first_cite}」，说明其中的冲突、你如何权衡，以及学到了什么。"
    second_prompt = (
        f"结合「{second.company_name} {second.role_title}」，"
        "面试官追问风险、取舍和复盘时你会怎么答？"
    )
    if second_cite:
        second_prompt = (
            f"结合「{second.company_name} {second.role_title}」已收录面经中的「{second_cite}」，"
            "面试官追问风险、取舍和复盘时你会怎么答？"
        )
    return [
        {
            "seq": 1,
            "kind": COMMON_KIND,
            "prompt": common_one,
            "target_application_id": None,
        },
        {
            "seq": 2,
            "kind": COMMON_KIND,
            "prompt": common_two,
            "target_application_id": None,
        },
        {
            "seq": 3,
            "kind": COMMON_KIND,
            "prompt": common_three,
            "target_application_id": None,
        },
        {
            "seq": 4,
            "kind": ROLE_KIND,
            "prompt": (
                f"针对「{first.company_name} {first.role_title}」，"
                + (f"结合已收录面经「{first_cite}」，" if first_cite else "")
                + "你会如何设计并落地这个岗位最核心的方案？"
            ),
            "target_application_id": first.id,
        },
        {
            "seq": 5,
            "kind": ROLE_KIND,
            "prompt": second_prompt,
            "target_application_id": second.id,
        },
    ]


def _fallback_template(
    waiting: list[Application],
    excerpts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    roles = _role_targets(waiting)
    first, second = roles[0], roles[1]
    cite = _first_citation(excerpts, application_id=first.id)
    if cite:
        return [
            {
                "seq": 1,
                "kind": COMMON_KIND,
                "prompt": (
                    f"结合已收录面经中的「{cite}」，用简历里最能代表你的一段经历，"
                    "讲清背景、你的动作和可验证结果。"
                ),
                "target_application_id": None,
            },
            {
                "seq": 2,
                "kind": COMMON_KIND,
                "prompt": f"对照面经材料「{cite}」，说明其中的冲突、你如何权衡，以及学到了什么。",
                "target_application_id": None,
            },
            {
                "seq": 3,
                "kind": COMMON_KIND,
                "prompt": "再用简历里一次协作或推进受阻的经历，讲清你怎么沟通、怎么收口。",
                "target_application_id": None,
            },
            {
                "seq": 4,
                "kind": ROLE_KIND,
                "prompt": (
                    f"针对「{first.company_name} {first.role_title}」，"
                    f"面试官按已收录面经追问「{cite}」时你会怎么答？"
                ),
                "target_application_id": first.id,
            },
            {
                "seq": 5,
                "kind": ROLE_KIND,
                "prompt": (
                    f"结合「{second.company_name} {second.role_title}」，"
                    "这个岗位最可能被追问的一点，你会怎么应对？"
                ),
                "target_application_id": second.id,
            },
        ]
    return [
        {
            "seq": 1,
            "kind": COMMON_KIND,
            "prompt": "请用简历里最能代表你的一段经历，讲清背景、你的动作和可验证结果。",
            "target_application_id": None,
        },
        {
            "seq": 2,
            "kind": COMMON_KIND,
            "prompt": "再选一段不同的简历经历，说明其中的冲突、你如何权衡，以及学到了什么。",
            "target_application_id": None,
        },
        {
            "seq": 3,
            "kind": COMMON_KIND,
            "prompt": "用简历里一次协作或推进受阻的经历，讲清你怎么沟通、怎么收口。",
            "target_application_id": None,
        },
        {
            "seq": 4,
            "kind": ROLE_KIND,
            "prompt": (
                f"针对「{first.company_name} {first.role_title}」，"
                "你会如何设计并落地这个岗位最核心的方案？"
            ),
            "target_application_id": first.id,
        },
        {
            "seq": 5,
            "kind": ROLE_KIND,
            "prompt": (
                f"结合「{second.company_name} {second.role_title}」，"
                "面试官追问风险、取舍和复盘时你会怎么答？"
            ),
            "target_application_id": second.id,
        },
    ]


def _first_citation(
    excerpts: list[dict[str, Any]] | None,
    *,
    application_id: str | None = None,
) -> str:
    citations = _citations_from_excerpts(excerpts, application_id=application_id, limit=1)
    return citations[0] if citations else ""


def _citations_from_excerpts(
    excerpts: list[dict[str, Any]] | None,
    *,
    application_id: str | None = None,
    limit: int = 2,
) -> list[str]:
    citations: list[str] = []
    for item in excerpts or []:
        item_app = item.get("application_id")
        if application_id and item_app and item_app != application_id:
            continue
        snippet = ""
        for text in item.get("texts") or []:
            snippet = re.sub(r"\s+", " ", str(text).strip())
            if snippet:
                break
        if not snippet:
            snippet = re.sub(r"\s+", " ", str(item.get("title") or "").strip())
        if not snippet:
            continue
        clipped = snippet[:_KNOWLEDGE_CITE_CHARS]
        if clipped not in citations:
            citations.append(clipped)
        if len(citations) >= limit:
            break
    return citations


def _build_llm_user_payload(
    candidate: Candidate,
    waiting: list[Application],
    excerpts: list[dict[str, Any]] | None = None,
) -> str:
    rows = []
    for index, item in enumerate(waiting):
        rows.append(
            {
                "id": item.id,
                "company_name": item.company_name,
                "role_title": item.role_title,
                "exam_points": item.exam_points,
                "interview_at": item.interview_at,
                "applied_at": item.applied_at.isoformat() if item.applied_at else None,
                "normalized_status": item.normalized_status,
                "urgency_rank": index + 1,
            }
        )
    resume_excerpt = (candidate.resume_text or "")[:2000]
    return json.dumps(
        {
            "resume_excerpt": resume_excerpt,
            "waiting_applications": rows,
            "knowledge_excerpts": excerpts or [],
        },
        ensure_ascii=False,
    )
