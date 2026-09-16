"""T-030：头像任务栏与 Key 失效真实验收（隔离库）。

覆盖 AC-050–054 的后端主路径。不打真实百炼，不把运营 llm_api_key
当成求职者 Key。frontend VITE_USE_MOCK=false 的页面验收另做。
"""

from __future__ import annotations

import ipaddress
import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from src.config.settings import get_settings
from src.services.llm_probe import LlmKeyProbeResult
from src.services.mail import LLM_KEY_INVALID_EMAIL_BODY
from src.services.scheduler import reset_clock, set_clock, set_scheduler_loop_enabled
from src.utils.crypto import decrypt_llm_api_key

TZ = ZoneInfo("Asia/Shanghai")
USER_KEY = "sk-t030-ok-user-not-operator"
NEW_KEY = "sk-t030-ok-user-replacement"
INVALID_KEY = "sk-t030-invalid-user"
OPERATOR_KEY = "sk-t030-operator-must-not-be-used"
OLD_FINGERPRINT = "T030OldResumeAlpha"
NEW_FINGERPRINT = "T030NewResumeOmega"
OLD_RESUME = f"{OLD_FINGERPRINT} Python intern with SQLite.".encode()
NEW_RESUME = f"{NEW_FINGERPRINT} Go intern distributed systems.".encode()


