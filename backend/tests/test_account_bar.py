from __future__ import annotations

import logging
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

_OLD_RESUME = b"Python backend intern"
_NEW_RESUME = b"Go backend intern and distributed systems"


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
    content: bytes = _OLD_RESUME,
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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_no_key_leak(
    response: Any,
    body: dict,
    *keys: str,
    logs: str = "",
) -> None:
    blob = response.text + logs
    for key in (FAKE_USER_KEY, FAKE_OPERATOR_KEY, FAKE_OTHER_KEY, *keys):
        assert key not in blob
    assert "llm_api_key_ciphertext" not in response.text
    data = body.get("data")
    if isinstance(data, dict):
        assert "llm_api_key" not in data
        candidate = data.get("candidate")
        if isinstance(candidate, dict):
            assert "llm_api_key" not in candidate


def _candidate_row(tmp_env: Path) -> tuple[str, str, str, str | None, str]:
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    row = db.execute(
        "select resume_filename, resume_path, resume_text, "
        "llm_api_key_ciphertext, llm_key_status from candidates"
    ).fetchone()
    db.close()
    assert row is not None
    return row


def test_replace_resume_updates_filename_without_email_or_key(
    client: TestClient,
    tmp_env: Path,
) -> None:
    created, body = _create(client)
    assert created.status_code == 201
    token = body["data"]["session_token"]
    old_filename, old_path, old_text, _, _ = _candidate_row(tmp_env)
    assert old_filename == "resume.txt"
    assert Path(old_path).read_bytes() == _OLD_RESUME

    response = client.post(
        "/api/candidates/current/resume",
        headers=_auth(token),
        files={"resume": ("new_cv.txt", _NEW_RESUME, "text/plain")},
    )
    payload = response.json()
    data = payload["data"]
    assert response.status_code == 200
    assert payload["success"] is True
    assert data["resume_filename"] == "new_cv.txt"
    assert data["resume_parse_ok"] is True
    assert "email" not in (response.request.content.decode() if response.request.content else "")
    assert "session_token" not in data
    assert data["has_llm_api_key"] is True
    _assert_no_key_leak(response, payload)

    filename, path, text, _, _ = _candidate_row(tmp_env)
    assert filename == "new_cv.txt"
    assert Path(path).is_file()
    assert Path(path).read_bytes() == _NEW_RESUME
    assert "distributed systems" in text
    assert text != old_text
    assert not Path(old_path).exists()

    db = sqlite3.connect(tmp_env / "offer_coming.db")
    session_count = db.execute("select count(*) from sessions").fetchone()[0]
    db.close()
    assert session_count == 1


def test_replace_resume_bad_file_keeps_old_disk_and_text(
    client: TestClient,
    tmp_env: Path,
) -> None:
    created, body = _create(client)
    assert created.status_code == 201
    token = body["data"]["session_token"]
    old_filename, old_path, old_text, _, _ = _candidate_row(tmp_env)

    response = client.post(
        "/api/candidates/current/resume",
        headers=_auth(token),
        files={"resume": ("bad.pdf", b"%PDF-1.4\nnot-a-real-pdf", "application/pdf")},
    )
    payload = response.json()
    assert response.status_code == 400
    assert payload["error_code"] == "VALIDATION_ERROR"
    _assert_no_key_leak(response, payload)

    filename, path, text, _, _ = _candidate_row(tmp_env)
    assert filename == old_filename
    assert path == old_path
    assert text == old_text
    assert Path(old_path).is_file()
    assert Path(old_path).read_bytes() == _OLD_RESUME


