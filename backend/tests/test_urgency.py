from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from src.db.models import Candidate, QuestionSet
from src.db.session import get_db_context
from src.services import nudge as nudge_mod
from src.services import questions as questions_mod
from src.services.questions import QuestionService
from src.services.scheduler import reset_clock, set_clock, set_scheduler_loop_enabled

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture(autouse=True)
def _isolate_scheduler_loop() -> None:
    set_scheduler_loop_enabled(False)
    yield
    reset_clock()
    set_scheduler_loop_enabled(True)


def _create_candidate(client: TestClient, email: str = "urgency@example.com") -> tuple[str, str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email},
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


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _insert_application(
    db_path: Path,
    candidate_id: str,
    *,
    app_id: str,
    company: str,
    role: str,
    normalized_status: str,
    status_text: str,
    interview_at: str | None = None,
    deadline: str | None = None,
    applied_at: str = "2026-09-10",
    created_at: str = "2026-09-10T01:00:00+00:00",
    exam_points: str = "结合简历中的 Python 项目考查接口设计",
    interview_summary: str = "",
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        insert into applications (
            id, candidate_id, company_name, role_title, jd_url, jd_text,
            exam_points, progress_text, status_text, normalized_status,
            deadline, applied_at, interview_at, interview_summary,
            created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            candidate_id,
            company,
            role,
            f"https://example.com/job/{app_id}",
            "",
            exam_points,
            "已约一面",
            status_text,
            normalized_status,
            deadline,
            applied_at,
            interview_at,
            interview_summary,
            created_at,
            created_at,
        ),
    )
    conn.commit()
    conn.close()


def test_role_questions_target_earlier_interview_not_empty_deadline(
    client: TestClient, tmp_env: Path
) -> None:
    token, _conversation_id, candidate_id = _create_candidate(client)
    db_path = tmp_env / "offer_coming.db"
    _insert_application(
        db_path,
        candidate_id,
        app_id="a_dated",
        company="有时间公司",
        role="后端",
        normalized_status="waiting_interview",
        status_text="等待面试",
        interview_at="2026-09-25",
        deadline="2026-09-25",
        applied_at="2026-09-12",
    )
    _insert_application(
        db_path,
        candidate_id,
        app_id="a_empty",
        company="空时间公司",
        role="前端",
        normalized_status="waiting_interview",
        status_text="等待面试",
        interview_at=None,
        deadline="2026-09-16",
        applied_at="2026-09-01",
    )
    _insert_application(
        db_path,
        candidate_id,
        app_id="a_rej",
        company="已挂公司",
        role="已挂岗位",
        normalized_status="rejected",
        status_text="已挂",
        interview_at="2026-09-15",
        deadline="2026-09-15",
    )

    response = _tick(client, 14, 10)
    assert response.status_code == 200
    current = client.get("/api/question-sets/current", headers=_headers(token))
    questions = current.json()["data"]["questions"]
    assert len(questions) == 5
    role_targets = [
        item["target_application_id"] for item in questions if item["kind"] == "role"
    ]
    assert role_targets
    assert role_targets[0] == "a_dated"
    assert "a_rej" not in {item["target_application_id"] for item in questions}


def test_earlier_interview_at_is_more_urgent_than_later(
    client: TestClient, tmp_env: Path
) -> None:
    token, _conversation_id, candidate_id = _create_candidate(client, "rank@example.com")
    db_path = tmp_env / "offer_coming.db"
    _insert_application(
        db_path,
        candidate_id,
        app_id="a_later",
        company="晚上面",
        role="后端",
        normalized_status="waiting_interview",
        status_text="等待面试",
        interview_at="2026-09-28T14:00:00+08:00",
        deadline="2026-09-28",
    )
    _insert_application(
        db_path,
        candidate_id,
        app_id="a_sooner",
        company="早上面",
        role="后端",
        normalized_status="waiting_interview",
        status_text="等待面试",
        interview_at="2026-09-20T10:00:00+08:00",
        deadline="2026-09-20",
    )
    _tick(client, 14, 10)
    questions = client.get("/api/question-sets/current", headers=_headers(token)).json()["data"][
        "questions"
    ]
    role_targets = [item["target_application_id"] for item in questions if item["kind"] == "role"]
    assert role_targets[0] == "a_sooner"


