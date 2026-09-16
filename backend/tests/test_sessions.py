from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from src.config.settings import get_settings
from src.utils.crypto import decrypt_llm_api_key
from tests.test_llm_probe import (
    FAKE_OTHER_KEY,
    FAKE_USER_KEY,
    ProbeStub,
    install_probe_stub,
    set_operator_key,
)


@pytest.fixture(autouse=True)
def _operator_key_and_ok_probe(
    tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> ProbeStub:
    set_operator_key()
    return install_probe_stub(monkeypatch, get_status=200)


def _create(client: TestClient, email: str = "foo@example.com") -> Any:
    return client.post(
        "/api/candidates",
        data={"email": email, "llm_api_key": FAKE_USER_KEY},
        files={"resume": ("resume.txt", b"experience", "text/plain")},
    )


def test_continue_known_email(client: TestClient) -> None:
    created = _create(client, email="  Foo@Example.COM ")
    assert created.status_code == 201
    first_token = created.json()["data"]["session_token"]

    response = client.post("/api/sessions", json={"email": "foo@example.com"})
    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["message"] == "已接上原来的简历和任务"
    assert body["data"]["candidate"]["email"] == "foo@example.com"
    assert body["data"]["candidate"]["has_llm_api_key"] is True
    assert body["data"]["llm_key_probe_status"] is None
    assert body["data"]["session_token"] != first_token
    assert "verification_code" not in body["data"]
    assert "verification_code" not in body
    assert FAKE_USER_KEY not in response.text
    assert "llm_api_key" not in body["data"]


def test_continue_unknown_email(client: TestClient) -> None:
    response = client.post("/api/sessions", json={"email": "nobody@example.com"})
    body = response.json()
    assert response.status_code == 404
    assert body["error_code"] == "NOT_FOUND"
    assert "还没有简历记录" in body["error"]


def test_continue_without_key_requires_subsidy(
    client: TestClient,
    tmp_env: Path,
) -> None:
    created = _create(client)
    assert created.status_code == 201
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        "update candidates set llm_api_key_ciphertext = null, llm_key_status = 'missing'"
    )
    db.commit()
    db.close()

    response = client.post("/api/sessions", json={"email": "foo@example.com"})
    body = response.json()
    assert response.status_code == 409
    assert body["error_code"] == "LLM_KEY_REQUIRED"
    assert "还没有百炼 Key" in body["error"]
    assert "verification_code" not in body


def test_continue_subsidy_key_then_enter(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _create(client)
    assert created.status_code == 201
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        "update candidates set llm_api_key_ciphertext = null, llm_key_status = 'missing'"
    )
    db.commit()
    db.close()

    stub = install_probe_stub(monkeypatch, get_status=200)
    response = client.post(
        "/api/sessions",
        json={"email": "foo@example.com", "llm_api_key": FAKE_OTHER_KEY},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["data"]["llm_key_probe_status"] == "ok"
    assert body["data"]["candidate"]["has_llm_api_key"] is True
    assert FAKE_OTHER_KEY not in response.text
    stub.assert_user_key_only(FAKE_OTHER_KEY)

    db = sqlite3.connect(tmp_env / "offer_coming.db")
    ciphertext = db.execute("select llm_api_key_ciphertext from candidates").fetchone()[0]
    db.close()
    assert decrypt_llm_api_key(ciphertext, get_settings().secret_key) == FAKE_OTHER_KEY


def test_continue_existing_key_ignores_extra_and_does_not_probe(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _create(client)
    assert created.status_code == 201
    stub = install_probe_stub(monkeypatch, get_status=200)
    response = client.post(
        "/api/sessions",
        json={"email": "foo@example.com", "llm_api_key": FAKE_OTHER_KEY},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["data"]["llm_key_probe_status"] is None
    assert stub.calls == []

    db = sqlite3.connect(tmp_env / "offer_coming.db")
    ciphertext = db.execute("select llm_api_key_ciphertext from candidates").fetchone()[0]
    db.close()
    assert decrypt_llm_api_key(ciphertext, get_settings().secret_key) == FAKE_USER_KEY
    assert FAKE_OTHER_KEY not in response.text


def test_continue_subsidy_401_does_not_write(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _create(client)
    assert created.status_code == 201
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        "update candidates set llm_api_key_ciphertext = null, llm_key_status = 'missing'"
    )
    db.commit()
    db.close()

    install_probe_stub(monkeypatch, get_status=401)
    response = client.post(
        "/api/sessions",
        json={"email": "foo@example.com", "llm_api_key": FAKE_OTHER_KEY},
    )
    body = response.json()
    assert response.status_code == 400
    assert body["error_code"] == "LLM_KEY_INVALID"
    assert FAKE_OTHER_KEY not in response.text

    db = sqlite3.connect(tmp_env / "offer_coming.db")
    ciphertext, status = db.execute(
        "select llm_api_key_ciphertext, llm_key_status from candidates"
    ).fetchone()
    db.close()
    assert ciphertext is None
    assert status == "missing"


def test_continue_subsidy_unreachable_still_saves(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _create(client)
    assert created.status_code == 201
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        "update candidates set llm_api_key_ciphertext = null, llm_key_status = 'missing'"
    )
    db.commit()
    db.close()

    install_probe_stub(monkeypatch, get_error=httpx.TimeoutException("timed out"))
    response = client.post(
        "/api/sessions",
        json={"email": "foo@example.com", "llm_api_key": FAKE_OTHER_KEY},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["data"]["llm_key_probe_status"] == "unreachable"
    assert "这次没连上" in body["message"]
    assert FAKE_OTHER_KEY not in response.text
