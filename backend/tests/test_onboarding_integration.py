"""T-029：邮箱 + Key + 简历进入与接续（隔离库，探测走 httpx stub）。

覆盖 AC-001/002/017/021/049 的接口主路径。不打真实百炼，不把运营 llm_api_key
当成求职者 Key。frontend VITE_USE_MOCK=false 的页面验收另做。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from src.config.settings import get_settings
from src.utils.crypto import decrypt_llm_api_key
from tests.test_llm_probe import (
    FAKE_OPERATOR_KEY,
    FAKE_OTHER_KEY,
    FAKE_USER_KEY,
    ProbeStub,
    install_probe_stub,
    set_operator_key,
)

_ONBOARDING = "请先填写邮箱、粘贴百炼 Key 并上传简历，才能和小凹对话。"
_RESUME = b"Liu Wentao, Python backend intern. Built BookSwap."
_APP_COMPANY = "T029验收科技"


@pytest.fixture(autouse=True)
def _operator_key_ok_probe_and_mock_llm(
    tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> ProbeStub:
    set_operator_key()
    object.__setattr__(get_settings(), "llm_allow_mock", True)
    return install_probe_stub(monkeypatch, get_status=200)


def _create(
    client: TestClient,
    *,
    email: str = "t029@example.com",
    filename: str = "t029-resume.txt",
    content: bytes = _RESUME,
    llm_api_key: str | None = FAKE_USER_KEY,
) -> tuple[Any, dict]:
    data: dict[str, str] = {"email": email}
    if llm_api_key is not None:
        data["llm_api_key"] = llm_api_key
    response = client.post(
        "/api/candidates",
        data=data,
        files={"resume": (filename, content, "text/plain")},
    )
    return response, response.json()


def _auth(token: str, *, sse: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if sse:
        headers["Accept"] = "text/event-stream"
    return headers


def _assert_no_key_leak(response: Any, body: dict, *keys: str) -> None:
    blob = response.text
    for key in keys or (FAKE_USER_KEY,):
        assert key not in blob
    assert FAKE_OPERATOR_KEY not in blob
    assert "llm_api_key_ciphertext" not in blob
    data = body.get("data")
    if isinstance(data, dict):
        assert "llm_api_key" not in data
        candidate = data.get("candidate")
        if isinstance(candidate, dict):
            assert "llm_api_key" not in candidate


def _strip_saved_key(tmp_env: Path) -> None:
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        "update candidates set llm_api_key_ciphertext = null, llm_key_status = 'missing'"
    )
    db.commit()
    db.close()


def test_ac002_missing_email_resume_or_key_cannot_enter(client: TestClient) -> None:
    missing_key, body = _create(client, llm_api_key=None)
    assert missing_key.status_code == 400
    assert body["error_code"] == "VALIDATION_ERROR"
    assert body["error"] == _ONBOARDING
    _assert_no_key_leak(missing_key, body)

    missing_file = client.post(
        "/api/candidates",
        data={"email": "t029@example.com", "llm_api_key": FAKE_USER_KEY},
    )
    file_body = missing_file.json()
    assert missing_file.status_code == 400
    assert file_body["error_code"] == "VALIDATION_ERROR"
    assert "填写邮箱" in file_body["error"]
    assert "粘贴" in file_body["error"] and "Key" in file_body["error"]
    assert "上传简历" in file_body["error"]
    _assert_no_key_leak(missing_file, file_body)

    bad_format, bad_body = _create(client, llm_api_key="not-a-dashscope-key")
    assert bad_format.status_code == 400
    assert bad_body["error_code"] == "VALIDATION_ERROR"
    assert bad_body["error"] == _ONBOARDING
    _assert_no_key_leak(bad_format, bad_body, "not-a-dashscope-key")

    unauth = client.get("/api/conversations/current")
    assert unauth.status_code == 401
    assert unauth.json()["error_code"] == "UNAUTHORIZED"


def test_ac001_create_with_key_then_send_message(
    client: TestClient, tmp_env: Path
) -> None:
    response, body = _create(client)
    assert response.status_code == 201, response.text
    data = body["data"]
    assert body["message"] == "已进入小凹"
    assert data["candidate"]["email"] == "t029@example.com"
    assert data["candidate"]["resume_filename"] == "t029-resume.txt"
    assert data["candidate"]["has_llm_api_key"] is True
    assert data["candidate"]["llm_key_status"] == "saved"
    assert data["llm_key_probe_status"] == "ok"
    assert data["session_token"]
    assert data["conversation_id"]
    _assert_no_key_leak(response, body)

    db = sqlite3.connect(tmp_env / "offer_coming.db")
    ciphertext = db.execute("select llm_api_key_ciphertext from candidates").fetchone()[0]
    db.close()
    assert ciphertext
    assert FAKE_USER_KEY not in ciphertext
    assert decrypt_llm_api_key(ciphertext, get_settings().secret_key) == FAKE_USER_KEY

    token = data["session_token"]
    conversation_id = data["conversation_id"]
    sent = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "你好小凹，这是 T-029 进入探测。"},
        headers=_auth(token, sse=True),
    )
    assert sent.status_code == 200, sent.text
    assert sent.headers["content-type"].startswith("text/event-stream")
    assert FAKE_USER_KEY not in sent.text
    assert FAKE_OPERATOR_KEY not in sent.text
    assert "你好小凹，这是 T-029 进入探测。" in sent.text or "event: done" in sent.text


def test_ac017_token_restores_resume_apps_and_today(client: TestClient) -> None:
    created, body = _create(client, email="t029.restore@example.com")
    assert created.status_code == 201
    token = body["data"]["session_token"]
    resume_name = body["data"]["candidate"]["resume_filename"]

    added = client.post(
        "/api/applications",
        json={
            "company_name": _APP_COMPANY,
            "role_title": "产品经理",
            "status_text": "等待面试",
            "interview_at": "2026-09-20",
        },
        headers=_auth(token),
    )
    assert added.status_code == 201, added.text

    current = client.get("/api/candidates/current", headers=_auth(token))
    payload = current.json()
    data = payload["data"]
    assert current.status_code == 200
    assert data["email"] == "t029.restore@example.com"
    assert data["resume_filename"] == resume_name
    assert data["has_llm_api_key"] is True
    assert data["llm_key_status"] == "saved"
    assert data["application_count"] == 1
    assert data["waiting_interview_count"] == 1
    assert data["today"]["question_set_status"] == "none"
    assert "llm_key_probe_status" not in data
    _assert_no_key_leak(current, payload)

    apps = client.get("/api/applications", headers=_auth(token))
    rows = apps.json()["data"]["applications"]
    assert len(rows) == 1
    assert rows[0]["company_name"] == _APP_COMPANY

    questions = client.get("/api/question-sets/current", headers=_auth(token))
    assert questions.status_code == 200
    assert questions.json()["data"]["status"] == "none"


def test_ac021_email_only_continues_existing_key(client: TestClient) -> None:
    created, first = _create(client, email="t029.device@example.com")
    assert created.status_code == 201
    first_token = first["data"]["session_token"]
    resume_name = first["data"]["candidate"]["resume_filename"]

    client.post(
        "/api/applications",
        json={"company_name": _APP_COMPANY, "role_title": "后端", "status_text": "等待面试"},
        headers=_auth(first_token),
    )

    resumed = client.post("/api/sessions", json={"email": "t029.device@example.com"})
    body = resumed.json()
    assert resumed.status_code == 200
    assert body["message"] == "已接上原来的简历和任务"
    assert body["data"]["session_token"] != first_token
    assert body["data"]["candidate"]["resume_filename"] == resume_name
    assert body["data"]["candidate"]["has_llm_api_key"] is True
    assert body["data"]["llm_key_probe_status"] is None
    assert "verification_code" not in resumed.text
    _assert_no_key_leak(resumed, body)

    current = client.get(
        "/api/candidates/current",
        headers=_auth(body["data"]["session_token"]),
    )
    data = current.json()["data"]
    assert data["application_count"] == 1
    assert data["resume_filename"] == resume_name
    assert data["has_llm_api_key"] is True


def test_ac049_email_without_key_requires_subsidy(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, _ = _create(client, email="t029.subsidy@example.com")
    assert created.status_code == 201
    _strip_saved_key(tmp_env)

    blocked = client.post("/api/sessions", json={"email": "t029.subsidy@example.com"})
    blocked_body = blocked.json()
    assert blocked.status_code == 409
    assert blocked_body["error_code"] == "LLM_KEY_REQUIRED"
    assert "还没有百炼 Key" in blocked_body["error"]
    assert "verification_code" not in blocked.text
    _assert_no_key_leak(blocked, blocked_body)

    stub = install_probe_stub(monkeypatch, get_status=200)
    entered = client.post(
        "/api/sessions",
        json={"email": "t029.subsidy@example.com", "llm_api_key": FAKE_OTHER_KEY},
    )
    body = entered.json()
    assert entered.status_code == 200, entered.text
    assert body["data"]["candidate"]["has_llm_api_key"] is True
    assert body["data"]["llm_key_probe_status"] == "ok"
    assert body["data"]["candidate"]["resume_filename"] == "t029-resume.txt"
    _assert_no_key_leak(entered, body, FAKE_OTHER_KEY, FAKE_USER_KEY)
    stub.assert_user_key_only(FAKE_OTHER_KEY)

    db = sqlite3.connect(tmp_env / "offer_coming.db")
    ciphertext = db.execute("select llm_api_key_ciphertext from candidates").fetchone()[0]
    db.close()
    assert decrypt_llm_api_key(ciphertext, get_settings().secret_key) == FAKE_OTHER_KEY


def test_probe_unreachable_still_enters(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = install_probe_stub(monkeypatch, get_error=httpx.TimeoutException("timed out"))
    response, body = _create(client, email="t029.unreach@example.com")
    assert response.status_code == 201
    assert body["data"]["llm_key_probe_status"] == "unreachable"
    assert "这次没连上" in body["message"]
    assert body["data"]["candidate"]["has_llm_api_key"] is True
    _assert_no_key_leak(response, body)
    stub.assert_user_key_only()


def test_probe_401_rejects_and_does_not_create(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = install_probe_stub(monkeypatch, get_status=401)
    response, body = _create(client, email="t029.invalid@example.com")
    assert response.status_code == 400
    assert body["error_code"] == "LLM_KEY_INVALID"
    assert "不能用" in body["error"]
    _assert_no_key_leak(response, body)
    stub.assert_user_key_only()
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    count = db.execute("select count(*) from candidates").fetchone()[0]
    db.close()
    assert count == 0


def test_create_request_uses_llm_api_key_field_and_never_echoes(
    client: TestClient,
) -> None:
    response, body = _create(client, email="t029.field@example.com")
    dumped = json.dumps(body, ensure_ascii=False)
    assert response.status_code == 201
    assert '"llm_api_key"' not in dumped
    assert FAKE_USER_KEY not in dumped
    assert FAKE_OPERATOR_KEY not in dumped
    assert get_settings().llm_api_key == FAKE_OPERATOR_KEY
    assert body["data"]["candidate"]["has_llm_api_key"] is True
