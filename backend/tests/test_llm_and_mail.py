from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from src.config.settings import get_settings
from src.services.llm_client import (
    LlmClient,
    LlmKeyInvalidError,
    LlmKeyMissingError,
    create_llm_http_client,
)
from src.services.mail import (
    LLM_KEY_INVALID_EMAIL_BODY,
    LLM_KEY_INVALID_EMAIL_SUBJECT,
    send_llm_key_invalid_email,
    send_nudge_email,
)

FAKE_USER_KEY = "sk-test-candidate-bearer"
FAKE_OPERATOR_KEY = "sk-test-operator-must-not-be-used"


def test_llm_http_client_disables_env_trust(tmp_env) -> None:
    client = create_llm_http_client(get_settings())
    assert client.trust_env is False
    assert get_settings().llm_api_key == ""


def test_smtp_unconfigured_does_not_pretend_sent(tmp_env) -> None:
    sent_ok, reason = send_nudge_email(
        to_email="user@example.com",
        subject="催促",
        body="待办",
    )
    assert sent_ok is False
    assert "尚未配置" in reason


@pytest.mark.asyncio
async def test_chat_completions_rejects_missing_or_empty_key(
    tmp_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    object.__setattr__(settings, "llm_api_key", FAKE_OPERATOR_KEY)
    object.__setattr__(settings, "llm_allow_mock", False)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": []})

    def factory(current=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("src.services.llm_client.create_llm_http_client", factory)
    client = LlmClient(settings)
    with pytest.raises(LlmKeyMissingError):
        await client.chat_completions(
            messages=[{"role": "user", "content": "hi"}],
            model="test-orchestrator",
        )
    with pytest.raises(LlmKeyMissingError):
        await client.chat_completions(
            api_key="   ",
            messages=[{"role": "user", "content": "hi"}],
            model="test-orchestrator",
        )
    assert seen == []


@pytest.mark.asyncio
async def test_chat_completions_uses_candidate_key_not_operator(
    tmp_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    object.__setattr__(settings, "llm_api_key", FAKE_OPERATOR_KEY)
    object.__setattr__(settings, "llm_allow_mock", False)
    captured: list[str | None] = []
    logs: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("Authorization"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    def factory(current=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(handler))

    def capture_info(message: str, *args: Any, **kwargs: Any) -> None:
        logs.append((message, kwargs))

    monkeypatch.setattr("src.services.llm_client.create_llm_http_client", factory)
    monkeypatch.setattr("src.services.llm_client.logger.info", capture_info)
    data = await LlmClient(settings).chat_completions(
        api_key=FAKE_USER_KEY,
        candidate_id="c_test",
        messages=[{"role": "user", "content": "hi"}],
        model="test-interview",
    )
    assert data["choices"][0]["message"]["content"] == "ok"
    assert captured == [f"Bearer {FAKE_USER_KEY}"]
    assert FAKE_OPERATOR_KEY not in (captured[0] or "")
    assert logs
    message, fields = logs[-1]
    assert "Chat Completions" in message
    assert fields.get("status_code") == 200
    assert fields.get("model") == "test-interview"
    assert fields.get("candidate_id") == "c_test"
    dumped = message + " " + " ".join(f"{k}={v}" for k, v in fields.items())
    assert FAKE_USER_KEY not in dumped
    assert FAKE_OPERATOR_KEY not in dumped
    assert "Authorization" not in dumped
    assert "Bearer" not in dumped


@pytest.mark.asyncio
async def test_chat_completions_401_is_invalid_and_not_retried(
    tmp_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    object.__setattr__(settings, "llm_api_key", FAKE_OPERATOR_KEY)
    object.__setattr__(settings, "llm_allow_mock", False)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        assert request.headers.get("Authorization") == f"Bearer {FAKE_USER_KEY}"
        return httpx.Response(
            401,
            json={"error": {"code": "invalid_api_key", "message": "invalid"}},
        )

    def factory(current=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("src.services.llm_client.create_llm_http_client", factory)
    with pytest.raises(LlmKeyInvalidError) as exc:
        await LlmClient(settings).chat_completions(
            api_key=FAKE_USER_KEY,
            candidate_id="c_test",
            messages=[{"role": "user", "content": "hi"}],
            model="test-orchestrator",
        )
    assert exc.value.error_code == "LLM_KEY_INVALID"
    assert "我的key" in exc.value.user_message
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_iter_chat_completions_yields_content_deltas(
    tmp_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    object.__setattr__(settings, "llm_api_key", FAKE_OPERATOR_KEY)
    object.__setattr__(settings, "llm_allow_mock", False)
    captured: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("Authorization"))
        body = json.loads(request.content.decode("utf-8"))
        assert body["stream"] is True
        sse = (
            'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=sse.encode("utf-8"),
        )

    def factory(current=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("src.services.llm_client.create_llm_http_client", factory)

    texts: list[str] = []
    complete = None
    async for event in LlmClient(settings).iter_chat_completions(
        api_key=FAKE_USER_KEY,
        candidate_id="c_test",
        messages=[{"role": "user", "content": "hi"}],
        model="test-orchestrator",
    ):
        if event.kind == "content":
            texts.append(event.text)
        elif event.kind == "complete":
            complete = event.response
    assert texts == ["你", "好"]
    assert complete is not None
    assert complete["choices"][0]["message"]["content"] == "你好"
    assert captured == [f"Bearer {FAKE_USER_KEY}"]
    assert FAKE_OPERATOR_KEY not in (captured[0] or "")


def test_key_invalid_email_unconfigured_does_not_pretend(tmp_env) -> None:
    sent_ok, reason = send_llm_key_invalid_email(to_email="user@example.com")
    assert sent_ok is False
    assert "尚未配置" in reason
    assert LLM_KEY_INVALID_EMAIL_SUBJECT
    assert "我的key" in LLM_KEY_INVALID_EMAIL_BODY