def test_replace_resume_unsupported_type_keeps_old(
    client: TestClient,
    tmp_env: Path,
) -> None:
    created, body = _create(client)
    token = body["data"]["session_token"]
    _, old_path, old_text, _, _ = _candidate_row(tmp_env)

    response = client.post(
        "/api/candidates/current/resume",
        headers=_auth(token),
        files={"resume": ("photo.exe", b"MZ not a resume", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    filename, path, text, _, _ = _candidate_row(tmp_env)
    assert filename == "resume.txt"
    assert path == old_path
    assert text == old_text
    assert Path(old_path).read_bytes() == _OLD_RESUME


def test_replace_resume_forbidden_without_stored_key(
    client: TestClient,
    tmp_env: Path,
) -> None:
    created, body = _create(client)
    token = body["data"]["session_token"]
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        "update candidates set llm_api_key_ciphertext = null, llm_key_status = 'missing'"
    )
    db.commit()
    db.close()

    response = client.post(
        "/api/candidates/current/resume",
        headers=_auth(token),
        files={"resume": ("new_cv.txt", _NEW_RESUME, "text/plain")},
    )
    payload = response.json()
    assert response.status_code == 403
    assert payload["error_code"] == "LLM_KEY_REQUIRED"
    _, path, text, _, _ = _candidate_row(tmp_env)
    assert Path(path).read_bytes() == _OLD_RESUME
    assert "Python backend intern" in text


def test_update_llm_key_allowed_without_existing_ciphertext(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, body = _create(client)
    token = body["data"]["session_token"]
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        "update candidates set llm_api_key_ciphertext = null, llm_key_status = 'missing'"
    )
    db.commit()
    db.close()
    stub = install_probe_stub(monkeypatch, get_status=200)

    response = client.put(
        "/api/candidates/current/llm-key",
        headers=_auth(token),
        json={"llm_api_key": FAKE_OTHER_KEY},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["data"]["has_llm_api_key"] is True
    assert payload["data"]["llm_key_status"] == "saved"
    assert payload["message"] == "已保存新的百炼 Key"
    _assert_no_key_leak(response, payload)
    stub.assert_user_key_only(FAKE_OTHER_KEY)
    _, _, _, cipher, status = _candidate_row(tmp_env)
    assert status == "saved"
    assert decrypt_llm_api_key(cipher, get_settings().secret_key) == FAKE_OTHER_KEY


def test_update_llm_key_rejects_non_sk(client: TestClient) -> None:
    created, body = _create(client)
    token = body["data"]["session_token"]
    response = client.put(
        "/api/candidates/current/llm-key",
        headers=_auth(token),
        json={"llm_api_key": "not-a-dashscope-key"},
    )
    payload = response.json()
    assert response.status_code == 400
    assert payload["error_code"] == "VALIDATION_ERROR"
    _assert_no_key_leak(response, payload, "not-a-dashscope-key")


def test_update_llm_key_probe_401_keeps_old_ciphertext(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created, body = _create(client)
    token = body["data"]["session_token"]
    _, _, _, old_cipher, old_status = _candidate_row(tmp_env)
    stub = install_probe_stub(monkeypatch, get_status=401)

    response = client.put(
        "/api/candidates/current/llm-key",
        headers=_auth(token),
        json={"llm_api_key": FAKE_OTHER_KEY},
    )
    payload = response.json()
    assert response.status_code == 400
    assert payload["error_code"] == "LLM_KEY_INVALID"
    _assert_no_key_leak(response, payload)
    stub.assert_user_key_only(FAKE_OTHER_KEY)

    _, _, _, cipher, status = _candidate_row(tmp_env)
    assert cipher == old_cipher
    assert status == old_status
    assert decrypt_llm_api_key(cipher, get_settings().secret_key) == FAKE_USER_KEY


def test_update_llm_key_unreachable_saves_and_mentions_offline(
    client: TestClient,
    tmp_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    created, body = _create(client)
    token = body["data"]["session_token"]
    stub = install_probe_stub(
        monkeypatch, get_error=httpx.TimeoutException("timed out")
    )
    caplog.set_level(logging.INFO)

    response = client.put(
        "/api/candidates/current/llm-key",
        headers=_auth(token),
        json={"llm_api_key": FAKE_OTHER_KEY},
    )
    payload = response.json()
    data = payload["data"]
    assert response.status_code == 200
    assert data["has_llm_api_key"] is True
    assert data["llm_key_status"] == "saved"
    assert data["llm_key_probe_status"] == "unreachable"
    assert "这次没连上" in payload["message"]
    assert "已保存新的百炼 Key" in payload["message"]
    _assert_no_key_leak(response, payload, logs=caplog.text)
    stub.assert_user_key_only(FAKE_OTHER_KEY)

    _, _, _, cipher, status = _candidate_row(tmp_env)
    assert status == "saved"
    assert decrypt_llm_api_key(cipher, get_settings().secret_key) == FAKE_OTHER_KEY


def test_revoke_session_then_same_email_can_reissue(
    client: TestClient,
    tmp_env: Path,
) -> None:
    created, body = _create(client, email="foo@example.com")
    token = body["data"]["session_token"]

    revoked = client.delete("/api/sessions/current", headers=_auth(token))
    payload = revoked.json()
    assert revoked.status_code == 200
    assert payload["data"]["revoked"] is True
    _assert_no_key_leak(revoked, payload)

    current = client.get("/api/candidates/current", headers=_auth(token))
    assert current.status_code == 401
    assert current.json()["error_code"] == "UNAUTHORIZED"

    db = sqlite3.connect(tmp_env / "offer_coming.db")
    sessions = db.execute("select count(*) from sessions").fetchone()[0]
    candidates = db.execute("select count(*) from candidates").fetchone()[0]
    resume_text = db.execute("select resume_text from candidates").fetchone()[0]
    db.close()
    assert sessions == 0
    assert candidates == 1
    assert "Python backend intern" in resume_text

    resumed = client.post("/api/sessions", json={"email": "foo@example.com"})
    resumed_body = resumed.json()
    assert resumed.status_code == 200
    new_token = resumed_body["data"]["session_token"]
    assert new_token
    assert new_token != token
    assert resumed_body["data"]["candidate"]["has_llm_api_key"] is True
    _assert_no_key_leak(resumed, resumed_body)

    current_ok = client.get("/api/candidates/current", headers=_auth(new_token))
    assert current_ok.status_code == 200
    assert current_ok.json()["data"]["resume_filename"] == "resume.txt"
