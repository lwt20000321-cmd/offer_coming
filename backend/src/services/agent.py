"""API-006：先调编排模型再分派。写入 llm_call_logs（只记 role/model/purpose）。"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from pycore.core.logger import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import AppSettings, get_settings
from src.db.models import Candidate, Conversation, LlmCallLog, Message
from src.models.candidate import MessagePublic, MessageType
from src.plugins.registry import (
    build_counseling_registry,
    build_interview_registry,
    build_knowledge_registry,
    build_nudge_registry,
    build_orchestrator_registry,
)
from src.repositories.conversation import ConversationRepository, MessageRepository
from src.services.application_status import (
    STATUS_PROGRESS_ASK,
    canonical_status_from_text,
    company_hint_from_url,
    looks_like_apply_intent,
    looks_like_jd_body,
    looks_like_missing_row_complaint,
    parse_application_fields,
)
from src.services.candidate import CandidateService
from src.services.knowledge_store import KnowledgeStore
from src.services.llm_client import (
    LlmClient,
    LlmClientError,
    LlmKeyInvalidError,
    LlmKeyMissingError,
    LlmQuotaExceededError,
    LlmUnavailableError,
    candidate_llm_context,
    resolved_candidate_api_key,
    should_skip_llm_http,
)
from src.services.prompt_context import build_shared_prompt_context, fill_shared_variables
from src.services.questions import (
    format_daily_questions_text,
    looks_like_five_question_ask,
    looks_like_question_answers,
    split_answer_texts,
)
from src.services.tools import (
    COUNSELING_TOOL_NAMES,
    INTERVIEW_TOOL_NAMES,
    KNOWLEDGE_TOOL_NAMES,
    NUDGE_TOOL_NAMES,
    ORCHESTRATOR_DEFINITIONS,
    STAGE_BY_TOOL,
    USER_INGEST_NOTICE,
    ToolExecutor,
    definitions_for,
    tone_level_for_hour,
)
from src.utils.crypto import new_id, utc_now
from src.utils.errors import Forbidden, NotFound

logger = get_logger()

_ROLE_REGISTRIES = {
    "interview": build_interview_registry,
    "nudge": build_nudge_registry,
    "knowledge": build_knowledge_registry,
    "counseling": build_counseling_registry,
}

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
PROMPT_FILES = (
    "xiaoao_system.md",
    "orchestrator.md",
    "exam_points.md",
    "questions.md",
    "review.md",
    "nudge.md",
    "counseling.md",
    "knowledge_search.md",
    "knowledge_ingest.md",
    "knowledge_evaluate.md",
)
_DELTA_CHUNK_CHARS = 4
_DELTA_PACE_SECONDS = 0.018
_URL_RE = re.compile(r"https?://[^\s\u3000）)】\"'<>]+")
_NUMBERED_STEM_RE = re.compile(r"(?m)^\s*[1-5][\.、．]")
_ANXIETY_MARKERS = ("焦虑", "不开心", "压力大", "好累", "崩溃", "难受", "抑郁", "不想面")
_BETTER_MARKERS = ("心情变好", "好多了", "没事了", "不焦虑", "心情好了", "好点了")
_PROGRESS_MARKERS = (
    "进度",
    "deadline",
    "截止",
    "已挂",
    "挂了",
    "被拒",
    "等待面试",
    "约面",
    "一面",
    "二面",
    "三面",
    "筛选",
    "测评",
    "简历挂",
    "状态",
    "黄了",
    "淘汰",
)
_ANSWER_MARKERS = ("第一题", "第二题", "第三题", "第四题", "第五题", "第1题", "第2题", "第3题", "第4题", "第5题", "作答")
_IDLE_MENU_MARKERS = ("生成题", "查面经", "改投递", "先确认心情", "确认心情", "要不要")
_STAGE_TEXT = {
    "thinking": "小凹正在思考...",
    "fetch_jd": "正在读取岗位链接",
    "update_application": "正在更新投递进度",
    "search_experiences": "正在查找面经",
    "ingest_document": "正在收录面经",
    "counseling": "正在回应你的心情",
    "questions": "正在准备今日题目",
    "review": "正在点评你的回答",
    "reply": "正在回复",
}
_busy: set[str] = set()
_busy_lock = threading.Lock()


def load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


def load_all_prompts() -> dict[str, str]:
    return {name: load_prompt(name) for name in PROMPT_FILES}


def try_acquire_conversation(conversation_id: str) -> bool:
    with _busy_lock:
        if conversation_id in _busy:
            return False
        _busy.add(conversation_id)
        return True


def release_conversation(conversation_id: str) -> None:
    with _busy_lock:
        _busy.discard(conversation_id)


def mark_conversation_busy(conversation_id: str) -> None:
    with _busy_lock:
        _busy.add(conversation_id)


def is_conversation_busy(conversation_id: str) -> bool:
    with _busy_lock:
        return conversation_id in _busy


def _sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _slim_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    applications = snapshot.get("applications") or []
    waiting = 0
    if isinstance(applications, list):
        waiting = sum(
            1
            for item in applications
            if isinstance(item, dict) and item.get("normalized_status") == "waiting_interview"
        )
    question_set = snapshot.get("question_set") if isinstance(snapshot.get("question_set"), dict) else {}
    today = snapshot.get("today") if isinstance(snapshot.get("today"), dict) else {}
    return {
        "beijing_time": snapshot.get("beijing_time"),
        "counseling_active": snapshot.get("counseling_active"),
        "resume_parse_ok": snapshot.get("resume_parse_ok"),
        "application_count": len(applications) if isinstance(applications, list) else 0,
        "waiting_interview_count": waiting,
        "question_set_status": question_set.get("status") or today.get("question_set_status"),
    }


def _chunk_text(text: str) -> list[str]:
    if not text:
        return [""]
    size = _DELTA_CHUNK_CHARS
    return [text[index : index + size] for index in range(0, len(text), size)]


def _strip_url(raw: str) -> str:
    return raw.rstrip("。．，,，、；;)]】")


def extract_urls(text: str) -> list[str]:
    found: list[str] = []
    for match in _URL_RE.findall(text or ""):
        cleaned = _strip_url(match)
        if cleaned not in found:
            found.append(cleaned)
    return found


def _prefer_name(current: str, extra: str) -> str:
    if not current:
        return extra
    if not extra:
        return current
    if current in extra:
        return extra
    if extra in current:
        return current
    return current


def _visible_status_text(stage: str) -> str:
    return _STAGE_TEXT.get(stage, "小凹正在思考...")


def _clip_user_reply(text: str, limit: int) -> str:
    value = (text or "").strip()
    if limit <= 0 or len(value) <= limit:
        return value
    cut = value[:limit]
    for sep in ("\n", "。", "！", "？", "；"):
        pos = cut.rfind(sep)
        if pos >= max(40, limit // 3):
            return cut[: pos + 1].strip()
    return cut.rstrip() + "…"


def _should_clip_user_reply(text: str, message_type: str) -> bool:
    if message_type in {"questions", "review"}:
        return False
    blob = text or ""
    if "今日五题：" in blob or "今日还没答完的题目" in blob:
        return False
    if len(_NUMBERED_STEM_RE.findall(blob)) >= 4:
        return False
    return True


class AgentService:
    def __init__(self, db: AsyncSession, settings: AppSettings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)
        self.candidates = CandidateService(db)
        self.llm = LlmClient(self.settings)
        self.knowledge_store = KnowledgeStore(db)
        self._status_events: list[tuple[str, str]] = []
        self._used_tools: list[str] = []
        self._attachment_body: str | None = None
        self._attachment_name: str | None = None
        self._conversation_id: str | None = None
        self._candidate: Candidate | None = None
        self._chat_history: list[dict[str, str]] = []
        self._current_user_message_id: str | None = None
        self.tools: ToolExecutor | None = None

    async def require_conversation(
        self, conversation_id: str, candidate: Candidate
    ) -> Conversation:
        conversation = await self.conversations.get_by_id(conversation_id)
        if conversation is None:
            raise NotFound("对话不存在。")
        if conversation.candidate_id != candidate.id:
            raise Forbidden("不能查看别人的对话。")
        return conversation

    async def stream_reply(
        self,
        candidate: Candidate,
        conversation: Conversation,
        user_text: str,
        *,
        upload_filename: str | None = None,
        upload_bytes: bytes | None = None,
    ) -> AsyncIterator[str]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def run() -> None:
            try:
                async for chunk in self._run_loop(
                    candidate,
                    conversation,
                    user_text,
                    upload_filename=upload_filename,
                    upload_bytes=upload_bytes,
                ):
                    await queue.put(chunk)
            except Exception as exc:
                logger.error("编排循环失败", detail=str(exc))
                notice = "小凹这会儿连不上，请稍后再发这句"
                try:
                    await self._add_message(
                        conversation.id, "assistant", "error_notice", notice
                    )
                    await self.db.commit()
                except Exception:
                    logger.exception("写入失败说明失败")
                await queue.put(
                    _sse(
                        "error",
                        {
                            "error": notice,
                            "error_code": "LLM_UNAVAILABLE",
                        },
                    )
                )
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            if not task.done():
                await task
            release_conversation(conversation.id)

    async def _run_loop(
        self,
        candidate: Candidate,
        conversation: Conversation,
        user_text: str,
        *,
        upload_filename: str | None = None,
        upload_bytes: bytes | None = None,
    ) -> AsyncIterator[str]:
        tools = ToolExecutor(self.db, candidate, self.settings)
        self.tools = tools
        self._candidate = candidate
        self._conversation_id = conversation.id
        self._status_events = []
        self._used_tools = []
        self._attachment_body = None
        self._attachment_name = None
        self._chat_history = []
        self._current_user_message_id = None
        self._resume_dispatched = False
        self._review_dispatched = False
        self._last_review_reply = ""
        self._api_key = ""
        stored_user = user_text
        if upload_bytes and upload_filename:
            path = self.knowledge_store.save_upload(
                candidate.id, upload_filename, upload_bytes
            )
            self._attachment_body = self.knowledge_store.parse_upload(path)
            self._attachment_name = upload_filename
            stored_user = user_text.strip() or upload_filename
        elif user_text.strip() and "面经" in user_text and not extract_urls(user_text):
            self._attachment_body = user_text.strip()
            self._attachment_name = None

        user_message = await self._add_message(
            conversation.id, "user", "chat", stored_user
        )
        self._current_user_message_id = user_message.id
        logger.info("已写入用户消息", message_id=user_message.id)
        await self.db.commit()
        yield _sse(
            "status",
            {"stage": "thinking", "text": _STAGE_TEXT["thinking"]},
        )
        await self.db.refresh(candidate)
        self._candidate = candidate
        tools.candidate = candidate

        if not (candidate.llm_api_key_ciphertext or "").strip():
            async for chunk in self._fail_key_error(conversation, LlmKeyMissingError()):
                yield chunk
            return
        if candidate.llm_key_status == "invalid":
            async for chunk in self._fail_key_error(conversation, LlmKeyInvalidError()):
                yield chunk
            return
        try:
            api_key = resolved_candidate_api_key(candidate)
        except LlmKeyMissingError as exc:
            async for chunk in self._fail_key_error(conversation, exc):
                yield chunk
            return
        self._api_key = api_key
        with candidate_llm_context(api_key, candidate.id):
            try:
                async for chunk in self._run_model_loop(
                    candidate,
                    conversation,
                    stored_user,
                ):
                    yield chunk
            except LlmKeyInvalidError as exc:
                await self.candidates.mark_llm_key_invalid(candidate)
                async for chunk in self._fail_key_error(conversation, exc):
                    yield chunk
            except LlmQuotaExceededError as exc:
                async for chunk in self._fail_key_error(conversation, exc):
                    yield chunk
            except LlmKeyMissingError as exc:
                async for chunk in self._fail_key_error(conversation, exc):
                    yield chunk

    async def _fail_key_error(
        self,
        conversation: Conversation,
        exc: LlmClientError,
    ) -> AsyncIterator[str]:
        notice = exc.user_message
        await self._add_message(conversation.id, "assistant", "error_notice", notice)
        await self.db.commit()
        yield _sse("error", {"error": notice, "error_code": exc.error_code})

    async def _run_model_loop(
        self,
        candidate: Candidate,
        conversation: Conversation,
        stored_user: str,
    ) -> AsyncIterator[str]:
        tools = self.tools
        assert tools is not None

        if self._should_record_before_llm(stored_user):
            recorded = await self._record_application_from_turn(
                stored_user,
                url=self._url_for_record(stored_user),
            )
            if recorded:
                await self.db.commit()
        extra_questions = await self._replay_today_questions(stored_user)
        if extra_questions:
            yield _sse("status", {"stage": "questions", "text": _STAGE_TEXT["questions"]})
            stored = await self._add_message(
                conversation.id, "assistant", "chat", extra_questions
            )
            await self.db.commit()
            snapshot = await tools.current_snapshot()
            async for chunk in self._yield_text_deltas(extra_questions):
                yield chunk
            yield _sse(
                "done",
                {
                    "message": MessagePublic.model_validate(stored).model_dump(mode="json"),
                    "snapshot": snapshot.model_dump(mode="json"),
                },
            )
            return

        prompts = load_all_prompts()
        snapshot_obs = await tools.get_snapshot({})
        shared = await build_shared_prompt_context(self.db, candidate)
        self._chat_history = await self._load_visible_history(conversation.id)
        llm_messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._orchestrator_prompt(
                    fill_shared_variables(prompts["orchestrator.md"], shared),
                    snapshot_obs,
                ),
            },
            *self._chat_history,
            {"role": "user", "content": stored_user},
        ]
        if self._attachment_body:
            llm_messages.append(
                {
                    "role": "user",
                    "content": f"用户发来一份面经正文：\n{self._attachment_body[:4000]}",
                }
            )

        current_stage: str | None = "thinking"
        format_errors = 0
        final_text = ""
        llm_failed = False
        live_streamed = ""
        steps = 0
        max_steps = self.settings.agent_max_steps
        runners = {
            "call_counseling_agent": self._run_counseling,
            "call_nudge_agent": self._run_nudge,
            "call_interview_agent": self._run_interview,
            "call_knowledge_agent": self._run_knowledge,
        }
        registry = build_orchestrator_registry(tools, runners)

        while steps < max_steps:
            steps += 1
            try:
                live = not self._used_tools
                turn_streamed = ""
                response: dict[str, Any] | None = None
                async for kind, payload in self._stream_orchestrator_turn(
                    llm_messages, live=live
                ):
                    if kind == "delta":
                        turn_streamed += str(payload)
                        yield _sse("delta", {"text": payload})
                    else:
                        response = payload
                if response is None:
                    llm_failed = True
                    break
            except LlmKeyInvalidError:
                raise
            except LlmQuotaExceededError:
                raise
            except LlmKeyMissingError:
                raise
            except (
                httpx.HTTPError,
                RuntimeError,
                TimeoutError,
                json.JSONDecodeError,
                LlmClientError,
            ) as exc:
                logger.info("编排调用失败，结束本轮", detail=type(exc).__name__)
                llm_failed = True
                break

            choice = (response.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []
            content = (message.get("content") or "").strip()
            if tool_calls:
                turn_streamed = ""

            if tool_calls:
                llm_messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        "tool_calls": tool_calls,
                    }
                )
                for call in tool_calls:
                    function = call.get("function") or {}
                    name = str(function.get("name") or "")
                    raw_args = function.get("arguments") or "{}"
                    try:
                        arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        if not isinstance(arguments, dict):
                            raise ValueError("arguments 不是对象")
                    except (json.JSONDecodeError, TypeError, ValueError):
                        format_errors += 1
                        observation = json.dumps(
                            {"error": "参数不是合法 JSON"},
                            ensure_ascii=False,
                        )
                        arguments = {}
                        name = name or "unknown"
                    else:
                        format_errors = 0
                        stage = self._stage_for_dispatch(name, arguments)
                        if stage != current_stage:
                            current_stage = stage
                            yield _sse(
                                "status",
                                {"stage": stage, "text": _visible_status_text(stage)},
                            )
                        observation = ""
                        try:
                            result = await registry.execute(name, **arguments)
                        except (LlmKeyInvalidError, LlmQuotaExceededError, LlmKeyMissingError):
                            raise
                        except LlmClientError as exc:
                            logger.info(
                                "子调用失败，本轮继续收口",
                                tool=name,
                                detail=type(exc).__name__,
                            )
                            observation = json.dumps(
                                {"ok": False, "error": exc.user_message},
                                ensure_ascii=False,
                            )
                        else:
                            if not result:
                                observation = json.dumps(
                                    {"ok": False, "error": result.error},
                                    ensure_ascii=False,
                                )
                            elif isinstance(result.data, str):
                                observation = result.data
                            else:
                                observation = json.dumps(
                                    result.data, ensure_ascii=False, default=str
                                )
                        self._used_tools.append(name)
                        for extra_stage, extra_text in self._status_events:
                            if extra_stage != current_stage:
                                current_stage = extra_stage
                                yield _sse(
                                    "status",
                                    {"stage": extra_stage, "text": extra_text},
                                )
                        self._status_events.clear()
                    llm_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(call.get("id") or name),
                            "content": observation,
                        }
                    )
                    if format_errors >= 2:
                        final_text = "这一轮参数有点乱，请换种说法再发一次，我还没有假装记下岗位。"
                        break
                if format_errors >= 2:
                    break
                continue

            final_text = content
            live_streamed = turn_streamed
            break

        if not final_text and not llm_failed:
            if steps >= max_steps:
                final_text = "这一轮步骤用尽了。请再发一句「继续」，我接着刚才的内容说。"
            else:
                final_text = "我在。你可以发岗位链接、进度，或者告诉我现在的心情。"

        assert self.tools is not None
        await self._persist_pending_exam_points()
        await self._persist_pending_ingest()
        extra_resume = await self._ensure_mood_better_dispatch(stored_user)
        extra_review = await self._ensure_answers_and_review(stored_user)
        extra_apply = await self._ensure_apply_and_status(stored_user)
        extra_questions = await self._replay_today_questions(stored_user)
        for extra_stage, extra_text in self._status_events:
            if extra_stage != current_stage:
                current_stage = extra_stage
                yield _sse(
                    "status",
                    {"stage": extra_stage, "text": extra_text},
                )
        self._status_events.clear()
        if extra_review:
            final_text = extra_review
        elif extra_questions:
            final_text = extra_questions
        elif extra_resume:
            if _looks_like_idle_menu(final_text) or not (final_text or "").strip():
                final_text = extra_resume
            elif extra_resume not in final_text:
                final_text = f"{final_text}\n\n{extra_resume}".strip()
        elif self._last_review_reply and (
            _looks_like_idle_menu(final_text) or "缺点" not in (final_text or "")
        ):
            final_text = self._last_review_reply
        if extra_apply and extra_apply not in (final_text or "") and not extra_review:
            if _looks_like_idle_menu(final_text) or not (final_text or "").strip():
                final_text = extra_apply
            else:
                final_text = f"{final_text}\n\n{extra_apply}".strip()
        if self.tools.counseling_turned_off:
            extra = await self._followup_after_counseling(self.tools)
            if extra and extra not in (final_text or ""):
                final_text = f"{final_text}\n\n{extra}".strip()

        if self.tools.ready_for_review and "review" not in (final_text or ""):
            if current_stage != "review":
                current_stage = "review"
                yield _sse("status", {"stage": "review", "text": _STAGE_TEXT["review"]})
            if "缺点" not in (final_text or "") and self._last_review_reply:
                final_text = self._last_review_reply
            elif should_skip_llm_http() and "缺点" not in final_text:
                final_text = await self._mock_review(self.tools, final_text)

        if self.tools.last_exam_points_text and self.tools.last_exam_points_text not in final_text:
            if self.tools.last_fetch_failed:
                pass
            elif not final_text or final_text.startswith("我在"):
                final_text = self.tools.last_exam_points_text
            elif "对照" not in final_text and "考查" not in final_text:
                final_text = f"{self.tools.last_exam_points_text}\n\n{final_text}".strip()

        if self.tools.last_kb_notice and self.tools.last_kb_notice not in final_text:
            if self.tools.last_exam_points_text or "对照" in final_text:
                final_text = f"{final_text}\n\n{self.tools.last_kb_notice}".strip()
            elif self._attachment_body:
                final_text = self.tools.last_kb_notice

        message_type = self._infer_message_type(self._used_tools, self.tools, llm_failed)
        if extra_review or self.tools.ready_for_review:
            message_type = "review"
        if llm_failed:
            notice = "小凹这会儿连不上，请稍后再发这句"
            await self._add_message(conversation.id, "assistant", "error_notice", notice)
            await self.db.commit()
            yield _sse("error", {"error": notice, "error_code": "LLM_UNAVAILABLE"})
            return

        if self.tools.last_fetch_failed and not self.tools.recorded_application_id:
            if not final_text:
                final_text = (
                    "这段岗位链接读不到。请换一个链接，或直接把 JD 正文发给我，我才能总结考查点。"
                )
            await self._add_message(
                conversation.id, "assistant", "error_notice", final_text
            )
            await self.db.commit()
            yield _sse("error", {"error": final_text, "error_code": "JD_FETCH_FAILED"})
            return

        if _should_clip_user_reply(final_text, message_type):
            final_text = _clip_user_reply(final_text, self.settings.llm_reply_max_chars)
        if extra_apply and STATUS_PROGRESS_ASK in extra_apply and STATUS_PROGRESS_ASK not in (final_text or ""):
            final_text = f"{final_text}\n\n{STATUS_PROGRESS_ASK}".strip()
        if live_streamed and not (final_text or "").startswith(live_streamed):
            live_streamed = final_text

        if self.tools.ready_for_review:
            await self.tools.save_review_text(final_text)

        if current_stage != "reply":
            current_stage = "reply"

        stored = await self._add_message(conversation.id, "assistant", message_type, final_text)
        if (
            self.tools.last_kb_notice
            and message_type == "jd_summary"
            and self.tools.last_search_failed
        ):
            await self._add_message(
                conversation.id, "assistant", "kb_notice", self.tools.last_kb_notice
            )
        await self.db.commit()
        snapshot = await self.tools.current_snapshot()
        async for chunk in self._yield_text_deltas(final_text, already=live_streamed):
            yield chunk
        yield _sse(
            "done",
            {
                "message": MessagePublic.model_validate(stored).model_dump(mode="json"),
                "snapshot": snapshot.model_dump(mode="json"),
            },
        )

    async def _yield_text_deltas(self, text: str, already: str = "") -> AsyncIterator[str]:
        rest = text or ""
        if already:
            if rest.startswith(already):
                rest = rest[len(already) :]
            elif already == rest:
                return
        if not rest:
            return
        pace = 0.0 if should_skip_llm_http() else _DELTA_PACE_SECONDS
        for piece in _chunk_text(rest):
            if not piece:
                continue
            yield _sse("delta", {"text": piece})
            if pace:
                await asyncio.sleep(pace)

    async def _stream_orchestrator_turn(
        self,
        llm_messages: list[dict[str, Any]],
        *,
        live: bool,
    ) -> AsyncIterator[tuple[str, Any]]:
        if should_skip_llm_http():
            response = await self._complete(
                llm_messages,
                role="orchestrator",
                model=self.settings.llm_orchestrator_model,
                temperature=self.settings.llm_orchestrator_temperature,
                tools=ORCHESTRATOR_DEFINITIONS,
                purpose="plan",
            )
            yield ("response", response)
            return
        await self._log_llm_call(
            "orchestrator", self.settings.llm_orchestrator_model, "plan"
        )
        saw_tools = False
        complete: dict[str, Any] | None = None
        async for event in self.llm.iter_chat_completions(
            api_key=self._api_key,
            candidate_id=self._candidate.id if self._candidate is not None else None,
            messages=llm_messages,
            model=self.settings.llm_orchestrator_model,
            tools=ORCHESTRATOR_DEFINITIONS,
            temperature=self.settings.llm_orchestrator_temperature,
            max_tokens=self.settings.llm_reply_max_tokens,
            enable_thinking=self.settings.llm_enable_thinking,
        ):
            if event.kind == "tool_delta":
                saw_tools = True
            elif event.kind == "content" and live and not saw_tools and event.text:
                yield ("delta", event.text)
            elif event.kind == "complete":
                complete = event.response
        if complete is None:
            raise LlmUnavailableError()
        yield ("response", complete)

    def _orchestrator_prompt(self, orchestrator_text: str, snapshot: dict[str, Any]) -> str:
        return "\n\n".join(
            [
                orchestrator_text,
                "## 当前快照\n" + json.dumps(_slim_snapshot(snapshot), ensure_ascii=False, default=str),
            ]
        )

    def _stage_for_dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        task = str(arguments.get("task") or "")
        if name == "call_interview_agent" and task == "exam_points":
            return "fetch_jd"
        if name == "call_interview_agent" and task == "review":
            return "review"
        if name == "call_knowledge_agent" and task == "search":
            return "search_experiences"
        if name == "call_knowledge_agent" and task == "ingest":
            return "ingest_document"
        if name == "call_nudge_agent" and task == "record":
            return "fetch_jd"
        return STAGE_BY_TOOL.get(name, "reply")

    async def _emit_tool_status(self, name: str) -> None:
        stage = STAGE_BY_TOOL.get(name, "reply")
        self._status_events.append((stage, _visible_status_text(stage)))

    async def _run_interview(self, **arguments: Any) -> dict[str, Any]:
        assert self.tools is not None
        task = str(arguments.get("task") or "exam_points")
        await self._log_llm_call("interview", self.settings.llm_interview_model, task)
        if task == "review":
            self._review_dispatched = True
        if should_skip_llm_http():
            logger.warning("llm_allow_mock 跳过 HTTP，面试回复走 Mock")
            result = await self._mock_interview(task, arguments)
        else:
            result = await self._run_role_loop_safe(
                role="interview",
                model=self.settings.llm_interview_model,
                temperature=self.settings.llm_interview_temperature,
                prompt_name="exam_points.md" if task == "exam_points" else "review.md",
                tool_names=INTERVIEW_TOOL_NAMES,
                user_payload=arguments,
                purpose=task,
            )
        if task == "exam_points":
            await self._capture_exam_points(result)
        if task == "review":
            await self._persist_pending_answers(arguments)
            reply = str(result.get("reply") or "").strip()
            if reply:
                self._last_review_reply = reply
        return result

    async def _run_nudge(self, **arguments: Any) -> dict[str, Any]:
        assert self.tools is not None
        task = str(arguments.get("task") or "update")
        await self._log_llm_call("nudge", self.settings.llm_nudge_model, task)
        if task == "resume_after_counseling":
            self._resume_dispatched = True
        if should_skip_llm_http():
            logger.warning("llm_allow_mock 跳过 HTTP，投递更新走 Mock")
            result = await self._mock_nudge(task, arguments)
        else:
            result = await self._run_role_loop_safe(
                role="nudge",
                model=self.settings.llm_nudge_model,
                temperature=self.settings.llm_nudge_temperature,
                prompt_name="nudge.md",
                tool_names=NUDGE_TOOL_NAMES,
                user_payload=arguments,
                purpose=task,
            )
        if task == "record":
            incoming = str(arguments.get("exam_points") or "").strip()
            if incoming and not (self.tools.last_exam_points_text or "").strip():
                self.tools.last_exam_points_text = incoming
            await self._persist_pending_exam_points()
        return result

    async def _run_knowledge(self, **arguments: Any) -> dict[str, Any]:
        assert self.tools is not None
        task = str(arguments.get("task") or "search")
        await self._log_llm_call("knowledge", self.settings.llm_knowledge_model, task)
        if should_skip_llm_http():
            logger.warning("llm_allow_mock 跳过 HTTP，知识库回复走 Mock")
            result = await self._mock_knowledge(task, arguments)
        else:
            result = await self._run_role_loop_safe(
                role="knowledge",
                model=self.settings.llm_knowledge_model,
                temperature=self.settings.llm_knowledge_temperature,
                prompt_name={
                    "search": "knowledge_search.md",
                    "ingest": "knowledge_ingest.md",
                    "evaluate_rejected": "knowledge_evaluate.md",
                }.get(task, "knowledge_search.md"),
                tool_names=KNOWLEDGE_TOOL_NAMES,
                user_payload=arguments,
                purpose=task,
            )
        if task == "ingest":
            persisted = await self._persist_pending_ingest(arguments)
            if persisted is not None:
                return persisted
        return result

    async def _run_counseling(self, **arguments: Any) -> dict[str, Any]:
        assert self.tools is not None
        await self._log_llm_call("counseling", self.settings.llm_counseling_model, "counsel")
        if should_skip_llm_http():
            logger.warning("llm_allow_mock 跳过 HTTP，辅导回复走 Mock")
            return await self._mock_counseling(arguments)
        return await self._run_role_loop_safe(
            role="counseling",
            model=self.settings.llm_counseling_model,
            temperature=self.settings.llm_counseling_temperature,
            prompt_name="counseling.md",
            tool_names=COUNSELING_TOOL_NAMES,
            user_payload=arguments,
            purpose="counsel",
        )

    async def _run_role_loop_safe(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            return await self._run_role_loop(**kwargs)
        except (LlmKeyInvalidError, LlmQuotaExceededError, LlmKeyMissingError):
            raise
        except LlmClientError as exc:
            logger.info(
                "子模型暂时不可用",
                role=kwargs.get("role"),
                purpose=kwargs.get("purpose"),
                detail=type(exc).__name__,
            )
            return {
                "ok": False,
                "error": exc.user_message,
                "reply": "",
            }

    async def _run_role_loop(
        self,
        *,
        role: str,
        model: str,
        temperature: float,
        prompt_name: str,
        tool_names: tuple[str, ...],
        user_payload: dict[str, Any],
        purpose: str,
    ) -> dict[str, Any]:
        assert self.tools is not None
        assert self._candidate is not None
        snapshot = await self.tools.get_snapshot({})
        shared = await build_shared_prompt_context(self.db, self._candidate)
        messages = [
            {
                "role": "system",
                "content": fill_shared_variables(load_prompt(prompt_name), shared)
                + "\n\n## 当前快照\n"
                + json.dumps(snapshot, ensure_ascii=False, default=str),
            },
            *self._chat_history,
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, default=str)},
        ]
        last_observation: dict[str, Any] = {}
        for _ in range(self.settings.agent_max_steps):
            response = await self._complete(
                messages,
                role=role,
                model=model,
                temperature=temperature,
                tools=definitions_for(tool_names),
                purpose=purpose,
                log_call=False,
            )
            choice = (response.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []
            content = (message.get("content") or "").strip()
            if not tool_calls:
                last_observation["reply"] = content
                return last_observation
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            }
            messages.append(assistant_msg)
            for call in tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                raw_args = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else {}
                    if not isinstance(arguments, dict):
                        arguments = {}
                except json.JSONDecodeError:
                    arguments = {}
                await self._emit_tool_status(name)
                registry = _ROLE_REGISTRIES[role](self.tools)
                plugin_result = await registry.execute(name, **arguments)
                if not plugin_result:
                    observation = json.dumps(
                        {"ok": False, "error": plugin_result.error},
                        ensure_ascii=False,
                    )
                elif isinstance(plugin_result.data, str):
                    observation = plugin_result.data
                else:
                    observation = json.dumps(
                        plugin_result.data, ensure_ascii=False, default=str
                    )
                self._used_tools.append(name)
                try:
                    loaded = json.loads(observation)
                    if isinstance(loaded, dict):
                        last_observation = loaded
                    else:
                        last_observation = {"observation": loaded}
                except json.JSONDecodeError:
                    last_observation = {"observation": observation}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or name),
                        "content": observation,
                    }
                )
        return last_observation or {"ok": False, "error": "步骤用尽"}

    async def _capture_exam_points(self, observation: dict[str, Any]) -> None:
        assert self.tools is not None
        if self.tools.last_fetch_failed or observation.get("ok") is False:
            return
        explicit = str(observation.get("exam_points") or "").strip()
        already = (self.tools.last_exam_points_text or "").strip()
        reply = str(observation.get("reply") or "").strip()
        jd_text = str(observation.get("text") or "").strip()
        exam = explicit or already or reply
        if not exam or exam == jd_text or exam.startswith("我在"):
            return
        self.tools.last_exam_points_text = exam
        observation["exam_points"] = exam
        await self._persist_pending_exam_points()

    async def _persist_pending_exam_points(self) -> None:
        assert self.tools is not None
        if self.tools.last_fetch_failed:
            return
        exam = (self.tools.last_exam_points_text or "").strip()
        app_id = self.tools.recorded_application_id
        if not exam or not app_id:
            return
        saved = await self.tools.save_exam_points(
            {"application_id": app_id, "exam_points": exam}
        )
        if saved.get("ok") and "save_exam_points" not in self._used_tools:
            self._used_tools.append("save_exam_points")

    async def _persist_pending_ingest(
        self, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        assert self.tools is not None
        payload = arguments or {}
        body = str(payload.get("body") or self._attachment_body or "").strip()
        if not body:
            return None
        item_id = self.tools.last_ingest_item_id
        if not item_id:
            existing = await self.knowledge_store.find_item_by_body(
                self.tools.candidate, body
            )
            if existing is not None:
                item_id = existing.id
                self.tools.last_ingest_item_id = item_id
        if item_id:
            if not self.tools.last_kb_notice:
                self.tools.last_kb_notice = USER_INGEST_NOTICE
            return {
                "ok": True,
                "knowledge_item_id": item_id,
                "notice": self.tools.last_kb_notice or USER_INGEST_NOTICE,
            }
        source_type = str(
            payload.get("source_type")
            or ("upload" if self._attachment_name else "paste")
        )
        await self._emit_tool_status("ingest_user_document")
        result = await self.tools.ingest_user_document(
            {
                "body": body,
                "title": payload.get("title") or self._attachment_name or "",
                "source_type": source_type,
                "application_id": payload.get("application_id") or "",
            }
        )
        if result.get("ok") and "ingest_user_document" not in self._used_tools:
            self._used_tools.append("ingest_user_document")
        return result

    async def _persist_pending_answers(self, arguments: dict[str, Any]) -> None:
        assert self.tools is not None
        user_text = str(arguments.get("user_text") or "")
        if not looks_like_question_answers(user_text):
            return
        saved = await self.tools.save_answers_from_user_text(user_text)
        if saved.get("ok") and "save_answers" not in self._used_tools:
            self._used_tools.append("save_answers")

    def _history_job_url(self) -> str:
        for item in reversed(self._chat_history):
            if str(item.get("role") or "") != "user":
                continue
            found = extract_urls(str(item.get("content") or ""))
            if found:
                return found[0]
        return ""

    def _url_from_turn(self, user_text: str) -> str:
        urls = extract_urls(user_text)
        if urls:
            return urls[0]
        named = parse_application_fields(user_text)["company_name"]
        if named and not looks_like_jd_body(user_text):
            return ""
        if (
            looks_like_missing_row_complaint(user_text)
            or looks_like_apply_intent(user_text)
            or looks_like_jd_body(user_text)
        ):
            return self._history_job_url()
        return ""

    def _url_for_record(self, user_text: str) -> str:
        return self._url_from_turn(user_text)

    def _should_record_before_llm(self, user_text: str) -> bool:
        if "面经" in (user_text or "") and not looks_like_apply_intent(user_text):
            return False
        urls = extract_urls(user_text)
        if urls and company_hint_from_url(urls[0]):
            return True
        if looks_like_apply_intent(user_text) or looks_like_missing_row_complaint(user_text):
            return True
        return looks_like_jd_body(user_text)

    def _jd_text_from_turn(self, user_text: str) -> str:
        if looks_like_jd_body(user_text):
            return user_text.strip()
        for item in reversed(self._chat_history):
            if str(item.get("role") or "") != "user":
                continue
            content = str(item.get("content") or "").strip()
            if looks_like_jd_body(content):
                return content
        return ""

    async def _record_application_from_turn(
        self,
        user_text: str,
        *,
        url: str = "",
        exam_points: str = "",
        jd_text: str = "",
        company_name: str = "",
        role_title: str = "",
    ) -> str:
        assert self.tools is not None
        current_has_url = bool(extract_urls(user_text))
        use_history = (not current_has_url) or looks_like_missing_row_complaint(user_text)
        fields = parse_application_fields(user_text)
        if use_history:
            for item in reversed(self._chat_history):
                if str(item.get("role") or "") != "user":
                    continue
                extra = parse_application_fields(str(item.get("content") or ""))
                fields["company_name"] = _prefer_name(fields["company_name"], extra["company_name"])
                fields["role_title"] = _prefer_name(fields["role_title"], extra["role_title"])
                if fields["company_name"] and fields["role_title"]:
                    break
        jd_body = user_text.strip() if looks_like_jd_body(user_text) else ""
        if not jd_body and use_history:
            jd_body = self._jd_text_from_turn(user_text)
        hint = company_hint_from_url(url or "")
        if hint and not fields["company_name"]:
            fields["company_name"] = hint
        payload = {
            "jd_url": url or None,
            "company_name": company_name or fields["company_name"],
            "role_title": role_title or fields["role_title"],
            "jd_text": jd_text or jd_body,
            "exam_points": exam_points,
        }
        if not any(
            [
                payload["jd_url"] and (payload["company_name"] or payload["role_title"] or payload["jd_text"]),
                payload["company_name"],
                payload["role_title"],
                payload["jd_text"],
            ]
        ):
            return ""
        recorded = await self.tools.record_application(payload)
        if recorded.get("ok") and "record_application" not in self._used_tools:
            self._used_tools.append("record_application")
        return str(self.tools.recorded_application_id or "")

    async def _ensure_apply_and_status(self, user_text: str) -> str:
        assert self.tools is not None
        url = self._url_from_turn(user_text)
        wants_row = (
            looks_like_apply_intent(user_text)
            or bool(extract_urls(user_text))
            or (
                looks_like_missing_row_complaint(user_text)
                and (bool(url) or bool(self._jd_text_from_turn(user_text)))
            )
        )
        recorded_id = self.tools.recorded_application_id
        pieces: list[str] = []
        if not recorded_id and wants_row:
            fetch_already_failed = self.tools.last_fetch_failed
            if url and not fetch_already_failed:
                try:
                    result = await self._run_interview(
                        task="exam_points", url=url, user_text=user_text
                    )
                except LlmClientError as exc:
                    logger.info(
                        "考查点失败，改为只写投递表",
                        detail=type(exc).__name__,
                    )
                    result = {"ok": False, "error": exc.user_message}
                if not self.tools.last_fetch_failed and result.get("ok") is not False:
                    reply = str(result.get("reply") or result.get("exam_points") or "").strip()
                    if reply:
                        pieces.append(reply)
                    try:
                        await self._run_nudge(
                            task="record",
                            user_text=user_text,
                            jd_url=url,
                            company_name=str(result.get("company_name") or ""),
                            role_title=str(result.get("role_title") or ""),
                            jd_text=str(result.get("jd_text") or ""),
                            exam_points=str(result.get("exam_points") or ""),
                        )
                    except LlmClientError as exc:
                        logger.info(
                            "补写投递失败，改为直接落库",
                            detail=type(exc).__name__,
                        )
                    recorded_id = self.tools.recorded_application_id
            if not recorded_id:
                recorded_id = await self._record_application_from_turn(user_text, url=url)
                if recorded_id and self.tools.last_fetch_failed:
                    pieces.append(
                        "这段岗位链接暂时读不到考查点。已经按你发的信息写入「我的投递」，之后把 JD 正文发我就行。"
                    )
                elif not recorded_id and not url:
                    try:
                        await self._run_nudge(task="record", user_text=user_text)
                    except LlmClientError as exc:
                        logger.info(
                            "无链接补写失败，改为直接落库",
                            detail=type(exc).__name__,
                        )
                    recorded_id = self.tools.recorded_application_id
                    if not recorded_id:
                        recorded_id = await self._record_application_from_turn(
                            user_text, url=url
                        )
        if not recorded_id:
            return "\n\n".join(pieces)
        status = canonical_status_from_text(user_text)
        if status:
            await self.tools.update_application(
                {"application_id": recorded_id, "status_text": status}
            )
            if "update_application" not in self._used_tools:
                self._used_tools.append("update_application")
            return "\n\n".join(pieces)
        snapshot = await self.tools.get_snapshot({})
        apps = snapshot.get("applications") or []
        row = next((item for item in apps if item.get("id") == recorded_id), None)
        if row is None and apps:
            row = apps[-1]
        if row and str(row.get("status_text") or "").strip():
            return "\n\n".join(pieces)
        pieces.append(STATUS_PROGRESS_ASK)
        return "\n\n".join(piece for piece in pieces if piece)

    async def _replay_today_questions(self, user_text: str) -> str:
        if not looks_like_five_question_ask(user_text):
            return ""
        if looks_like_question_answers(user_text) or looks_like_apply_intent(user_text):
            return ""
        assert self.tools is not None
        question_set = await self.tools.current_question_set()
        items = [
            item
            for item in (question_set.questions or [])
            if str(getattr(item, "prompt", "") or "").strip()
        ]
        if len(items) < 5:
            return ""
        return format_daily_questions_text(items)

    async def _ensure_mood_better_dispatch(self, user_text: str) -> str:
        assert self.tools is not None
        if not any(marker in user_text for marker in _BETTER_MARKERS):
            return ""
        if any(marker in user_text for marker in _ANXIETY_MARKERS):
            return ""
        if self._resume_dispatched:
            return ""
        if not await self._has_pending_resume_todos():
            return ""
        if self.tools.candidate.counseling_active:
            await self.tools.set_counseling_state({"active": False})
            if "set_counseling_state" not in self._used_tools:
                self._used_tools.append("set_counseling_state")
        result = await self._run_nudge(
            task="resume_after_counseling",
            user_text=user_text,
        )
        if "call_nudge_agent" not in self._used_tools:
            self._used_tools.append("call_nudge_agent")
        reply = str(result.get("reply") or "").strip()
        extra = await self._followup_after_counseling(self.tools)
        if extra and extra not in reply:
            reply = f"{reply}\n\n{extra}".strip() if reply else extra
        return reply

    async def _ensure_answers_and_review(self, user_text: str) -> str:
        assert self.tools is not None
        if not looks_like_question_answers(user_text):
            return ""
        if any(marker in user_text for marker in _ANXIETY_MARKERS):
            return ""
        if not self.tools.ready_for_review:
            saved = await self.tools.save_answers_from_user_text(user_text)
            if saved.get("ok") and "save_answers" not in self._used_tools:
                self._used_tools.append("save_answers")
        if not self.tools.ready_for_review:
            return ""
        if self._review_dispatched:
            return ""
        self._status_events.append(("review", _STAGE_TEXT["review"]))
        result = await self._run_interview(task="review", user_text=user_text)
        if "call_interview_agent" not in self._used_tools:
            self._used_tools.append("call_interview_agent")
        return str(result.get("reply") or self._last_review_reply or "").strip()

    async def _has_pending_resume_todos(self) -> bool:
        assert self.tools is not None
        question_set = await self.tools.current_question_set()
        unanswered = [item for item in question_set.questions if not item.answer]
        if unanswered and question_set.status not in {
            "completed",
            "voided",
            "rest_day",
            "none",
        }:
            return True
        from src.services.scheduler import now_beijing

        hour = now_beijing().hour
        apps = await self.tools.candidates.list_applications(self.tools.candidate)
        waiting = [item for item in apps if item.normalized_status == "waiting_interview"]
        if waiting and 10 <= hour <= 23:
            return True
        return bool(unanswered)

    async def _mock_interview(self, task: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self.tools is not None
        if task == "exam_points":
            url = str(arguments.get("url") or "")
            if not url and self._conversation_id:
                url = (extract_urls(str(arguments.get("user_text") or "")) or [""])[0]
            await self._emit_tool_status("fetch_jd")
            fetched = await self.tools.fetch_jd_tool({"url": url})
            self._used_tools.append("fetch_jd")
            if not fetched.get("ok"):
                return fetched
            snapshot = await self.tools.get_snapshot({})
            resume = str(snapshot.get("resume_excerpt") or "简历摘要")[:200]
            exam_points = (
                f"对照你的简历（{resume}），这个岗位主要考查项目落地和后端基础，"
                "而不是只复述 JD。"
            )
            return {
                "ok": True,
                "jd_url": url,
                "jd_text": fetched.get("text") or "",
                "company_name": "示例公司",
                "role_title": "后端开发",
                "exam_points": exam_points,
            }
        if task == "review":
            snapshot = await self.tools.get_snapshot({})
            question_set = snapshot.get("question_set") or {}
            questions = question_set.get("questions") or []
            user_text = str(arguments.get("user_text") or "")
            answers = split_answer_texts(user_text, len(questions) or 5)
            items = []
            for question, answer in zip(questions, answers, strict=False):
                items.append({"question_id": question.get("id"), "answer": answer})
            if items:
                saved = await self.tools.save_answers({"items": items})
                self._used_tools.append("save_answers")
                if saved.get("ready_for_review"):
                    return {
                        "ok": True,
                        "ready_for_review": True,
                        "reply": (
                            "五题都齐了。主要缺点是回答偏结果、少过程；"
                            "建议下次用「做了什么-难点-你的决策」讲。"
                            "这五题里简历深挖对准你的项目经历，业务题对准等待面试岗位的考查点，"
                            "优先练更近的面试。"
                        ),
                    }
                return {"ok": True, "reply": "这几题我记下了。还没答完的继续发就行。"}
            return {"ok": False, "error": "我还没对上今日题目，请按第一题、第二题来说。"}
        return {"ok": True, "reply": "我们按考查点和已收录材料继续准备。"}

    async def _mock_nudge(self, task: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self.tools is not None
        if task == "record":
            recorded = await self.tools.record_application(arguments)
            self._used_tools.append("record_application")
            application_id = str(recorded.get("application_id") or "")
            exam_points = str(arguments.get("exam_points") or "").strip()
            if recorded.get("ok") and application_id and exam_points:
                await self.tools.save_exam_points(
                    {"application_id": application_id, "exam_points": exam_points}
                )
                self._used_tools.append("save_exam_points")
            status_text = str(arguments.get("status_text") or "").strip() or canonical_status_from_text(
                str(arguments.get("user_text") or "")
            )
            if recorded.get("ok") and application_id and status_text:
                await self.tools.update_application(
                    {"application_id": application_id, "status_text": status_text}
                )
                self._used_tools.append("update_application")
            return recorded
        if task == "update":
            snapshot = await self.tools.get_snapshot({})
            apps = snapshot.get("applications") or []
            if not apps:
                return {"ok": False, "error": "还没有岗位记录。请先发岗位链接或公司名。"}
            user_text = str(arguments.get("user_text") or "")
            payload: dict[str, Any] = {
                "application_id": arguments.get("application_id") or apps[0].get("id")
            }
            status_text = arguments.get("status_text") or _status_from_user(user_text)
            if status_text:
                payload["status_text"] = status_text
            if arguments.get("progress_text") or "进度" in user_text or "约" in user_text:
                payload["progress_text"] = arguments.get("progress_text") or user_text
            deadline = arguments.get("deadline") or _deadline_from_user(user_text)
            if deadline:
                payload["deadline"] = deadline
            updated = await self.tools.update_application(payload)
            self._used_tools.append("update_application")
            return updated
        if task == "resume_after_counseling":
            extra = await self._followup_after_counseling(self.tools)
            return {"ok": True, "reply": extra}
        return {"ok": True}

    async def _mock_knowledge(self, task: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self.tools is not None
        if task == "search":
            application_id = str(
                arguments.get("application_id") or self.tools.recorded_application_id or ""
            )
            await self._emit_tool_status("search_public_experiences")
            result = await self.tools.search_public_experiences({"application_id": application_id})
            self._used_tools.append("search_public_experiences")
            return result
        if task == "ingest":
            body = str(arguments.get("body") or self._attachment_body or "").strip()
            source_type = str(
                arguments.get("source_type")
                or ("upload" if self._attachment_name else "paste")
            )
            await self._emit_tool_status("ingest_user_document")
            result = await self.tools.ingest_user_document(
                {
                    "body": body,
                    "title": arguments.get("title") or self._attachment_name or "",
                    "source_type": source_type,
                    "application_id": arguments.get("application_id") or "",
                }
            )
            self._used_tools.append("ingest_user_document")
            return result
        if task == "evaluate_rejected":
            result = await self.tools.evaluate_items_for_rejected_role(arguments)
            self._used_tools.append("evaluate_items_for_rejected_role")
            return result
        if task == "apply_user_decision":
            result = await self.tools.apply_deletion_decision(arguments)
            self._used_tools.append("apply_deletion_decision")
            return result
        return {"ok": False, "error": "未知知识库任务"}

    async def _mock_counseling(self, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self.tools is not None
        user_text = str(arguments.get("user_text") or "")
        if "active" in arguments:
            active = bool(arguments.get("active"))
        else:
            active = not any(marker in user_text for marker in _BETTER_MARKERS)
        result = await self.tools.set_counseling_state({"active": active})
        self._used_tools.append("set_counseling_state")
        if active:
            return {
                "ok": True,
                **result,
                "reply": "我先陪你缓一缓。现在最压着你的是什么感觉？",
            }
        return {"ok": True, **result, "reply": "好，那我们把辅导先放下，接着准备面试。"}

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        *,
        role: str,
        model: str,
        temperature: float,
        tools: list[dict[str, Any]] | None,
        purpose: str,
        log_call: bool = True,
    ) -> dict[str, Any]:
        if log_call:
            await self._log_llm_call(role, model, purpose)
        if should_skip_llm_http():
            logger.warning("llm_allow_mock 跳过 HTTP，走 Mock 回复")
            return self._mock_complete(messages, role=role)
        return await self.llm.chat_completions(
            api_key=self._api_key,
            candidate_id=self._candidate.id if self._candidate is not None else None,
            messages=messages,
            model=model,
            tools=tools,
            temperature=temperature,
            max_tokens=self.settings.llm_reply_max_tokens if role == "orchestrator" else None,
            enable_thinking=self.settings.llm_enable_thinking,
        )

    def _mock_complete(self, messages: list[dict[str, Any]], *, role: str) -> dict[str, Any]:
        last = messages[-1]
        if role != "orchestrator":
            return _assistant_text("我在。")
        if last.get("role") == "user":
            return self._mock_from_user(_last_user_text(messages))
        if last.get("role") == "tool":
            return self._mock_from_tool(messages)
        return _assistant_text("我在。")

    def _mock_from_user(self, text: str) -> dict[str, Any]:
        if self._attachment_body:
            return _tool_call(
                "call_knowledge_agent",
                {
                    "task": "ingest",
                    "body": self._attachment_body,
                    "title": self._attachment_name or "",
                    "source_type": "upload" if self._attachment_name else "paste",
                },
            )
        urls = extract_urls(text)
        if urls:
            return _tool_call(
                "call_interview_agent",
                {"task": "exam_points", "url": urls[0], "user_text": text},
            )
        if looks_like_apply_intent(text):
            return _tool_call(
                "call_nudge_agent",
                {"task": "record", "user_text": text},
            )
        if any(marker in text for marker in _BETTER_MARKERS):
            return _tool_call(
                "call_counseling_agent", {"user_text": text, "active": False}
            )
        if any(marker in text for marker in _ANXIETY_MARKERS):
            return _tool_call(
                "call_counseling_agent", {"user_text": text, "active": True}
            )
        if any(marker in text for marker in _PROGRESS_MARKERS):
            return _tool_call("call_nudge_agent", {"task": "update", "user_text": text})
        if any(marker in text for marker in _ANSWER_MARKERS):
            return _tool_call("call_interview_agent", {"task": "review", "user_text": text})
        return _assistant_text("我在。你可以发岗位链接、更新进度，或者告诉我现在的心情。")

    def _mock_from_tool(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        name, observation = _last_tool(messages)
        user_text = _last_user_text(messages)
        if name == "call_interview_agent":
            if not observation.get("ok"):
                error = observation.get("error") or "这段岗位链接读不到。"
                return _assistant_text(
                    f"{error}请换一个链接，或直接把 JD 正文发给我，我才能总结考查点。"
                )
            if observation.get("ready_for_review"):
                return _assistant_text(str(observation.get("reply") or ""))
            if observation.get("exam_points"):
                return _tool_call(
                    "call_nudge_agent",
                    {
                        "task": "record",
                        "jd_url": observation.get("jd_url") or "",
                        "company_name": observation.get("company_name") or "示例公司",
                        "role_title": observation.get("role_title") or "后端开发",
                        "jd_text": observation.get("jd_text") or "",
                        "exam_points": observation.get("exam_points"),
                    },
                )
            return _assistant_text(str(observation.get("reply") or "我看到了。"))
        if name == "call_nudge_agent":
            application_id = str(observation.get("application_id") or "")
            exam = ""
            if self.tools is not None:
                exam = self.tools.last_exam_points_text
            status_text = observation.get("status_text") or ""
            normalized = observation.get("normalized_status") or ""
            if application_id and exam:
                return _tool_call(
                    "call_knowledge_agent",
                    {"task": "search", "application_id": application_id},
                )
            if status_text or normalized:
                return _assistant_text(f"已记下：{status_text or '进度已更新'}（{normalized}）。")
            if observation.get("reply"):
                return _assistant_text(str(observation["reply"]))
            return _assistant_text("已记下当前投递。")
        if name == "call_knowledge_agent":
            if observation.get("notice") and observation.get("ok"):
                exam = self.tools.last_exam_points_text if self.tools is not None else ""
                if exam:
                    return _assistant_text(f"{exam}\n\n{observation['notice']}")
                return _assistant_text(str(observation["notice"]))
            if observation.get("error") or observation.get("ok") is False:
                reason = str(
                    observation.get("error")
                    or "这次没找到可核对的面经，你可以在对话里发文件或粘贴。"
                )
                exam = self.tools.last_exam_points_text if self.tools is not None else ""
                if exam:
                    return _assistant_text(f"{exam}\n\n{reason}")
                return _assistant_text(reason)
            return _assistant_text("我记下了。")
        if name == "call_counseling_agent":
            active = bool(observation.get("counseling_active"))
            reply = str(observation.get("reply") or "")
            if active:
                return _assistant_text(reply or "我先陪你缓一缓。现在最压着你的是什么感觉？")
            return _assistant_text(reply or "好，那我们把辅导先放下，接着准备面试。")
        if name == "get_snapshot":
            return _assistant_text("我看到了当前投递和当日安排。")
        return _assistant_text(user_text and "我在。" or "我在。")

    async def _followup_after_counseling(self, tools: ToolExecutor) -> str:
        question_set = await tools.current_question_set()
        unanswered = [item for item in question_set.questions if not item.answer]
        if unanswered and question_set.status not in {"completed", "voided", "rest_day", "none"}:
            lines = ["今日还没答完的题目："]
            for item in unanswered:
                lines.append(f"- {item.prompt}")
            return "\n".join(lines)
        tz = ZoneInfo(self.settings.timezone)
        hour = utc_now().astimezone(tz).hour
        if 10 <= hour <= 23:
            level = tone_level_for_hour(hour)
            return f"心情先放到一边。今天的待办还在，语气档 {level}：先把进度或未完成的题推进一格。"
        return "心情先放下。有未完成的进度或题目随时接着说。"

    async def _mock_review(self, tools: ToolExecutor, current: str) -> str:
        extra = (
            "五题都齐了。主要缺点是回答偏结果、少过程；"
            "建议下次用「做了什么-难点-你的决策」讲。"
        )
        return f"{current}\n\n{extra}".strip()

    def _infer_message_type(
        self,
        used_tools: list[str],
        tools: ToolExecutor,
        llm_failed: bool,
    ) -> MessageType:
        if llm_failed or tools.last_fetch_failed:
            return "error_notice"
        if tools.ready_for_review:
            return "review"
        if "save_exam_points" in used_tools or tools.last_exam_points_text:
            return "jd_summary"
        if "ingest_user_document" in used_tools:
            return "kb_notice"
        if "set_counseling_state" in used_tools and not tools.counseling_turned_off:
            return "counseling"
        if tools.counseling_turned_off:
            return "questions"
        return "chat"

    async def _log_llm_call(self, role: str, model: str, purpose: str) -> None:
        if self._candidate is None:
            return
        self.db.add(
            LlmCallLog(
                id=new_id("l"),
                candidate_id=self._candidate.id,
                conversation_id=self._conversation_id,
                role=role,
                model=model,
                purpose=purpose,
            )
        )
        await self.db.flush()
        logger.info("已记录模型调用", role=role, model=model, purpose=purpose)

    async def _add_message(
        self,
        conversation_id: str,
        role: Literal["user", "assistant"],
        message_type: MessageType,
        content: str,
    ) -> Message:
        row = Message(
            id=new_id("m"),
            conversation_id=conversation_id,
            role=role,
            message_type=message_type,
            content=content,
            created_at=utc_now(),
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def _load_visible_history(self, conversation_id: str) -> list[dict[str, str]]:
        limit = max(0, self.settings.llm_history_max_messages)
        rows = await self.messages.list_recent_for_conversation(
            conversation_id, limit=limit + 1
        )
        return _visible_chat_history(
            rows,
            exclude_id=self._current_user_message_id,
            max_messages=limit,
            max_chars=self.settings.llm_history_max_chars,
        )


def _visible_chat_history(
    rows: Sequence[Any],
    *,
    exclude_id: str | None,
    max_messages: int,
    max_chars: int,
) -> list[dict[str, str]]:
    visible: list[dict[str, str]] = []
    for row in rows:
        if exclude_id and getattr(row, "id", None) == exclude_id:
            continue
        role = str(getattr(row, "role", "") or "")
        if role not in {"user", "assistant"}:
            continue
        text = _truncate_history_text(str(getattr(row, "content", "") or ""), max_chars)
        if not text:
            continue
        visible.append({"role": role, "content": text})
    if max_messages >= 0:
        visible = visible[-max_messages:]
    return visible


def _truncate_history_text(text: str, limit: int) -> str:
    value = (text or "").strip()
    if limit <= 0 or len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def _assistant_text(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text, "tool_calls": []}}]}


def _tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"call_{name}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                    ],
                }
            }
        ]
    }


def _last_tool(messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    last = messages[-1]
    tool_id = str(last.get("tool_call_id") or "")
    name = tool_id.removeprefix("call_") if tool_id.startswith("call_") else ""
    for item in reversed(messages):
        if item.get("role") == "assistant":
            for call in item.get("tool_calls") or []:
                if str(call.get("id") or "") == tool_id:
                    name = str((call.get("function") or {}).get("name") or name)
                    break
            break
    raw = last.get("content")
    if isinstance(raw, dict):
        observation = raw
    else:
        try:
            loaded = json.loads(str(raw or "{}"))
            observation = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            observation = {}
    return name, observation


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for item in reversed(messages):
        if item.get("role") == "user":
            return str(item.get("content") or "")
    return ""


def _combined_user_text(messages: list[dict[str, Any]]) -> str:
    parts = [str(item.get("content") or "") for item in messages if item.get("role") == "user"]
    return "\n".join(part for part in parts if part)


def _status_from_user(text: str) -> str:
    canonical = canonical_status_from_text(text)
    if canonical:
        return canonical
    if "还在沟通" in (text or ""):
        return "还在沟通中"
    return ""


def _looks_like_idle_menu(text: str) -> bool:
    return any(marker in (text or "") for marker in _IDLE_MENU_MARKERS)


def _deadline_from_user(text: str) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else None
