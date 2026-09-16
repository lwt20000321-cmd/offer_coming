from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from src.config.settings import get_settings
from src.services.knowledge_search import (
    KnowledgeSearchService,
    body_is_unusable,
    host_is_blocked,
    parse_search_payload,
    usable_items,
)
from src.services.llm_client import LlmClient, create_llm_http_client


def _start(client: TestClient, email: str = "kbsearch@example.com") -> tuple[str, str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email},
        files={"resume": ("resume.txt", b"Python intern", "text/plain")},
    )
    assert response.status_code == 201
    data = response.json()["data"]
    current = client.get(
        "/api/candidates/current",
        headers={"Authorization": f"Bearer {data['session_token']}"},
    )
    return data["session_token"], data["conversation_id"], current.json()["data"]["id"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}


def _allow_public_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    import ipaddress

    def fake_host_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr("src.services.jd_fetch._host_ips", fake_host_ips)


def _patch_public_jd(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    _allow_public_hosts(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.host}")
        return httpx.Response(
            200,
            text="<html><body><p>招聘 Python 后端</p></body></html>",
            headers={"content-type": "text/html"},
        )

    def factory(settings=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr("src.services.jd_fetch.create_jd_http_client", factory)
    return seen


def test_llm_client_disables_env_trust(tmp_env: Path) -> None:
    client = create_llm_http_client(get_settings())
    assert client.trust_env is False


def test_no_dashscope_import_in_backend_src() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    hits: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "import dashscope" in text or "from dashscope" in text:
            hits.append(str(path))
    assert hits == []


def test_xhs_only_body_is_unusable() -> None:
    assert body_is_unusable("")
    assert body_is_unusable("小红书上有帖")
    assert body_is_unusable("小红书上有相关帖")
    assert not body_is_unusable("一面问了项目难点，需要讲清楚取舍。")


def test_usable_items_drop_empty_and_xhs_only() -> None:
    payload = {
        "failed": False,
        "items": [
            {"title": "空", "body": ""},
            {"title": "伪成功", "body": "小红书上有帖"},
            {"title": "可用", "body": "一面问了缓存穿透怎么处理。"},
        ],
    }
    items = usable_items(payload, limit=3)
    assert [item["title"] for item in items] == ["可用"]


@pytest.mark.asyncio
async def test_search_http_json_contains_enable_search(
    tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "method": request.method,
                "url": str(request.url),
                "body": json.loads(request.content.decode("utf-8")),
            }
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "items": [
                                        {
                                            "title": "公开面经",
                                            "body": "一面问了索引和事务。",
                                            "source_hint": "博客",
                                        }
                                    ],
                                    "failed": False,
                                    "fail_reason": None,
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    def factory(settings=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(handler))

    settings = get_settings()
    object.__setattr__(settings, "llm_api_key", "test-key-not-real")
    monkeypatch.setattr("src.services.llm_client.create_llm_http_client", factory)
    service = KnowledgeSearchService(settings)
    result = await service.search_public_experiences(
        company_name="示例公司",
        role_title="后端开发",
        prompt="查找面经",
    )
    object.__setattr__(settings, "llm_api_key", "")
    assert result["ok"] is True
    assert captured
    assert captured[0]["method"] == "POST"
    assert captured[0]["url"].endswith("/chat/completions")
    assert captured[0]["body"]["enable_search"] is True
    assert captured[0]["body"]["search_options"]["forced_search"] is True
    assert captured[0]["body"]["model"] == "test-knowledge"
    assert all("xiaohongshu.com" not in item["url"] for item in captured)
    assert all("xhslink.com" not in item["url"] for item in captured)
    assert all(item["method"] != "GET" for item in captured)


@pytest.mark.asyncio
async def test_search_posts_enable_search_and_skips_blocked_hosts(
    tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    async def fake_chat(self, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "items": [
                                    {
                                        "title": "公开面经",
                                        "body": "一面问了索引和事务。",
                                        "source_hint": "博客",
                                    }
                                ],
                                "failed": False,
                                "fail_reason": None,
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    settings = get_settings()
    object.__setattr__(settings, "llm_api_key", "test-key-not-real")
    monkeypatch.setattr(LlmClient, "chat_completions", fake_chat)
    service = KnowledgeSearchService(settings)
    result = await service.search_public_experiences(
        company_name="示例公司",
        role_title="后端开发",
        prompt="查找面经",
    )
    assert result["ok"] is True
    assert seen
    assert seen[0]["enable_search"] is True
    assert seen[0]["model"] == "test-knowledge"
    assert seen[0]["search_options"]["forced_search"] is True
    assert host_is_blocked("https://www.xiaohongshu.com/discovery/item/1")
    object.__setattr__(settings, "llm_api_key", "")


@pytest.mark.asyncio
async def test_search_without_key_is_mock_failure(tmp_env: Path) -> None:
    service = KnowledgeSearchService(get_settings())
    result = await service.search_public_experiences(
        company_name="示例公司",
        role_title="后端开发",
        prompt="查找面经",
    )
    assert result["ok"] is False
    assert result.get("mocked") is True
    assert "发文件" in result["fail_reason"] or "粘贴" in result["fail_reason"]


def test_failed_search_does_not_persist_items(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, candidate_id = _start(client)
    jd_seen = _patch_public_jd(monkeypatch)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "https://jobs.example.com/python"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert all("xiaohongshu.com" not in item and "xhslink.com" not in item for item in jd_seen)
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    items = db.execute(
        "select id, title from knowledge_items where candidate_id=?",
        (candidate_id,),
    ).fetchall()
    apps = db.execute(
        "select id, exam_points from applications where candidate_id=?",
        (candidate_id,),
    ).fetchall()
    db.close()
    assert items == []
    assert apps
    assert "对照" in apps[0][1]


def test_xhs_link_does_not_get_and_does_not_write_row(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, candidate_id = _start(client, "xhs@example.com")
    seen = _patch_public_jd(monkeypatch)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "https://www.xiaohongshu.com/explore/abc"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert seen == []
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    count = db.execute(
        "select count(*) from applications where candidate_id=?",
        (candidate_id,),
    ).fetchone()[0]
    items = db.execute(
        "select count(*) from knowledge_items where candidate_id=?",
        (candidate_id,),
    ).fetchone()[0]
    db.close()
    assert count == 0
    assert items == 0


def test_parse_search_payload_reads_fenced_json() -> None:
    raw = '```json\n{"items":[{"title":"t","body":"一面问了锁","source_hint":"博客"}],"failed":false,"fail_reason":null}\n```'
    payload = parse_search_payload(raw)
    assert payload["failed"] is False
    assert payload["items"][0]["body"] == "一面问了锁"