@pytest.mark.asyncio
async def test_completed_appends_summary_without_overwriting_handwritten(
    client: TestClient, tmp_env: Path
) -> None:
    token, _conversation_id, candidate_id = _create_candidate(client, "review@example.com")
    db_path = tmp_env / "offer_coming.db"
    _insert_application(
        db_path,
        candidate_id,
        app_id="a_wait",
        company="示例公司",
        role="后端开发",
        normalized_status="waiting_interview",
        status_text="等待面试",
        interview_at="2026-09-20",
        deadline="2026-09-20",
        interview_summary="手写总结不要覆盖",
    )
    _tick(client, 14, 10)
    assert client.get("/api/question-sets/current", headers=_headers(token)).json()["data"][
        "status"
    ] == "in_progress"

    async with get_db_context() as db:
        candidate = (
            await db.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        question_set = (
            await db.execute(
                select(QuestionSet).where(
                    QuestionSet.candidate_id == candidate_id,
                    QuestionSet.beijing_date == "2026-09-14",
                )
            )
        ).scalar_one()
        await QuestionService(db).complete_with_review(
            candidate,
            question_set,
            "点评：回答偏结果、少过程。",
            datetime(2026, 9, 14, 11, 0, tzinfo=TZ),
        )

    listed = client.get("/api/applications", headers=_headers(token)).json()["data"]["applications"]
    row = next(item for item in listed if item["id"] == "a_wait")
    assert row["interview_summary"].startswith("手写总结不要覆盖")
    assert "点评：回答偏结果、少过程。" in row["interview_summary"]
    assert row["interview_summary"] != "点评：回答偏结果、少过程。"


def test_hourly_nudge_llm_log_role_is_nudge(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_chat(self, **kwargs):  # noqa: ANN001
        assert kwargs.get("model") == "test-nudge"
        return {"choices": [{"message": {"content": "现在温柔催一下进度。"}}]}

    monkeypatch.setattr(nudge_mod, "_llm_configured", lambda: True)
    monkeypatch.setattr(nudge_mod.LlmClient, "chat_completions", fake_chat)

    _create_candidate(client, "nudge-log@example.com")
    db_path = tmp_env / "offer_coming.db"
    conn = sqlite3.connect(db_path)
    candidate_id = conn.execute("select id from candidates").fetchone()[0]
    conn.close()
    _insert_application(
        db_path,
        candidate_id,
        app_id="a_nudge",
        company="催促公司",
        role="后端",
        normalized_status="waiting_interview",
        status_text="等待面试",
        interview_at="2026-09-20",
        deadline="2026-09-20",
    )
    response = _tick(client, 14, 10)
    assert response.status_code == 200

    conn = sqlite3.connect(db_path)
    roles = [
        row[0]
        for row in conn.execute(
            "select role from llm_call_logs where purpose='hourly_nudge'"
        ).fetchall()
    ]
    models = [
        row[0]
        for row in conn.execute(
            "select model from llm_call_logs where purpose='hourly_nudge'"
        ).fetchall()
    ]
    conn.close()
    assert roles
    assert set(roles) == {"nudge"}
    assert "counseling" not in roles
    assert "interview" not in roles
    assert set(models) == {"test-nudge"}


def test_no_key_nudge_and_questions_mark_degrade(client: TestClient, tmp_env: Path) -> None:
    token, _conversation_id, candidate_id = _create_candidate(client, "degrade@example.com")
    _insert_application(
        tmp_env / "offer_coming.db",
        candidate_id,
        app_id="a_deg",
        company="降级公司",
        role="后端",
        normalized_status="waiting_interview",
        status_text="等待面试",
        interview_at="2026-09-20",
        deadline="2026-09-20",
    )
    _tick(client, 14, 10)
    current = client.get("/api/question-sets/current", headers=_headers(token))
    assert len(current.json()["data"]["questions"]) == 5
    assert questions_mod.last_used_fallback is True
    assert "降级" in questions_mod.last_degrade_log
    assert nudge_mod.last_used_fallback is True
    assert "降级" in nudge_mod.last_degrade_log
