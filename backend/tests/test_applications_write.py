from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from src.db.models import apply_sqlite_patches
from src.utils.crypto import beijing_today


def _start(
    client: TestClient,
    email: str = "writer@example.com",
) -> tuple[str, str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email},
        files={"resume": ("resume.txt", b"Python backend intern", "text/plain")},
    )
    assert response.status_code == 201
    data = response.json()["data"]
    return data["session_token"], data["conversation_id"], data["candidate"]["id"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _list_fields(item: dict) -> None:
    for key in (
        "applied_at",
        "interview_at",
        "interview_summary",
        "exam_points",
        "deadline",
        "normalized_status",
    ):
        assert key in item
    assert "knowledge_review_pending" not in item


def test_list_application_public_has_table_fields(client: TestClient) -> None:
    token, _, _ = _start(client)
    created = client.post(
        "/api/applications",
        json={"company_name": "示例公司", "role_title": "后端", "interview_at": "2026-09-20"},
        headers=_headers(token),
    )
    assert created.status_code == 201
    listed = client.get("/api/applications", headers=_headers(token))
    assert listed.status_code == 200
    apps = listed.json()["data"]["applications"]
    assert len(apps) == 1
    _list_fields(apps[0])
    assert apps[0]["applied_at"] == beijing_today()
    assert apps[0]["interview_at"] == "2026-09-20"
    assert apps[0]["deadline"] == "2026-09-20"
    assert apps[0]["exam_points"] == ""
    assert apps[0]["normalized_status"] == "other"


def test_create_empty_row_defaults_applied_at(client: TestClient) -> None:
    token, _, _ = _start(client)
    response = client.post("/api/applications", json={}, headers=_headers(token))
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["applied_at"] == beijing_today()
    assert data["interview_at"] is None
    assert data["deadline"] is None
    assert data["exam_points"] == ""
    assert data["normalized_status"] == "other"
    assert "knowledge_review_pending" not in data


def test_create_without_url_skips_exam_points(client: TestClient) -> None:
    token, _, _ = _start(client)
    response = client.post(
        "/api/applications",
        json={"company_name": "无链公司", "role_title": "前端"},
        headers=_headers(token),
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["jd_url"] is None
    assert data["exam_points"] == ""


def test_create_duplicate_jd_url_is_conflict(client: TestClient) -> None:
    token, _, _ = _start(client)
    url = "https://example.com/job/dup"
    first = client.post(
        "/api/applications",
        json={"jd_url": url, "company_name": "甲"},
        headers=_headers(token),
    )
    assert first.status_code == 201
    second = client.post(
        "/api/applications",
        json={"jd_url": url, "company_name": "乙"},
        headers=_headers(token),
    )
    assert second.status_code == 409
    assert second.json()["error_code"] == "CONFLICT"
    listed = client.get("/api/applications", headers=_headers(token))
    assert len(listed.json()["data"]["applications"]) == 1


def test_create_multiple_empty_urls_allowed(client: TestClient) -> None:
    token, _, _ = _start(client)
    first = client.post("/api/applications", json={}, headers=_headers(token))
    second = client.post("/api/applications", json={"company_name": "空链"}, headers=_headers(token))
    assert first.status_code == 201
    assert second.status_code == 201


def test_create_invalid_applied_at_is_400(client: TestClient) -> None:
    token, _, _ = _start(client)
    response = client.post(
        "/api/applications",
        json={"applied_at": "not-a-date"},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_ERROR"


def test_patch_updates_only_sent_fields(client: TestClient) -> None:
    token, _, _ = _start(client)
    created = client.post(
        "/api/applications",
        json={
            "company_name": "原公司",
            "role_title": "原岗位",
            "progress_text": "已投",
            "interview_summary": "手写",
        },
        headers=_headers(token),
    )
    app_id = created.json()["data"]["id"]
    patched = client.patch(
        f"/api/applications/{app_id}",
        json={"interview_summary": "追加总结"},
        headers=_headers(token),
    )
    assert patched.status_code == 200
    data = patched.json()["data"]
    assert data["company_name"] == "原公司"
    assert data["role_title"] == "原岗位"
    assert data["progress_text"] == "已投"
    assert data["interview_summary"] == "追加总结"
    assert data["knowledge_review_pending"] is False


def test_patch_clear_interview_at_clears_deadline(client: TestClient) -> None:
    token, _, _ = _start(client)
    created = client.post(
        "/api/applications",
        json={"interview_at": "2026-09-20T10:00:00+08:00"},
        headers=_headers(token),
    )
    app_id = created.json()["data"]["id"]
    assert created.json()["data"]["deadline"] == "2026-09-20"
    patched = client.patch(
        f"/api/applications/{app_id}",
        json={"interview_at": None},
        headers=_headers(token),
    )
    assert patched.status_code == 200
    data = patched.json()["data"]
    assert data["interview_at"] is None
    assert data["deadline"] is None


def test_patch_duplicate_jd_url_is_conflict(client: TestClient) -> None:
    token, _, _ = _start(client)
    first = client.post(
        "/api/applications",
        json={"jd_url": "https://example.com/job/a"},
        headers=_headers(token),
    )
    second = client.post(
        "/api/applications",
        json={"jd_url": "https://example.com/job/b"},
        headers=_headers(token),
    )
    assert first.status_code == 201
    assert second.status_code == 201
    app_id = second.json()["data"]["id"]
    patched = client.patch(
        f"/api/applications/{app_id}",
        json={"jd_url": "https://example.com/job/a"},
        headers=_headers(token),
    )
    assert patched.status_code == 409
    assert patched.json()["error_code"] == "CONFLICT"


def test_patch_same_row_keeps_own_jd_url(client: TestClient) -> None:
    token, _, _ = _start(client)
    created = client.post(
        "/api/applications",
        json={"jd_url": "https://example.com/job/keep", "company_name": "甲"},
        headers=_headers(token),
    )
    app_id = created.json()["data"]["id"]
    patched = client.patch(
        f"/api/applications/{app_id}",
        json={"jd_url": "https://example.com/job/keep", "company_name": "乙"},
        headers=_headers(token),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["company_name"] == "乙"


def test_patch_owner_checks(client: TestClient) -> None:
    token_a, _, _ = _start(client, "owner-a@example.com")
    token_b, _, _ = _start(client, "owner-b@example.com")
    created = client.post(
        "/api/applications",
        json={"company_name": "甲的岗"},
        headers=_headers(token_a),
    )
    app_id = created.json()["data"]["id"]
    forbidden = client.patch(
        f"/api/applications/{app_id}",
        json={"company_name": "抢行"},
        headers=_headers(token_b),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error_code"] == "FORBIDDEN"
    missing = client.patch(
        "/api/applications/a_not_exist",
        json={"company_name": "无"},
        headers=_headers(token_a),
    )
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "NOT_FOUND"


def test_patch_rejected_with_knowledge_inserts_kb_ask(
    client: TestClient, tmp_env: Path
) -> None:
    token, conversation_id, candidate_id = _start(client, "reject@example.com")
    created = client.post(
        "/api/applications",
        json={"company_name": "已挂公司", "role_title": "后端"},
        headers=_headers(token),
    )
    app_id = created.json()["data"]["id"]
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        """
        insert into knowledge_items (
            id, candidate_id, application_id, title, source_type, source_url,
            body, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "k_rel",
            candidate_id,
            app_id,
            "相关面经",
            "paste",
            None,
            "一面问项目",
            "2026-09-14T00:00:00+00:00",
            "2026-09-14T00:00:00+00:00",
        ),
    )
    db.commit()
    db.close()

    patched = client.patch(
        f"/api/applications/{app_id}",
        json={"status_text": "已挂"},
        headers=_headers(token),
    )
    assert patched.status_code == 200
    data = patched.json()["data"]
    assert data["normalized_status"] == "rejected"
    assert data["knowledge_review_pending"] is True

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    assert messages.status_code == 200
    rows = messages.json()["data"]["messages"]
    asks = [item for item in rows if item["message_type"] == "kb_ask"]
    assert len(asks) == 1
    assert "面经" in asks[0]["content"]

    listed = client.get("/api/applications", headers=_headers(token))
    assert "knowledge_review_pending" not in listed.json()["data"]["applications"][0]


def test_patch_rejected_without_knowledge_is_not_pending(client: TestClient) -> None:
    token, conversation_id, _ = _start(client, "no-kb@example.com")
    created = client.post(
        "/api/applications",
        json={"company_name": "无面经公司"},
        headers=_headers(token),
    )
    app_id = created.json()["data"]["id"]
    patched = client.patch(
        f"/api/applications/{app_id}",
        json={"status_text": "被拒了"},
        headers=_headers(token),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["normalized_status"] == "rejected"
    assert patched.json()["data"]["knowledge_review_pending"] is False
    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    assert messages.json()["data"]["messages"] == []


def test_delete_removes_row_keeps_knowledge(client: TestClient, tmp_env: Path) -> None:
    token, _, candidate_id = _start(client, "deleter@example.com")
    created = client.post(
        "/api/applications",
        json={"company_name": "待删"},
        headers=_headers(token),
    )
    app_id = created.json()["data"]["id"]
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        """
        insert into knowledge_items (
            id, candidate_id, application_id, title, source_type, source_url,
            body, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "k_keep",
            candidate_id,
            app_id,
            "保留面经",
            "upload",
            None,
            "正文仍在",
            "2026-09-14T00:00:00+00:00",
            "2026-09-14T00:00:00+00:00",
        ),
    )
    db.commit()
    db.close()

    deleted = client.delete(f"/api/applications/{app_id}", headers=_headers(token))
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"id": app_id, "deleted": True}
    listed = client.get("/api/applications", headers=_headers(token))
    assert listed.json()["data"]["applications"] == []

    db = sqlite3.connect(tmp_env / "offer_coming.db")
    title, body = db.execute(
        "select title, body from knowledge_items where id=?",
        ("k_keep",),
    ).fetchone()
    db.close()
    assert title == "保留面经"
    assert body == "正文仍在"


def test_delete_owner_checks(client: TestClient) -> None:
    token_a, _, _ = _start(client, "del-a@example.com")
    token_b, _, _ = _start(client, "del-b@example.com")
    created = client.post(
        "/api/applications",
        json={"company_name": "甲"},
        headers=_headers(token_a),
    )
    app_id = created.json()["data"]["id"]
    forbidden = client.delete(f"/api/applications/{app_id}", headers=_headers(token_b))
    assert forbidden.status_code == 403
    missing = client.delete("/api/applications/a_missing", headers=_headers(token_a))
    assert missing.status_code == 404


def test_unauthorized_write_is_401(client: TestClient) -> None:
    created = client.post("/api/applications", json={})
    assert created.status_code == 401
    patched = client.patch("/api/applications/a_x", json={"company_name": "x"})
    assert patched.status_code == 401
    deleted = client.delete("/api/applications/a_x")
    assert deleted.status_code == 401


def test_schema_has_new_application_columns_and_knowledge_tables(
    client: TestClient, tmp_env: Path
) -> None:
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
    columns = {row[1] for row in db.execute("pragma table_info(applications)")}
    db.close()
    assert {
        "knowledge_items",
        "knowledge_segments",
        "knowledge_search_runs",
        "llm_call_logs",
    } <= tables
    assert {"applied_at", "interview_at", "interview_summary"} <= columns


def test_sqlite_patches_add_missing_application_columns(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        create table applications (
            id text primary key,
            candidate_id text,
            company_name text,
            role_title text,
            jd_url text,
            jd_text text,
            exam_points text,
            progress_text text,
            status_text text,
            normalized_status text,
            deadline text,
            created_at text,
            updated_at text
        )
        """
    )
    conn.commit()
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        apply_sqlite_patches(connection)
    engine.dispose()
    conn.close()
    conn = sqlite3.connect(path)
    names = {row[1] for row in conn.execute("pragma table_info(applications)")}
    conn.close()
    assert {"applied_at", "interview_at", "interview_summary"} <= names
