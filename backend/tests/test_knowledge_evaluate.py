from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from src.db.models import Candidate, KnowledgeSegment
from src.db.session import get_db_context
from src.services import knowledge_store as store_mod
from src.services.tools import ToolExecutor


def _start(
    client: TestClient,
    email: str = "kb-eval@example.com",
) -> tuple[str, str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email},
        files={"resume": ("resume.txt", b"Python intern", "text/plain")},
    )
    assert response.status_code == 201
    data = response.json()["data"]
    return data["session_token"], data["conversation_id"], data["candidate"]["id"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _insert_item(
    db_path: Path,
    *,
    item_id: str,
    candidate_id: str,
    title: str,
    body: str,
    application_id: str | None = None,
) -> None:
    db = sqlite3.connect(db_path)
    db.execute(
        """
        insert into knowledge_items (
            id, candidate_id, application_id, title, source_type, source_url,
            body, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            candidate_id,
            application_id,
            title,
            "paste",
            None,
            body,
            "2026-09-14T00:00:00+00:00",
            "2026-09-14T00:00:00+00:00",
        ),
    )
    db.commit()
    db.close()


def _insert_segment(
    db_path: Path,
    *,
    segment_id: str,
    item_id: str,
    ordinal: int,
    text: str,
) -> None:
    db = sqlite3.connect(db_path)
    db.execute(
        """
        insert into knowledge_segments (
            id, knowledge_item_id, ordinal, text, status
        ) values (?, ?, ?, ?, ?)
        """,
        (segment_id, item_id, ordinal, text, "active"),
    )
    db.commit()
    db.close()


def _create_app_with_knowledge(
    client: TestClient,
    tmp_env: Path,
    email: str,
    *,
    prefix: str = "k",
) -> tuple[str, str, str, str, str, str]:
    token, conversation_id, candidate_id = _start(client, email)
    created = client.post(
        "/api/applications",
        json={"company_name": "已挂公司", "role_title": "后端"},
        headers=_headers(token),
    )
    assert created.status_code == 201
    app_id = created.json()["data"]["id"]
    keep_id = f"{prefix}_keep"
    del_id = f"{prefix}_del"
    db_path = tmp_env / "offer_coming.db"
    _insert_item(
        db_path,
        item_id=keep_id,
        candidate_id=candidate_id,
        title="通用项目面经",
        body="讲项目难点",
        application_id=app_id,
    )
    _insert_item(
        db_path,
        item_id=del_id,
        candidate_id=candidate_id,
        title="只针对该岗",
        body="只问这个已挂岗位的业务",
        application_id=app_id,
    )
    _insert_segment(
        db_path, segment_id=f"{prefix}_s_keep", item_id=keep_id, ordinal=0, text="讲项目难点"
    )
    _insert_segment(
        db_path, segment_id=f"{prefix}_s_del", item_id=del_id, ordinal=0, text="只问这个已挂岗位"
    )
    return token, conversation_id, candidate_id, app_id, keep_id, del_id


def test_patch_rejected_inserts_kb_ask_and_keeps_items(
    client: TestClient, tmp_env: Path
) -> None:
    token, conversation_id, _candidate_id, app_id, keep_id, del_id = _create_app_with_knowledge(
        client, tmp_env, "patch-ask@example.com"
    )
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
    asks = [
        item
        for item in messages.json()["data"]["messages"]
        if item["message_type"] == "kb_ask"
    ]
    assert len(asks) == 1
    assert "面经" in asks[0]["content"]
    assert "判断完" in asks[0]["content"] or "删" in asks[0]["content"]

    listed = client.get("/api/knowledge-items", headers=_headers(token))
    assert listed.status_code == 200
    ids = {item["id"] for item in listed.json()["data"]["items"]}
    assert {keep_id, del_id} <= ids


def test_evaluate_failure_saves_status_and_does_not_delete(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(self, **kwargs):  # noqa: ANN001
        raise RuntimeError("evaluate failed")

    monkeypatch.setattr(store_mod, "_llm_configured", lambda: True)
    monkeypatch.setattr(store_mod.LlmClient, "chat_completions", boom)

    token, conversation_id, _candidate_id, app_id, keep_id, del_id = _create_app_with_knowledge(
        client, tmp_env, "eval-fail@example.com"
    )
    patched = client.patch(
        f"/api/applications/{app_id}",
        json={"status_text": "被拒了"},
        headers=_headers(token),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["normalized_status"] == "rejected"
    assert patched.json()["data"]["knowledge_review_pending"] is True

    asks = [
        item
        for item in client.get(
            f"/api/conversations/{conversation_id}/messages",
            headers=_headers(token),
        ).json()["data"]["messages"]
        if item["message_type"] == "kb_ask"
    ]
    assert asks
    assert "判断完" in asks[0]["content"]

    listed = client.get("/api/knowledge-items", headers=_headers(token))
    ids = {item["id"] for item in listed.json()["data"]["items"]}
    assert {keep_id, del_id} <= ids


def test_evaluate_success_marks_pending_delete_not_hard_delete(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_chat(self, **kwargs):  # noqa: ANN001
        assert kwargs.get("model") == "test-knowledge"
        payload = {
            "keep": [{"item_id": "ok_keep", "segment_ids": [], "reason": "其它岗仍能用"}],
            "suggest_delete": [
                {"item_id": "ok_del", "segment_ids": ["ok_s_del"], "reason": "只针对已挂岗位"}
            ],
        }
        return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}

    monkeypatch.setattr(store_mod, "_llm_configured", lambda: True)
    monkeypatch.setattr(store_mod.LlmClient, "chat_completions", fake_chat)

    token, conversation_id, _candidate_id, app_id, keep_id, del_id = _create_app_with_knowledge(
        client, tmp_env, "eval-ok@example.com", prefix="ok"
    )
    patched = client.patch(
        f"/api/applications/{app_id}",
        json={"status_text": "已挂"},
        headers=_headers(token),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["knowledge_review_pending"] is True
    asks = [
        item
        for item in client.get(
            f"/api/conversations/{conversation_id}/messages",
            headers=_headers(token),
        ).json()["data"]["messages"]
        if item["message_type"] == "kb_ask"
    ]
    assert asks
    assert "建议删除" in asks[0]["content"] or del_id in asks[0]["content"]

    listed = client.get("/api/knowledge-items", headers=_headers(token))
    ids = {item["id"] for item in listed.json()["data"]["items"]}
    assert {keep_id, del_id} <= ids

    conn = sqlite3.connect(tmp_env / "offer_coming.db")
    statuses = dict(
        conn.execute("select id, status from knowledge_segments").fetchall()
    )
    conn.close()
    assert statuses["ok_s_del"] == "pending_delete"
    assert statuses["ok_s_keep"] == "active"


@pytest.mark.asyncio
async def test_apply_deletion_decision_accepted_ids_and_keep_all(
    client: TestClient, tmp_env: Path
) -> None:
    token, _conversation_id, candidate_id, app_id, keep_id, del_id = _create_app_with_knowledge(
        client, tmp_env, "decide@example.com", prefix="dec"
    )
    client.patch(
        f"/api/applications/{app_id}",
        json={"status_text": "已挂"},
        headers=_headers(token),
    )

    async with get_db_context() as db:
        candidate = (
            await db.execute(select(Candidate).where(Candidate.id == candidate_id))
        ).scalar_one()
        tools = ToolExecutor(db, candidate)
        deleted = await tools.apply_deletion_decision({"accepted_ids": [del_id]})
        assert deleted["ok"] is True
        assert del_id in deleted["deleted"]

    listed = client.get("/api/knowledge-items", headers=_headers(token))
    ids = {item["id"] for item in listed.json()["data"]["items"]}
    assert del_id not in ids
    assert keep_id in ids

    token_b, _cid_b, candidate_b, app_b, keep_b, del_b = _create_app_with_knowledge(
        client, tmp_env, "keepall@example.com", prefix="all"
    )
    client.patch(
        f"/api/applications/{app_b}",
        json={"status_text": "已挂"},
        headers=_headers(token_b),
    )
    async with get_db_context() as db:
        candidate = (
            await db.execute(select(Candidate).where(Candidate.id == candidate_b))
        ).scalar_one()
        tools = ToolExecutor(db, candidate)
        kept = await tools.apply_deletion_decision({"keep_all": True})
        assert kept["ok"] is True
        assert kept["kept_all"] is True

    listed_b = client.get("/api/knowledge-items", headers=_headers(token_b))
    ids_b = {item["id"] for item in listed_b.json()["data"]["items"]}
    assert {keep_b, del_b} <= ids_b

    async with get_db_context() as db:
        rows = (
            await db.execute(
                select(KnowledgeSegment.status).where(
                    KnowledgeSegment.id.in_(["all_s_keep", "all_s_del"])
                )
            )
        ).all()
    assert {row[0] for row in rows} == {"active"}
