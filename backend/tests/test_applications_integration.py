"""T-019：投递表与对话写表隔离集成测试。

真实 TestClient 打 API-007/009/010/011 与 API-006 写表；隔离库；llm_api_key 置空，不打百炼。
不把 frontend mocks 当主路径。AC-003/026 模型表述标未验，只核写库与快照。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from src.config.settings import get_settings
from src.db.models import Candidate
from src.db.session import get_db_context
from src.services.agent import AgentService, _assistant_text, _last_tool, _tool_call
from src.services.knowledge_search import KnowledgeSearchService
from src.services.llm_client import LlmClient
from src.services.scheduler import reset_clock, set_clock, set_scheduler_loop_enabled
from src.services.tools import ToolExecutor
from src.utils.crypto import beijing_today

TZ = ZoneInfo("Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TABLE_FIELDS = (
    "company_name",
    "role_title",
    "jd_url",
    "applied_at",
    "interview_at",
    "status_text",
    "interview_summary",
)
READABLE_JD_URL = "https://example.com/jobs/readable-t019"
FAILED_JD_URL = "https://127.0.0.1/jobs/unreadable-t019"
RESUME_TEXT = "刘文韬，Python 后端。做过校园二手书交易平台 BookSwap。"
REAL_PATH_EXAM_POINTS = (
    "对照你的简历里 BookSwap 项目，这个岗位会问 Python 落地与 Agent 编排，而不是只复述 JD。"
)


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


def _list_apps(client: TestClient, token: str) -> list[dict[str, Any]]:
    response = client.get("/api/applications", headers=_headers(token))
    assert response.status_code == 200, response.text
    apps = response.json()["data"]["applications"]
    assert isinstance(apps, list)
    return apps


def _assert_table_fields(item: dict[str, Any]) -> None:
    for key in TABLE_FIELDS:
        assert key in item
    assert "knowledge_review_pending" not in item


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


def _incomplete_failed_url_rows(apps: list[dict[str, Any]], url: str) -> list[dict[str, Any]]:
    return [
        item
        for item in apps
        if (item.get("jd_url") or "") == url
        and not (item.get("company_name") or "").strip()
        and not (item.get("role_title") or "").strip()
        and not (item.get("exam_points") or "").strip()
    ]


async def _snapshot_apps(candidate_id: str) -> list[dict[str, Any]]:
    async with get_db_context() as db:
        candidate = (
            await db.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        tools = ToolExecutor(db, candidate)
        snapshot = await tools.get_snapshot({})
    apps = snapshot.get("applications") or []
    assert isinstance(apps, list)
    return apps


def _tick(client: TestClient, day: int, hour: int) -> None:
    current = datetime(2026, 9, day, hour, 0, 0, tzinfo=TZ)
    set_clock(lambda value=current: value)
    response = client.post("/internal/scheduler/tick", params={"at": current.isoformat()})
    assert response.status_code == 200, response.text


def test_env_example_keeps_real_api_as_main_path() -> None:
    text = (PROJECT_ROOT / "frontend" / ".env.example").read_text(encoding="utf-8")
    assert "VITE_USE_MOCK=false" in text
    assert "VITE_API_BASE_URL=/api" in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("VITE_USE_MOCK="):
            assert stripped == "VITE_USE_MOCK=false"


def test_unauthenticated_cannot_read_table(client: TestClient) -> None:
    response = client.get("/api/applications")
    assert response.status_code == 401


def test_add_patch_list_delete_and_default_applied_at(client: TestClient) -> None:
    token, _, _ = _start(client, "t019.crud@example.com")
    created = client.post(
        "/api/applications",
        json={"company_name": "星云科技", "role_title": "后端开发"},
        headers=_headers(token),
    )
    assert created.status_code == 201, created.text
    row = created.json()["data"]
    _assert_table_fields(row)
    assert row["applied_at"] == beijing_today()
    assert row["interview_at"] is None
    app_id = row["id"]

    patched = client.patch(
        f"/api/applications/{app_id}",
        json={
            "company_name": "星云科技改",
            "status_text": "等待面试",
            "interview_summary": "手写总结",
            "interview_at": "2026-09-20",
        },
        headers=_headers(token),
    )
    assert patched.status_code == 200, patched.text
    listed = _list_apps(client, token)
    assert len(listed) == 1
    current = listed[0]
    _assert_table_fields(current)
    assert current["company_name"] == "星云科技改"
    assert current["status_text"] == "等待面试"
    assert current["normalized_status"] == "waiting_interview"
    assert current["interview_summary"] == "手写总结"
    assert current["interview_at"] == "2026-09-20"
    assert current["deadline"] == "2026-09-20"
    assert current["applied_at"] == beijing_today()

    deleted = client.delete(f"/api/applications/{app_id}", headers=_headers(token))
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"id": app_id, "deleted": True}
    assert _list_apps(client, token) == []


@pytest.mark.asyncio
async def test_hand_edit_is_visible_to_conversation_snapshot(client: TestClient) -> None:
    token, _, candidate_id = _start(client, "t019.snapshot@example.com")
    created = client.post(
        "/api/applications",
        json={"company_name": "青禾", "role_title": "渠道增长", "status_text": "已投"},
        headers=_headers(token),
    )
    assert created.status_code == 201
    app_id = created.json()["data"]["id"]
    patched = client.patch(
        f"/api/applications/{app_id}",
        json={"company_name": "青禾手改", "status_text": "等待面试", "progress_text": "已约下周一面"},
        headers=_headers(token),
    )
    assert patched.status_code == 200

    listed = _list_apps(client, token)
    snap = await _snapshot_apps(candidate_id)
    assert len(listed) == 1
    assert len(snap) == 1
    assert snap[0]["id"] == listed[0]["id"] == app_id
    assert snap[0]["company_name"] == listed[0]["company_name"] == "青禾手改"
    assert snap[0]["status_text"] == listed[0]["status_text"] == "等待面试"
    assert snap[0]["progress_text"] == listed[0]["progress_text"] == "已约下周一面"


def test_same_jd_url_conflicts_on_create(client: TestClient) -> None:
    token, _, _ = _start(client, "t019.dup@example.com")
    url = "https://example.com/job/t019-dup"
    first = client.post(
        "/api/applications",
        json={"jd_url": url, "company_name": "甲", "role_title": "后端"},
        headers=_headers(token),
    )
    assert first.status_code == 201
    second = client.post(
        "/api/applications",
        json={"jd_url": url, "company_name": "乙", "role_title": "前端"},
        headers=_headers(token),
    )
    assert second.status_code == 409
    assert second.json()["error_code"] == "CONFLICT"
    listed = _list_apps(client, token)
    assert len(listed) == 1
    assert listed[0]["id"] == first.json()["data"]["id"]
    assert listed[0]["company_name"] == "甲"


@pytest.mark.asyncio
async def test_same_jd_url_updates_existing_row_via_tool(client: TestClient) -> None:
    token, _, candidate_id = _start(client, "t019.reuse@example.com")
    url = "https://example.com/job/t019-reuse"
    created = client.post(
        "/api/applications",
        json={"jd_url": url, "company_name": "原公司", "role_title": "原岗位"},
        headers=_headers(token),
    )
    assert created.status_code == 201
    app_id = created.json()["data"]["id"]

    async with get_db_context() as db:
        candidate = (
            await db.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        tools = ToolExecutor(db, candidate)
        recorded = await tools.record_application(
            {
                "jd_url": url,
                "company_name": "更新公司",
                "role_title": "更新岗位",
                "jd_text": "JD 正文",
                "exam_points": "对照简历的考查点",
            }
        )
        assert recorded["ok"] is True
        assert recorded["created"] is False
        assert recorded["application_id"] == app_id
        saved = await tools.save_exam_points(
            {"application_id": app_id, "exam_points": "对照简历的考查点"}
        )
        assert saved["ok"] is True

    listed = _list_apps(client, token)
    assert len(listed) == 1
    assert listed[0]["id"] == app_id
    assert listed[0]["company_name"] == "更新公司"
    assert listed[0]["role_title"] == "更新岗位"
    assert listed[0]["exam_points"] == "对照简历的考查点"
    assert listed[0]["applied_at"] == beijing_today()


@pytest.mark.asyncio
async def test_record_application_rejects_url_only_row(client: TestClient) -> None:
    _token, _, candidate_id = _start(client, "t019.urlonly@example.com")
    async with get_db_context() as db:
        candidate = (
            await db.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        tools = ToolExecutor(db, candidate)
        result = await tools.record_application({"jd_url": FAILED_JD_URL})
        assert result["ok"] is False
    listed = _list_apps(client, _token)
    assert _incomplete_failed_url_rows(listed, FAILED_JD_URL) == []
    assert listed == []


def test_unreadable_jd_conversation_does_not_write_complete_row(
    client: TestClient,
) -> None:
    token, conversation_id, _ = _start(client, "t019.failjd@example.com")
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": f"请看这个岗位 {FAILED_JD_URL}"},
        headers=_headers(token, sse=True),
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(response.text)
    assert events
    notice = " ".join(
        str(payload.get("error") or payload.get("message", {}).get("content") or "")
        for _name, payload in events
    )
    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    rows = messages.json()["data"]["messages"]
    error_rows = [item for item in rows if item["message_type"] == "error_notice"]
    assert error_rows, "失败路径应落库 error_notice"
    combined = f"{notice}\n{error_rows[-1]['content']}"
    assert "链接" in combined
    assert any(marker in combined for marker in ("换", "粘贴", "正文", "JD"))
    listed = _list_apps(client, token)
    assert _incomplete_failed_url_rows(listed, FAILED_JD_URL) == []
    assert not any((item.get("jd_url") or "") == FAILED_JD_URL for item in listed)


def test_readable_jd_conversation_writes_row_without_paid_llm(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_jd(url: str, settings=None) -> dict[str, Any]:
        assert url == READABLE_JD_URL
        return {"ok": True, "text": "Python 后端，熟悉 FastAPI 与 SQLite。", "error": None}

    monkeypatch.setattr("src.services.tools.fetch_jd", fake_fetch_jd)
    token, conversation_id, _ = _start(client, "t019.okjd@example.com")
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": f"我投了这个岗位，链接是 {READABLE_JD_URL}"},
        headers=_headers(token, sse=True),
    )
    assert response.status_code == 200, response.text
    events = _sse_events(response.text)
    assert events[-1][0] == "done"
    listed = _list_apps(client, token)
    assert len(listed) == 1
    row = listed[0]
    _assert_table_fields(row)
    assert row["jd_url"] == READABLE_JD_URL
    assert row["company_name"]
    assert row["role_title"]
    assert row["applied_at"] == beijing_today()
    assert (row.get("exam_points") or "").strip()
    assert "[Mock]" not in (row.get("company_name") or "")
    assert "[Mock]" not in (row.get("exam_points") or "")


def test_real_orchestrator_order_saves_exam_points_before_record_and_search(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有 Key 走真实分派：interview 只回考查点 reply，随后 record+search 不传 exam_points。"""

    async def fake_fetch_jd(url: str, settings=None) -> dict[str, Any]:
        assert url == READABLE_JD_URL
        return {"ok": True, "text": "Evaboot 招聘 Agentic Python Engineer。", "error": None}

    async def fake_search(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "items": [],
            "fail_reason": "这次没找到可核对的面经，你可以在对话里发文件或粘贴。",
        }

    async def forbid_bailian(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("测试禁止真实百炼调用")

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
        last = messages[-1]
        if role == "orchestrator":
            if last.get("role") == "user":
                return _tool_call(
                    "call_interview_agent",
                    {
                        "task": "exam_points",
                        "url": READABLE_JD_URL,
                        "user_text": str(last.get("content") or ""),
                    },
                )
            name, observation = _last_tool(messages)
            if name == "call_interview_agent":
                return _tool_call(
                    "call_nudge_agent",
                    {
                        "task": "record",
                        "jd_url": READABLE_JD_URL,
                        "company_name": "Evaboot",
                        "role_title": "Agentic Python Engineer",
                    },
                )
            if name == "call_nudge_agent":
                return _tool_call(
                    "call_knowledge_agent",
                    {
                        "task": "search",
                        "application_id": str(observation.get("application_id") or ""),
                    },
                )
            return _assistant_text(
                "成功记录投递。这次没找到可核对的面经，你可以在对话里发文件或粘贴。"
            )
        if role == "interview":
            if last.get("role") == "user":
                payload = json.loads(str(last.get("content") or "{}"))
                return _tool_call(
                    "fetch_jd",
                    {"url": str(payload.get("url") or READABLE_JD_URL)},
                )
            return _assistant_text(REAL_PATH_EXAM_POINTS)
        if role == "nudge":
            if last.get("role") == "user":
                payload = json.loads(str(last.get("content") or "{}"))
                return _tool_call(
                    "record_application",
                    {
                        "jd_url": str(payload.get("jd_url") or READABLE_JD_URL),
                        "company_name": str(payload.get("company_name") or "Evaboot"),
                        "role_title": str(payload.get("role_title") or "Agentic Python Engineer"),
                    },
                )
            return _assistant_text("已记下投递")
        if role == "knowledge":
            if last.get("role") == "user":
                payload = json.loads(str(last.get("content") or "{}"))
                return _tool_call(
                    "search_public_experiences",
                    {"application_id": str(payload.get("application_id") or "")},
                )
            return _assistant_text("这次没找到可核对的面经，你可以在对话里发文件或粘贴。")
        return _assistant_text("我在。")

    monkeypatch.setattr("src.services.tools.fetch_jd", fake_fetch_jd)
    monkeypatch.setattr(KnowledgeSearchService, "search_public_experiences", fake_search)
    monkeypatch.setattr(LlmClient, "chat_completions", forbid_bailian)
    monkeypatch.setattr(AgentService, "_complete", fake_complete)

    settings = get_settings()
    object.__setattr__(settings, "llm_api_key", "test-key-not-real")
    try:
        token, conversation_id, _ = _start(client, "t019.realpath@example.com")
        response = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={
                "content": (
                    "我投了 Evaboot 的 Agentic Python Engineer，"
                    f"链接是 {READABLE_JD_URL}，请对照我的简历总结考查点。"
                )
            },
            headers=_headers(token, sse=True),
        )
    finally:
        object.__setattr__(settings, "llm_api_key", "")

    assert response.status_code == 200, response.text
    events = _sse_events(response.text)
    assert events[-1][0] == "done"
    done = events[-1][1]
    message = done["message"]
    content = str(message.get("content") or "")
    exam_at = content.find(REAL_PATH_EXAM_POINTS)
    record_at = content.find("成功记录")
    kb_at = content.find("面经")
    assert exam_at != -1, content
    assert record_at == -1 or exam_at < record_at
    assert kb_at == -1 or exam_at < kb_at
    assert message["message_type"] == "jd_summary"
    listed = _list_apps(client, token)
    assert len(listed) == 1
    row = listed[0]
    assert row["company_name"] == "Evaboot"
    assert row["role_title"] == "Agentic Python Engineer"
    assert row["jd_url"] == READABLE_JD_URL
    assert row["applied_at"] == beijing_today()
    assert row["exam_points"] == REAL_PATH_EXAM_POINTS


