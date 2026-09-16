"""T-009：出题循环、催促语气与辅导收束真实验收。

测试时钟 + POST /internal/scheduler/tick（debug）。隔离库；SMTP 置空不误发。
对话通道验收；不把真实收件箱当门禁。AC-011 需真实百炼点评。
禁止把密钥写入断言、日志或失败信息。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import src.services.questions as questions_mod
from fastapi.testclient import TestClient
from pycore.core.config import ConfigManager
from src.config.settings import get_settings, load_app_config, load_settings_from_dict
from src.db.session import configure_engine_from_settings
from src.services.llm_client import LlmClient
from src.services.scheduler import reset_clock, set_clock, set_scheduler_loop_enabled

TZ = ZoneInfo("Asia/Shanghai")
RESUME_UNIQUE = "校园二手书交易平台 BookSwap"
RESUME_TEXT = (
    "刘文韬，Python 后端实习生。"
    f"独立做过{RESUME_UNIQUE}，使用 FastAPI 和 SQLite 完成订单对账与库存同步。"
    "熟悉异步接口和会话管理。"
)
MOCK_REVIEW_FINGERPRINT = "主要缺点是回答偏结果、少过程"
HARSH_NUDGE_MARKERS = ("骂醒", "十二点以前", "把五题写完", "把三题写完", "语气档", "立刻做，别再拖")


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
    payload["database_path"] = str(tmp_path / "t009.db")
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
        pytest.fail("未配置 llm_api_key，不能把 AC-011 写成通过。")


def _use_scheduler_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    """调度出题/催促走规则模板，避免整点 LLM 拖垮 120s；AC-010 允许模板降级。"""
    monkeypatch.setattr("src.services.questions._llm_configured", lambda: False)
    monkeypatch.setattr("src.services.nudge._llm_configured", lambda: False)


def _count_llm_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    stats = {"calls": 0}
    original = LlmClient.chat_completions

    async def wrapped(self: LlmClient, *args: Any, **kwargs: Any) -> dict[str, Any]:
        stats["calls"] += 1
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(LlmClient, "chat_completions", wrapped)
    return stats


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


def _headers(token: str, sse: bool = False) -> dict[str, str]:
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


def _iso(day: int, hour: int) -> str:
    current = datetime(2026, 9, day, hour, 0, 0, tzinfo=TZ)
    set_clock(lambda value=current: value)
    return current.isoformat()


def _tick(client: TestClient, day: int, hour: int):
    response = client.post("/internal/scheduler/tick", params={"at": _iso(day, hour)})
    assert response.status_code == 200, response.text
    return response


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def _insert_application(
    db_path: Path,
    candidate_id: str,
    *,
    app_id: str,
    company: str,
    role: str,
    normalized_status: str,
    status_text: str,
    progress: str = "已约下周一面",
    deadline: str | None = "2026-09-20",
    exam_points: str = "结合简历中的 Python 项目考查接口设计",
) -> None:
    conn = _connect(db_path)
    conn.execute(
        """
        insert into applications (
            id, candidate_id, company_name, role_title, jd_url, jd_text,
            exam_points, progress_text, status_text, normalized_status,
            deadline, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            candidate_id,
            company,
            role,
            "https://example.com/job/1",
            "",
            exam_points,
            progress,
            status_text,
            normalized_status,
            deadline,
            "2026-09-13T01:00:00+00:00",
            "2026-09-13T01:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()


def _seed_waiting_rejected_and_far(db_path: Path, candidate_id: str) -> None:
    _insert_application(
        db_path,
        candidate_id,
        app_id="a_near",
        company="星云科技",
        role="后端开发",
        normalized_status="waiting_interview",
        status_text="等待面试",
        progress="已投简历",
        deadline="2026-09-18",
        exam_points="对照 BookSwap 考查 FastAPI 对账与库存同步",
    )
    _insert_application(
        db_path,
        candidate_id,
        app_id="a_far",
        company="远日公司",
        role="后端实习生",
        normalized_status="waiting_interview",
        status_text="等待面试",
        progress="初筛通过",
        deadline="2026-10-30",
        exam_points="异步接口",
    )
    _insert_application(
        db_path,
        candidate_id,
        app_id="a_rej",
        company="已挂公司",
        role="已挂岗位",
        normalized_status="rejected",
        status_text="已挂",
        progress="流程结束",
        deadline=None,
        exam_points="不应出现",
    )


def _messages(client: TestClient, token: str, conversation_id: str) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    assert response.status_code == 200
    rows = response.json()["data"]["messages"]
    assert isinstance(rows, list)
    return rows


def _current_set(client: TestClient, token: str) -> dict[str, Any]:
    response = client.get("/api/question-sets/current", headers=_headers(token))
    assert response.status_code == 200
    return response.json()["data"]


def _set_counseling_active(db_path: Path, candidate_id: str, active: bool) -> None:
    """隔离库写入真实辅导标志（调度按此跳过狠催）。不改业务代码。"""
    conn = _connect(db_path)
    conn.execute(
        "update candidates set counseling_active=? where id=?",
        (1 if active else 0, candidate_id),
    )
    conn.commit()
    conn.close()


def _assert_five_question_structure(data: dict[str, Any], *, beijing_date: str) -> None:
    assert data["beijing_date"] == beijing_date
    assert data["status"] == "in_progress"
    questions = data["questions"]
    assert len(questions) == 5
    kinds = [item["kind"] for item in questions]
    assert "common" in kinds
    assert "role" in kinds
    targets = {item["target_application_id"] for item in questions}
    assert "a_rej" not in targets
    role_targets = [
        item["target_application_id"] for item in questions if item["kind"] == "role"
    ]
    assert role_targets
    assert set(role_targets) <= {"a_near", "a_far"}
    assert "a_near" in role_targets
    assert all(item["prompt"] for item in questions)


def test_updated_progress_five_questions_exclude_rejected_and_evening_tone(
    live_client: TestClient,
    live_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-005 / AC-006 / AC-008 / AC-010：对话可见五题与催促；已挂不出题仍在进度里。"""
    _use_scheduler_templates(monkeypatch)
    llm_stats = _count_llm_calls(monkeypatch)
    token, conversation_id, candidate_id = _start(
        live_client, "t009.progress@example.com"
    )
    db_path = live_env / "t009.db"
    _seed_waiting_rejected_and_far(db_path, candidate_id)

    updated = live_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": (
                "星云科技这个后端岗位等待面试，进度改成已约下周一面，"
                "deadline 2026-09-20。"
            )
        },
        headers=_headers(token, sse=True),
    )
    assert updated.status_code == 200
    update_events = _sse_events(updated.text)
    assert update_events[-1][0] == "done"

    _tick(live_client, 13, 10)
    morning_set = _current_set(live_client, token)
    _assert_five_question_structure(morning_set, beijing_date="2026-09-13")

    morning_messages = _messages(live_client, token, conversation_id)
    types = [item["message_type"] for item in morning_messages]
    assert "questions" in types
    assert "nudge" in types
    question_msg = next(item for item in morning_messages if item["message_type"] == "questions")
    assert "今日五题" in question_msg["content"] or "1." in question_msg["content"]
    assert "【共性】" not in question_msg["content"]
    assert "考查点" not in question_msg["content"]
    assert "已挂公司" not in question_msg["content"]
    assert "不应出现" not in question_msg["content"]

    morning_nudges = [
        item["content"] for item in morning_messages if item["message_type"] == "nudge"
    ]
    assert morning_nudges
    morning_text = morning_nudges[-1]
    assert "投递进度 / deadline" not in morning_text
    assert "考查点" not in morning_text
    assert any(q["prompt"] in morning_text for q in morning_set["questions"])

    _tick(live_client, 13, 23)
    night_messages = _messages(live_client, token, conversation_id)
    night_nudges = [
        item["content"] for item in night_messages if item["message_type"] == "nudge"
    ]
    assert len(night_nudges) >= 2
    night_text = night_nudges[-1]
    assert night_text != morning_text
    assert "题" in night_text or "1." in night_text

    conn = _connect(db_path)
    rows = conn.execute(
        "select beijing_hour_key, tone_level from nudge_logs "
        "where channel='chat' order by beijing_hour_key"
    ).fetchall()
    conn.close()
    assert ("2026-09-13T10", 1) in rows
    assert ("2026-09-13T23", 4) in rows
    if "慢慢" in morning_text or "陪你" in morning_text:
        assert "立刻" in night_text or "别再拖" in night_text or "还没搞定" in night_text
    assert llm_stats["calls"] >= 1
    if questions_mod.last_used_fallback:
        kinds = [item["kind"] for item in morning_set["questions"]]
        assert kinds.count("common") >= 1
        assert kinds.count("role") >= 1


def test_void_rest_then_third_day_new_questions(
    live_client: TestClient, live_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-018：24:00 作废；第二天不催这些题；第三天新五题。"""
    _use_scheduler_templates(monkeypatch)
    token, conversation_id, candidate_id = _start(
        live_client, "t009.void@example.com"
    )
    db_path = live_env / "t009.db"
    _seed_waiting_rejected_and_far(db_path, candidate_id)
    _tick(live_client, 13, 10)
    first = _current_set(live_client, token)
    _assert_five_question_structure(first, beijing_date="2026-09-13")
    first_prompts = [item["prompt"] for item in first["questions"]]

    _tick(live_client, 14, 0)
    conn = _connect(db_path)
    voided = conn.execute(
        "select status, beijing_date from question_sets where beijing_date='2026-09-13'"
    ).fetchone()
    rest = conn.execute(
        "select status, beijing_date from question_sets where beijing_date='2026-09-14'"
    ).fetchone()
    next_date = conn.execute(
        "select next_question_date from candidates where id=?", (candidate_id,)
    ).fetchone()[0]
    conn.close()
    assert voided == ("voided", "2026-09-13")
    assert rest == ("rest_day", "2026-09-14")
    assert str(next_date).startswith("2026-09-15")

    set_clock(lambda: datetime(2026, 9, 14, 10, 0, tzinfo=TZ))
    rest_current = _current_set(live_client, token)
    assert rest_current["status"] == "rest_day"
    assert rest_current["beijing_date"] == "2026-09-14"
    assert rest_current["questions"] == []

    before = _messages(live_client, token, conversation_id)
    before_ids = {item["id"] for item in before if item["message_type"] == "nudge"}
    _tick(live_client, 14, 10)
    after = _messages(live_client, token, conversation_id)
    rest_nudges = [
        item["content"]
        for item in after
        if item["message_type"] == "nudge" and item["id"] not in before_ids
    ]
    assert rest_nudges
    rest_text = rest_nudges[-1]
    assert "未完成面试题" not in rest_text
    for prompt in first_prompts:
        assert prompt not in rest_text

    _tick(live_client, 15, 10)
    set_clock(lambda: datetime(2026, 9, 15, 10, 0, tzinfo=TZ))
    third = _current_set(live_client, token)
    _assert_five_question_structure(third, beijing_date="2026-09-15")
    assert third["id"] != first["id"]
    third_ids = [item["id"] for item in third["questions"]]
    first_ids = [item["id"] for item in first["questions"]]
    assert third_ids != first_ids
    third_messages = _messages(live_client, token, conversation_id)
    question_msgs = [
        item for item in third_messages if item["message_type"] == "questions"
    ]
    assert len(question_msgs) >= 2
    assert any(item["message_type"] == "nudge" for item in third_messages)


def test_answer_review_then_next_day_new_questions(
    live_client: TestClient,
    live_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-011 / AC-020：五题答完真实点评；次日 10:00 新五题。"""
    _require_paid_llm()
    _use_scheduler_templates(monkeypatch)
    llm_stats = _count_llm_calls(monkeypatch)
    token, conversation_id, candidate_id = _start(
        live_client, "t009.review@example.com"
    )
    db_path = live_env / "t009.db"
    _seed_waiting_rejected_and_far(db_path, candidate_id)
    _tick(live_client, 13, 10)
    today = _current_set(live_client, token)
    _assert_five_question_structure(today, beijing_date="2026-09-13")
    questions = today["questions"]
    first_ids = [item["id"] for item in questions]

    answer_body = (
        "请调用 save_answers 保存今日五题作答，五题都答完后给出面试缺点总结和改进建议。\n"
        f"第一题（{questions[0]['id']}）："
        "BookSwap 里我用 FastAPI 做订单对账，难点是并发下库存扣减，结果对账差异降到 0。\n"
        f"第二题（{questions[1]['id']}）："
        "会先画数据流，再用 SQLite 事务保证库存同步，并补幂等键。\n"
        f"第三题（{questions[2]['id']}）："
        "协作卡住时我会把冲突摊开，对齐目标和截止，再收口。\n"
        f"第四题（{questions[3]['id']}）："
        "面试官追问风险时我会讲锁粒度、失败重试和监控告警，而不是只说结果。\n"
        f"第五题（{questions[4]['id']}）："
        "业务题里我会先讲用户路径，再讲取舍和可验证结果。"
    )
    response = live_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": answer_body},
        headers=_headers(token, sse=True),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "done", f"末事件应为 done，实际 {events[-1][0]}"
    done = events[-1][1]
    message = done["message"]
    content = str(message.get("content") or "")
    assert llm_stats["calls"] >= 1
    assert MOCK_REVIEW_FINGERPRINT not in content
    assert message["message_type"] == "review"
    review_hit = any(
        token_text in content
        for token_text in ("缺点", "不足", "缺乏", "建议", "下次", "改进", "补一句")
    )
    assert review_hit, "真实点评应指出表达问题并给出改进建议"

    set_clock(lambda: datetime(2026, 9, 13, 10, 0, tzinfo=TZ))
    completed = _current_set(live_client, token)
    assert completed["status"] == "completed"
    assert completed["beijing_date"] == "2026-09-13"
    assert completed["review"]
    assert all(item.get("answer") for item in completed["questions"])

    review_rows = [
        item
        for item in _messages(live_client, token, conversation_id)
        if item["message_type"] == "review"
    ]
    assert review_rows

    _tick(live_client, 14, 10)
    set_clock(lambda: datetime(2026, 9, 14, 10, 0, tzinfo=TZ))
    nxt = _current_set(live_client, token)
    _assert_five_question_structure(nxt, beijing_date="2026-09-14")
    assert nxt["id"] != today["id"]
    next_ids = [item["id"] for item in nxt["questions"]]
    assert next_ids != first_ids
    next_messages = _messages(live_client, token, conversation_id)
    assert any(
        item["message_type"] == "questions" and item["content"]
        for item in next_messages
    )
    assert any(item["message_type"] == "nudge" for item in next_messages)


def test_counseling_active_skips_hourly_nudge(
    live_client: TestClient, live_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-019：隔离库置 counseling_active 后再 tick，对话不插入狠催。"""
    _use_scheduler_templates(monkeypatch)
    token, conversation_id, candidate_id = _start(
        live_client, "t009.nudge-skip@example.com"
    )
    db_path = live_env / "t009.db"
    _seed_waiting_rejected_and_far(db_path, candidate_id)
    _tick(live_client, 13, 10)
    today = _current_set(live_client, token)
    _assert_five_question_structure(today, beijing_date="2026-09-13")

    before = _messages(live_client, token, conversation_id)
    before_ids = {item["id"] for item in before}
    before_nudge_ids = {item["id"] for item in before if item["message_type"] == "nudge"}
    _set_counseling_active(db_path, candidate_id, True)
    conn = _connect(db_path)
    flag = conn.execute(
        "select counseling_active from candidates where id=?", (candidate_id,)
    ).fetchone()[0]
    conn.close()
    assert flag == 1

    _tick(live_client, 13, 11)
    after_tick = _messages(live_client, token, conversation_id)
    new_nudges = [
        item
        for item in after_tick
        if item["message_type"] == "nudge" and item["id"] not in before_nudge_ids
    ]
    assert new_nudges == [], "辅导中整点不得向对话插入狠催"
    for item in after_tick:
        if item["id"] not in before_ids:
            assert not any(marker in (item.get("content") or "") for marker in HARSH_NUDGE_MARKERS)

    conn = _connect(db_path)
    eleven = conn.execute(
        "select count(*) from nudge_logs where beijing_hour_key='2026-09-13T11'"
    ).fetchone()[0]
    conn.close()
    assert eleven == 0


def test_feeling_better_resumes_same_turn_questions(
    live_client: TestClient,
    live_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-014：心情变好后确认结束辅导，同一轮接上当日题目或催促。"""
    _require_paid_llm()
    _use_scheduler_templates(monkeypatch)
    llm_stats = _count_llm_calls(monkeypatch)
    token, conversation_id, candidate_id = _start(
        live_client, "t009.counsel-end@example.com"
    )
    db_path = live_env / "t009.db"
    _seed_waiting_rejected_and_far(db_path, candidate_id)
    _tick(live_client, 13, 10)
    today = _current_set(live_client, token)
    _assert_five_question_structure(today, beijing_date="2026-09-13")
    prompts = [item["prompt"] for item in today["questions"]]
    _set_counseling_active(db_path, candidate_id, True)

    better = live_client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": (
                "心情变好了，好多了。请结束心理辅导，立刻继续今天还没答完的面试题，"
                "不要再只安慰，把题目接上。"
            )
        },
        headers=_headers(token, sse=True),
    )
    assert better.status_code == 200
    events = _sse_events(better.text)
    assert events[-1][0] == "done"
    done = events[-1][1]
    snapshot = done["snapshot"]
    assert isinstance(snapshot, dict)
    reply = str(done["message"].get("content") or "")
    assert llm_stats["calls"] >= 1
    assert not any(marker in reply for marker in HARSH_NUDGE_MARKERS)

    continued = any(prompt in reply for prompt in prompts) or (
        "题" in reply and ("面试" in reply or "继续" in reply or "今日" in reply)
    )
    assert continued, (
        "心情变好后同一轮应接上当日题目或未完成催促。"
        f"counseling_active={snapshot.get('today', {}).get('counseling_active')}"
    )
    assert "心情" in reply or "好" in reply or "结束" in reply or "辅导" in reply
