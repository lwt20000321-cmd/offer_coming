from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from src.config.settings import get_settings
from src.services.llm_probe import probe_llm_api_key
from src.utils.crypto import decrypt_llm_api_key, encrypt_llm_api_key

FAKE_USER_KEY = "sk-test-user-probe-key"
FAKE_OPERATOR_KEY = "sk-test-operator-must-not-be-used"
FAKE_OTHER_KEY = "sk-test-other-must-not-overwrite"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class ProbeStub:
    def __init__(
        self,
        *,
        get_status: int | None = 200,
        post_status: int | None = None,
        get_error: Exception | None = None,
        post_error: Exception | None = None,
        get_payload: dict[str, Any] | None = None,
        post_payload: dict[str, Any] | None = None,
    ) -> None:
        self.get_status = get_status
        self.post_status = post_status
        self.get_error = get_error
        self.post_error = post_error
        self.get_payload = get_payload or {}
        self.post_payload = post_payload or {}
        self.calls: list[tuple[str, str | None]] = []

    def assert_user_key_only(self, user_key: str = FAKE_USER_KEY) -> None:
        assert self.calls
        expected = f"Bearer {user_key}"
        for _method, auth in self.calls:
            assert auth == expected
            assert FAKE_OPERATOR_KEY not in (auth or "")


class _FakeAsyncClient:
    def __init__(self, stub: ProbeStub) -> None:
        self._stub = stub

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def _auth(self, headers: dict[str, str] | None) -> str | None:
        if not headers:
            return None
        return headers.get("Authorization")

    async def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        self._stub.calls.append(("GET", self._auth(headers)))
        if self._stub.get_error is not None:
            raise self._stub.get_error
        assert self._stub.get_status is not None
        return FakeResponse(self._stub.get_status, self._stub.get_payload)

    async def post(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> FakeResponse:
        self._stub.calls.append(("POST", self._auth(headers)))
        if self._stub.post_error is not None:
            raise self._stub.post_error
        assert self._stub.post_status is not None
        return FakeResponse(self._stub.post_status, self._stub.post_payload)


def install_probe_stub(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> ProbeStub:
    stub = ProbeStub(**kwargs)

    def factory(*args: Any, **kw: Any) -> _FakeAsyncClient:
        assert kw.get("trust_env") is False
        return _FakeAsyncClient(stub)

    monkeypatch.setattr("httpx.AsyncClient", factory)
    return stub


def set_operator_key() -> None:
    get_settings().llm_api_key = FAKE_OPERATOR_KEY


@pytest.fixture(autouse=True)
def _operator_key_and_ok_probe(
    tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> ProbeStub:
    set_operator_key()
    return install_probe_stub(monkeypatch, get_status=200)


async def test_probe_ok_uses_submitted_key_not_operator(
    _operator_key_and_ok_probe: ProbeStub,
) -> None:
    result = await probe_llm_api_key(FAKE_USER_KEY)
    assert result.verdict == "ok"
    assert get_settings().llm_api_key == FAKE_OPERATOR_KEY
    _operator_key_and_ok_probe.assert_user_key_only()


async def test_probe_401_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = install_probe_stub(monkeypatch, get_status=401)
    result = await probe_llm_api_key(FAKE_USER_KEY)
    assert result.verdict == "invalid"
    stub.assert_user_key_only()


async def test_probe_invalid_api_key_body_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = install_probe_stub(
        monkeypatch,
        get_status=400,
        get_payload={"error": {"code": "invalid_api_key", "message": "Incorrect API key"}},
    )
    result = await probe_llm_api_key(FAKE_USER_KEY)
    assert result.verdict == "invalid"
    stub.assert_user_key_only()


async def test_probe_429_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = install_probe_stub(monkeypatch, get_status=429)
    result = await probe_llm_api_key(FAKE_USER_KEY)
    assert result.verdict == "unreachable"
    stub.assert_user_key_only()


async def test_probe_5xx_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = install_probe_stub(monkeypatch, get_status=503)
    result = await probe_llm_api_key(FAKE_USER_KEY)
    assert result.verdict == "unreachable"
    stub.assert_user_key_only()


async def test_probe_timeout_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = install_probe_stub(monkeypatch, get_error=httpx.TimeoutException("timed out"))
    result = await probe_llm_api_key(FAKE_USER_KEY)
    assert result.verdict == "unreachable"
    stub.assert_user_key_only()


async def test_probe_models_404_falls_back_to_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = install_probe_stub(monkeypatch, get_status=404, post_status=200)
    result = await probe_llm_api_key(FAKE_USER_KEY)
    assert result.verdict == "ok"
    assert [method for method, _auth in stub.calls] == ["GET", "POST"]
    stub.assert_user_key_only()


def test_hkdf_roundtrip_is_not_plaintext() -> None:
    secret = "test-secret-key-not-for-production"
    ciphertext = encrypt_llm_api_key(FAKE_USER_KEY, secret)
    assert FAKE_USER_KEY not in ciphertext
    assert decrypt_llm_api_key(ciphertext, secret) == FAKE_USER_KEY
