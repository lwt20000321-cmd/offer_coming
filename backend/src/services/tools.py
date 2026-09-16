"""API-006 Agent 工具：真实写库，观察回传给模型。"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pycore.core.logger import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import AppSettings, get_settings
from src.db.models import Application, Candidate, Question, QuestionSet
from src.models.candidate import ApplicationPublic, CandidateCurrentPublic, QuestionSetPublic
from src.models.knowledge import KnowledgeSourceType
from src.repositories.knowledge import KnowledgeRepository
from src.services.candidate import CandidateService
from src.services.jd_fetch import fetch_jd
from src.services.knowledge_search import KnowledgeSearchService, host_is_blocked
from src.services.knowledge_store import KnowledgeStore
from src.services.prompt_context import render_prompt
from src.services.questions import parse_user_answers
from src.services.application_status import (
    NormalizedStatus,
    normalize_application_status,
)
from src.utils.crypto import beijing_today, new_id, utc_now

logger = get_logger()

USER_INGEST_NOTICE = "已收入你发来的面经，可在「我的面经」查看。"
_RESUME_EXCERPT_CHARS = 1500

STAGE_BY_TOOL = {
    "fetch_jd": "fetch_jd",
    "record_application": "fetch_jd",
    "save_exam_points": "fetch_jd",
    "update_application": "update_application",
    "set_counseling_state": "counseling",
    "save_answers": "questions",
    "get_snapshot": "reply",
    "get_knowledge_excerpts": "reply",
    "append_interview_summary": "review",
    "search_public_experiences": "search_experiences",
    "ingest_user_document": "ingest_document",
    "evaluate_items_for_rejected_role": "reply",
    "apply_deletion_decision": "reply",
    "call_interview_agent": "fetch_jd",
    "call_nudge_agent": "update_application",
    "call_knowledge_agent": "search_experiences",
    "call_counseling_agent": "counseling",
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "record_application",
            "description": "记录或复用一条已投岗位。",
            "parameters": {
                "type": "object",
                "properties": {
                    "jd_url": {"type": "string"},
                    "company_name": {"type": "string"},
                    "role_title": {"type": "string"},
                    "jd_text": {"type": "string"},
                    "exam_points": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_jd",
            "description": "对用户给出的 http(s) 岗位链接发出 GET，抽取可读文本。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_exam_points",
            "description": "把对照简历后的考查点写到岗位上。",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "exam_points": {"type": "string"},
                },
                "required": ["application_id", "exam_points"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_application",
            "description": "更新岗位进度、状态或 deadline，并做状态归一。",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "company_name": {"type": "string"},
                    "role_title": {"type": "string"},
                    "progress_text": {"type": "string"},
                    "status_text": {"type": "string"},
                    "deadline": {"type": "string"},
                    "interview_at": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_snapshot",
            "description": "读取简历摘要、投递列表、当日题集和辅导标记。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_answers",
            "description": "保存当日题目作答；五题齐则 ready_for_review。",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question_id": {"type": "string"},
                                "answer": {"type": "string"},
                            },
                            "required": ["question_id", "answer"],
                        },
                    }
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_counseling_state",
            "description": "打开或关闭心理辅导标记。",
            "parameters": {
                "type": "object",
                "properties": {"active": {"type": "boolean"}},
                "required": ["active"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_knowledge_excerpts",
            "description": "按岗位读取仍有效的面经摘录。",
            "parameters": {
                "type": "object",
                "properties": {"application_id": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_interview_summary",
            "description": "把点评追加到岗位面试总结，已有手写则追加不覆盖。",
            "parameters": {
                "type": "object",
                "properties": {
                    "application_id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_public_experiences",
            "description": "对已记录岗位检索公开面经。",
            "parameters": {
                "type": "object",
                "properties": {"application_id": {"type": "string"}},
                "required": ["application_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ingest_user_document",
            "description": "收录用户发来的面经文件或粘贴正文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "source_type": {"type": "string"},
                    "application_id": {"type": "string"},
                },
                "required": ["body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_items_for_rejected_role",
            "description": "已挂岗位相关面经只标待删，不真删。",
            "parameters": {
                "type": "object",
                "properties": {"application_id": {"type": "string"}},
                "required": ["application_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_deletion_decision",
            "description": "按用户决定删除或全部保留面经。",
            "parameters": {
                "type": "object",
                "properties": {
                    "accepted_ids": {"type": "array", "items": {"type": "string"}},
                    "keep_all": {"type": "boolean"},
                },
            },
        },
    },
]


ORCHESTRATOR_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "call_counseling_agent",
            "description": "安抚情绪并询问心理状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_text": {"type": "string"},
                    "active": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_nudge_agent",
            "description": "写或更新投递表，或在辅导结束后接上催促。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "jd_url": {"type": "string"},
                    "company_name": {"type": "string"},
                    "role_title": {"type": "string"},
                    "jd_text": {"type": "string"},
                    "exam_points": {"type": "string"},
                    "application_id": {"type": "string"},
                    "progress_text": {"type": "string"},
                    "status_text": {"type": "string"},
                    "deadline": {"type": "string"},
                    "interview_at": {"type": "string"},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_interview_agent",
            "description": "考查点、讲解或点评。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "application_id": {"type": "string"},
                    "url": {"type": "string"},
                    "jd_text": {"type": "string"},
                    "exam_points": {"type": "string"},
                    "user_text": {"type": "string"},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_knowledge_agent",
            "description": "检索、收录或处理已挂面经。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "application_id": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "source_type": {"type": "string"},
                    "accepted_ids": {"type": "array", "items": {"type": "string"}},
                    "keep_all": {"type": "boolean"},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_snapshot",
            "description": "读取简历摘要、投递表、面经标题、当日题和辅导标记。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

NUDGE_TOOL_NAMES = ("record_application", "update_application", "get_snapshot")
INTERVIEW_TOOL_NAMES = (
    "fetch_jd",
    "save_exam_points",
    "get_knowledge_excerpts",
    "save_answers",
    "append_interview_summary",
    "get_snapshot",
)
KNOWLEDGE_TOOL_NAMES = (
    "search_public_experiences",
    "ingest_user_document",
    "evaluate_items_for_rejected_role",
    "apply_deletion_decision",
    "get_snapshot",
)
COUNSELING_TOOL_NAMES = ("set_counseling_state", "get_snapshot")


def definitions_for(names: tuple[str, ...]) -> list[dict[str, Any]]:
    wanted = set(names)
    return [
        item
        for item in TOOL_DEFINITIONS
        if item.get("function", {}).get("name") in wanted
    ]


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _application_public(row: Application) -> dict[str, Any]:
    return ApplicationPublic.model_validate(row).model_dump(mode="json")


class ToolExecutor:
    def __init__(
        self,
        db: AsyncSession,
        candidate: Candidate,
        settings: AppSettings | None = None,
    ) -> None:
        self.db = db
        self.candidate = candidate
        self.settings = settings or get_settings()
        self.candidates = CandidateService(db)
        self.knowledge = KnowledgeStore(db)
        self.knowledge_items = KnowledgeRepository(db)
        self.last_fetch_failed = False
        self.ready_for_review = False
        self.counseling_turned_off = False
        self.last_search_failed = False
        self.last_kb_notice = ""
        self.last_exam_points_text = ""
        self.last_ingest_item_id = ""
        self.recorded_application_id: str | None = None

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        handlers = {
            "record_application": self.record_application,
            "fetch_jd": self.fetch_jd_tool,
            "save_exam_points": self.save_exam_points,
            "update_application": self.update_application,
            "get_snapshot": self.get_snapshot,
            "save_answers": self.save_answers,
            "set_counseling_state": self.set_counseling_state,
            "get_knowledge_excerpts": self.get_knowledge_excerpts,
            "append_interview_summary": self.append_interview_summary,
            "search_public_experiences": self.search_public_experiences,
            "ingest_user_document": self.ingest_user_document,
            "evaluate_items_for_rejected_role": self.evaluate_items_for_rejected_role,
            "apply_deletion_decision": self.apply_deletion_decision,
        }
        handler = handlers.get(name)
        if handler is None:
            return _dump({"ok": False, "error": f"未知工具：{name}"})
        result = await handler(arguments)
        await self.db.commit()
        return _dump(result)

    async def fetch_jd_tool(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = str(arguments.get("url") or "").strip()
        if not url:
            self.last_fetch_failed = True
            return {"ok": False, "text": "", "error": "缺少岗位链接。"}
        if host_is_blocked(url):
            self.last_fetch_failed = True
            logger.info("岗位链接主机不可抓，未发起请求")
            return {
                "ok": False,
                "text": "",
                "error": (
                    "这段岗位链接读不到。请换一个链接，"
                    "或直接把 JD 正文发给我，我才能总结考查点。"
                ),
            }
        result = await fetch_jd(url, self.settings)
        self.last_fetch_failed = not bool(result.get("ok"))
        return result

    async def record_application(self, arguments: dict[str, Any]) -> dict[str, Any]:
        jd_url = str(arguments.get("jd_url") or "").strip() or None
        company_name = str(arguments.get("company_name") or "").strip()
        role_title = str(arguments.get("role_title") or "").strip()
        jd_text = str(arguments.get("jd_text") or "").strip()
        exam_points = str(arguments.get("exam_points") or "").strip()
        if jd_url and not any([company_name, role_title, jd_text, exam_points]):
            return {"ok": False, "error": "岗位信息不足，未写入投递表。"}
        existing = None
        if jd_url:
            existing = await self._find_application(jd_url=jd_url)
        if existing is None and company_name:
            existing = await self._find_application(
                company_name=company_name, role_title=role_title or None
            )
        now = utc_now()
        if existing is not None:
            if company_name:
                existing.company_name = company_name
            if role_title:
                existing.role_title = role_title
            if jd_text:
                existing.jd_text = jd_text
            if exam_points:
                existing.exam_points = exam_points
                self.last_exam_points_text = exam_points
            existing.updated_at = now
            await self.db.flush()
            self.recorded_application_id = existing.id
            logger.info("已复用已投岗位", application_id=existing.id)
            return {"application_id": existing.id, "created": False, "ok": True}
        row = Application(
            id=new_id("a"),
            candidate_id=self.candidate.id,
            company_name=company_name,
            role_title=role_title,
            jd_url=jd_url,
            jd_text=jd_text,
            exam_points=exam_points,
            progress_text="",
            status_text="",
            normalized_status="other",
            deadline=None,
            applied_at=date.fromisoformat(beijing_today()),
            interview_at=None,
            interview_summary="",
            created_at=now,
            updated_at=now,
        )
        self.db.add(row)
        await self.db.flush()
        self.recorded_application_id = row.id
        if exam_points:
            self.last_exam_points_text = exam_points
        logger.info("已记录已投岗位", application_id=row.id)
        return {"application_id": row.id, "created": True, "ok": True}

    async def save_exam_points(self, arguments: dict[str, Any]) -> dict[str, Any]:
        application_id = str(arguments.get("application_id") or "")
        exam_points = str(arguments.get("exam_points") or "").strip()
        row = await self._get_application(application_id)
        if row is None:
            return {"ok": False, "error": "找不到这条投递"}
        if not exam_points:
            return {"ok": False, "error": "考查点不能为空"}
        row.exam_points = exam_points
        row.updated_at = utc_now()
        await self.db.flush()
        self.last_exam_points_text = exam_points
        self.recorded_application_id = row.id
        logger.info("已保存考查点", application_id=row.id)
        return {"ok": True}

    async def update_application(self, arguments: dict[str, Any]) -> dict[str, Any]:
        row = await self._find_application(
            application_id=str(arguments.get("application_id") or "") or None,
            company_name=str(arguments.get("company_name") or "") or None,
            role_title=str(arguments.get("role_title") or "") or None,
        )
        if row is None:
            return {"error": "找不到要更新的岗位，请先记录投递。"}
        if "progress_text" in arguments and arguments["progress_text"] is not None:
            row.progress_text = str(arguments["progress_text"])
        if "deadline" in arguments and arguments["deadline"] is not None:
            deadline = str(arguments["deadline"]).strip()
            row.deadline = deadline or None
            if deadline and not arguments.get("interview_at"):
                row.interview_at = deadline
        if "interview_at" in arguments:
            raw_time = arguments["interview_at"]
            if raw_time is None or str(raw_time).strip() == "":
                row.interview_at = None
                row.deadline = None
            else:
                value = str(raw_time).strip()
                row.interview_at = value
                row.deadline = value[:10] if len(value) >= 10 else value
        previous_status = row.normalized_status
        if "status_text" in arguments and arguments["status_text"] is not None:
            status_text = str(arguments["status_text"])
            row.status_text = status_text
            row.normalized_status = normalize_application_status(status_text)
        became_rejected = (
            row.normalized_status == "rejected" and previous_status != "rejected"
        )
        if row.normalized_status == "waiting_interview":
            await self._ensure_next_question_date()
        row.updated_at = utc_now()
        await self.db.flush()
        if became_rejected:
            related = await self.knowledge_items.list_by_candidate(
                self.candidate.id, row.id
            )
            if related:
                await self.evaluate_items_for_rejected_role(
                    {"application_id": row.id}
                )
        logger.info(
            "已更新投递进度",
            application_id=row.id,
            normalized_status=row.normalized_status,
        )
        return _application_public(row)

    async def get_snapshot(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        summary = await self.candidates.current_summary(self.candidate)
        applications = await self.candidates.list_applications(self.candidate)
        question_set = await self.candidates.current_question_set(self.candidate)
        tz = ZoneInfo(self.settings.timezone)
        now = utc_now().astimezone(tz)
        excerpt = (self.candidate.resume_text or "")[:_RESUME_EXCERPT_CHARS]
        knowledge_rows = await self.knowledge_items.list_by_candidate(self.candidate.id)
        return {
            "beijing_time": now.isoformat(),
            "beijing_date": beijing_today(),
            "counseling_active": self.candidate.counseling_active,
            "resume_filename": self.candidate.resume_filename,
            "resume_parse_ok": self.candidate.resume_parse_ok,
            "resume_excerpt": excerpt,
            "applications": [item.model_dump(mode="json") for item in applications],
            "knowledge_titles": [
                {"id": item.id, "title": item.title} for item in knowledge_rows
            ],
            "today": summary.today.model_dump(mode="json"),
            "question_set": question_set.model_dump(mode="json"),
            "snapshot": summary.model_dump(mode="json"),
        }

    async def save_answers(self, arguments: dict[str, Any]) -> dict[str, Any]:
        items = arguments.get("items")
        if not isinstance(items, list) or not items:
            return {"error": "items 不能为空"}
        today = beijing_today()
        question_set = await self._today_question_set(today)
        if question_set is None:
            return {"error": "今天还没有题集"}
        questions = await self._list_questions(question_set.id)
        by_id = {item.id: item for item in questions}
        for raw in items:
            if not isinstance(raw, dict):
                return {"error": "作答格式不对"}
            question = by_id.get(str(raw.get("question_id") or ""))
            if question is None:
                return {"error": "题目不属于今天的题集"}
            question.answer = str(raw.get("answer") or "")
        answered = [item for item in questions if item.answer]
        if answered and question_set.status in {"pending", "none"}:
            question_set.status = "in_progress"
        ready = bool(questions) and all(item.answer for item in questions)
        self.ready_for_review = ready
        await self.db.flush()
        logger.info(
            "已保存作答",
            question_set_id=question_set.id,
            ready_for_review=ready,
        )
        return {
            "status": question_set.status,
            "ready_for_review": ready,
            "answered_count": len(answered),
            "question_count": len(questions),
        }

    async def save_answers_from_user_text(self, user_text: str) -> dict[str, Any]:
        today = beijing_today()
        question_set = await self._today_question_set(today)
        if question_set is None:
            return {"ok": False, "error": "今天还没有题集"}
        if question_set.status in {"completed", "voided", "rest_day"}:
            return {"ok": False, "error": "今日题集已结束"}
        questions = await self._list_questions(question_set.id)
        items = parse_user_answers(user_text, questions)
        if not items or not any(item["answer"] for item in items):
            return {"ok": False, "error": "没有可保存的作答"}
        saved = await self.save_answers({"items": items})
        saved["ok"] = "error" not in saved
        return saved

    async def set_counseling_state(self, arguments: dict[str, Any]) -> dict[str, Any]:
        active = bool(arguments.get("active"))
        was_active = self.candidate.counseling_active
        self.candidate.counseling_active = active
        self.candidate.updated_at = utc_now()
        if was_active and not active:
            self.counseling_turned_off = True
        await self.db.flush()
        logger.info("已更新辅导状态", counseling_active=active)
        return {"counseling_active": active}

    async def save_review_text(self, review: str) -> None:
        from src.services.questions import QuestionService
        from src.services.scheduler import now_beijing

        today = beijing_today()
        question_set = await self._today_question_set(today)
        if question_set is None:
            return
        await QuestionService(self.db).complete_with_review(
            self.candidate,
            question_set,
            review,
            now_beijing(),
        )
        await self.db.commit()
        logger.info("已保存点评并标记题集完成", question_set_id=question_set.id)

    async def current_snapshot(self) -> CandidateCurrentPublic:
        return await self.candidates.current_summary(self.candidate)

    async def current_question_set(self) -> QuestionSetPublic:
        return await self.candidates.current_question_set(self.candidate)

    async def get_knowledge_excerpts(self, arguments: dict[str, Any]) -> dict[str, Any]:
        application_id = str(arguments.get("application_id") or "") or None
        excerpts = await self.knowledge.excerpts_for(self.candidate, application_id)
        return {"ok": True, "excerpts": excerpts}

    async def append_interview_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        application_id = str(arguments.get("application_id") or "")
        text = str(arguments.get("text") or "").strip()
        row = await self._get_application(application_id) if application_id else None
        if row is None:
            apps = await self.candidates.list_applications(self.candidate)
            waiting = [item for item in apps if item.normalized_status == "waiting_interview"]
            if not waiting:
                return {"ok": False, "error": "找不到要追加总结的岗位"}
            row = await self._get_application(waiting[0].id)
        if row is None or not text:
            return {"ok": False, "error": "总结不能为空"}
        if row.interview_summary.strip():
            row.interview_summary = f"{row.interview_summary.rstrip()}\n{text}"
        else:
            row.interview_summary = text
        row.updated_at = utc_now()
        await self.db.flush()
        logger.info("已追加面试总结", application_id=row.id)
        return {"ok": True, "application_id": row.id}

    async def search_public_experiences(self, arguments: dict[str, Any]) -> dict[str, Any]:
        application_id = str(arguments.get("application_id") or "")
        row = await self._get_application(application_id)
        if row is None:
            self.last_search_failed = True
            return {"ok": False, "error": "找不到要检索的岗位", "items": []}
        if not row.company_name.strip() or not row.role_title.strip():
            self.last_search_failed = True
            return {
                "ok": False,
                "error": "还认不出公司和岗位，没法查找面经。",
                "items": [],
            }
        run = await self.knowledge_items.create_search_run(
            application_id=row.id, status="running"
        )
        prompt = await render_prompt("knowledge_search.md", self.db, self.candidate)
        searcher = KnowledgeSearchService(self.settings)
        result = await searcher.search_public_experiences(
            company_name=row.company_name,
            role_title=row.role_title,
            prompt=prompt,
        )
        if not result.get("ok"):
            self.last_search_failed = True
            reason = str(
                result.get("fail_reason")
                or "这次没找到可核对的面经，你可以在对话里发文件或粘贴。"
            )
            await self.knowledge_items.update_search_run(
                run, status="failed", error_text=reason
            )
            self.last_kb_notice = reason
            return {"ok": False, "error": reason, "items": [], "ingested": []}
        ingested_ids: list[str] = []
        for item in result.get("items") or []:
            created = await self.knowledge.ingest(
                self.candidate,
                title=str(item.get("title") or ""),
                body=str(item.get("body") or ""),
                source_type="search",
                application_id=row.id,
            )
            ingested_ids.append(created.id)
        await self.knowledge_items.update_search_run(run, status="succeeded")
        notice = f"已收入{row.company_name} {row.role_title}相关面经，可在「我的面经」查看。"
        self.last_kb_notice = notice
        self.last_search_failed = False
        return {
            "ok": True,
            "items": result.get("items") or [],
            "ingested": ingested_ids,
            "notice": notice,
        }

    async def ingest_user_document(self, arguments: dict[str, Any]) -> dict[str, Any]:
        body = str(arguments.get("body") or "").strip()
        if not body:
            return {"ok": False, "error": "没有可收录的面经正文。"}
        existing = await self.knowledge.find_item_by_body(self.candidate, body)
        if existing is not None:
            self.last_kb_notice = USER_INGEST_NOTICE
            self.last_ingest_item_id = existing.id
            return {
                "ok": True,
                "knowledge_item_id": existing.id,
                "notice": USER_INGEST_NOTICE,
            }
        source_type = str(arguments.get("source_type") or "paste")
        typed_source: KnowledgeSourceType = "paste"
        if source_type == "upload":
            typed_source = "upload"
        elif source_type == "search":
            typed_source = "search"
        application_id = str(arguments.get("application_id") or "") or None
        title = str(arguments.get("title") or "").strip() or body[:40]
        row = await self.knowledge.ingest(
            self.candidate,
            title=title,
            body=body,
            source_type=typed_source,
            application_id=application_id,
        )
        self.last_kb_notice = USER_INGEST_NOTICE
        self.last_ingest_item_id = row.id
        return {"ok": True, "knowledge_item_id": row.id, "notice": USER_INGEST_NOTICE}

    async def evaluate_items_for_rejected_role(self, arguments: dict[str, Any]) -> dict[str, Any]:
        application_id = str(arguments.get("application_id") or "")
        if not application_id:
            return {"ok": False, "asked": False, "error": "缺少岗位"}
        result = await self.knowledge.evaluate_for_rejected(self.candidate, application_id)
        return result

    async def apply_deletion_decision(self, arguments: dict[str, Any]) -> dict[str, Any]:
        keep_all = bool(arguments.get("keep_all"))
        accepted = arguments.get("accepted_ids")
        ids = [str(item) for item in accepted] if isinstance(accepted, list) else []
        rows = await self.knowledge_items.list_by_candidate(self.candidate.id)
        item_ids = [row.id for row in rows]
        if keep_all or not ids:
            await self.knowledge_items.apply_keep_all(item_ids)
            return {"ok": True, "deleted": [], "kept_all": True}
        deleted = await self.knowledge_items.delete_by_ids(ids)
        remain = [item_id for item_id in item_ids if item_id not in deleted]
        await self.knowledge_items.apply_keep_all(remain)
        return {"ok": True, "deleted": deleted, "kept_all": False}

    async def _ensure_next_question_date(self) -> None:
        if self.candidate.next_question_date is not None:
            return
        tz = ZoneInfo(self.settings.timezone)
        now = utc_now().astimezone(tz)
        today = now.date()
        nxt = today + timedelta(days=1) if now.hour == 0 else today
        self.candidate.next_question_date = nxt
        self.candidate.updated_at = utc_now()

    async def _get_application(self, application_id: str) -> Application | None:
        if not application_id:
            return None
        result = await self.db.execute(
            select(Application).where(
                Application.id == application_id,
                Application.candidate_id == self.candidate.id,
            )
        )
        return result.scalar_one_or_none()

    async def _find_application(
        self,
        *,
        application_id: str | None = None,
        jd_url: str | None = None,
        company_name: str | None = None,
        role_title: str | None = None,
    ) -> Application | None:
        if application_id:
            found = await self._get_application(application_id)
            if found is not None:
                return found
        stmt = select(Application).where(Application.candidate_id == self.candidate.id)
        if jd_url:
            stmt = stmt.where(Application.jd_url == jd_url)
        elif company_name and role_title:
            stmt = stmt.where(
                Application.company_name == company_name,
                Application.role_title == role_title,
            )
        elif company_name:
            stmt = stmt.where(Application.company_name == company_name)
        else:
            return None
        stmt = stmt.order_by(Application.updated_at.desc())
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def _today_question_set(self, beijing_date: str) -> QuestionSet | None:
        result = await self.db.execute(
            select(QuestionSet).where(
                QuestionSet.candidate_id == self.candidate.id,
                QuestionSet.beijing_date == beijing_date,
            )
        )
        return result.scalar_one_or_none()

    async def _list_questions(self, question_set_id: str) -> list[Question]:
        result = await self.db.execute(
            select(Question)
            .where(Question.question_set_id == question_set_id)
            .order_by(Question.seq.asc())
        )
        return list(result.scalars().all())


def tone_level_for_hour(hour: int) -> int:
    if 10 <= hour <= 12:
        return 1
    if 13 <= hour <= 17:
        return 2
    if 18 <= hour <= 21:
        return 3
    if 22 <= hour <= 23:
        return 4
    return 1
