import json
import re
from pathlib import Path
from typing import Any, cast

from pycore.core.logger import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.db.models import (
    Application,
    Candidate,
    Conversation,
    KnowledgeItem,
    LlmCallLog,
    Message,
)
from src.models.knowledge import (
    KNOWLEDGE_EXCERPT_CHARS,
    KnowledgeItemListPublic,
    KnowledgeItemPublic,
    KnowledgeSegmentPublic,
    KnowledgeSourceType,
)
from src.repositories.knowledge import KnowledgeRepository
from src.services.llm_client import (
    SCHEDULER_KEY_NOTICE,
    LlmClient,
    LlmKeyInvalidError,
    resolved_candidate_api_key,
    should_skip_llm_http,
)
from src.services.prompt_context import render_prompt
from src.services.resume import parse_resume
from src.utils.crypto import new_id, utc_now
from src.utils.errors import Forbidden, NotFound, ValidationFailed
from src.utils.paths import resolve_upload_dir

logger = get_logger()

_KNOWLEDGE_SUBDIR = "knowledge"
_EVAL_INCOMPLETE = (
    "还没判断完面经是否还留着。已挂状态已经记下，相关面经先留着，等判断完再问你删不删。"
)
_DEFAULT_EVALUATE_PROMPT = (
    "岗位已挂后，判断相关面经整篇或局部对其它准备是否仍有用。"
    "只标建议删除，不要真的删。只输出 JSON："
    '{"keep":[{"item_id":"k_01","segment_ids":[],"reason":"其它岗仍能用"}],'
    '"suggest_delete":[{"item_id":"k_02","segment_ids":["s_3"],"reason":"只针对已挂岗位"}]}'
)


def resolve_knowledge_upload_dir() -> Path:
    settings = get_settings()
    return resolve_upload_dir(str(Path(settings.upload_dir) / _KNOWLEDGE_SUBDIR))