def test_updated_row_drives_nudge_and_deleted_row_leaves_questions(
    client: TestClient,
) -> None:
    token, conversation_id, _ = _start(client, "t019.nudge@example.com")
    keep = client.post(
        "/api/applications",
        json={
            "company_name": "星云科技",
            "role_title": "后端开发",
            "status_text": "等待面试",
            "progress_text": "初筛中",
            "interview_at": "2026-10-30",
        },
        headers=_headers(token),
    )
    drop = client.post(
        "/api/applications",
        json={
            "company_name": "待删公司",
            "role_title": "待删岗位",
            "status_text": "等待面试",
        },
        headers=_headers(token),
    )
    assert keep.status_code == 201
    assert drop.status_code == 201
    keep_id = keep.json()["data"]["id"]
    drop_id = drop.json()["data"]["id"]

    patched = client.patch(
        f"/api/applications/{keep_id}",
        json={
            "progress_text": "已约下周一面",
            "interview_at": "2026-09-20",
            "status_text": "等待面试",
        },
        headers=_headers(token),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["deadline"] == "2026-09-20"

    deleted = client.delete(f"/api/applications/{drop_id}", headers=_headers(token))
    assert deleted.status_code == 200
    remaining = _list_apps(client, token)
    assert [item["id"] for item in remaining] == [keep_id]
    assert remaining[0]["progress_text"] == "已约下周一面"
    assert remaining[0]["interview_at"] == "2026-09-20"

    _tick(client, 14, 10)
    questions = client.get("/api/question-sets/current", headers=_headers(token))
    assert questions.status_code == 200
    payload = questions.json()["data"]
    targets = {item.get("target_application_id") for item in payload["questions"]}
    assert drop_id not in targets
    assert keep_id in targets or any(
        "星云科技" in (item.get("prompt") or "") for item in payload["questions"]
    )

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    assert messages.status_code == 200
    nudges = [
        item["content"]
        for item in messages.json()["data"]["messages"]
        if item["message_type"] == "nudge"
    ]
    assert nudges
    latest = nudges[-1]
    assert "已约下周一面" in latest
    assert "2026-09-20" in latest
    assert "待删公司" not in latest
