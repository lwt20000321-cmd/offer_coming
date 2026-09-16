"""所有 Agent 提示词共用的模板变量。缺项写「（暂无）」，不编造未登记字段。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.db.models import Application, Candidate
from src.utils.crypto import beijing_today, utc_now

MISSING = "（暂无）"
SHARED_PROMPT_KEYS = (
    "current_date",
    "user_profile",
    "resume",
    "job_list",
    "calendar",
    "uploaded_documents",
    "knowledge_base",
)
SHARED_CONTEXT_BLOCK = """## 共享上下文

以下由系统在调用时填入；缺项为「（暂无）」，不要编造未出现的内容。

当前日期：
{{current_date}}

用户背景、求职方向、工作年限、城市、偏好等：
{{user_profile}}

用户最新简历：
{{resume}}

岗位及投递记录：
{{job_list}}

面试安排和用户可用时间：
{{calendar}}

用户上传的 JD、面经、笔记、截图等：
{{uploaded_documents}}

知识库检索结果：
{{knowledge_base}}
"""

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
_RESUME_CHARS = 1500
_JD_CHARS = 600
_EXAM_CHARS = 400
_SUMMARY_CHARS = 400
_DOC_BODY_CHARS = 400
_KB_TEXT_CHARS = 300
_MAX_KB_ITEMS = 8
_MAX_DOCS = 8


def fill_shared_variables(template: str, values: Mapping[str, str]) -> str:
    filled = template
    keys = list(SHARED_PROMPT_KEYS)
    for key in values:
        if key not in keys:
            keys.append(key)
    for key in keys:
        filled = filled.replace("{{" + key + "}}", values.get(key) or MISSING)
    return filled


def empty_shared_values() -> dict[str, str]:
    return {key: MISSING for key in SHARED_PROMPT_KEYS}


async def build_shared_prompt_context(
    db: AsyncSession,
    candidate: Candidate,
    *,
    knowledge_override: str | None = None,
) -> dict[str, str]:
    from src.repositories.application import ApplicationRepository
    from src.services.candidate import CandidateService
    from src.services.knowledge_store import KnowledgeStore

    applications = await ApplicationRepository(db).list_by_candidate(candidate.id)
    summary = await CandidateService(db).current_summary(candidate)
    excerpts = await KnowledgeStore(db).excerpts_for(candidate)
    tz = ZoneInfo(get_settings().timezone)
    now = utc_now().astimezone(tz)
    today = beijing_today()
    knowledge_text = (
        knowledge_override.strip()
        if knowledge_override and knowledge_override.strip()
        else _format_knowledge(excerpts)
    )
    return {
        "current_date": today,
        "user_profile": _format_user_profile(candidate, applications),
        "resume": _format_resume(candidate),
        "job_list": _format_job_list(applications),
        "calendar": _format_calendar(candidate, applications, summary.today.model_dump(), now, today),
        "uploaded_documents": _format_uploaded_documents(applications, excerpts),
        "knowledge_base": knowledge_text,
        "session_ended": (
            "否（心理辅导进行中）"
            if candidate.counseling_active
            else "否（当前未标记为辅导会话；用户明确说暂停、结束或休息时，本轮按已结束收尾）"
        ),
        "recent_event": _format_recent_event(applications),
        "current_emotion": "以用户本轮原话为准，不要编造未出现的情绪标签。",
        "ready_for_action": (
            "尚未确认愿意继续求职任务；用户明确说心情变好后再接回题目或催促。"
            if candidate.counseling_active
            else "以用户本轮表述为准，不要催促。"
        ),
    }


async def render_prompt(
    name: str,
    db: AsyncSession,
    candidate: Candidate,
    *,
    fallback: str | None = None,
    knowledge_override: str | None = None,
) -> str:
    path = _PROMPTS_DIR / name
    template = fallback or ""
    try:
        if path.is_file():
            template = path.read_text(encoding="utf-8")
    except OSError:
        if not template:
            raise
    values = await build_shared_prompt_context(
        db, candidate, knowledge_override=knowledge_override
    )
    return fill_shared_variables(template, values)


def format_knowledge_override(excerpts: list[dict[str, Any]] | None) -> str | None:
    if not excerpts:
        return None
    text = _format_knowledge(excerpts)
    return None if text == MISSING else text


def _clip(text: str | None, limit: int) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def _format_resume(candidate: Candidate) -> str:
    parse_state = "已解析" if candidate.resume_parse_ok else "未解析或解析失败"
    body = _clip(candidate.resume_text, _RESUME_CHARS) or MISSING
    return "\n".join(
        [
            f"文件：{candidate.resume_filename or MISSING}（{parse_state}）",
            body,
        ]
    )


def _format_user_profile(candidate: Candidate, applications: list[Application]) -> str:
    waiting_roles = [
        f"{item.company_name} {item.role_title}".strip()
        for item in applications
        if item.normalized_status == "waiting_interview"
        and (item.company_name.strip() or item.role_title.strip())
    ]
    all_roles = [
        f"{item.company_name} {item.role_title}".strip()
        for item in applications
        if item.company_name.strip() or item.role_title.strip()
    ]
    direction = "、".join(waiting_roles or all_roles) if (waiting_roles or all_roles) else MISSING
    return "\n".join(
        [
            f"邮箱：{candidate.email or MISSING}",
            f"求职方向：{direction}",
            f"工作年限：{MISSING}（未单独建档，勿编造；可对照最新简历）",
            f"城市：{MISSING}（未单独建档，勿编造；可对照最新简历）",
            f"偏好：{MISSING}（未单独建档，勿编造）",
        ]
    )


def _format_job_list(applications: list[Application]) -> str:
    if not applications:
        return MISSING
    lines: list[str] = []
    for item in applications:
        applied = item.applied_at.isoformat() if item.applied_at else MISSING
        lines.append(
            "\n".join(
                [
                    f"- id={item.id}",
                    f"  公司/岗位：{(item.company_name or '').strip() or MISSING} / {(item.role_title or '').strip() or MISSING}",
                    f"  状态：{(item.status_text or '').strip() or MISSING}（{item.normalized_status}）",
                    f"  进度：{(item.progress_text or '').strip() or MISSING}",
                    f"  投递日：{applied}",
                    f"  面试时间：{(item.interview_at or '').strip() or MISSING}",
                    f"  deadline：{(item.deadline or '').strip() or MISSING}",
                    f"  考查点：{_clip(item.exam_points, _EXAM_CHARS) or MISSING}",
                ]
            )
        )
    return "\n".join(lines)


def _format_recent_event(applications: list[Application]) -> str:
    if not applications:
        return MISSING
    latest = max(applications, key=lambda item: item.updated_at or item.created_at)
    company = (latest.company_name or "").strip() or MISSING
    role = (latest.role_title or "").strip() or MISSING
    status = (latest.status_text or "").strip() or latest.normalized_status or MISSING
    progress = (latest.progress_text or "").strip()
    parts = [f"最近更新：{company} / {role}，状态 {status}"]
    if progress:
        parts.append(f"进度 {progress}")
    return "；".join(parts)


def _format_calendar(
    candidate: Candidate,
    applications: list[Application],
    today: dict[str, Any],
    now: Any,
    beijing_date: str,
) -> str:
    next_q = (
        candidate.next_question_date.isoformat()
        if candidate.next_question_date is not None
        else MISSING
    )
    question_day = "是" if today.get("is_question_day") else "否"
    rest_day = "是" if today.get("is_rest_day") else "否"
    counseling = "是" if candidate.counseling_active else "否"
    lines = [
        f"当前北京时间：{now.isoformat()}",
        f"当前北京日期：{beijing_date}",
        f"是否出题日：{question_day}",
        f"是否休息整理日：{rest_day}",
        f"当日题集状态：{today.get('question_set_status') or MISSING}",
        f"下次出题日：{next_q}",
        f"心理辅导进行中：{counseling}",
        f"用户可用时间：{MISSING}（未单独建档；出题日催促窗口为北京时间 10:00–24:00）",
        "面试安排：",
    ]
    schedule = []
    for item in applications:
        interview = (item.interview_at or "").strip()
        deadline = (item.deadline or "").strip()
        if not interview and not deadline:
            continue
        label = f"{item.company_name} {item.role_title}".strip() or item.id
        schedule.append(
            f"- {label}：面试 {interview or MISSING}；deadline {deadline or MISSING}"
        )
    lines.append("\n".join(schedule) if schedule else MISSING)
    return "\n".join(lines)


def _format_uploaded_documents(
    applications: list[Application],
    excerpts: list[dict[str, Any]],
) -> str:
    jd_blocks: list[str] = []
    note_blocks: list[str] = []
    jingyan_blocks: list[str] = []
    for item in applications:
        label = f"{item.company_name} {item.role_title}".strip() or item.id
        jd = _clip(item.jd_text, _JD_CHARS)
        if jd:
            jd_blocks.append(f"- {label}：{jd}")
        summary = _clip(item.interview_summary, _SUMMARY_CHARS)
        if summary:
            note_blocks.append(f"- {label} 面试总结：{summary}")
    for excerpt in excerpts[:_MAX_DOCS]:
        title = str(excerpt.get("title") or "").strip() or str(excerpt.get("item_id") or "材料")
        source = str(excerpt.get("source_type") or "").strip()
        texts = excerpt.get("texts") if isinstance(excerpt.get("texts"), list) else []
        body = _clip("\n".join(str(piece) for piece in texts), _DOC_BODY_CHARS)
        if not body:
            continue
        line = f"- {title}：{body}"
        if source in {"upload", "paste"}:
            note_blocks.append(line)
        else:
            jingyan_blocks.append(line)
    return "\n\n".join(
        [
            "岗位 JD：\n" + ("\n".join(jd_blocks) if jd_blocks else MISSING),
            "面经：\n" + ("\n".join(jingyan_blocks) if jingyan_blocks else MISSING),
            "笔记：\n" + ("\n".join(note_blocks) if note_blocks else MISSING),
            f"截图：{MISSING}（未单独建档；当前只收录文本面经，勿编造截图内容）",
        ]
    )


def _format_knowledge(excerpts: list[dict[str, Any]]) -> str:
    if not excerpts:
        return MISSING
    lines: list[str] = []
    for excerpt in excerpts[:_MAX_KB_ITEMS]:
        title = str(excerpt.get("title") or "").strip() or str(excerpt.get("item_id") or "条目")
        app_id = str(excerpt.get("application_id") or "").strip() or MISSING
        texts = excerpt.get("texts") if isinstance(excerpt.get("texts"), list) else []
        pieces = [_clip(str(piece), _KB_TEXT_CHARS) for piece in texts if str(piece).strip()]
        body = "\n".join(piece for piece in pieces if piece) or MISSING
        lines.append(f"- {title}（application_id={app_id}）\n{body}")
    return "\n".join(lines) if lines else MISSING