class KnowledgeStore:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.items = KnowledgeRepository(db)
        resolve_knowledge_upload_dir()

    async def list_items(
        self,
        candidate: Candidate,
        application_id: str | None = None,
    ) -> list[KnowledgeItemListPublic]:
        rows = await self.items.list_by_candidate(candidate.id, application_id)
        applications = await self._applications_for(rows)
        logger.info("已列出面经", candidate_id=candidate.id, count=len(rows))
        return [self._to_list_public(row, applications) for row in rows]

    async def get_item(self, candidate: Candidate, item_id: str) -> KnowledgeItemPublic:
        row = await self._owned_item(candidate, item_id, action="查看")
        applications = await self._applications_for([row])
        segments = await self.items.list_segments(row.id)
        listed = self._to_list_public(row, applications)
        logger.info("已读取面经正文", knowledge_item_id=row.id)
        return KnowledgeItemPublic(
            **listed.model_dump(),
            body=row.body,
            segments=[KnowledgeSegmentPublic.model_validate(seg) for seg in segments],
        )

    async def delete_item(self, candidate: Candidate, item_id: str) -> str:
        row = await self._owned_item(candidate, item_id, action="删")
        deleted_id = row.id
        await self.items.delete_item(row)
        logger.info("已手删面经", knowledge_item_id=deleted_id)
        return deleted_id

    async def ingest_upload_file(
        self,
        candidate: Candidate,
        filename: str | None,
        content: bytes,
    ) -> KnowledgeItemPublic:
        safe_name = filename or "experience.txt"
        path = self.save_upload(candidate.id, safe_name, content)
        body = self.parse_upload(path)
        existing = await self.find_item_by_body(candidate, body)
        if existing is not None:
            logger.info("面经正文已在库中", knowledge_item_id=existing.id)
            return await self.get_item(candidate, existing.id)
        title = Path(safe_name).stem.strip() or body[:40]
        row = await self.ingest(
            candidate,
            title=title,
            body=body,
            source_type="upload",
        )
        return await self.get_item(candidate, row.id)

    async def find_item_by_body(
        self, candidate: Candidate, body: str
    ) -> KnowledgeItem | None:
        text = (body or "").strip()
        if not text:
            return None
        for row in await self.items.list_by_candidate(candidate.id):
            if (row.body or "").strip() == text:
                return row
        return None

    async def excerpts_for(
        self,
        candidate: Candidate,
        application_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = await self.items.list_by_candidate(candidate.id, application_id)
        return await self._excerpts_from_rows(rows)

    async def excerpts_for_waiting(
        self,
        candidate: Candidate,
        waiting: list[Application],
    ) -> list[dict[str, Any]]:
        waiting_ids = {item.id for item in waiting}
        rows = await self.items.list_by_candidate(candidate.id)
        selected = [
            row
            for row in rows
            if row.application_id is None or row.application_id in waiting_ids
        ]
        return await self._excerpts_from_rows(selected)

    async def _excerpts_from_rows(self, rows: list[KnowledgeItem]) -> list[dict[str, Any]]:
        excerpts: list[dict[str, Any]] = []
        for item in rows:
            segments = await self.items.list_segments(item.id)
            active = [seg.text for seg in segments if seg.status == "active"]
            if not active and item.body:
                active = [item.body[:500]]
            if not active:
                continue
            excerpts.append(
                {
                    "item_id": item.id,
                    "title": item.title,
                    "application_id": item.application_id,
                    "source_type": item.source_type,
                    "texts": active,
                }
            )
        return excerpts

    async def ingest(
        self,
        candidate: Candidate,
        *,
        title: str,
        body: str,
        source_type: KnowledgeSourceType,
        application_id: str | None = None,
        source_url: str | None = None,
    ) -> KnowledgeItem:
        text = (body or "").strip()
        if not text:
            raise ValidationFailed("没有可收录的面经正文。")
        heading = (title or "").strip() or text[:40]
        row = await self.items.create_item(
            candidate_id=candidate.id,
            title=heading,
            body=text,
            source_type=source_type,
            application_id=application_id,
            source_url=source_url,
        )
        for ordinal, piece in enumerate(_split_segments(text)):
            await self.items.create_segment(
                knowledge_item_id=row.id,
                ordinal=ordinal,
                text=piece,
            )
        logger.info(
            "已收录面经",
            knowledge_item_id=row.id,
            source_type=source_type,
        )
        return row

    def save_upload(self, candidate_id: str, filename: str, content: bytes) -> Path:
        settings = get_settings()
        if not content:
            raise ValidationFailed("请发送可用的面经文件。")
        if len(content) > settings.knowledge_max_bytes:
            raise ValidationFailed("面经文件过大，请换一份更小的文件。")
        suffix = Path(filename).suffix.lower()
        allowed = {
            item.lower() if item.startswith(".") else f".{item.lower()}"
            for item in settings.knowledge_allowed_extensions
        }
        if suffix not in allowed:
            raise ValidationFailed("面经格式不支持，请上传 pdf、docx 或 txt 文件。")
        safe_name = Path(filename).name.replace("/", "_").replace("\\", "_")
        dest = resolve_knowledge_upload_dir() / candidate_id / safe_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        logger.info("面经原件已保存", candidate_id=candidate_id)
        return dest

    def parse_upload(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".md":
            suffix = ".txt"
        text, ok = parse_resume(path, suffix)
        if not ok or not text.strip():
            raise ValidationFailed("没能读出这份面经的正文，请改发文本或换一份文件。")
        return text.strip()

    async def evaluate_for_rejected(
        self,
        candidate: Candidate,
        application_id: str,
    ) -> dict[str, Any]:
        rows = await self.items.list_by_candidate(candidate.id, application_id)
        if not rows:
            return {
                "ok": True,
                "asked": False,
                "evaluated": False,
                "pending": False,
                "content": "",
                "keep": [],
                "suggest_delete": [],
            }
        parsed = await self._call_evaluate_model(candidate, application_id, rows)
        if parsed is not None and parsed.get("_llm_key_invalid"):
            return {
                "ok": True,
                "asked": True,
                "evaluated": False,
                "pending": True,
                "content": SCHEDULER_KEY_NOTICE,
                "keep": [],
                "suggest_delete": [],
            }
        if parsed is None:
            logger.warning(
                "已挂面经评估未完成，状态已保存且不自动删",
                application_id=application_id,
            )
            await self.insert_kb_ask(candidate, _EVAL_INCOMPLETE)
            return {
                "ok": True,
                "asked": True,
                "evaluated": False,
                "pending": True,
                "content": _EVAL_INCOMPLETE,
                "keep": [],
                "suggest_delete": [],
            }
        pending_ids = await self._collect_pending_ids(parsed.get("suggest_delete") or [])
        if pending_ids:
            await self.items.mark_pending_delete(pending_ids)
        content = _format_ask_text(parsed)
        await self.insert_kb_ask(candidate, content)
        logger.info(
            "已写入面经去留询问",
            application_id=application_id,
            pending_segments=len(pending_ids),
        )
        return {
            "ok": True,
            "asked": True,
            "evaluated": True,
            "pending": True,
            "content": content,
            "keep": parsed.get("keep") or [],
            "suggest_delete": parsed.get("suggest_delete") or [],
        }

    async def insert_kb_ask(self, candidate: Candidate, content: str) -> None:
        result = await self.db.execute(
            select(Conversation).where(Conversation.candidate_id == candidate.id)
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            logger.error("已挂询问时找不到对话", candidate_id=candidate.id)
            return
        message = Message(
            id=new_id("m"),
            conversation_id=conversation.id,
            role="assistant",
            message_type="kb_ask",
            content=content,
            created_at=utc_now(),
        )
        self.db.add(message)
        await self.db.flush()

    async def insert_error_notice(self, candidate: Candidate, content: str) -> None:
        result = await self.db.execute(
            select(Conversation).where(Conversation.candidate_id == candidate.id)
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            logger.error("写入 Key 失效说明时找不到对话", candidate_id=candidate.id)
            return
        message = Message(
            id=new_id("m"),
            conversation_id=conversation.id,
            role="assistant",
            message_type="error_notice",
            content=content,
            created_at=utc_now(),
        )
        self.db.add(message)
        await self.db.flush()

    async def _call_evaluate_model(
        self,
        candidate: Candidate,
        application_id: str,
        rows: list[KnowledgeItem],
    ) -> dict[str, Any] | None:
        if not _llm_configured():
            logger.info("未走百炼 HTTP，已挂评估标明尚未判断完", application_id=application_id)
            return None
        settings = get_settings()
        api_key = resolved_candidate_api_key(candidate)
        items_payload: list[dict[str, Any]] = []
        for item in rows:
            segments = await self.items.list_segments(item.id)
            items_payload.append(
                {
                    "item_id": item.id,
                    "title": item.title,
                    "body": item.body,
                    "segments": [
                        {"segment_id": seg.id, "text": seg.text} for seg in segments
                    ],
                }
            )
        user = json.dumps(
            {"application_id": application_id, "items": items_payload},
            ensure_ascii=False,
        )
        prompt = await render_prompt(
            "knowledge_evaluate.md",
            self.db,
            candidate,
            fallback=_DEFAULT_EVALUATE_PROMPT,
        )
        try:
            await _log_knowledge_call(self.db, candidate.id, settings.llm_knowledge_model)
            data = await LlmClient().chat_completions(
                api_key=api_key,
                candidate_id=candidate.id,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user},
                ],
                model=settings.llm_knowledge_model,
                temperature=settings.llm_knowledge_temperature,
            )
        except LlmKeyInvalidError:
            logger.warning("已挂评估遇 Key 无效", candidate_id=candidate.id)
            candidate.llm_key_status = "invalid"
            await self.insert_error_notice(candidate, SCHEDULER_KEY_NOTICE)
            return {"_llm_key_invalid": True}
        except Exception as exc:
            logger.warning("已挂评估模型调用失败", detail=type(exc).__name__)
            return None
        content = _choice_text(data)
        return _parse_evaluate_payload(content)

    async def _collect_pending_ids(self, suggest_delete: list[Any]) -> list[str]:
        pending: list[str] = []
        for raw in suggest_delete:
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get("item_id") or "")
            seg_ids = raw.get("segment_ids") or []
            if isinstance(seg_ids, list) and seg_ids:
                pending.extend(str(item) for item in seg_ids if item)
                continue
            if not item_id:
                continue
            segments = await self.items.list_segments(item_id)
            pending.extend(seg.id for seg in segments)
        return pending

    async def _owned_item(
        self,
        candidate: Candidate,
        item_id: str,
        *,
        action: str,
    ) -> KnowledgeItem:
        row = await self.items.get_by_id(item_id)
        if row is None:
            raise NotFound("找不到这条面经。")
        if row.candidate_id != candidate.id:
            raise Forbidden(f"不能{action}别人的面经。")
        return row

    async def _applications_for(self, rows: list[KnowledgeItem]) -> dict[str, Application]:
        ids = [row.application_id for row in rows if row.application_id]
        return await self.items.applications_by_ids(ids)

    def _to_list_public(
        self,
        row: KnowledgeItem,
        applications: dict[str, Application],
    ) -> KnowledgeItemListPublic:
        related = applications.get(row.application_id or "")
        return KnowledgeItemListPublic(
            id=row.id,
            title=row.title,
            source_type=cast(KnowledgeSourceType, row.source_type),
            source_url=row.source_url,
            application_id=row.application_id,
            company_name=related.company_name if related is not None else "",
            role_title=related.role_title if related is not None else "",
            created_at=row.created_at,
            excerpt=row.body[:KNOWLEDGE_EXCERPT_CHARS],
        )


def _split_segments(body: str) -> list[str]:
    parts = [item.strip() for item in re.split(r"\n\s*\n", body) if item.strip()]
    return parts or [body.strip()]


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


def _parse_evaluate_payload(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_extract_json_text(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    keep = data.get("keep")
    suggest = data.get("suggest_delete")
    if not isinstance(keep, list) or not isinstance(suggest, list):
        return None
    return {"keep": keep, "suggest_delete": suggest}


def _format_ask_text(parsed: dict[str, Any]) -> str:
    lines = ["这份岗位已挂。我看了相关面经："]
    keep = parsed.get("keep") or []
    suggest = parsed.get("suggest_delete") or []
    if keep:
        lines.append("仍有用：")
        for item in keep:
            if not isinstance(item, dict):
                continue
            reason = str(item.get("reason") or "其它准备还能用")
            item_id = str(item.get("item_id") or "")
            lines.append(f"- {item_id}：{reason}")
    if suggest:
        lines.append("建议删除：")
        for item in suggest:
            if not isinstance(item, dict):
                continue
            reason = str(item.get("reason") or "只针对已挂岗位")
            item_id = str(item.get("item_id") or "")
            lines.append(f"- {item_id}：{reason}")
    lines.append("这些条目现在还在「我的面经」里。要按建议删，还是全部留下？")
    return "\n".join(lines)


async def _log_knowledge_call(db: AsyncSession, candidate_id: str, model: str) -> None:
    result = await db.execute(
        select(Conversation.id).where(Conversation.candidate_id == candidate_id)
    )
    conversation_id = result.scalar_one_or_none()
    db.add(
        LlmCallLog(
            id=new_id("l"),
            candidate_id=candidate_id,
            conversation_id=conversation_id,
            role="knowledge",
            model=model,
            purpose="evaluate_rejected",
        )
    )
    await db.flush()
    logger.info("已记录模型调用", role="knowledge", model=model, purpose="evaluate_rejected")
