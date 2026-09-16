from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from src.config.settings import get_settings
from src.services.llm_probe import LlmKeyProbeResult
from src.services.mail import (
    LLM_KEY_INVALID_EMAIL_BODY,
    LLM_KEY_INVALID_EMAIL_SUBJECT,
    llm_key_invalid_dedup_key,
)
from src.services.scheduler import (
    reset_clock,
    set_clock,
    set_scheduler_loop_enabled,
)

TZ = ZoneInfo("Asia/Shanghai")
FAKE_USER_KEY = "sk-test-scheduler-user-key"
FAKE_OPERATOR_KEY = "sk-test-operator-must-not-be-used"


@pytest.fixture(autouse=True)
def _isolate_scheduler_loop(tmp_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    object.__setattr__(get_settings(), "llm_allow_mock", True)

    async def ok_probe(api_key: str, settings=None):  # noqa: ANN001
        return LlmKeyProbeResult(verdict="ok", http_status=200)

    monkeypatch.setattr("src.services.candidate.probe_llm_api_key", ok_probe)
    set_scheduler_loop_enabled(False)
    yield
    reset_clock()
    set_scheduler_loop_enabled(True)


def _create_candidate(client: TestClient, email: str = "nudge@example.com") -> tuple[str, str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email, "llm_api_key": FAKE_USER_KEY},
        files={"resume": ("resume.txt", b"Python backend intern", "text/plain")},
    )
    assert response.status_code == 201
    data = response.json()["data"]
    return data["session_token"], data["conversation_id"], data["candidate"]["id"]


def _iso(day: int, hour: int) -> str:
    current = datetime(2026, 9, day, hour, 0, 0, tzinfo=TZ)
    set_clock(lambda value=current: value)
    return current.isoformat()


def _tick(client: TestClient, day: int, hour: int):
    return client.post("/internal/scheduler/tick", params={"at": _iso(day, hour)})


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
    conn = sqlite3.connect(db_path)
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


