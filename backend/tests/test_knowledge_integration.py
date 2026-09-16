"""T-020：知识库与我的面经隔离集成测试。

真实 TestClient 打 API-006/012/013/014 与 API-010；隔离库；llm_api_key 置空，不打百炼。
检索成功走测试双，不把双模拟写成 AC-037 真实 enable_search 通过。
不把 frontend mocks 当主路径。页面版式、D-006-A 跳转、真实联网搜索由 Tester 验收。
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
from src.models.knowledge import KNOWLEDGE_EXCERPT_CHARS
from src.services import knowledge_store as store_mod
from src.services import questions as questions_mod
from src.services.agent import AgentService, _assistant_text, _last_tool, _tool_call
from src.services.knowledge_search import KnowledgeSearchService, host_is_blocked
from src.services.llm_client import LlmClient
from src.services.scheduler import reset_clock, set_clock, set_scheduler_loop_enabled
from src.services.tools import INTERVIEW_TOOL_NAMES, USER_INGEST_NOTICE, ToolExecutor

PROJECT_ROOT = Path(__file__).resolve().parents[2]
READABLE_JD_URL = "https://example.com/jobs/readable-t020"
RESUME_TEXT = "刘文韬，Python 后端。做过校园二手书交易平台 BookSwap。"
EXAM_POINTS = (
    "对照你的简历里 BookSwap 项目，这个岗位会问 Python 落地与缓存取舍，而不是只复述 JD。"
)
SEARCH_TITLE = "星河渠道公开面经"
SEARCH_BODY = "公开检索面经：幂等键与缓存穿透怎么答。"
FILE_BODY = "对话文件面经：事务隔离级别和索引选择。"
QUESTION_FINGERPRINT = "布谷鸟过滤器幂等键"
QUESTION_KNOWLEDGE_BODY = f"T020FIX-{QUESTION_FINGERPRINT}：出题要追问幂等键怎么设计。"
KEEP_BODY = "通用项目难点：怎么讲缓存穿透的取舍。"
DEL_BODY = "只针对星河渠道的激活指标口径。"
SUCCESS_MARKERS = ("已收入", "我的面经")
FAKE_FOUND_TITLE = "已找到面经"


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
        if name == "delta":
            parts.append(str(payload.get("text") or ""))
        elif name == "done":
            parts.append(str((payload.get("message") or {}).get("content") or ""))
        elif name == "error":
            parts.append(str(payload.get("error") or ""))
    return "\n".join(parts)


def _list_kb(client: TestClient, token: str, **params: str) -> list[dict[str, Any]]:
    response = client.get(
        "/api/knowledge-items",
        params=params or None,
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert isinstance(items, list)
    return items


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


def _detail(client: TestClient, token: str, item_id: str) -> dict[str, Any]:
    response = client.get(f"/api/knowledge-items/{item_id}", headers=_headers(token))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _assert_no_xhs_get(seen: list[str]) -> None:
    for item in seen:
        lowered = item.lower()
        assert "xiaohongshu.com" not in lowered
        assert "xhslink.com" not in lowered
        if item.upper().startswith("GET "):
            assert "xiaohongshu" not in lowered
            assert "xhslink" not in lowered


def _patch_readable_jd(monkeypatch: pytest.MonkeyPatch, seen: list[str]) -> None:
    async def fake_fetch_jd(url: str, settings=None) -> dict[str, Any]:
        seen.append(f"FETCH {url}")
        assert "xiaohongshu.com" not in url
        assert "xhslink.com" not in url
        assert url == READABLE_JD_URL
        return {
            "ok": True,
            "text": "星河科技招聘渠道增长，熟悉指标口径与 Python。",
            "error": None,
        }

    monkeypatch.setattr("src.services.tools.fetch_jd", fake_fetch_jd)


async def _forbid_bailian(self, **kwargs: Any) -> dict[str, Any]:
    raise AssertionError("测试禁止真实百炼调用")


def _post_chat(
    client: TestClient,
    token: str,
    conversation_id: str,
    *,
    content: str | None = None,
    upload: tuple[str, bytes, str] | None = None,
) -> tuple[Any, list[tuple[str, dict[str, Any]]]]:
    url = f"/api/conversations/{conversation_id}/messages"
    headers = _headers(token, sse=True)
    if upload is not None:
        data = {"content": content or ""}
        response = client.post(
            url,
            data=data,
            files={"file": upload},
            headers=headers,
        )
    else:
        response = client.post(url, json={"content": content or ""}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return response, _sse_events(response.text)


def test_env_example_keeps_real_api_as_main_path() -> None:
    text = (PROJECT_ROOT / "frontend" / ".env.example").read_text(encoding="utf-8")
    assert "VITE_USE_MOCK=false" in text
    assert "VITE_API_BASE_URL=/api" in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("VITE_USE_MOCK="):
            assert stripped == "VITE_USE_MOCK=false"


def test_empty_list_and_unauthenticated(client: TestClient) -> None:
    listed = client.get("/api/knowledge-items")
    assert listed.status_code == 401
    assert listed.json()["error_code"] == "UNAUTHORIZED"
    detail = client.get("/api/knowledge-items/k_any")
    assert detail.status_code == 401
    deleted = client.delete("/api/knowledge-items/k_any")
    assert deleted.status_code == 401

    token, _, _ = _start(client, "t020.empty@example.com")
    items = _list_kb(client, token)
    assert items == []


def test_search_failure_keeps_exam_points_row_and_no_fake_item(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    _patch_readable_jd(monkeypatch, seen)
    monkeypatch.setattr(LlmClient, "chat_completions", _forbid_bailian)

    token, conversation_id, _ = _start(client, "t020.failsearch@example.com")
    _response, events = _post_chat(
        client,
        token,
        conversation_id,
        content=f"我投了这个岗位，链接是 {READABLE_JD_URL}",
    )
    assert events[-1][0] == "done"
    visible = _visible_text(events)
    exam_at = visible.find("对照")
    fail_at = visible.find("发文件")
    if fail_at < 0:
        fail_at = visible.find("粘贴")
    assert exam_at != -1, visible
    assert fail_at != -1, visible
    assert exam_at < fail_at
    assert "已收入" not in visible
    assert FAKE_FOUND_TITLE not in visible

    listed = _list_apps(client, token)
    assert len(listed) == 1
    row = listed[0]
    assert row["jd_url"] == READABLE_JD_URL
    assert (row.get("company_name") or "").strip()
    assert (row.get("role_title") or "").strip()
    assert (row.get("exam_points") or "").strip()

    items = _list_kb(client, token)
    titles = {item["title"] for item in items}
    assert FAKE_FOUND_TITLE not in titles
    assert items == []
    _assert_no_xhs_get(seen)
    assert host_is_blocked("https://www.xiaohongshu.com/explore/1")


def test_search_success_test_double_ingests_notice_and_list(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """测试双成功入库。AC-037 真实 enable_search 由 Tester 验收，本测试不视为真实通过。"""

    seen: list[str] = []
    search_calls: list[dict[str, Any]] = []

    async def fake_search(self, **kwargs: Any) -> dict[str, Any]:
        search_calls.append(kwargs)
        return {
            "ok": True,
            "items": [
                {
                    "title": SEARCH_TITLE,
                    "body": SEARCH_BODY,
                    "source_hint": "博客",
                }
            ],
            "fail_reason": None,
            "mocked": True,
        }

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
                        "company_name": "星河科技",
                        "role_title": "渠道增长",
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
                f"{EXAM_POINTS}\n\n已收入星河科技 渠道增长相关面经，可在「我的面经」查看。"
            )
        if role == "interview":
            if last.get("role") == "user":
                payload = json.loads(str(last.get("content") or "{}"))
                return _tool_call(
                    "fetch_jd",
                    {"url": str(payload.get("url") or READABLE_JD_URL)},
                )
            return _assistant_text(EXAM_POINTS)
        if role == "nudge":
            if last.get("role") == "user":
                payload = json.loads(str(last.get("content") or "{}"))
                return _tool_call(
                    "record_application",
                    {
                        "jd_url": str(payload.get("jd_url") or READABLE_JD_URL),
                        "company_name": str(payload.get("company_name") or "星河科技"),
                        "role_title": str(payload.get("role_title") or "渠道增长"),
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
            return _assistant_text(
                "已收入星河科技 渠道增长相关面经，可在「我的面经」查看。"
            )
        return _assistant_text("我在。")

    _patch_readable_jd(monkeypatch, seen)
    monkeypatch.setattr(KnowledgeSearchService, "search_public_experiences", fake_search)
    monkeypatch.setattr(LlmClient, "chat_completions", _forbid_bailian)
    monkeypatch.setattr(AgentService, "_complete", fake_complete)

    settings = get_settings()
    object.__setattr__(settings, "llm_api_key", "test-key-not-real")
    try:
        token, conversation_id, _ = _start(client, "t020.oksearch@example.com")
        _response, events = _post_chat(
            client,
            token,
            conversation_id,
            content=f"我投了星河科技渠道增长，链接是 {READABLE_JD_URL}",
        )
    finally:
        object.__setattr__(settings, "llm_api_key", "")

    assert events[-1][0] == "done"
    done = events[-1][1]
    content = str((done.get("message") or {}).get("content") or "")
    exam_at = content.find(EXAM_POINTS)
    kb_at = content.find("已收入")
    assert exam_at != -1, content
    assert kb_at != -1, content
    assert exam_at < kb_at
    assert all(marker in content for marker in SUCCESS_MARKERS)
    assert search_calls, "测试双须实际走到检索成功路径"
    assert search_calls[0]["company_name"] == "星河科技"
    assert search_calls[0]["role_title"] == "渠道增长"

    listed = _list_apps(client, token)
    assert len(listed) == 1
    row = listed[0]
    assert row["company_name"] == "星河科技"
    assert row["role_title"] == "渠道增长"
    assert row["exam_points"] == EXAM_POINTS

    items = _list_kb(client, token)
    assert len(items) == 1
    item = items[0]
    assert item["title"] == SEARCH_TITLE
    assert item["source_type"] == "search"
    assert item["application_id"] == row["id"]
    assert SEARCH_BODY[:KNOWLEDGE_EXCERPT_CHARS] in item["excerpt"]
    assert FAKE_FOUND_TITLE not in item["title"]
    detail = _detail(client, token, item["id"])
    assert SEARCH_BODY in detail["body"]
    _assert_no_xhs_get(seen)


def test_conversation_file_ingest_lists_body_then_delete_drops_excerpts(
    client: TestClient,
) -> None:
    token, conversation_id, _candidate_id = _start(client, "t020.ingest@example.com")
    _response, events = _post_chat(
        client,
        token,
        conversation_id,
        content="帮我收一下这份面经",
        upload=("exp.txt", FILE_BODY.encode("utf-8"), "text/plain"),
    )
    assert events[-1][0] == "done"
    visible = _visible_text(events)
    assert "已收入" in visible
    assert "正在收录面经" in [
        str(payload.get("text") or "") for name, payload in events if name == "status"
    ]
    assert "已收入面经" not in visible or "可在「我的面经」查看" in visible

    items = _list_kb(client, token)
    assert len(items) == 1
    item = items[0]
    for key in (
        "id",
        "title",
        "source_type",
        "excerpt",
        "application_id",
        "company_name",
        "role_title",
    ):
        assert key in item
    assert "body" not in item
    assert item["source_type"] == "upload"
    assert FILE_BODY[:KNOWLEDGE_EXCERPT_CHARS] in item["excerpt"]
    detail = _detail(client, token, item["id"])
    assert FILE_BODY in detail["body"]
    assert detail["segments"]

    keep = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": f"再贴一份面经：{KEEP_BODY}"},
        headers=_headers(token, sse=True),
    )
    assert keep.status_code == 200, keep.text
    listed = _list_kb(client, token)
    assert len(listed) == 2
    keep_item = next(row for row in listed if KEEP_BODY[:20] in row["excerpt"])
    file_item = next(row for row in listed if FILE_BODY[:20] in row["excerpt"])

    deleted = client.delete(
        f"/api/knowledge-items/{file_item['id']}",
        headers=_headers(token),
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"] == {"id": file_item["id"], "deleted": True}
    remaining = _list_kb(client, token)
    remaining_ids = {row["id"] for row in remaining}
    assert file_item["id"] not in remaining_ids
    assert keep_item["id"] in remaining_ids
    missing = client.get(
        f"/api/knowledge-items/{file_item['id']}",
        headers=_headers(token),
    )
    assert missing.status_code == 404

    refreshed = _list_kb(client, token)
    assert {row["id"] for row in refreshed} == remaining_ids


def _knowledge_text_only_complete(exam_first: bool = False):
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
                if exam_first:
                    return _tool_call(
                        "call_interview_agent",
                        {"task": "exam_points", "user_text": str(last.get("content") or "")},
                    )
                return _tool_call("call_knowledge_agent", {"task": "ingest"})
            name, _observation = _last_tool(messages)
            if name == "call_interview_agent":
                return _tool_call("call_knowledge_agent", {"task": "ingest"})
            return _assistant_text("已收录面经")
        if role == "interview":
            return _assistant_text(EXAM_POINTS)
        if role == "knowledge":
            return _assistant_text("已收录面经")
        return _assistant_text("我在。")

    return fake_complete


def test_knowledge_text_only_ingest_is_persisted_and_listed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有 Key 时 knowledge 只回「已收录」、不调 ingest，编排层补写库。禁止真实百炼。"""

    monkeypatch.setattr(LlmClient, "chat_completions", _forbid_bailian)
    monkeypatch.setattr(AgentService, "_complete", _knowledge_text_only_complete())
    settings = get_settings()
    object.__setattr__(settings, "llm_api_key", "test-key-not-real")
    try:
        token, conversation_id, _ = _start(client, "t020.textonly@example.com")
        _response, events = _post_chat(
            client,
            token,
            conversation_id,
            content="帮我收一下这份面经",
            upload=("exp.txt", FILE_BODY.encode("utf-8"), "text/plain"),
        )
    finally:
        object.__setattr__(settings, "llm_api_key", "")

    assert events[-1][0] == "done"
    visible = _visible_text(events)
    assert USER_INGEST_NOTICE in visible
    assert "正在收录面经" in [
        str(payload.get("text") or "") for name, payload in events if name == "status"
    ]
    items = _list_kb(client, token)
    assert len(items) == 1
    item = items[0]
    assert item["source_type"] == "upload"
    assert FILE_BODY[:KNOWLEDGE_EXCERPT_CHARS] in item["excerpt"]
    detail = _detail(client, token, item["id"])
    assert FILE_BODY in detail["body"]

    object.__setattr__(settings, "llm_api_key", "test-key-not-real")
    try:
        paste = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": f"再贴一份面经：{KEEP_BODY}"},
            headers=_headers(token, sse=True),
        )
    finally:
        object.__setattr__(settings, "llm_api_key", "")
    assert paste.status_code == 200, paste.text
    paste_visible = _visible_text(_sse_events(paste.text))
    assert USER_INGEST_NOTICE in paste_visible
    listed = _list_kb(client, token)
    assert len(listed) == 2
    assert any(KEEP_BODY[:20] in row["excerpt"] for row in listed)
    assert any(FILE_BODY[:20] in row["excerpt"] for row in listed)


