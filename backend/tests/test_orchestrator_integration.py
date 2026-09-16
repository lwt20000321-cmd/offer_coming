"""T-021：五模型分派、缓急与过程说明隔离集成测试。

真实 TestClient 打 API-006/007/010 与调度 tick；隔离库。
用测试双写入 llm_call_logs.role/model/purpose，禁止真实百炼。
浏览器外观（头像/气泡）与付费模型对照留给 Tester。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from src.config.settings import get_settings
from src.services import nudge as nudge_mod
from src.services import questions as questions_mod
from src.services.agent import AgentService, _assistant_text, _last_tool, _tool_call
from src.services.knowledge_search import KnowledgeSearchService
from src.services.llm_client import LlmClient
from src.services.scheduler import reset_clock, set_clock, set_scheduler_loop_enabled

TZ = ZoneInfo("Asia/Shanghai")
READABLE_JD_URL = "https://example.com/jobs/readable-t021"
RESUME_TEXT = "刘文韬，Python 后端。做过校园二手书交易平台 BookSwap。"
EXAM_POINTS = (
    "对照你的简历里 BookSwap 项目，这个岗位会问 Python 落地与 Agent 编排，而不是只复述 JD。"
)
REVIEW_TEXT = (
    "五题都齐了。主要缺点是回答偏结果、少过程；"
    "建议下次用「做了什么-难点-你的决策」讲。"
)
COUNSEL_TEXT = "我先陪你缓一缓。现在最压着你的是什么感觉？"
BETTER_TEXT = "好，那我们把辅导先放下，接着准备面试。"
RESUME_NUDGE = "心情先放到一边。今天这五题我们慢慢写，我在。优先盯面试更近的那家。"
HANDWRITTEN = "手写总结不要覆盖"
FORBIDDEN_JARGON = ("主体", "子 Agent", "子Agent", "工具")
HARSH_NUDGE = ("骂醒", "别装忙了", "立刻做，别再拖", "最狠")
FIVE_MODELS = {
    "orchestrator": "test-orchestrator",
    "nudge": "test-nudge",
    "interview": "test-interview",
    "knowledge": "test-knowledge",
    "counseling": "test-counseling",
}


@pytest.fixture(autouse=True)
def _isolate_scheduler_loop() -> Iterator[None]:
    set_scheduler_loop_enabled(False)
    yield
    reset_clock()
    set_scheduler_loop_enabled(True)


def _start(client: TestClient, email: str) -> tuple[str, str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email},
        files={"resume": ("resume.txt", RESUME_TEXT.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    return data["session_token"], data["conversation_id"], data["candidate"]["id"]


def _headers(token: str, *, sse: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if sse:
        headers["Accept"] = "text/event-stream"
    return headers


def _sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    name = ""
    for line in body.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and name:
            payload = json.loads(line.split(":", 1)[1].strip())
            if not isinstance(payload, dict):
                raise AssertionError("SSE data 解析后应是 dict")
            events.append((name, payload))
            name = ""
    return events


def _visible_text(events: list[tuple[str, dict[str, Any]]]) -> str:
    parts: list[str] = []
    for name, payload in events:
        if name == "status":
            parts.append(str(payload.get("text") or ""))
        elif name == "delta":
            parts.append(str(payload.get("text") or ""))
        elif name == "done":
            parts.append(str((payload.get("message") or {}).get("content") or ""))
        elif name == "error":
            parts.append(str(payload.get("error") or ""))
    return "\n".join(parts)


def _status_texts(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [str(payload.get("text") or "") for name, payload in events if name == "status"]


def _list_apps(client: TestClient, token: str) -> list[dict[str, Any]]:
    response = client.get("/api/applications", headers=_headers(token))
    assert response.status_code == 200, response.text
    apps = response.json()["data"]["applications"]
    assert isinstance(apps, list)
    return apps


def _messages(client: TestClient, token: str, conversation_id: str) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]["messages"]
    assert isinstance(rows, list)
    return rows


def _post_chat(
    client: TestClient,
    token: str,
    conversation_id: str,
    content: str,
) -> list[tuple[str, dict[str, Any]]]:
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": content},
        headers=_headers(token, sse=True),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return _sse_events(response.text)


def _tick(client: TestClient, day: int, hour: int) -> None:
    current = datetime(2026, 9, day, hour, 0, 0, tzinfo=TZ)
    set_clock(lambda value=current: value)
    response = client.post("/internal/scheduler/tick", params={"at": current.isoformat()})
    assert response.status_code == 200, response.text


def _logs(db_path: Path, candidate_id: str) -> list[tuple[str, str, str]]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "select role, model, purpose from llm_call_logs where candidate_id=? order by rowid",
        (candidate_id,),
    ).fetchall()
    conn.close()
    return [(str(role), str(model), str(purpose)) for role, model, purpose in rows]


def _assert_no_jargon(*texts: str) -> None:
    blob = "\n".join(texts)
    for word in FORBIDDEN_JARGON:
        assert word not in blob, blob


def _assert_logs_safe(rows: list[tuple[str, str, str]]) -> None:
    blob = json.dumps(rows, ensure_ascii=False)
    assert "sk-" not in blob
    assert "Bearer" not in blob
    assert "llm_api_key" not in blob
    for role, model, purpose in rows:
        assert role
        assert model
        assert purpose
        assert "\n" not in purpose
        assert len(purpose) < 80


def _assert_five_models_distinct() -> None:
    settings = get_settings()
    names = {
        settings.llm_orchestrator_model,
        settings.llm_nudge_model,
        settings.llm_interview_model,
        settings.llm_knowledge_model,
        settings.llm_counseling_model,
    }
    assert names == set(FIVE_MODELS.values())
    assert len(names) == 5


async def _forbid_bailian(self: LlmClient, **kwargs: Any) -> dict[str, Any]:
    raise AssertionError("测试禁止真实百炼调用")


@contextmanager
def _fake_llm_key() -> Iterator[None]:
    settings = get_settings()
    object.__setattr__(settings, "llm_api_key", "test-key-not-real")
    try:
        yield
    finally:
        object.__setattr__(settings, "llm_api_key", "")


def _patch_readable_jd(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_jd(url: str, settings=None) -> dict[str, Any]:
        assert url == READABLE_JD_URL
        return {
            "ok": True,
            "text": "星云科技招聘 Python 后端，熟悉接口与项目落地。",
            "error": None,
        }

    monkeypatch.setattr("src.services.tools.fetch_jd", fake_fetch_jd)


def _patch_failed_search(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(self: KnowledgeSearchService, **kwargs: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "items": [],
            "fail_reason": "这次没找到可核对的面经，你可以在对话里发文件或粘贴。",
        }

    monkeypatch.setattr(KnowledgeSearchService, "search_public_experiences", fake_search)


def _payload(messages: list[dict[str, Any]]) -> dict[str, Any]:
    last = messages[-1]
    if last.get("role") != "user":
        return {}
    raw = last.get("content")
    if isinstance(raw, dict):
        return raw
    try:
        loaded = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _snapshot(messages: list[dict[str, Any]]) -> dict[str, Any]:
    marker = "## 当前快照\n"
    for item in messages:
        if item.get("role") != "system":
            continue
        content = str(item.get("content") or "")
        if marker not in content:
            continue
        raw = content.split(marker, 1)[1]
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _user_blob(messages: list[dict[str, Any]]) -> str:
    parts = [str(item.get("content") or "") for item in messages if item.get("role") == "user"]
    return "\n".join(parts)


class DispatchDouble:
    """有 Key 形态的编排测试双：先规划再分派，不打百炼。"""

    def __init__(self) -> None:
        self.seen_models: list[tuple[str, str, str]] = []

    async def complete(
        self,
        service: AgentService,
        messages: list[dict[str, Any]],
        *,
        role: str,
        model: str,
        temperature: float,
        tools: list[dict[str, Any]] | None,
        purpose: str,
        log_call: bool = True,
    ) -> dict[str, Any]:
        self.seen_models.append((role, model, purpose))
        if log_call:
            await service._log_llm_call(role, model, purpose)
        expected = FIVE_MODELS.get(role)
        assert expected is not None, role
        assert model == expected
        if role == "orchestrator":
            return self._orchestrator(messages)
        if role == "interview":
            return self._interview(messages, purpose)
        if role == "nudge":
            return self._nudge(messages)
        if role == "knowledge":
            return self._knowledge(messages)
        if role == "counseling":
            return self._counseling(messages)
        return _assistant_text("我在。")

    def _orchestrator(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        last = messages[-1]
        if last.get("role") == "user":
            text = str(last.get("content") or "")
            if any(marker in text for marker in ("焦虑", "不开心", "不想面", "压力大")):
                return _tool_call(
                    "call_counseling_agent", {"user_text": text, "active": True}
                )
            if any(marker in text for marker in ("心情变好", "好多了", "心情好了")):
                return _tool_call(
                    "call_counseling_agent", {"user_text": text, "active": False}
                )
            if any(marker in text for marker in ("第一题", "第二题", "第三题")):
                return _tool_call(
                    "call_interview_agent", {"task": "review", "user_text": text}
                )
            if READABLE_JD_URL in text or "http" in text:
                return _tool_call(
                    "call_interview_agent",
                    {"task": "exam_points", "url": READABLE_JD_URL, "user_text": text},
                )
            return _assistant_text("我在。")
        name, observation = _last_tool(messages)
        if name == "call_interview_agent":
            if observation.get("ready_for_review"):
                return _assistant_text(str(observation.get("reply") or REVIEW_TEXT))
            if observation.get("exam_points") or observation.get("reply"):
                exam = str(observation.get("exam_points") or observation.get("reply") or "")
                return _tool_call(
                    "call_nudge_agent",
                    {
                        "task": "record",
                        "jd_url": READABLE_JD_URL,
                        "company_name": "星云科技",
                        "role_title": "Python 后端",
                        "jd_text": str(observation.get("jd_text") or ""),
                        "exam_points": exam,
                    },
                )
            return _assistant_text(str(observation.get("reply") or "我看到了。"))
        if name == "call_nudge_agent":
            if observation.get("reply") and not observation.get("application_id"):
                return _assistant_text(str(observation["reply"]))
            application_id = str(observation.get("application_id") or "")
            if application_id:
                return _tool_call(
                    "call_knowledge_agent",
                    {"task": "search", "application_id": application_id},
                )
            return _assistant_text(RESUME_NUDGE)
        if name == "call_knowledge_agent":
            exam = ""
            notice = str(
                observation.get("notice")
                or observation.get("error")
                or observation.get("fail_reason")
                or "这次没找到可核对的面经，你可以在对话里发文件或粘贴。"
            )
            snap = _snapshot(messages)
            apps = snap.get("applications") or []
            if isinstance(apps, list) and apps:
                exam = str((apps[0] or {}).get("exam_points") or "")
            text = f"{EXAM_POINTS}\n\n{notice}" if not exam else f"{exam}\n\n{notice}"
            return _assistant_text(text)
        if name == "call_counseling_agent":
            active = bool(observation.get("counseling_active"))
            reply = str(observation.get("reply") or "")
            if active:
                return _assistant_text(reply or COUNSEL_TEXT)
            return _tool_call(
                "call_nudge_agent",
                {"task": "resume_after_counseling", "user_text": _user_blob(messages)},
            )
        return _assistant_text("我在。")

    def _interview(self, messages: list[dict[str, Any]], purpose: str) -> dict[str, Any]:
        last = messages[-1]
        payload = _payload(messages)
        task = str(payload.get("task") or purpose or "exam_points")
        if last.get("role") == "user":
            if task == "review":
                snap = _snapshot(messages)
                question_set = snap.get("question_set") or {}
                questions = question_set.get("questions") or []
                items = []
                answers = ["做过 SQLite。", "会拆服务。", "会讲取舍。"]
                for index, question in enumerate(questions):
                    items.append(
                        {
                            "question_id": question.get("id"),
                            "answer": answers[index] if index < len(answers) else "记下了。",
                        }
                    )
                return _tool_call("save_answers", {"items": items})
            return _tool_call(
                "fetch_jd",
                {"url": str(payload.get("url") or READABLE_JD_URL)},
            )
        name, observation = _last_tool(messages)
        if name == "save_answers":
            return _assistant_text(str(observation.get("reply") or REVIEW_TEXT))
        return _assistant_text(EXAM_POINTS)

    def _nudge(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        last = messages[-1]
        payload = _payload(messages)
        task = str(payload.get("task") or "update")
        if last.get("role") == "user":
            if task == "record":
                return _tool_call(
                    "record_application",
                    {
                        "jd_url": str(payload.get("jd_url") or READABLE_JD_URL),
                        "company_name": str(payload.get("company_name") or "星云科技"),
                        "role_title": str(payload.get("role_title") or "Python 后端"),
                        "jd_text": str(payload.get("jd_text") or ""),
                    },
                )
            if task == "resume_after_counseling":
                return _assistant_text(RESUME_NUDGE)
            return _assistant_text("已记下当前投递。")
        return _assistant_text("已记下投递")

    def _knowledge(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        last = messages[-1]
        payload = _payload(messages)
        if last.get("role") == "user":
            return _tool_call(
                "search_public_experiences",
                {"application_id": str(payload.get("application_id") or "")},
            )
        return _assistant_text("这次没找到可核对的面经，你可以在对话里发文件或粘贴。")

    def _counseling(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        last = messages[-1]
        payload = _payload(messages)
        if last.get("role") == "user":
            if "active" in payload:
                active = bool(payload.get("active"))
            else:
                text = str(payload.get("user_text") or "")
                active = not any(marker in text for marker in ("心情变好", "好多了", "心情好了"))
            return _tool_call("set_counseling_state", {"active": active})
        name, observation = _last_tool(messages)
        if name == "set_counseling_state" and observation.get("counseling_active"):
            return _assistant_text(COUNSEL_TEXT)
        return _assistant_text(BETTER_TEXT)


class MenuOnlyOrchestrator(DispatchDouble):
    """复现真实主体只回菜单/问心情、不调子 Agent 的情况。"""

    def _orchestrator(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        last = messages[-1]
        if last.get("role") == "user":
            text = str(last.get("content") or "")
            if any(marker in text for marker in ("焦虑", "不开心", "不想面", "压力大")):
                return _tool_call(
                    "call_counseling_agent", {"user_text": text, "active": True}
                )
            if any(marker in text for marker in ("心情变好", "好多了", "心情好了")):
                return _assistant_text("你可以选择：要不要生成题、查面经，还是改投递？")
            if any(marker in text for marker in ("第一题", "第二题", "第三题")):
                return _assistant_text("先确认一下你现在的心情？")
            return super()._orchestrator(messages)
        name, observation = _last_tool(messages)
        if name == "call_counseling_agent":
            active = bool(observation.get("counseling_active"))
            reply = str(observation.get("reply") or "")
            if active:
                return _assistant_text(reply or COUNSEL_TEXT)
            return _assistant_text("你可以选择：要不要生成题、查面经，还是改投递？")
        return super()._orchestrator(messages)


def _install_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    double: DispatchDouble | None = None,
) -> DispatchDouble:
    current = double or DispatchDouble()

    async def fake_complete(
        self: AgentService,
        messages: list[dict[str, Any]],
        *,
        role: str,
        model: str,
        temperature: float,
        tools: list[dict[str, Any]] | None,
        purpose: str,
        log_call: bool = True,
    ) -> dict[str, Any]:
        return await current.complete(
            self,
            messages,
            role=role,
            model=model,
            temperature=temperature,
            tools=tools,
            purpose=purpose,
            log_call=log_call,
        )

    monkeypatch.setattr(AgentService, "_complete", fake_complete)
    monkeypatch.setattr(LlmClient, "chat_completions", _forbid_bailian)
    return current


def _install_scheduler_models(
    monkeypatch: pytest.MonkeyPatch,
    *,
    urgent_id: str,
    urgent_company: str,
) -> None:
    async def fake_chat(self: LlmClient, **kwargs: Any) -> dict[str, Any]:
        model = str(kwargs.get("model") or "")
        dumped = json.dumps(kwargs, ensure_ascii=False, default=str)
        assert "sk-" not in dumped
        assert "Bearer" not in dumped
        assert "llm_api_key" not in dumped
        messages = kwargs.get("messages") or []
        assert isinstance(messages, list)
        if model == FIVE_MODELS["interview"]:
            payload = {
                "questions": [
                    {
                        "seq": 1,
                        "kind": "common",
                        "prompt": "请把简历里 BookSwap 项目讲深一层。",
                        "target_application_id": None,
                    },
                    {
                        "seq": 2,
                        "kind": "common",
                        "prompt": "再选一段简历经历，说明冲突和权衡。",
                        "target_application_id": None,
                    },
                    {
                        "seq": 3,
                        "kind": "common",
                        "prompt": "用一次协作受阻的经历，讲清沟通和收口。",
                        "target_application_id": None,
                    },
                    {
                        "seq": 4,
                        "kind": "role",
                        "prompt": f"针对{urgent_company}，结合考查点讲接口取舍。",
                        "target_application_id": urgent_id,
                    },
                    {
                        "seq": 5,
                        "kind": "role",
                        "prompt": f"针对{urgent_company}，讲一次线上事故怎么收口。",
                        "target_application_id": urgent_id,
                    },
                ]
            }
            return {
                "choices": [
                    {"message": {"content": json.dumps(payload, ensure_ascii=False)}}
                ]
            }
        if model == FIVE_MODELS["nudge"]:
            user = str((messages[-1] or {}).get("content") or "")
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "现在 10:00 啦，我们慢慢看一眼就好，不着急。"
                                f"更优先催面试更近的{urgent_company}。"
                                f"\n{user}"
                            )
                        }
                    }
                ]
            }
        raise AssertionError(f"调度测试双未覆盖 model={model}")

    monkeypatch.setattr(questions_mod, "_llm_configured", lambda: True)
    monkeypatch.setattr(nudge_mod, "_llm_configured", lambda: True)
    monkeypatch.setattr(questions_mod.LlmClient, "chat_completions", fake_chat)
    monkeypatch.setattr(nudge_mod.LlmClient, "chat_completions", fake_chat)


def test_five_models_exam_points_questions_nudge_review(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-029/033/035/041：五套 model 名对照日志；主体先规划；过程说明仅业务句。"""
    _assert_five_models_distinct()
    _patch_readable_jd(monkeypatch)
    _patch_failed_search(monkeypatch)
    _install_dispatch(monkeypatch)

    token, conversation_id, candidate_id = _start(client, "t021.five@example.com")
    with _fake_llm_key():
        events = _post_chat(
            client,
            token,
            conversation_id,
            f"我投了这个岗位，链接是 {READABLE_JD_URL}",
        )
    assert events[-1][0] == "done"
    names = [name for name, _ in events]
    assert "status" in names
    assert set(names) <= {"status", "delta", "done", "error"}
    visible = _visible_text(events)
    _assert_no_jargon(visible, json.dumps([payload for _, payload in events], ensure_ascii=False))
    assert EXAM_POINTS in visible
    statuses = _status_texts(events)
    assert "正在读取岗位链接" in statuses
    assert all("主体" not in text and "工具" not in text for text in statuses)

    listed = _list_apps(client, token)
    assert len(listed) == 1
    row = listed[0]
    assert row["jd_url"] == READABLE_JD_URL
    assert row["company_name"] == "星云科技"
    assert (row.get("exam_points") or "").strip()
    assert EXAM_POINTS in (row.get("exam_points") or "")

    logs = _logs(tmp_env / "offer_coming.db", candidate_id)
    _assert_logs_safe(logs)
    assert logs
    assert logs[0] == ("orchestrator", FIVE_MODELS["orchestrator"], "plan")
    later_roles = [role for role, _model, _purpose in logs[1:]]
    assert "interview" in later_roles
    assert "nudge" in later_roles
    exam_logs = [
        item for item in logs if item[0] == "interview" and item[2] == "exam_points"
    ]
    assert exam_logs
    assert all(item[1] == FIVE_MODELS["interview"] for item in exam_logs)
    nudge_record = [item for item in logs if item[0] == "nudge" and item[2] == "record"]
    assert nudge_record
    assert all(item[1] == FIVE_MODELS["nudge"] for item in nudge_record)
    for role, model, purpose in logs:
        if purpose in {"exam_points", "questions", "review"}:
            assert role == "interview"
            assert model == FIVE_MODELS["interview"]
            assert model != FIVE_MODELS["counseling"]
        if purpose in {"record", "hourly_nudge", "resume_after_counseling"}:
            assert role == "nudge"
            assert model == FIVE_MODELS["nudge"]
            assert model != FIVE_MODELS["counseling"]

    patched = client.patch(
        f"/api/applications/{row['id']}",
        json={
            "status_text": "等待面试",
            "interview_at": "2026-09-20",
            "interview_summary": HANDWRITTEN,
        },
        headers=_headers(token),
    )
    assert patched.status_code == 200, patched.text
    urgent_id = row["id"]
    _install_scheduler_models(
        monkeypatch, urgent_id=urgent_id, urgent_company="星云科技"
    )
    with _fake_llm_key():
        _tick(client, 14, 10)

    current = client.get("/api/question-sets/current", headers=_headers(token))
    assert current.status_code == 200, current.text
    questions = current.json()["data"]["questions"]
    assert len(questions) == 5
    kinds = [item["kind"] for item in questions]
    assert "common" in kinds
    assert "role" in kinds
    role_targets = [
        item["target_application_id"] for item in questions if item["kind"] == "role"
    ]
    assert role_targets
    assert role_targets[0] == urgent_id

    after_tick = _logs(tmp_env / "offer_coming.db", candidate_id)
    _assert_logs_safe(after_tick)
    question_logs = [
        item for item in after_tick if item[0] == "interview" and item[2] == "questions"
    ]
    hourly = [
        item for item in after_tick if item[0] == "nudge" and item[2] == "hourly_nudge"
    ]
    assert question_logs
    assert all(item[1] == FIVE_MODELS["interview"] for item in question_logs)
    assert hourly
    assert all(item[1] == FIVE_MODELS["nudge"] for item in hourly)
    assert all(item[1] != FIVE_MODELS["counseling"] for item in question_logs + hourly)

    chat = _messages(client, token, conversation_id)
    nudges = [item["content"] for item in chat if item["message_type"] == "nudge"]
    assert nudges
    assert "慢慢" in nudges[-1] or "不着急" in nudges[-1]
    q_msgs = [item["content"] for item in chat if item["message_type"] == "questions"]
    assert q_msgs
    assert "共性" in q_msgs[-1]
    assert "岗位" in q_msgs[-1]
    _assert_no_jargon(*[item["content"] for item in chat])

    with _fake_llm_key():
        review_events = _post_chat(
            client,
            token,
            conversation_id,
            "第一题：做过 SQLite。第二题：会拆服务。第三题：会讲取舍。第四题：会讲方案。第五题：会讲复盘。",
        )
    assert review_events[-1][0] == "done"
    review_visible = _visible_text(review_events)
    assert "缺点" in review_visible
    assert "建议" in review_visible
    _assert_no_jargon(review_visible)

    final_logs = _logs(tmp_env / "offer_coming.db", candidate_id)
    _assert_logs_safe(final_logs)
    review_logs = [
        item for item in final_logs if item[0] == "interview" and item[2] == "review"
    ]
    assert review_logs
    assert all(item[1] == FIVE_MODELS["interview"] for item in review_logs)
    assert all(item[1] != FIVE_MODELS["counseling"] for item in review_logs)

    after_review = _list_apps(client, token)[0]
    summary = after_review["interview_summary"]
    assert summary.startswith(HANDWRITTEN)
    assert "缺点" in summary
    assert summary != REVIEW_TEXT


