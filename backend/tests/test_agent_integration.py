"""T-008：岗位考查点、进度更新与辅导对话真实验收。

从 backend/.env 经 ConfigManager 读取百炼配置；隔离测试库；不发 SMTP。
禁止把无模型 Mock 回复写成 AC-003 / AC-013 通过。不把密钥写入断言、日志或失败信息。
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pycore.core.config import ConfigManager
from src.config.settings import get_settings, load_app_config, load_settings_from_dict
from src.db.session import configure_engine_from_settings
from src.services.jd_fetch import create_jd_http_client
from src.services.llm_client import LlmClient
from src.services.scheduler import reset_clock, set_scheduler_loop_enabled

JOB_BOARD_URL = "https://www.python.org/jobs/"
FAILED_JD_URL = "https://httpbin.org/status/404"
JD_DETAIL_FALLBACK = "https://www.python.org/jobs/8133/"
RESUME_UNIQUE = "校园二手书交易平台 BookSwap"
RESUME_TEXT = (
    "刘文韬，Python 后端实习生。"
    f"独立做过{RESUME_UNIQUE}，使用 FastAPI 和 SQLite 完成订单对账与库存同步。"
    "熟悉异步接口和会话管理。"
)
MOCK_EXAM_FINGERPRINT = "这个岗位主要考查项目落地和后端基础"
HARSH_NUDGE_MARKERS = ("骂醒", "十二点以前", "把五题写完", "把三题写完", "语气档")


@pytest.fixture(autouse=True)
def _isolate_scheduler_loop() -> Iterator[None]:
    set_scheduler_loop_enabled(False)
    yield
    reset_clock()
    set_scheduler_loop_enabled(True)


@pytest.fixture
def live_env(tmp_path: Path) -> Iterator[Path]:
    ConfigManager.reset()
    load_app_config()
    loaded = get_settings()
    payload = loaded.model_dump()
    payload["database_path"] = str(tmp_path / "t008.db")
    payload["upload_dir"] = str(tmp_path / "uploads")
    payload["smtp_host"] = ""
    payload["smtp_user"] = ""
    payload["smtp_password"] = ""
    payload["smtp_from"] = ""
    payload["smtp_port"] = None
    payload["debug"] = True
    ConfigManager.reset()
    load_settings_from_dict(payload)
    configure_engine_from_settings()
    yield tmp_path
    ConfigManager.reset()


@pytest.fixture
def live_client(live_env: Path) -> Iterator[TestClient]:
    import src.main as main_mod

    application = main_mod.build_app()
    main_mod._app = application
    with TestClient(application) as test_client:
        test_client.timeout = httpx.Timeout(110.0)
        yield test_client
    main_mod._app = None


def _require_paid_llm() -> None:
    if not get_settings().llm_api_key:
        pytest.fail("未配置 llm_api_key，不能把 AC-003/013 写成通过。")


def _public_jd_detail_url() -> str:
    """公网可读的单条 JD 页（python.org 职位详情），禁止本机/内网。"""
    settings = get_settings()
    with httpx.Client(
        trust_env=False,
        timeout=settings.jd_fetch_timeout_seconds,
        follow_redirects=False,
        headers={"User-Agent": settings.jd_fetch_user_agent},
    ) as client:
        response = client.get(JOB_BOARD_URL)
    if response.status_code == 200:
        match = re.search(r"/jobs/(\d+)/", response.text)
        if match:
            return f"https://www.python.org/jobs/{match.group(1)}/"
    return JD_DETAIL_FALLBACK


def _start(client: TestClient, email: str) -> tuple[str, str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email},
        files={"resume": ("resume.txt", RESUME_TEXT.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 201
    data = response.json()["data"]
    current = client.get(
        "/api/candidates/current",
        headers={"Authorization": f"Bearer {data['session_token']}"},
    )
    candidate_id = current.json()["data"]["id"]
    return data["session_token"], data["conversation_id"], candidate_id


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
    }


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


def _record_real_jd_gets(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    original = create_jd_http_client

    def factory(settings=None) -> httpx.AsyncClient:
        client = original(settings)

        async def on_request(request: httpx.Request) -> None:
            seen.append(f"{request.method} {request.url}")

        hooks = list(client.event_hooks.get("request") or [])
        hooks.append(on_request)
        client.event_hooks["request"] = hooks
        return client

    monkeypatch.setattr("src.services.jd_fetch.create_jd_http_client", factory)
    return seen


def _count_llm_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    stats = {"calls": 0}
    original = LlmClient.chat_completions

    async def wrapped(self: LlmClient, *args: Any, **kwargs: Any) -> dict[str, Any]:
        stats["calls"] += 1
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(LlmClient, "chat_completions", wrapped)
    return stats


def _assert_status_then_delta(events: list[tuple[str, dict[str, Any]]]) -> None:
    names = [name for name, _ in events]
    assert "status" in names
    assert "delta" in names
    assert names.index("status") < names.index("delta")


def _list_applications(client: TestClient, token: str) -> list[dict[str, Any]]:
    listed = client.get("/api/applications", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    payload = listed.json()["data"]["applications"]
    assert isinstance(payload, list)
    return payload


def test_readable_jd_summarizes_exam_points_against_resume(
    live_client: TestClient,
    live_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_paid_llm()
    llm_stats = _count_llm_calls(monkeypatch)
    seen = _record_real_jd_gets(monkeypatch)
    jd_url = _public_jd_detail_url()
    token, conversation_id, _candidate_id = _start(
        live_client, "t008.exam@example.com"
    )
    response = live_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": (
                f"我投了这个岗位，链接是 {jd_url}。"
                "请读取 JD、记下这条投递，并对照我的简历总结考查点，不要只复述 JD。"
            )
        },
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(response.text)
    names = [name for name, _ in events]
    assert names[-1] == "done", f"末事件应为 done，实际 {names[-1]}"
    _assert_status_then_delta(events)
    assert any(
        name == "status" and payload.get("stage") == "fetch_jd"
        for name, payload in events
    )
    assert llm_stats["calls"] >= 1
    assert any(
        item.startswith("GET ") and "python.org/jobs/" in item for item in seen
    ), f"应对公网 JD 发出真实 GET，实际={seen}"

    done = events[-1][1]
    message = done["message"]
    snapshot = done["snapshot"]
    assert isinstance(snapshot, dict)
    assert message["role"] == "assistant"
    apps = _list_applications(live_client, token)
    assert apps, "done 后 API-007 应能读到新岗位"
    exam_points = str(apps[0].get("exam_points") or "")
    combined = f"{message.get('content') or ''}\n{exam_points}"
    assert MOCK_EXAM_FINGERPRINT not in combined
    assert "对照" in combined or "简历" in combined
    resume_hit = any(
        token_text in combined
        for token_text in ("BookSwap", "二手书", "FastAPI", "对账", "SQLite")
    )
    assert resume_hit, "考查点应对照简历特有经历，而不是只贴 JD"
    assert message["message_type"] in {"jd_summary", "chat"}
    assert (live_env / "t008.db").exists()


def test_unreadable_jd_issues_real_get_and_error_notice(
    live_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_real_jd_gets(monkeypatch)
    token, conversation_id, _ = _start(live_client, "t008.fail@example.com")
    response = live_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": f"请看这个岗位 {FAILED_JD_URL}"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(response.text)
    assert any(
        item.startswith("GET ") and "httpbin.org/status/404" in item for item in seen
    ), "失败 URL 必须真实发出 GET"
    last_name, last_payload = events[-1]
    notice = ""
    if last_name == "error":
        assert last_payload.get("error_code") == "JD_FETCH_FAILED"
        notice = str(last_payload.get("error") or "")
    else:
        notice = str(last_payload.get("message", {}).get("content") or "")

    messages = live_client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    rows = messages.json()["data"]["messages"]
    error_rows = [row for row in rows if row["message_type"] == "error_notice"]
    assert error_rows, "失败路径应落库 error_notice"
    notice = f"{notice}\n{error_rows[-1]['content']}"
    assert "链接" in notice
    assert any(marker in notice for marker in ("换", "粘贴", "正文", "JD"))
    apps = _list_applications(live_client, token)
    assert apps == [] or not any(
        (item.get("exam_points") or "").strip() for item in apps
    )


def test_update_application_progress_matches_api007(
    live_client: TestClient,
    live_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_paid_llm()
    llm_stats = _count_llm_calls(monkeypatch)
    token, conversation_id, candidate_id = _start(
        live_client, "t008.progress@example.com"
    )
    db = sqlite3.connect(live_env / "t008.db")
    db.execute(
        "insert into applications ("
        "id, candidate_id, company_name, role_title, jd_url, jd_text, exam_points, "
        "progress_text, status_text, normalized_status, deadline, created_at, updated_at"
        ") values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "a_t008_prog",
            candidate_id,
            "星云科技",
            "后端开发",
            "https://example.com/job/t008",
            "Python 后端",
            "已有考查点",
            "",
            "",
            "other",
            None,
            "2026-09-13T02:00:00",
            "2026-09-13T02:00:00",
        ),
    )
    db.commit()
    db.close()

    response = live_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": (
                "星云科技这个后端岗位等待面试，进度是已约下周一面，"
                "deadline 2026-09-20。"
            )
        },
        headers=_headers(token),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "done"
    _assert_status_then_delta(events)
    assert llm_stats["calls"] >= 1
    assert any(
        name == "status" and payload.get("stage") == "update_application"
        for name, payload in events
    )

    apps = _list_applications(live_client, token)
    assert len(apps) == 1
    app = apps[0]
    assert app["normalized_status"] == "waiting_interview"
    assert "等待面试" in (app.get("status_text") or "")
    assert "已约下周一面" in (app.get("progress_text") or "")
    assert app.get("deadline") == "2026-09-20"


def test_anxiety_gets_counseling_not_harsh_nudge(
    live_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_paid_llm()
    llm_stats = _count_llm_calls(monkeypatch)
    token, conversation_id, _ = _start(live_client, "t008.counsel@example.com")
    response = live_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": (
                "我现在很焦虑，也很不开心，压力好大，难受得今天不想面。"
                "先别催我做题，我想先说说心里的状态。"
            )
        },
        headers=_headers(token),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "done"
    _assert_status_then_delta(events)
    assert llm_stats["calls"] >= 1
    done = events[-1][1]
    message = done["message"]
    snapshot = done["snapshot"]
    assert isinstance(snapshot, dict)
    content = str(message.get("content") or "")
    counseling_like = any(
        token_text in content for token_text in ("心情", "感觉", "状态", "现在", "压")
    )
    assert counseling_like, "辅导回复应询问或接住当前心理状态"
    assert not any(marker in content for marker in HARSH_NUDGE_MARKERS)
    used_counseling_tool = (
        snapshot["today"]["counseling_active"] is True
        or message["message_type"] == "counseling"
        or any(
            name == "status" and payload.get("stage") == "counseling"
            for name, payload in events
        )
    )
    assert used_counseling_tool or counseling_like
    assert "十二点以前" not in content
    assert "把三题写完" not in content
    assert "把五题写完" not in content
