from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from src.models.knowledge import KNOWLEDGE_EXCERPT_CHARS

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
_BLOCKED_HOSTS = ("xiaohongshu.com", "xhslink.com")
_SCOPED_FILES = (
    "api/routes/knowledge_items.py",
    "models/knowledge.py",
    "repositories/knowledge.py",
    "services/knowledge_store.py",
)


def _start(
    client: TestClient,
    email: str = "kb@example.com",
) -> tuple[str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email},
        files={"resume": ("resume.txt", b"Python intern", "text/plain")},
    )
    assert response.status_code == 201
    data = response.json()["data"]
    return data["session_token"], data["candidate"]["id"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _insert_item(
    db_path: Path,
    *,
    item_id: str,
    candidate_id: str,
    title: str,
    body: str,
    source_type: str = "paste",
    application_id: str | None = None,
    source_url: str | None = None,
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
            source_type,
            source_url,
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
    status: str = "active",
) -> None:
    db = sqlite3.connect(db_path)
    db.execute(
        """
        insert into knowledge_segments (
            id, knowledge_item_id, ordinal, text, status
        ) values (?, ?, ?, ?, ?)
        """,
        (segment_id, item_id, ordinal, text, status),
    )
    db.commit()
    db.close()


def _list_fields(item: dict) -> None:
    for key in (
        "id",
        "title",
        "source_type",
        "source_url",
        "application_id",
        "company_name",
        "role_title",
        "created_at",
        "excerpt",
    ):
        assert key in item
    assert "body" not in item
    assert "segments" not in item
    assert item["source_type"] in {"search", "upload", "paste"}


def test_empty_list_is_200_not_404(client: TestClient) -> None:
    token, _ = _start(client)
    response = client.get("/api/knowledge-items", headers=_headers(token))
    assert response.status_code == 200
    assert response.json()["data"]["items"] == []


def test_list_returns_owner_items_and_excerpt(client: TestClient, tmp_env: Path) -> None:
    token, candidate_id = _start(client, "owner@example.com")
    created = client.post(
        "/api/applications",
        json={"company_name": "示例公司", "role_title": "后端"},
        headers=_headers(token),
    )
    assert created.status_code == 201
    app_id = created.json()["data"]["id"]
    long_body = "一面问项目经验。" + ("补充细节" * 50)
    _insert_item(
        tmp_env / "offer_coming.db",
        item_id="k_owned",
        candidate_id=candidate_id,
        title="已收录面经",
        body=long_body,
        source_type="upload",
        application_id=app_id,
    )
    _, other_id = _start(client, "other-owner@example.com")
    _insert_item(
        tmp_env / "offer_coming.db",
        item_id="k_other",
        candidate_id=other_id,
        title="别人的面经",
        body="不应出现",
        source_type="search",
    )

    listed = client.get("/api/knowledge-items", headers=_headers(token))
    assert listed.status_code == 200
    items = listed.json()["data"]["items"]
    assert len(items) == 1
    _list_fields(items[0])
    assert items[0]["id"] == "k_owned"
    assert items[0]["title"] == "已收录面经"
    assert items[0]["source_type"] == "upload"
    assert items[0]["application_id"] == app_id
    assert items[0]["company_name"] == "示例公司"
    assert items[0]["role_title"] == "后端"
    assert items[0]["excerpt"] == long_body[:KNOWLEDGE_EXCERPT_CHARS]
    assert len(items[0]["excerpt"]) == KNOWLEDGE_EXCERPT_CHARS
    titles = {item["title"] for item in items}
    assert "已找到面经" not in titles
    assert "别人的面经" not in titles


def test_list_filters_by_application_id(client: TestClient, tmp_env: Path) -> None:
    token, candidate_id = _start(client, "filter@example.com")
    first = client.post(
        "/api/applications",
        json={"company_name": "甲", "role_title": "前端"},
        headers=_headers(token),
    )
    second = client.post(
        "/api/applications",
        json={"company_name": "乙", "role_title": "后端"},
        headers=_headers(token),
    )
    app_a = first.json()["data"]["id"]
    app_b = second.json()["data"]["id"]
    db_path = tmp_env / "offer_coming.db"
    _insert_item(
        db_path,
        item_id="k_a",
        candidate_id=candidate_id,
        title="甲岗面经",
        body="甲正文",
        application_id=app_a,
    )
    _insert_item(
        db_path,
        item_id="k_b",
        candidate_id=candidate_id,
        title="乙岗面经",
        body="乙正文",
        application_id=app_b,
    )

    filtered = client.get(
        "/api/knowledge-items",
        params={"application_id": app_a},
        headers=_headers(token),
    )
    assert filtered.status_code == 200
    items = filtered.json()["data"]["items"]
    assert [item["id"] for item in items] == ["k_a"]
    assert items[0]["company_name"] == "甲"


def test_get_detail_includes_body_and_pending_delete(
    client: TestClient, tmp_env: Path
) -> None:
    token, candidate_id = _start(client, "detail@example.com")
    created = client.post(
        "/api/applications",
        json={"company_name": "丙", "role_title": "算法"},
        headers=_headers(token),
    )
    app_id = created.json()["data"]["id"]
    db_path = tmp_env / "offer_coming.db"
    body = "第一段仍有用。第二段建议删除但仍要看见。"
    _insert_item(
        db_path,
        item_id="k_detail",
        candidate_id=candidate_id,
        title="待评估面经",
        body=body,
        source_type="search",
        source_url="https://example.com/exp",
        application_id=app_id,
    )
    _insert_segment(
        db_path,
        segment_id="s_keep",
        item_id="k_detail",
        ordinal=0,
        text="第一段仍有用。",
        status="active",
    )
    _insert_segment(
        db_path,
        segment_id="s_pending",
        item_id="k_detail",
        ordinal=1,
        text="第二段建议删除但仍要看见。",
        status="pending_delete",
    )

    detail = client.get("/api/knowledge-items/k_detail", headers=_headers(token))
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["title"] == "待评估面经"
    assert data["body"] == body
    assert "第二段建议删除但仍要看见。" in data["body"]
    assert data["excerpt"] == body
    assert data["source_type"] == "search"
    assert data["source_url"] == "https://example.com/exp"
    assert data["company_name"] == "丙"
    statuses = {seg["id"]: seg for seg in data["segments"]}
    assert statuses["s_keep"]["status"] == "active"
    assert statuses["s_pending"]["status"] == "pending_delete"
    assert statuses["s_pending"]["text"] == "第二段建议删除但仍要看见。"
    assert statuses["s_pending"]["ordinal"] == 1


def test_get_forbidden_and_missing(client: TestClient, tmp_env: Path) -> None:
    token_a, candidate_a = _start(client, "kb-a@example.com")
    token_b, _ = _start(client, "kb-b@example.com")
    _insert_item(
        tmp_env / "offer_coming.db",
        item_id="k_secret",
        candidate_id=candidate_a,
        title="甲的面经",
        body="仅甲可见",
    )
    forbidden = client.get("/api/knowledge-items/k_secret", headers=_headers(token_b))
    assert forbidden.status_code == 403
    assert forbidden.json()["error_code"] == "FORBIDDEN"
    missing = client.get("/api/knowledge-items/k_missing", headers=_headers(token_a))
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "NOT_FOUND"


def test_delete_removes_from_list_and_detail(client: TestClient, tmp_env: Path) -> None:
    token, candidate_id = _start(client, "deleter-kb@example.com")
    db_path = tmp_env / "offer_coming.db"
    _insert_item(
        db_path,
        item_id="k_gone",
        candidate_id=candidate_id,
        title="要删的面经",
        body="删后不应再出现",
    )
    _insert_segment(
        db_path,
        segment_id="s_gone",
        item_id="k_gone",
        ordinal=0,
        text="删后不应再出现",
    )
    deleted = client.delete("/api/knowledge-items/k_gone", headers=_headers(token))
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"id": "k_gone", "deleted": True}

    listed = client.get("/api/knowledge-items", headers=_headers(token))
    assert listed.json()["data"]["items"] == []
    missing = client.get("/api/knowledge-items/k_gone", headers=_headers(token))
    assert missing.status_code == 404

    db = sqlite3.connect(db_path)
    item_row = db.execute(
        "select id from knowledge_items where id=?", ("k_gone",)
    ).fetchone()
    segment_row = db.execute(
        "select id from knowledge_segments where id=?", ("s_gone",)
    ).fetchone()
    db.close()
    assert item_row is None
    assert segment_row is None


