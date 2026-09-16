from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from src.config.settings import get_settings
from src.services.llm_probe import LlmKeyProbeResult
from src.services.scheduler import (
    reset_clock,
    set_clock,
    set_scheduler_loop_enabled,
)

TZ = ZoneInfo("Asia/Shanghai")
FAKE_USER_KEY = "sk-test-question-sets-user-key"


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


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_current_question_set_none_without_schedule(client: TestClient) -> None:
    response = client.post(
        "/api/candidates",
        data={"email": "qs-empty@example.com", "llm_api_key": FAKE_USER_KEY},
        files={"resume": ("resume.txt", b"resume text", "text/plain")},
    )
    assert response.status_code == 201
    token = response.json()["data"]["session_token"]
    current = client.get("/api/question-sets/current", headers=_headers(token))
    assert current.status_code == 200
    body = current.json()
    assert body["success"] is True
    assert body["data"]["status"] == "none"
    assert body["data"]["questions"] == []
    assert body["data"]["beijing_date"]


def test_current_question_set_after_question_day_tick(
    client: TestClient, tmp_env: Path
) -> None:
    created = client.post(
        "/api/candidates",
        data={"email": "qs-five@example.com", "llm_api_key": FAKE_USER_KEY},
        files={"resume": ("resume.txt", b"Python project", "text/plain")},
    )
    data = created.json()["data"]
    token = data["session_token"]
    candidate_id = data["candidate"]["id"]

    conn = sqlite3.connect(tmp_env / "offer_coming.db")
    conn.execute(
        """
        insert into applications (
            id, candidate_id, company_name, role_title, jd_url, jd_text,
            exam_points, progress_text, status_text, normalized_status,
            deadline, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "a_qs",
            candidate_id,
            "示例公司",
            "后端开发",
            "https://example.com/job/1",
            "",
            "接口与简历项目对照",
            "已约一面",
            "等待面试",
            "waiting_interview",
            "2026-09-20",
            "2026-09-13T01:00:00+00:00",
            "2026-09-13T01:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    at = datetime(2026, 9, 13, 10, 0, tzinfo=TZ)
    set_clock(lambda value=at: value)
    tick = client.post("/internal/scheduler/tick", params={"at": at.isoformat()})
    assert tick.status_code == 200

    current = client.get("/api/question-sets/current", headers=_headers(token))
    payload = current.json()["data"]
    assert payload["status"] == "in_progress"
    assert payload["beijing_date"] == "2026-09-13"
    assert len(payload["questions"]) == 5
    assert {item["kind"] for item in payload["questions"]} >= {"common", "role"}
    assert all("id" in item and "prompt" in item for item in payload["questions"])