def test_knowledge_text_only_ingest_keeps_exam_points_before_notice(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(LlmClient, "chat_completions", _forbid_bailian)
    monkeypatch.setattr(
        AgentService, "_complete", _knowledge_text_only_complete(exam_first=True)
    )
    settings = get_settings()
    object.__setattr__(settings, "llm_api_key", "test-key-not-real")
    try:
        token, conversation_id, _ = _start(client, "t020.examfirst@example.com")
        _response, events = _post_chat(
            client,
            token,
            conversation_id,
            content="帮我收一下这份面经",
            upload=("exp.txt", FILE_BODY.encode("utf-8"), "text/plain"),
        )
    finally:
        object.__setattr__(settings, "llm_api_key", "")

    assert events[-1][0] == "done"
    content = str((events[-1][1].get("message") or {}).get("content") or "")
    exam_at = content.find(EXAM_POINTS)
    kb_at = content.find("已收入")
    assert exam_at != -1, content
    assert kb_at != -1, content
    assert exam_at < kb_at
    assert USER_INGEST_NOTICE in content
    items = _list_kb(client, token)
    assert len(items) == 1
    assert FILE_BODY[:KNOWLEDGE_EXCERPT_CHARS] in items[0]["excerpt"]


@pytest.mark.asyncio
async def test_knowledge_excerpts_are_question_input_and_drop_after_delete(
    client: TestClient,
) -> None:
    token, _conversation_id, candidate_id = _start(client, "t020.excerpts@example.com")
    created = client.post(
        "/api/applications",
        json={
            "company_name": "星河科技",
            "role_title": "渠道增长",
            "status_text": "等待面试",
        },
        headers=_headers(token),
    )
    assert created.status_code == 201
    app_id = created.json()["data"]["id"]

    async with get_db_context() as db:
        candidate = (
            await db.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        tools = ToolExecutor(db, candidate)
        dropped = await tools.ingest_user_document(
            {
                "title": SEARCH_TITLE,
                "body": SEARCH_BODY,
                "source_type": "search",
                "application_id": app_id,
            }
        )
        kept = await tools.ingest_user_document(
            {
                "title": "通用项目面经",
                "body": KEEP_BODY,
                "source_type": "paste",
                "application_id": app_id,
            }
        )
        assert dropped["ok"] is True
        assert kept["ok"] is True
        snapshot = await tools.get_snapshot({})
        excerpts = await tools.get_knowledge_excerpts({"application_id": app_id})

    drop_id = str(dropped["knowledge_item_id"])
    keep_id = str(kept["knowledge_item_id"])
    titles = {row["id"] for row in snapshot.get("knowledge_titles") or []}
    assert {drop_id, keep_id} <= titles
    joined = json.dumps(excerpts, ensure_ascii=False)
    assert SEARCH_BODY in joined
    assert KEEP_BODY in joined
    assert "get_knowledge_excerpts" in INTERVIEW_TOOL_NAMES
    assert excerpts["ok"] is True

    deleted = client.delete(f"/api/knowledge-items/{drop_id}", headers=_headers(token))
    assert deleted.status_code == 200

    async with get_db_context() as db:
        candidate = (
            await db.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        tools = ToolExecutor(db, candidate)
        after = await tools.get_knowledge_excerpts({"application_id": app_id})
        after_snap = await tools.get_snapshot({})

    after_text = json.dumps(after, ensure_ascii=False)
    after_titles = {row["id"] for row in after_snap.get("knowledge_titles") or []}
    assert SEARCH_BODY not in after_text
    assert drop_id not in after_titles
    assert KEEP_BODY in after_text
    assert keep_id in after_titles


async def _seed_related_knowledge(
    candidate_id: str, application_id: str
) -> tuple[str, str]:
    async with get_db_context() as db:
        candidate = (
            await db.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        tools = ToolExecutor(db, candidate)
        keep_row = await tools.ingest_user_document(
            {
                "title": "通用项目面经",
                "body": KEEP_BODY,
                "source_type": "paste",
                "application_id": application_id,
            }
        )
        del_row = await tools.ingest_user_document(
            {
                "title": "只针对该岗",
                "body": DEL_BODY,
                "source_type": "paste",
                "application_id": application_id,
            }
        )
        assert keep_row["ok"] is True
        assert del_row["ok"] is True
        return str(keep_row["knowledge_item_id"]), str(del_row["knowledge_item_id"])


@pytest.mark.asyncio
async def test_rejected_asks_then_conversation_accepted_ids_and_keep_all(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, conversation_id, candidate_id = _start(client, "t020.ask@example.com")
    created = client.post(
        "/api/applications",
        json={"company_name": "已挂公司", "role_title": "后端", "status_text": "已投"},
        headers=_headers(token),
    )
    assert created.status_code == 201
    app_id = created.json()["data"]["id"]
    keep_id, del_id = await _seed_related_knowledge(candidate_id, app_id)
    before = _list_kb(client, token)
    assert len(before) == 2
    del_detail = _detail(client, token, del_id)
    del_seg = del_detail["segments"][0]["id"]

    async def fake_eval_chat(self, **kwargs: Any) -> dict[str, Any]:
        payload = {
            "keep": [{"item_id": keep_id, "segment_ids": [], "reason": "其它岗仍能用"}],
            "suggest_delete": [
                {
                    "item_id": del_id,
                    "segment_ids": [del_seg],
                    "reason": "只针对已挂岗位",
                }
            ],
        }
        return {
            "choices": [
                {"message": {"content": json.dumps(payload, ensure_ascii=False)}}
            ]
        }

    monkeypatch.setattr(store_mod, "_llm_configured", lambda: True)
    monkeypatch.setattr(store_mod.LlmClient, "chat_completions", fake_eval_chat)

    patched = client.patch(
        f"/api/applications/{app_id}",
        json={"status_text": "已挂"},
        headers=_headers(token),
    )
    assert patched.status_code == 200, patched.text
    data = patched.json()["data"]
    assert data["normalized_status"] == "rejected"
    assert data["knowledge_review_pending"] is True

    asks = [item for item in _messages(client, token, conversation_id) if item["message_type"] == "kb_ask"]
    assert asks, "已挂且有面经时应写入 kb_ask"
    assert "面经" in asks[-1]["content"]
    assert "删" in asks[-1]["content"] or "建议删除" in asks[-1]["content"]

    still = _list_kb(client, token)
    still_ids = {item["id"] for item in still}
    assert {keep_id, del_id} <= still_ids
    pending_detail = _detail(client, token, del_id)
    statuses = {seg["id"]: seg["status"] for seg in pending_detail["segments"]}
    assert statuses[del_seg] == "pending_delete"
    assert DEL_BODY in pending_detail["body"]

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
                text = str(last.get("content") or "")
                if "保留" in text:
                    return _tool_call(
                        "call_knowledge_agent",
                        {"task": "apply_user_decision", "keep_all": True},
                    )
                return _tool_call(
                    "call_knowledge_agent",
                    {"task": "apply_user_decision", "accepted_ids": [del_id]},
                )
            return _assistant_text("已按你的决定处理面经去留。")
        if role == "knowledge":
            if last.get("role") == "user":
                payload = json.loads(str(last.get("content") or "{}"))
                arguments: dict[str, Any] = {}
                if payload.get("keep_all"):
                    arguments["keep_all"] = True
                if payload.get("accepted_ids"):
                    arguments["accepted_ids"] = payload.get("accepted_ids")
                return _tool_call("apply_deletion_decision", arguments)
            return _assistant_text("已按你的决定处理面经去留。")
        return _assistant_text("我在。")

    monkeypatch.setattr(AgentService, "_complete", fake_complete)
    monkeypatch.setattr(LlmClient, "chat_completions", _forbid_bailian)
    settings = get_settings()
    object.__setattr__(settings, "llm_api_key", "test-key-not-real")
    try:
        _response, events = _post_chat(
            client,
            token,
            conversation_id,
            content="同意删除建议删除的那些面经",
        )
    finally:
        object.__setattr__(settings, "llm_api_key", "")

    assert events[-1][0] == "done"
    after_delete = _list_kb(client, token)
    after_ids = {item["id"] for item in after_delete}
    assert del_id not in after_ids
    assert keep_id in after_ids

    token_b, conversation_b, candidate_b = _start(client, "t020.keepall@example.com")
    created_b = client.post(
        "/api/applications",
        json={"company_name": "保留公司", "role_title": "后端", "status_text": "已投"},
        headers=_headers(token_b),
    )
    app_b = created_b.json()["data"]["id"]
    keep_b, del_b = await _seed_related_knowledge(candidate_b, app_b)
    ids_b = {keep_b, del_b}
    assert {item["id"] for item in _list_kb(client, token_b)} == ids_b

    monkeypatch.setattr(store_mod, "_llm_configured", lambda: False)
    patched_b = client.patch(
        f"/api/applications/{app_b}",
        json={"status_text": "已挂"},
        headers=_headers(token_b),
    )
    assert patched_b.status_code == 200
    assert patched_b.json()["data"]["knowledge_review_pending"] is True
    asks_b = [
        item
        for item in _messages(client, token_b, conversation_b)
        if item["message_type"] == "kb_ask"
    ]
    assert asks_b
    assert {item["id"] for item in _list_kb(client, token_b)} == ids_b

    object.__setattr__(settings, "llm_api_key", "test-key-not-real")
    try:
        _post_chat(client, token_b, conversation_b, content="这些面经请全部保留")
    finally:
        object.__setattr__(settings, "llm_api_key", "")

    kept = {item["id"] for item in _list_kb(client, token_b)}
    assert ids_b <= kept


@pytest.mark.asyncio
async def test_degraded_questions_cite_ingested_experience(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """知识已入库 + 面试 JSON 失败走降级时，题目须含面经指纹。禁止真实百炼。"""

    async def fake_chat(self, **kwargs: Any) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "not-json"}}]}

    monkeypatch.setattr(questions_mod, "_llm_configured", lambda: True)
    monkeypatch.setattr(questions_mod.LlmClient, "chat_completions", fake_chat)
    monkeypatch.setattr(LlmClient, "chat_completions", _forbid_bailian)

    token, conversation_id, candidate_id = _start(client, "t020.ac040@example.com")
    created = client.post(
        "/api/applications",
        json={
            "company_name": "青禾复验",
            "role_title": "后端",
            "status_text": "等待面试",
        },
        headers=_headers(token),
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["data"]["id"]

    async with get_db_context() as db:
        candidate = (
            await db.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        tools = ToolExecutor(db, candidate)
        ingested = await tools.ingest_user_document(
            {
                "title": QUESTION_FINGERPRINT,
                "body": QUESTION_KNOWLEDGE_BODY,
                "source_type": "paste",
                "application_id": app_id,
            }
        )
        assert ingested["ok"] is True

    listed = _list_kb(client, token)
    assert len(listed) == 1
    assert QUESTION_FINGERPRINT in listed[0]["excerpt"] or QUESTION_FINGERPRINT in listed[0]["title"]

    at = datetime(2026, 9, 14, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    set_clock(lambda value=at: value)
    tick = client.post("/internal/scheduler/tick", params={"at": at.isoformat()})
    assert tick.status_code == 200, tick.text
    assert questions_mod.last_used_fallback is True

    current = client.get("/api/question-sets/current", headers=_headers(token))
    assert current.status_code == 200, current.text
    questions = current.json()["data"]["questions"]
    assert len(questions) == 5
    joined = "\n".join(str(item.get("prompt") or "") for item in questions)
    assert QUESTION_FINGERPRINT in joined, joined
    assert "占位业务题" not in joined
    kinds = [item["kind"] for item in questions]
    assert kinds.count("common") == 3
    assert kinds.count("role") == 2

    messages = _messages(client, token, conversation_id)
    question_msgs = [item for item in messages if item["message_type"] == "questions"]
    assert question_msgs
    assert QUESTION_FINGERPRINT in question_msgs[-1]["content"]

    still = _list_kb(client, token)
    assert len(still) == 1
    detail = _detail(client, token, still[0]["id"])
    assert QUESTION_KNOWLEDGE_BODY in detail["body"]