def test_delete_owner_checks(client: TestClient, tmp_env: Path) -> None:
    token_a, candidate_a = _start(client, "del-kb-a@example.com")
    token_b, _ = _start(client, "del-kb-b@example.com")
    _insert_item(
        tmp_env / "offer_coming.db",
        item_id="k_keep_owner",
        candidate_id=candidate_a,
        title="甲保留",
        body="别人不能删",
    )
    forbidden = client.delete(
        "/api/knowledge-items/k_keep_owner", headers=_headers(token_b)
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error_code"] == "FORBIDDEN"
    missing = client.delete("/api/knowledge-items/k_absent", headers=_headers(token_a))
    assert missing.status_code == 404
    still = client.get("/api/knowledge-items/k_keep_owner", headers=_headers(token_a))
    assert still.status_code == 200


def test_unauthorized_is_401(client: TestClient) -> None:
    listed = client.get("/api/knowledge-items")
    assert listed.status_code == 401
    assert listed.json()["error_code"] == "UNAUTHORIZED"
    detail = client.get("/api/knowledge-items/k_any")
    assert detail.status_code == 401
    deleted = client.delete("/api/knowledge-items/k_any")
    assert deleted.status_code == 401
    uploaded = client.post(
        "/api/knowledge-items",
        files={"file": ("exp.txt", "一面问幂等。".encode("utf-8"), "text/plain")},
    )
    assert uploaded.status_code == 401


def test_upload_txt_is_listed_with_body(client: TestClient) -> None:
    token, _ = _start(client, "kb-upload@example.com")
    marker = "TUPLOAD-布谷鸟过滤器幂等键"
    response = client.post(
        "/api/knowledge-items",
        headers=_headers(token),
        files={"file": ("渠道面经.txt", marker.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 201
    item = response.json()["data"]
    assert item["source_type"] == "upload"
    assert item["title"] == "渠道面经"
    assert marker in item["body"]
    listed = client.get("/api/knowledge-items", headers=_headers(token))
    ids = {row["id"] for row in listed.json()["data"]["items"]}
    assert item["id"] in ids
    detail = client.get(f"/api/knowledge-items/{item['id']}", headers=_headers(token))
    assert detail.status_code == 200
    assert marker in detail.json()["data"]["body"]


def test_upload_duplicate_body_does_not_add_row(client: TestClient) -> None:
    token, _ = _start(client, "kb-dup@example.com")
    body = "同一段面经正文。".encode("utf-8")
    first = client.post(
        "/api/knowledge-items",
        headers=_headers(token),
        files={"file": ("same.txt", body, "text/plain")},
    )
    second = client.post(
        "/api/knowledge-items",
        headers=_headers(token),
        files={"file": ("same.txt", body, "text/plain")},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    listed = client.get("/api/knowledge-items", headers=_headers(token))
    assert len(listed.json()["data"]["items"]) == 1


def test_upload_rejects_empty_and_unsupported(client: TestClient) -> None:
    token, _ = _start(client, "kb-badfile@example.com")
    empty = client.post(
        "/api/knowledge-items",
        headers=_headers(token),
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert empty.status_code == 400
    bad = client.post(
        "/api/knowledge-items",
        headers=_headers(token),
        files={"file": ("note.exe", b"not-an-experience", "application/octet-stream")},
    )
    assert bad.status_code == 400


def test_knowledge_upload_dir_exists(client: TestClient, tmp_env: Path) -> None:
    token, _ = _start(client, "dir@example.com")
    listed = client.get("/api/knowledge-items", headers=_headers(token))
    assert listed.status_code == 200
    assert (tmp_env / "uploads" / "knowledge").is_dir()


def test_no_xiaohongshu_or_xhslink_fetch() -> None:
    for relative in _SCOPED_FILES:
        text = (_SRC_ROOT / relative).read_text(encoding="utf-8")
        lowered = text.lower()
        for host in _BLOCKED_HOSTS:
            assert host not in lowered
        assert "httpx" not in lowered
        assert "requests." not in lowered
