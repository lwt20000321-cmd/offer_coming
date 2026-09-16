from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from src.config.settings import get_settings
from src.db.models import apply_sqlite_patches
from src.utils.crypto import decrypt_llm_api_key
from tests.test_llm_probe import (
    FAKE_OPERATOR_KEY,
    FAKE_USER_KEY,
    ProbeStub,
    install_probe_stub,
    set_operator_key,
)

_ONBOARDING = "请先填写邮箱、粘贴百炼 Key 并上传简历，才能和小凹对话。"


@pytest.fixture(autouse=True)
def _operator_key_and_ok_probe(
    tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> ProbeStub:
    set_operator_key()
    return install_probe_stub(monkeypatch, get_status=200)


def _create(
    client: TestClient,
    email: str = "user@example.com",
    filename: str = "resume.txt",
    content: bytes = b"Python backend intern",
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


def _assert_no_key_leak(response: Any, body: dict, key: str = FAKE_USER_KEY) -> None:
    assert key not in response.text
    assert FAKE_OPERATOR_KEY not in response.text
    assert "llm_api_key_ciphertext" not in response.text
    data = body.get("data")
    if isinstance(data, dict):
        assert "llm_api_key" not in data
        candidate = data.get("candidate")
        if isinstance(candidate, dict):
            assert "llm_api_key" not in candidate


def test_create_candidate_returns_201_session(client: TestClient, tmp_env: Path) -> None:
    response, body = _create(client)
    assert response.status_code == 201
    assert body["success"] is True
    data = body["data"]
    assert "session_token" in data
    assert data["candidate"]["email"] == "user@example.com"
    assert data["candidate"]["resume_filename"] == "resume.txt"
    assert data["candidate"]["resume_parse_ok"] is True
    assert data["candidate"]["has_llm_api_key"] is True
    assert data["candidate"]["llm_key_status"] == "saved"
    assert data["conversation_id"]
    assert data["llm_key_probe_status"] == "ok"
    assert body["message"] == "已进入小凹"
    _assert_no_key_leak(response, body)

    db = sqlite3.connect(tmp_env / "offer_coming.db")
    token_hash, stored_path, ciphertext, status = db.execute(
        "select token_hash, resume_path, llm_api_key_ciphertext, llm_key_status "
        "from sessions join candidates on candidates.id = sessions.candidate_id"
    ).fetchone()
    db.close()
    expected = hashlib.sha256(
        f"{get_settings().secret_key}:{data['session_token']}".encode()
    ).hexdigest()
    assert token_hash == expected
    assert data["session_token"] not in token_hash
    assert Path(stored_path).is_file()
    assert ciphertext
    assert FAKE_USER_KEY not in ciphertext
    assert status == "saved"
    assert decrypt_llm_api_key(ciphertext, get_settings().secret_key) == FAKE_USER_KEY


def test_missing_email_is_validation_error(client: TestClient) -> None:
    response = client.post(
        "/api/candidates",
        data={"llm_api_key": FAKE_USER_KEY},
        files={"resume": ("resume.txt", b"hello", "text/plain")},
    )
    body = response.json()
    assert response.status_code == 400
    assert body["error_code"] == "VALIDATION_ERROR"
    assert "邮箱" in body["error"]
    _assert_no_key_leak(response, body)


def test_missing_file_is_validation_error(client: TestClient) -> None:
    response = client.post(
        "/api/candidates",
        data={"email": "user@example.com", "llm_api_key": FAKE_USER_KEY},
    )
    body = response.json()
    assert response.status_code == 400
    assert body["error_code"] == "VALIDATION_ERROR"
    assert "简历" in body["error"]
    _assert_no_key_leak(response, body)


def test_missing_key_is_validation_error(client: TestClient) -> None:
    response, body = _create(client, llm_api_key=None)
    assert response.status_code == 400
    assert body["error_code"] == "VALIDATION_ERROR"
    assert body["error"] == _ONBOARDING
    assert "填写邮箱" in body["error"]
    assert "粘贴" in body["error"] and "Key" in body["error"]
    assert "上传简历" in body["error"]


def test_key_must_start_with_sk(client: TestClient) -> None:
    response, body = _create(client, llm_api_key="not-a-dashscope-key")
    assert response.status_code == 400
    assert body["error_code"] == "VALIDATION_ERROR"
    assert "填写邮箱" in body["error"]
    assert "粘贴" in body["error"] and "Key" in body["error"]
    assert "上传简历" in body["error"]
    _assert_no_key_leak(response, body, key="not-a-dashscope-key")


def test_invalid_email_is_validation_error(client: TestClient) -> None:
    response, body = _create(client, email="not-an-email")
    assert response.status_code == 400
    assert body["error_code"] == "VALIDATION_ERROR"
    assert "邮箱" in body["error"]


def test_oversized_resume_is_validation_error(client: TestClient) -> None:
    response, body = _create(client, content=b"x" * 4096)
    assert response.status_code == 400
    assert body["error_code"] == "VALIDATION_ERROR"
    assert "过大" in body["error"]


def test_existing_email_conflict(client: TestClient) -> None:
    first, _ = _create(client)
    assert first.status_code == 201
    second, body = _create(client)
    assert second.status_code == 409
    assert body["error_code"] == "CONFLICT"
    assert "已有记录" in body["error"]
    _assert_no_key_leak(second, body)


def test_probe_401_does_not_create_candidate(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = install_probe_stub(monkeypatch, get_status=401)
    response, body = _create(client)
    assert response.status_code == 400
    assert body["error_code"] == "LLM_KEY_INVALID"
    assert "不能用" in body["error"]
    _assert_no_key_leak(response, body)
    stub.assert_user_key_only()
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    count = db.execute("select count(*) from candidates").fetchone()[0]
    db.close()
    assert count == 0


def test_probe_unreachable_still_creates(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = install_probe_stub(monkeypatch, get_error=httpx.TimeoutException("timed out"))
    response, body = _create(client)
    assert response.status_code == 201
    assert body["data"]["llm_key_probe_status"] == "unreachable"
    assert "这次没连上" in body["message"]
    assert body["data"]["candidate"]["has_llm_api_key"] is True
    _assert_no_key_leak(response, body)
    stub.assert_user_key_only()


def test_current_candidate_exposes_key_status_not_secret(client: TestClient) -> None:
    _created, body = _create(client)
    token = body["data"]["session_token"]
    current = client.get(
        "/api/candidates/current",
        headers={"Authorization": f"Bearer {token}"},
    )
    payload = current.json()
    data = payload["data"]
    assert current.status_code == 200
    assert data["has_llm_api_key"] is True
    assert data["llm_key_status"] == "saved"
    assert "llm_key_probe_status" not in data
    _assert_no_key_leak(current, payload)


def test_current_candidate_missing_key_status(
    client: TestClient,
    tmp_env: Path,
) -> None:
    _created, body = _create(client)
    token = body["data"]["session_token"]
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        "update candidates set llm_api_key_ciphertext = null, llm_key_status = 'missing'"
    )
    db.commit()
    db.close()
    current = client.get(
        "/api/candidates/current",
        headers={"Authorization": f"Bearer {token}"},
    )
    data = current.json()["data"]
    assert data["has_llm_api_key"] is False
    assert data["llm_key_status"] == "missing"


def test_sqlite_patches_add_candidate_key_columns(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        create table candidates (
            id text primary key,
            email text,
            resume_filename text,
            resume_path text,
            resume_text text,
            resume_parse_ok integer,
            counseling_active integer,
            next_question_date text,
            created_at text,
            updated_at text
        )
        """
    )
    conn.execute(
        "insert into candidates (id, email, resume_filename, resume_path, resume_text, "
        "resume_parse_ok, counseling_active, created_at, updated_at) "
        "values ('c_old', 'old@example.com', 'r.txt', '/tmp/r.txt', '', 0, 0, 't', 't')"
    )
    conn.commit()
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        apply_sqlite_patches(connection)
    engine.dispose()
    conn.close()
    conn = sqlite3.connect(path)
    info = {row[1]: row for row in conn.execute("pragma table_info(candidates)")}
    status, ciphertext = conn.execute(
        "select llm_key_status, llm_api_key_ciphertext from candidates where id='c_old'"
    ).fetchone()
    conn.close()
    assert "llm_api_key_ciphertext" in info
    assert "llm_key_status" in info
    assert "llm_key_updated_at" in info
    assert status == "missing"
    assert ciphertext is None
    # notnull + dflt_value
    assert info["llm_key_status"][3] == 1
    assert "missing" in str(info["llm_key_status"][4])