def test_anxiety_round_skips_harsh_nudge(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-030/034：焦虑轮只派辅导；同句提到岗位也不写骂醒催促。"""
    _install_dispatch(monkeypatch)
    token, conversation_id, candidate_id = _start(client, "t021.anxiety@example.com")
    with _fake_llm_key():
        events = _post_chat(
            client,
            token,
            conversation_id,
            f"我很焦虑，{READABLE_JD_URL} 这个岗位的面试题好难，不想面",
        )
    assert events[-1][0] == "done"
    visible = _visible_text(events)
    _assert_no_jargon(visible)
    assert "缓一缓" in visible or "感觉" in visible
    for marker in HARSH_NUDGE:
        assert marker not in visible
    done = events[-1][1]
    assert done["message"]["message_type"] == "counseling"
    assert done["snapshot"]["today"]["counseling_active"] is True

    logs = _logs(tmp_env / "offer_coming.db", candidate_id)
    _assert_logs_safe(logs)
    assert logs[0][0] == "orchestrator"
    assert logs[0][1] == FIVE_MODELS["orchestrator"]
    assert logs[0][2] == "plan"
    roles = [role for role, _model, _purpose in logs]
    assert "counseling" in roles
    assert ("nudge", FIVE_MODELS["nudge"], "hourly_nudge") not in logs
    assert ("nudge", FIVE_MODELS["nudge"], "resume_after_counseling") not in logs
    counsel = [item for item in logs if item[0] == "counseling"]
    assert counsel
    assert all(item[1] == FIVE_MODELS["counseling"] and item[2] == "counsel" for item in counsel)
    assert all(item[0] != "nudge" for item in logs)


def test_mood_better_dispatches_nudge_or_interview(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-031：心情变好后主体再派催促/面试；外观仍无后台分工字样。"""
    _install_dispatch(monkeypatch)
    token, conversation_id, candidate_id = _start(client, "t021.better@example.com")
    created = client.post(
        "/api/applications",
        json={
            "company_name": "早上面",
            "role_title": "后端",
            "status_text": "等待面试",
            "interview_at": "2026-09-20",
        },
        headers=_headers(token),
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["data"]["id"]
    _install_scheduler_models(monkeypatch, urgent_id=app_id, urgent_company="早上面")
    with _fake_llm_key():
        _tick(client, 14, 10)
        anxious = _post_chat(client, token, conversation_id, "我很焦虑，今天不想面")
        better = _post_chat(client, token, conversation_id, "心情变好了")

    assert anxious[-1][0] == "done"
    assert anxious[-1][1]["snapshot"]["today"]["counseling_active"] is True
    assert better[-1][0] == "done"
    better_visible = _visible_text(better)
    _assert_no_jargon(better_visible)
    assert better[-1][1]["snapshot"]["today"]["counseling_active"] is False
    assert "今日还没答完" in better_visible or "慢慢" in better_visible or "待办" in better_visible
    for marker in HARSH_NUDGE:
        assert marker not in better_visible

    logs = _logs(tmp_env / "offer_coming.db", candidate_id)
    _assert_logs_safe(logs)
    roles_after = [role for role, _model, _purpose in logs]
    assert "orchestrator" in roles_after
    assert "counseling" in roles_after
    assert "nudge" in roles_after or "interview" in roles_after
    resumed = [
        item
        for item in logs
        if item[0] == "nudge" and item[2] == "resume_after_counseling"
    ]
    interview_after = [
        item for item in logs if item[0] == "interview" and item[2] in {"questions", "exam_points"}
    ]
    assert resumed or interview_after
    if resumed:
        assert all(item[1] == FIVE_MODELS["nudge"] for item in resumed)
        assert all(item[1] != FIVE_MODELS["counseling"] for item in resumed)

    stored = _messages(client, token, conversation_id)
    _assert_no_jargon(*[item["content"] for item in stored])
    types = {item["message_type"] for item in stored if item["role"] == "assistant"}
    assert "counseling" in types
    assert types <= {
        "chat",
        "counseling",
        "questions",
        "nudge",
        "jd_summary",
        "review",
        "kb_notice",
        "error_notice",
    }


def test_replies_share_xiaoao_without_agent_labels(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-032：考查点与辅导落在同一对话；无主体/子 Agent 标签。头像由 Tester 看网页。"""
    _patch_readable_jd(monkeypatch)
    _patch_failed_search(monkeypatch)
    _install_dispatch(monkeypatch)
    token, conversation_id, _candidate_id = _start(client, "t021.ui@example.com")
    with _fake_llm_key():
        jd_events = _post_chat(
            client,
            token,
            conversation_id,
            f"请看 {READABLE_JD_URL}",
        )
        counsel_events = _post_chat(
            client,
            token,
            conversation_id,
            "我现在好焦虑",
        )
    assert jd_events[-1][0] == "done"
    assert counsel_events[-1][0] == "done"
    stored = _messages(client, token, conversation_id)
    assistant = [item for item in stored if item["role"] == "assistant"]
    assert any(item["message_type"] == "jd_summary" for item in assistant)
    assert any(item["message_type"] == "counseling" for item in assistant)
    for item in stored:
        assert item["role"] in {"user", "assistant"}
        _assert_no_jargon(item["content"], item["role"], item["message_type"])
        assert "第二角色" not in item["content"]
    visible = _visible_text(jd_events) + "\n" + _visible_text(counsel_events)
    _assert_no_jargon(visible)


def test_urgency_empty_interview_at_and_ten_am_nudge(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-007/010/036/045：更近 interview_at 优先；空面试时间不按截止日加急；10:00 温柔催。"""
    token, conversation_id, candidate_id = _start(client, "t021.urgency@example.com")
    empty = client.post(
        "/api/applications",
        json={
            "company_name": "空时间公司",
            "role_title": "前端",
            "status_text": "等待面试",
        },
        headers=_headers(token),
    )
    later = client.post(
        "/api/applications",
        json={
            "company_name": "晚上面",
            "role_title": "后端",
            "status_text": "等待面试",
        },
        headers=_headers(token),
    )
    sooner = client.post(
        "/api/applications",
        json={
            "company_name": "早上面",
            "role_title": "后端",
            "status_text": "等待面试",
        },
        headers=_headers(token),
    )
    rejected = client.post(
        "/api/applications",
        json={
            "company_name": "已挂公司",
            "role_title": "已挂岗位",
            "status_text": "已挂",
        },
        headers=_headers(token),
    )
    assert {empty.status_code, later.status_code, sooner.status_code, rejected.status_code} == {201}
    empty_id = empty.json()["data"]["id"]
    later_id = later.json()["data"]["id"]
    sooner_id = sooner.json()["data"]["id"]
    rejected_id = rejected.json()["data"]["id"]

    filled = client.patch(
        f"/api/applications/{sooner_id}",
        json={"interview_at": "2026-09-20T10:00:00+08:00"},
        headers=_headers(token),
    )
    assert filled.status_code == 200, filled.text
    later_patch = client.patch(
        f"/api/applications/{later_id}",
        json={"interview_at": "2026-09-28T14:00:00+08:00"},
        headers=_headers(token),
    )
    assert later_patch.status_code == 200, later_patch.text
    filled_empty = client.patch(
        f"/api/applications/{empty_id}",
        json={"interview_at": "2026-09-18"},
        headers=_headers(token),
    )
    assert filled_empty.status_code == 200, filled_empty.text
    assert filled_empty.json()["data"]["interview_at"] == "2026-09-18"
    emptied = client.patch(
        f"/api/applications/{empty_id}",
        json={"interview_at": None},
        headers=_headers(token),
    )
    assert emptied.status_code == 200, emptied.text
    assert emptied.json()["data"]["interview_at"] in {None, ""}

    listed = {item["id"]: item for item in _list_apps(client, token)}
    assert listed[sooner_id]["interview_at"] == "2026-09-20T10:00:00+08:00"
    assert listed[later_id]["interview_at"] == "2026-09-28T14:00:00+08:00"
    assert listed[empty_id]["interview_at"] in {None, ""}

    db_path = tmp_env / "offer_coming.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "update applications set deadline=? where id=?",
        ("2026-09-16", empty_id),
    )
    conn.commit()
    conn.close()

    _install_scheduler_models(monkeypatch, urgent_id=sooner_id, urgent_company="早上面")
    with _fake_llm_key():
        _tick(client, 14, 10)

    payload = client.get("/api/question-sets/current", headers=_headers(token)).json()["data"]
    questions = payload["questions"]
    assert len(questions) == 5
    kinds = [item["kind"] for item in questions]
    assert "common" in kinds and "role" in kinds
    role_targets = [
        item["target_application_id"] for item in questions if item["kind"] == "role"
    ]
    assert role_targets
    assert role_targets[0] == sooner_id
    assert empty_id not in role_targets or role_targets[0] != empty_id
    assert rejected_id not in {
        item["target_application_id"] for item in questions
    }

    chat = _messages(client, token, conversation_id)
    q_msgs = [item for item in chat if item["message_type"] == "questions"]
    assert q_msgs
    assert "共性" in q_msgs[-1]["content"]
    assert "岗位" in q_msgs[-1]["content"]
    assert "已挂公司" not in q_msgs[-1]["content"]
    nudges = [item for item in chat if item["message_type"] == "nudge"]
    assert nudges
    latest = nudges[-1]["content"]
    assert "早上面" in latest
    assert latest.find("早上面") < latest.find("空时间公司") or "空时间公司" not in latest
    assert "慢慢" in latest or "不着急" in latest
    for marker in HARSH_NUDGE:
        assert marker not in latest
    _assert_no_jargon(latest, q_msgs[-1]["content"])

    logs = _logs(tmp_env / "offer_coming.db", candidate_id)
    _assert_logs_safe(logs)
    hourly = [item for item in logs if item[2] == "hourly_nudge"]
    assert hourly
    assert {item[0] for item in hourly} == {"nudge"}
    assert {item[1] for item in hourly} == {FIVE_MODELS["nudge"]}
    q_logs = [item for item in logs if item[2] == "questions"]
    assert q_logs
    assert {item[0] for item in q_logs} == {"interview"}
    assert {item[1] for item in q_logs} == {FIVE_MODELS["interview"]}


def test_review_appends_handwritten_summary(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-011：五题答完出点评；面试总结追加手写，不覆盖。"""
    _install_dispatch(monkeypatch)
    token, conversation_id, candidate_id = _start(client, "t021.review@example.com")
    created = client.post(
        "/api/applications",
        json={
            "company_name": "星云科技",
            "role_title": "后端开发",
            "status_text": "等待面试",
            "interview_at": "2026-09-20",
            "interview_summary": HANDWRITTEN,
        },
        headers=_headers(token),
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["data"]["id"]
    _install_scheduler_models(monkeypatch, urgent_id=app_id, urgent_company="星云科技")
    with _fake_llm_key():
        _tick(client, 14, 10)
        events = _post_chat(
            client,
            token,
            conversation_id,
            "第一题：做过 SQLite。第二题：会拆服务。第三题：会讲取舍。第四题：会讲方案。第五题：会讲复盘。",
        )
    assert events[-1][0] == "done"
    visible = _visible_text(events)
    assert "缺点" in visible
    assert "建议" in visible
    assert events[-1][1]["message"]["message_type"] == "review"
    _assert_no_jargon(visible)

    listed = _list_apps(client, token)
    row = next(item for item in listed if item["id"] == app_id)
    assert row["interview_summary"].startswith(HANDWRITTEN)
    assert "缺点" in row["interview_summary"]
    assert row["interview_summary"] != REVIEW_TEXT
    assert HANDWRITTEN in row["interview_summary"]

    logs = _logs(tmp_env / "offer_coming.db", candidate_id)
    _assert_logs_safe(logs)
    review_logs = [item for item in logs if item[2] == "review"]
    assert review_logs
    assert all(item[0] == "interview" and item[1] == FIVE_MODELS["interview"] for item in review_logs)
    assert all(item[1] != FIVE_MODELS["counseling"] for item in review_logs)


def test_mood_better_dispatches_when_orchestrator_returns_menu(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-031：真实主体只回菜单时，编排层仍派 nudge/interview。"""
    _install_dispatch(monkeypatch, MenuOnlyOrchestrator())
    token, conversation_id, candidate_id = _start(client, "t021.menu.better@example.com")
    created = client.post(
        "/api/applications",
        json={
            "company_name": "早上面",
            "role_title": "后端",
            "status_text": "等待面试",
            "interview_at": "2026-09-20",
        },
        headers=_headers(token),
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["data"]["id"]
    _install_scheduler_models(monkeypatch, urgent_id=app_id, urgent_company="早上面")
    with _fake_llm_key():
        _tick(client, 14, 10)
        anxious = _post_chat(client, token, conversation_id, "我很焦虑，今天不想面")
        better = _post_chat(client, token, conversation_id, "心情变好了")

    assert anxious[-1][0] == "done"
    assert anxious[-1][1]["snapshot"]["today"]["counseling_active"] is True
    assert better[-1][0] == "done"
    better_visible = _visible_text(better)
    _assert_no_jargon(better_visible)
    assert better[-1][1]["snapshot"]["today"]["counseling_active"] is False
    assert "今日还没答完" in better_visible or "慢慢" in better_visible or "待办" in better_visible
    assert "生成题" not in better_visible
    for marker in HARSH_NUDGE:
        assert marker not in better_visible

    logs = _logs(tmp_env / "offer_coming.db", candidate_id)
    _assert_logs_safe(logs)
    resumed = [
        item
        for item in logs
        if item[0] == "nudge" and item[2] == "resume_after_counseling"
    ]
    interview_after = [
        item for item in logs if item[0] == "interview" and item[2] in {"questions", "exam_points"}
    ]
    assert resumed or interview_after
    if resumed:
        assert all(item[1] == FIVE_MODELS["nudge"] for item in resumed)
        assert all(item[1] != FIVE_MODELS["counseling"] for item in resumed)


def test_review_when_orchestrator_only_asks_mood(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-011/029：主体只问心情时，编排层仍 save_answers、interview review 并追加总结。"""
    _install_dispatch(monkeypatch, MenuOnlyOrchestrator())
    token, conversation_id, candidate_id = _start(client, "t021.menu.review@example.com")
    created = client.post(
        "/api/applications",
        json={
            "company_name": "星云科技",
            "role_title": "后端开发",
            "status_text": "等待面试",
            "interview_at": "2026-09-20",
            "interview_summary": HANDWRITTEN,
        },
        headers=_headers(token),
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["data"]["id"]
    _install_scheduler_models(monkeypatch, urgent_id=app_id, urgent_company="星云科技")
    with _fake_llm_key():
        _tick(client, 14, 10)
        events = _post_chat(
            client,
            token,
            conversation_id,
            "第一题：做过 SQLite。第二题：会拆服务。第三题：会讲取舍。第四题：会讲方案。第五题：会讲复盘。",
        )
    assert events[-1][0] == "done"
    visible = _visible_text(events)
    assert "缺点" in visible
    assert "建议" in visible
    assert "先确认" not in visible
    assert events[-1][1]["message"]["message_type"] == "review"
    _assert_no_jargon(visible)

    current = client.get("/api/question-sets/current", headers=_headers(token))
    assert current.status_code == 200, current.text
    questions = current.json()["data"]["questions"]
    assert len(questions) == 5
    assert all((item.get("answer") or "").strip() for item in questions)

    listed = _list_apps(client, token)
    row = next(item for item in listed if item["id"] == app_id)
    assert row["interview_summary"].startswith(HANDWRITTEN)
    assert "缺点" in row["interview_summary"]
    assert HANDWRITTEN in row["interview_summary"]

    logs = _logs(tmp_env / "offer_coming.db", candidate_id)
    _assert_logs_safe(logs)
    review_logs = [item for item in logs if item[2] == "review"]
    assert review_logs
    assert all(item[0] == "interview" and item[1] == FIVE_MODELS["interview"] for item in review_logs)
    assert all(item[1] != FIVE_MODELS["counseling"] for item in review_logs)
