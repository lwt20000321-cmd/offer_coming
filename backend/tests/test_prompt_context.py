from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from src.config.settings import get_settings
from src.db.models import Application, Candidate
from src.db.session import get_db_context
from src.services.agent import PROMPT_FILES, load_all_prompts
from src.services.llm_probe import LlmKeyProbeResult
from src.services.prompt_context import (
    SHARED_CONTEXT_BLOCK,
    SHARED_PROMPT_KEYS,
    build_shared_prompt_context,
    fill_shared_variables,
    render_prompt,
)
from src.utils.crypto import beijing_today

FAKE_USER_KEY = "sk-test-prompt-context-key"


@pytest.fixture(autouse=True)
def _candidate_key_and_mock_llm(tmp_env, monkeypatch: pytest.MonkeyPatch) -> None:
    object.__setattr__(get_settings(), "llm_allow_mock", True)

    async def ok_probe(api_key: str, settings: Any = None) -> LlmKeyProbeResult:
        return LlmKeyProbeResult(verdict="ok", http_status=200)

    monkeypatch.setattr("src.services.candidate.probe_llm_api_key", ok_probe)


def _start(client: TestClient, email: str = "prompt.ctx@example.com") -> tuple[str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email, "llm_api_key": FAKE_USER_KEY},
        files={
            "resume": (
                "resume.txt",
                b"Backend intern in Hangzhou, 2 years Python",
                "text/plain",
            )
        },
    )
    assert response.status_code == 201
    data = response.json()["data"]
    return data["session_token"], data["candidate"]["id"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_all_agent_prompts_declare_shared_variables() -> None:
    prompts = load_all_prompts()
    assert set(prompts) == set(PROMPT_FILES)
    for name, text in prompts.items():
        assert SHARED_CONTEXT_BLOCK.strip() in text, f"{name} missing shared context block"
        for key in SHARED_PROMPT_KEYS:
            assert "{{" + key + "}}" in text, f"{name} missing {{{{{key}}}}}"


def test_fill_shared_variables_replaces_placeholders() -> None:
    template = "日期={{current_date}}\n档案={{user_profile}}\n岗位={{job_list}}"
    filled = fill_shared_variables(
        template,
        {
            "current_date": "2026-09-15",
            "user_profile": "邮箱：a@b.com",
            "job_list": "青禾 / 后端",
            "calendar": "出题日：是",
            "uploaded_documents": "简历.txt",
            "knowledge_base": "（暂无）",
            "resume": "Backend intern",
        },
    )
    assert "{{current_date}}" not in filled
    assert "2026-09-15" in filled
    assert "青禾 / 后端" in filled


@pytest.mark.asyncio
async def test_shared_context_fills_from_candidate_data(client: TestClient) -> None:
    token, candidate_id = _start(client)
    created = client.post(
        "/api/applications",
        json={
            "company_name": "青禾",
            "role_title": "后端",
            "status_text": "等待面试",
            "interview_at": "2026-09-20 10:00",
        },
        headers=_headers(token),
    )
    assert created.status_code == 201

    async with get_db_context() as db:
        candidate = (
            await db.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        application = (
            await db.execute(select(Application).where(Application.candidate_id == candidate_id))
        ).scalar_one()
        application.deadline = "2026-09-18"
        await db.flush()
        values = await build_shared_prompt_context(db, candidate)
        rendered = await render_prompt("orchestrator.md", db, candidate)
        counseling = await render_prompt("counseling.md", db, candidate)

    assert values["current_date"] == beijing_today()
    assert "prompt.ctx@example.com" in values["user_profile"]
    assert "求职方向" in values["user_profile"]
    assert "偏好" in values["user_profile"]
    assert "青禾" in values["user_profile"]
    assert "Hangzhou" not in values["user_profile"]
    assert "青禾" in values["job_list"]
    assert "后端" in values["job_list"]
    assert "2026-09-20" in values["calendar"]
    assert "2026-09-18" in values["calendar"]
    assert "用户可用时间" in values["calendar"]
    assert "Hangzhou" in values["resume"]
    assert "resume.txt" in values["resume"]
    assert "岗位 JD" in values["uploaded_documents"]
    assert "截图" in values["uploaded_documents"]
    assert values["knowledge_base"] == "（暂无）"
    assert "心理辅导进行中" in values["session_ended"] or "未标记为辅导会话" in values["session_ended"]
    assert "青禾" in values["recent_event"]
    assert "后端" in values["recent_event"]
    assert "等待面试" in values["recent_event"]
    assert "原话" in values["current_emotion"]
    assert "催促" in values["ready_for_action"]
    assert "{{session_ended}}" not in counseling
    assert "{{recent_event}}" not in counseling
    assert "{{current_emotion}}" not in counseling
    assert "{{ready_for_action}}" not in counseling
    assert "诡秘" in counseling
    assert "心情变好" in counseling
    for key in SHARED_PROMPT_KEYS:
        assert "{{" + key + "}}" not in rendered
    assert beijing_today() in rendered
    assert "prompt.ctx@example.com" in rendered
    assert "识别用户" in rendered or "专业 Agent" in rendered