@pytest.fixture(autouse=True)
def _isolate_scheduler_and_probe(tmp_env: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    object.__setattr__(get_settings(), "llm_allow_mock", True)
    object.__setattr__(get_settings(), "llm_api_key", OPERATOR_KEY)

    async def ok_probe(api_key: str, settings: Any = None) -> LlmKeyProbeResult:
        if api_key == INVALID_KEY:
            return LlmKeyProbeResult(verdict="invalid", http_status=401)
        return LlmKeyProbeResult(verdict="ok", http_status=200)

    monkeypatch.setattr("src.services.candidate.probe_llm_api_key", ok_probe)
    set_scheduler_loop_enabled(False)
    yield
    reset_clock()
    set_scheduler_loop_enabled(True)


def _create(
    client: TestClient,
    *,
    email: str = "t030@example.com",
    filename: str = "old-resume.txt",
    content: bytes = OLD_RESUME,
    llm_api_key: str = USER_KEY,
) -> tuple[str, str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email, "llm_api_key": llm_api_key},
        files={"resume": (filename, content, "text/plain")},
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    return data["session_token"], data["conversation_id"], data["candidate"]["id"]


def _auth(token: str, *, sse: bool = False) -> dict[str, str]:
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
            events.append((name, json.loads(line.split(":", 1)[1].strip())))
            name = ""
    return events


def _assert_no_key_leak(response: Any, body: dict, *keys: str, logs: str = "") -> None:
    blob = response.text + logs
    for key in (USER_KEY, NEW_KEY, INVALID_KEY, OPERATOR_KEY, *keys):
        assert key not in blob
    assert "llm_api_key_ciphertext" not in blob
    data = body.get("data")
    if isinstance(data, dict):
        assert "llm_api_key" not in data
        candidate = data.get("candidate")
        if isinstance(candidate, dict):
            assert "llm_api_key" not in candidate


def _connect(tmp_env: Path) -> sqlite3.Connection:
    return sqlite3.connect(tmp_env / "offer_coming.db")


def _patch_public_jd(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<html><body><p>招聘后端，需要项目经验与接口设计</p></body></html>"

    def fake_host_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            return [ipaddress.ip_address("93.184.216.34")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    def factory(settings: Any = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr("src.services.jd_fetch._host_ips", fake_host_ips)
    monkeypatch.setattr("src.services.jd_fetch.create_jd_http_client", factory)


def _install_llm_transport(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_for: dict[str, int] | None = None,
) -> list[str | None]:
    auths: list[str | None] = []
    verdicts = status_for or {}

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization")
        auths.append(auth)
        token = (auth or "").removeprefix("Bearer ").strip()
        status = verdicts.get(token, 200)
        if status == 401:
            return httpx.Response(
                401,
                json={"error": {"code": "invalid_api_key", "message": "denied"}},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "我在。这是占位模型回复。",
                        }
                    }
                ]
            },
        )

    def factory(current: Any = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("src.services.llm_client.create_llm_http_client", factory)
    return auths


def _seed_waiting(tmp_env: Path, candidate_id: str) -> None:
    conn = _connect(tmp_env)
    conn.execute(
        """
        insert into applications (
            id, candidate_id, company_name, role_title, jd_url, jd_text,
            exam_points, progress_text, status_text, normalized_status,
            deadline, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "a_t030_wait",
            candidate_id,
            "T030公司",
            "后端",
            "https://example.com/job/t030",
            "",
            "旧考查点",
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


def _tick(client: TestClient, day: int, hour: int) -> Any:
    current = datetime(2026, 9, day, hour, 0, 0, tzinfo=TZ)
    set_clock(lambda value=current: value)
    return client.post("/internal/scheduler/tick", params={"at": current.isoformat()})


def test_ac051_replace_resume_then_exam_points_use_new_text(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, conversation_id, _candidate_id = _create(client)
    _patch_public_jd(monkeypatch)

    replaced = client.post(
        "/api/candidates/current/resume",
        headers=_auth(token),
        files={"resume": ("new-resume.txt", NEW_RESUME, "text/plain")},
    )
    payload = replaced.json()
    assert replaced.status_code == 200
    assert payload["data"]["resume_filename"] == "new-resume.txt"
    assert payload["data"]["resume_parse_ok"] is True
    assert payload["data"]["has_llm_api_key"] is True
    _assert_no_key_leak(replaced, payload)
    conn = _connect(tmp_env)
    resume_text = conn.execute("select resume_text from candidates").fetchone()[0]
    conn.close()
    assert NEW_FINGERPRINT in resume_text
    assert OLD_FINGERPRINT not in resume_text

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "https://jobs.example.com/t030 这个岗位"},
        headers=_auth(token, sse=True),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "done"
    content = events[-1][1]["message"]["content"]
    assert NEW_FINGERPRINT in content
    assert OLD_FINGERPRINT not in content
    conn = _connect(tmp_env)
    exam = conn.execute("select exam_points from applications").fetchone()[0]
    conn.close()
    assert NEW_FINGERPRINT in exam
    assert OLD_FINGERPRINT not in exam


def test_ac052_update_key_not_echoed_and_later_calls_use_new_key(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token, conversation_id, _candidate_id = _create(client)
    updated = client.put(
        "/api/candidates/current/llm-key",
        headers=_auth(token),
        json={"llm_api_key": NEW_KEY},
    )
    payload = updated.json()
    assert updated.status_code == 200
    assert payload["data"]["has_llm_api_key"] is True
    assert payload["data"]["llm_key_status"] == "saved"
    assert "已保存" in payload["message"]
    _assert_no_key_leak(updated, payload, logs=caplog.text)

    conn = _connect(tmp_env)
    cipher = conn.execute("select llm_api_key_ciphertext from candidates").fetchone()[0]
    conn.close()
    assert decrypt_llm_api_key(cipher, get_settings().secret_key) == NEW_KEY

    object.__setattr__(get_settings(), "llm_allow_mock", False)
    auths = _install_llm_transport(monkeypatch)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "你好，帮我规划一下"},
        headers=_auth(token, sse=True),
    )
    assert response.status_code == 200
    assert auths
    assert all(item == f"Bearer {NEW_KEY}" for item in auths)
    assert all(OPERATOR_KEY not in (item or "") for item in auths)
    assert all(USER_KEY not in (item or "") for item in auths)
    _assert_no_key_leak(response, {"data": {}}, logs=caplog.text)


def test_ac053_revoke_then_old_token_401_same_email_reenters(
    client: TestClient,
    tmp_env: Path,
) -> None:
    token, _conversation_id, _candidate_id = _create(client, email="t030.exit@example.com")
    revoked = client.delete("/api/sessions/current", headers=_auth(token))
    payload = revoked.json()
    assert revoked.status_code == 200
    assert payload["data"]["revoked"] is True
    _assert_no_key_leak(revoked, payload)

    current = client.get("/api/candidates/current", headers=_auth(token))
    assert current.status_code == 401
    assert current.json()["error_code"] == "UNAUTHORIZED"

    resumed = client.post("/api/sessions", json={"email": "t030.exit@example.com"})
    body = resumed.json()
    assert resumed.status_code == 200
    new_token = body["data"]["session_token"]
    assert new_token
    assert new_token != token
    assert body["data"]["candidate"]["has_llm_api_key"] is True
    assert body["data"]["candidate"]["resume_filename"] == "old-resume.txt"
    _assert_no_key_leak(resumed, body)

    ok = client.get("/api/candidates/current", headers=_auth(new_token))
    assert ok.status_code == 200
    assert ok.json()["data"]["resume_filename"] == "old-resume.txt"


def test_ac054_injected_invalid_key_sse_no_fake_success(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, conversation_id, candidate_id = _create(client, email="t030.invalid@example.com")
    object.__setattr__(get_settings(), "llm_allow_mock", False)
    auths = _install_llm_transport(monkeypatch, status_for={USER_KEY: 401})
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "你好，帮我规划一下"},
        headers=_auth(token, sse=True),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["error_code"] == "LLM_KEY_INVALID"
    assert "我的key" in events[-1][1]["error"]
    assert not any(name == "done" for name, _ in events)
    assert auths
    assert all(item == f"Bearer {USER_KEY}" for item in auths)
    assert all(OPERATOR_KEY not in (item or "") for item in auths)

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_auth(token),
    )
    rows = messages.json()["data"]["messages"]
    assistant = [item for item in rows if item["role"] == "assistant"]
    assert assistant[-1]["message_type"] == "error_notice"
    assert "我的key" in assistant[-1]["content"]
    assert not any(
        item["role"] == "assistant" and item["message_type"] not in {"error_notice"}
        for item in rows
    )
    conn = _connect(tmp_env)
    status = conn.execute(
        "select llm_key_status from candidates where id=?",
        (candidate_id,),
    ).fetchone()[0]
    conn.close()
    assert status == "invalid"


def test_ac054_scheduler_invalid_key_notice_skips_models_smtp_unconfigured(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, conversation_id, candidate_id = _create(client, email="t030.nudge@example.com")
    _seed_waiting(tmp_env, candidate_id)
    conn = _connect(tmp_env)
    conn.execute(
        "update candidates set llm_key_status='invalid' where id=?",
        (candidate_id,),
    )
    conn.commit()
    conn.close()

    async def boom(self: Any, **kwargs: Any) -> None:
        raise AssertionError("调度在 Key 不可用时不得调用模型")

    monkeypatch.setattr("src.services.llm_client.LlmClient.chat_completions", boom)
    tick = _tick(client, 13, 10)
    assert tick.status_code == 200

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_auth(token),
    )
    rows = messages.json()["data"]["messages"]
    types = [item["message_type"] for item in rows]
    assert "nudge" not in types
    assert "questions" not in types
    assert "error_notice" in types
    assert any(
        item["content"] == LLM_KEY_INVALID_EMAIL_BODY
        for item in rows
        if item["message_type"] == "error_notice"
    )
    conn = _connect(tmp_env)
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
    assert email_rows[0][1]
    assert "llm_key_invalid:2026-09-13" in email_rows[0][2]
    joined = " ".join(item["content"] for item in rows)
    assert "已发送" not in joined
    assert "已经发到邮箱" not in joined
    assert OPERATOR_KEY not in joined