def _seed_waiting_and_rejected(db_path: Path, candidate_id: str) -> None:
    _insert_application(
        db_path,
        candidate_id,
        app_id="a_wait",
        company="示例公司",
        role="后端开发",
        normalized_status="waiting_interview",
        status_text="等待面试",
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


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_ten_am_writes_five_questions_excluding_rejected(
    client: TestClient, tmp_env: Path
) -> None:
    token, conversation_id, candidate_id = _create_candidate(client)
    db_path = tmp_env / "offer_coming.db"
    _seed_waiting_and_rejected(db_path, candidate_id)

    response = _tick(client, 13, 10)
    assert response.status_code == 200

    current = client.get("/api/question-sets/current", headers=_headers(token))
    assert current.status_code == 200
    data = current.json()["data"]
    assert data["status"] == "in_progress"
    assert len(data["questions"]) == 5
    kinds = {item["kind"] for item in data["questions"]}
    assert "common" in kinds
    assert "role" in kinds
    targets = {item["target_application_id"] for item in data["questions"]}
    assert "a_rej" not in targets
    assert "a_wait" in targets

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    types = [item["message_type"] for item in messages.json()["data"]["messages"]]
    assert "questions" in types
    assert "nudge" in types
    rows = messages.json()["data"]["messages"]
    question_text = next(item["content"] for item in rows if item["message_type"] == "questions")
    assert "今日五题" in question_text
    assert "【共性】" not in question_text
    assert "考查点" not in question_text
    assert "优先级" not in question_text
    nudge_text = next(item["content"] for item in rows if item["message_type"] == "nudge")
    assert "投递进度 / deadline" not in nudge_text
    assert "考查点" not in nudge_text
    assert "优先级" not in nudge_text


def test_json_validation_failure_falls_back_to_template(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.services.questions as questions_mod

    async def fake_chat(self, **kwargs):  # noqa: ANN001
        return {"choices": [{"message": {"content": "not-json"}}]}

    monkeypatch.setattr(questions_mod, "_llm_configured", lambda: True)
    monkeypatch.setattr(questions_mod.LlmClient, "chat_completions", fake_chat)

    token, _conversation_id, candidate_id = _create_candidate(client)
    db_path = tmp_env / "offer_coming.db"
    _seed_waiting_and_rejected(db_path, candidate_id)
    response = _tick(client, 13, 10)
    assert response.status_code == 200
    assert questions_mod.last_used_fallback is True
    assert "降级" in questions_mod.last_degrade_log

    current = client.get("/api/question-sets/current", headers=_headers(token))
    questions = current.json()["data"]["questions"]
    assert len(questions) == 5
    kinds = [item["kind"] for item in questions]
    assert kinds.count("common") == 3
    assert kinds.count("role") == 2
    assert all(item["target_application_id"] != "a_rej" for item in questions)


def test_tone_23_is_stricter_than_10(client: TestClient, tmp_env: Path) -> None:
    token, conversation_id, candidate_id = _create_candidate(client)
    _seed_waiting_and_rejected(tmp_env / "offer_coming.db", candidate_id)
    _tick(client, 13, 10)
    _tick(client, 13, 23)

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    nudges = [
        item["content"]
        for item in messages.json()["data"]["messages"]
        if item["message_type"] == "nudge"
    ]
    assert len(nudges) == 2
    morning, night = nudges[0], nudges[1]
    assert "慢慢" in morning or "陪你" in morning
    assert "立刻" in night or "别再拖" in night
    assert night != morning

    conn = _connect(tmp_env / "offer_coming.db")
    rows = conn.execute(
        "select beijing_hour_key, tone_level from nudge_logs where channel='chat' order by beijing_hour_key"
    ).fetchall()
    conn.close()
    assert rows == [("2026-09-13T10", 1), ("2026-09-13T23", 4)]


def test_outside_nudge_window_does_not_prompt(client: TestClient, tmp_env: Path) -> None:
    token, conversation_id, candidate_id = _create_candidate(client)
    _seed_waiting_and_rejected(tmp_env / "offer_coming.db", candidate_id)
    _tick(client, 13, 1)
    _tick(client, 13, 9)

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    assert messages.json()["data"]["messages"] == []
    current = client.get("/api/question-sets/current", headers=_headers(token))
    assert current.json()["data"]["status"] == "none"


def test_midnight_voids_and_sets_rest_then_third_day_new_questions(
    client: TestClient, tmp_env: Path
) -> None:
    token, conversation_id, candidate_id = _create_candidate(client)
    db_path = tmp_env / "offer_coming.db"
    _seed_waiting_and_rejected(db_path, candidate_id)
    _tick(client, 13, 10)
    _tick(client, 14, 0)

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
    assert voided[0] == "voided"
    assert rest == ("rest_day", "2026-09-14")
    assert str(next_date).startswith("2026-09-15")

    current = client.get("/api/question-sets/current", headers=_headers(token))
    assert current.json()["data"]["status"] == "rest_day"
    assert current.json()["data"]["questions"] == []

    before = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    rest_nudge = _tick(client, 14, 10)
    assert rest_nudge.status_code == 200
    after = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    new_nudges = [
        item["content"]
        for item in after.json()["data"]["messages"]
        if item["message_type"] == "nudge"
        and item["id"]
        not in {row["id"] for row in before.json()["data"]["messages"] if row["message_type"] == "nudge"}
    ]
    assert new_nudges
    assert "未完成面试题" not in new_nudges[-1]
    assert "请把简历里某段项目经历讲深一层" not in new_nudges[-1]

    _tick(client, 15, 10)
    set_clock(lambda: datetime(2026, 9, 15, 10, 0, tzinfo=TZ))
    third = client.get("/api/question-sets/current", headers=_headers(token))
    assert third.json()["data"]["status"] == "in_progress"
    assert len(third.json()["data"]["questions"]) == 5
    assert third.json()["data"]["beijing_date"] == "2026-09-15"


def test_completed_stops_nudge_and_sets_next_day(
    client: TestClient, tmp_env: Path
) -> None:
    token, conversation_id, candidate_id = _create_candidate(client)
    db_path = tmp_env / "offer_coming.db"
    _seed_waiting_and_rejected(db_path, candidate_id)
    _tick(client, 13, 10)

    conn = _connect(db_path)
    conn.execute("update question_sets set status='completed' where beijing_date='2026-09-13'")
    conn.commit()
    conn.close()

    before = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    before_nudge = sum(
        1 for item in before.json()["data"]["messages"] if item["message_type"] == "nudge"
    )
    _tick(client, 13, 11)
    after = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    after_nudge = sum(
        1 for item in after.json()["data"]["messages"] if item["message_type"] == "nudge"
    )
    assert after_nudge == before_nudge

    conn = _connect(db_path)
    next_date = conn.execute(
        "select next_question_date from candidates where id=?", (candidate_id,)
    ).fetchone()[0]
    conn.close()
    assert str(next_date).startswith("2026-09-14")


def test_counseling_skips_nudge(client: TestClient, tmp_env: Path) -> None:
    token, conversation_id, candidate_id = _create_candidate(client)
    db_path = tmp_env / "offer_coming.db"
    _seed_waiting_and_rejected(db_path, candidate_id)
    conn = _connect(db_path)
    conn.execute("update candidates set counseling_active=1 where id=?", (candidate_id,))
    conn.commit()
    conn.close()

    _tick(client, 13, 10)
    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    types = [item["message_type"] for item in messages.json()["data"]["messages"]]
    assert "nudge" not in types


def test_same_hour_key_is_idempotent(client: TestClient, tmp_env: Path) -> None:
    token, conversation_id, candidate_id = _create_candidate(client)
    _seed_waiting_and_rejected(tmp_env / "offer_coming.db", candidate_id)
    _tick(client, 13, 10)
    _tick(client, 13, 10)

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    types = [item["message_type"] for item in messages.json()["data"]["messages"]]
    assert types.count("nudge") == 1
    assert types.count("questions") == 1

    conn = _connect(tmp_env / "offer_coming.db")
    count = conn.execute(
        "select count(*) from nudge_logs where beijing_hour_key='2026-09-13T10'"
    ).fetchone()[0]
    conn.close()
    assert count == 2


def test_smtp_unconfigured_still_writes_chat_nudge(
    client: TestClient, tmp_env: Path
) -> None:
    token, conversation_id, candidate_id = _create_candidate(client)
    _seed_waiting_and_rejected(tmp_env / "offer_coming.db", candidate_id)
    _tick(client, 13, 10)

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    nudges = [
        item["content"]
        for item in messages.json()["data"]["messages"]
        if item["message_type"] == "nudge"
    ]
    assert nudges
    assert "邮件" in nudges[0]
    assert "发不出去" in nudges[0] or "尚未配置" in nudges[0]

    conn = _connect(tmp_env / "offer_coming.db")
    rows = conn.execute(
        "select channel, sent_ok, error_text from nudge_logs where beijing_hour_key='2026-09-13T10'"
    ).fetchall()
    conn.close()
    by_channel = {row[0]: row for row in rows}
    assert by_channel["chat"][1] == 1
    assert by_channel["email"][1] == 0
    assert by_channel["email"][2]
    assert "smtp" in by_channel["email"][2].lower() or "配置" in by_channel["email"][2]


def _forbid_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(self, **kwargs):  # noqa: ANN001
        raise AssertionError("调度在 Key 不可用时不得调用模型")

    monkeypatch.setattr("src.services.llm_client.LlmClient.chat_completions", boom)
    monkeypatch.setattr("src.services.questions.LlmClient.chat_completions", boom)
    monkeypatch.setattr("src.services.nudge.LlmClient.chat_completions", boom)


def test_invalid_key_skips_models_and_writes_error_notice(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, candidate_id = _create_candidate(client, email="inv@example.com")
    _seed_waiting_and_rejected(tmp_env / "offer_coming.db", candidate_id)
    conn = _connect(tmp_env / "offer_coming.db")
    conn.execute(
        "update candidates set llm_key_status='invalid' where id=?",
        (candidate_id,),
    )
    conn.commit()
    conn.close()
    _forbid_llm(monkeypatch)
    object.__setattr__(get_settings(), "llm_api_key", FAKE_OPERATOR_KEY)
    response = _tick(client, 13, 10)
    assert response.status_code == 200

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    rows = messages.json()["data"]["messages"]
    types = [item["message_type"] for item in rows]
    assert "nudge" not in types
    assert "questions" not in types
    assert "error_notice" in types
    assert any("我的key" in item["content"] for item in rows if item["message_type"] == "error_notice")
    conn = _connect(tmp_env / "offer_coming.db")
    llm_calls = conn.execute("select count(*) from llm_call_logs").fetchone()[0]
    qcount = conn.execute("select count(*) from question_sets").fetchone()[0]
    email_rows = conn.execute(
        "select sent_ok, error_text, beijing_hour_key from nudge_logs where channel='email'"
    ).fetchall()
    conn.close()
    assert llm_calls == 0
    assert qcount == 0
    assert email_rows
    assert email_rows[0][0] == 0
    assert "llm_key_invalid:2026-09-13" in email_rows[0][2]
    assert email_rows[0][1]
    joined = " ".join(item["content"] for item in rows)
    assert "已发送" not in joined and "已经发到邮箱" not in joined


def test_missing_ciphertext_skips_models(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, candidate_id = _create_candidate(client, email="miss@example.com")
    _seed_waiting_and_rejected(tmp_env / "offer_coming.db", candidate_id)
    conn = _connect(tmp_env / "offer_coming.db")
    conn.execute(
        "update candidates set llm_api_key_ciphertext=null, llm_key_status='missing' where id=?",
        (candidate_id,),
    )
    conn.commit()
    conn.close()
    _forbid_llm(monkeypatch)
    _tick(client, 13, 10)
    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    types = [item["message_type"] for item in messages.json()["data"]["messages"]]
    assert "error_notice" in types
    assert "nudge" not in types
    assert "questions" not in types


def test_invalid_key_email_template_and_daily_dedup(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, candidate_id = _create_candidate(client, email="mail@example.com")
    _seed_waiting_and_rejected(tmp_env / "offer_coming.db", candidate_id)
    conn = _connect(tmp_env / "offer_coming.db")
    conn.execute(
        "update candidates set llm_key_status='invalid' where id=?",
        (candidate_id,),
    )
    conn.commit()
    conn.close()
    _forbid_llm(monkeypatch)
    sent: list[tuple[str, str, str]] = []

    def fake_send(*, to_email: str, subject: str, body: str) -> tuple[bool, str]:
        sent.append((to_email, subject, body))
        return True, ""

    monkeypatch.setattr("src.services.nudge.send_nudge_email", fake_send)
    monkeypatch.setattr("src.services.mail.send_nudge_email", fake_send)
    _tick(client, 13, 10)
    _tick(client, 13, 11)
    assert len(sent) == 1
    assert sent[0][1] == LLM_KEY_INVALID_EMAIL_SUBJECT
    assert sent[0][2] == LLM_KEY_INVALID_EMAIL_BODY
    assert "我的key" in sent[0][2]
    conn = _connect(tmp_env / "offer_coming.db")
    email_ok = conn.execute(
        "select sent_ok from nudge_logs where channel='email' and beijing_hour_key=?",
        (llm_key_invalid_dedup_key("2026-09-13"),),
    ).fetchone()
    notices = conn.execute(
        "select count(*) from messages where message_type='error_notice'"
    ).fetchone()[0]
    conn.close()
    assert email_ok is not None and email_ok[0] == 1
    assert notices == 2
