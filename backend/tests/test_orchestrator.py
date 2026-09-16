from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pycore.core.exceptions import ConfigurationError
from src.config.settings import load_settings_from_dict
from src.services.agent import load_all_prompts
from src.services.knowledge_search import host_is_blocked

TEST_SECRET = "test-secret-key-not-for-production"
_CORS = [
    "http://localhost:5199",
    "http://127.0.0.1:5199",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
]
_FIVE_MODELS = {
    "llm_orchestrator_model": "test-orchestrator",
    "llm_nudge_model": "test-nudge",
    "llm_interview_model": "test-interview",
    "llm_knowledge_model": "test-knowledge",
    "llm_counseling_model": "test-counseling",
}


def _start(client: TestClient, email: str = "orch@example.com") -> tuple[str, str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email},
        files={"resume": ("resume.txt", b"Python backend intern with SQLite", "text/plain")},
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


def _allow_public_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    import ipaddress

    def fake_host_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            if host.lower() in {"localhost", "metadata.google.internal"}:
                return [ipaddress.ip_address("127.0.0.1")]
            return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr("src.services.jd_fetch._host_ips", fake_host_ips)


def _patch_public_jd(monkeypatch: pytest.MonkeyPatch, status_code: int = 200, text: str = "") -> list[str]:
    seen: list[str] = []
    html = text or "<html><body><p>招聘 Python 后端，需要项目经验</p></body></html>"
    _allow_public_hosts(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        return httpx.Response(status_code, text=html, headers={"content-type": "text/html"})

    def factory(settings=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr("src.services.jd_fetch.create_jd_http_client", factory)
    return seen


def _base_settings(tmp_path: Path, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "debug": True,
        "secret_key": TEST_SECRET,
        "host": "127.0.0.1",
        "port": 8099,
        "cors_origins": _CORS,
        "database_path": str(tmp_path / "offer_coming.db"),
        "upload_dir": str(tmp_path / "uploads"),
        "timezone": "Asia/Shanghai",
        "llm_api_key": "",
        **_FIVE_MODELS,
    }
    data.update(overrides)
    return data


def test_five_models_must_be_unique(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_settings_from_dict(
            _base_settings(
                tmp_path,
                llm_orchestrator_model="same",
                llm_nudge_model="same",
                llm_interview_model="same",
                llm_knowledge_model="same",
                llm_counseling_model="same",
            )
        )


def test_missing_model_fails_startup(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_settings_from_dict(_base_settings(tmp_path, llm_knowledge_model=""))


def test_env_example_lists_five_models_without_secrets() -> None:
    text = (
        Path(__file__).resolve().parents[1] / ".env.example"
    ).read_text(encoding="utf-8")
    for field in (
        "llm_orchestrator_model",
        "llm_nudge_model",
        "llm_interview_model",
        "llm_knowledge_model",
        "llm_counseling_model",
        "llm_base_url",
        "llm_api_key",
    ):
        assert field in text
    assert "llm_model=" not in text
    assert "sk-" not in text
    key_line = next(line for line in text.splitlines() if line.startswith("llm_api_key"))
    assert key_line.strip() == "llm_api_key="


def test_prompts_include_orchestrator_and_knowledge_files() -> None:
    prompts = load_all_prompts()
    assert "识别用户" in prompts["orchestrator.md"] or "专业 Agent" in prompts["orchestrator.md"]
    assert "不要编造" in prompts["knowledge_search.md"]
    assert "文件" in prompts["knowledge_ingest.md"] or "粘贴" in prompts["knowledge_ingest.md"]
    assert "JSON" in prompts["knowledge_evaluate.md"]


def test_dispatch_writes_orchestrator_log_first(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, candidate_id = _start(client)
    _patch_public_jd(monkeypatch)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "请看 https://jobs.example.com/python 这个岗位"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    names = [name for name, _ in events]
    assert "status" in names
    assert names[-1] == "done"
    visible = json.dumps([payload for _, payload in events], ensure_ascii=False)
    assert "主体" not in visible
    assert "子 Agent" not in visible
    assert "子Agent" not in visible
    status_texts = [payload.get("text", "") for name, payload in events if name == "status"]
    assert "正在读取岗位链接" in status_texts
    assert all("主体" not in text and "工具" not in text for text in status_texts)

    db = sqlite3.connect(tmp_env / "offer_coming.db")
    logs = db.execute(
        "select role, model, purpose from llm_call_logs where candidate_id=? order by rowid",
        (candidate_id,),
    ).fetchall()
    db.close()
    assert logs
    assert logs[0][0] == "orchestrator"
    assert logs[0][1] == "test-orchestrator"
    later = [row[0] for row in logs[1:]]
    assert "interview" in later or "nudge" in later or "knowledge" in later


def test_same_jd_url_updates_existing_row(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, candidate_id = _start(client, "dup@example.com")
    _patch_public_jd(monkeypatch)
    first = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "https://jobs.example.com/python"},
        headers=_headers(token),
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "再看一次 https://jobs.example.com/python"},
        headers=_headers(token),
    )
    assert second.status_code == 200
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    rows = db.execute(
        "select id, jd_url, exam_points from applications where candidate_id=?",
        (candidate_id,),
    ).fetchall()
    db.close()
    assert len(rows) == 1
    assert rows[0][1] == "https://jobs.example.com/python"
    assert "对照" in rows[0][2]


def test_failed_jd_does_not_write_row(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, candidate_id = _start(client, "failjd@example.com")
    _patch_public_jd(monkeypatch, status_code=403, text="no")
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "https://jobs.example.com/blocked"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["error_code"] == "JD_FETCH_FAILED"
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    count = db.execute(
        "select count(*) from applications where candidate_id=?",
        (candidate_id,),
    ).fetchone()[0]
    db.close()
    assert count == 0


def test_blocked_host_is_recognized() -> None:
    assert host_is_blocked("https://www.xiaohongshu.com/explore/1")
    assert host_is_blocked("https://xhslink.com/abc")
    assert not host_is_blocked("https://jobs.example.com/python")


def test_conversation_file_ingest_mentions_and_persists(
    client: TestClient, tmp_env: Path
) -> None:
    token, conversation_id, candidate_id = _start(client, "ingest@example.com")
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        data={"content": "帮我收一下这份面经"},
        files={"file": ("exp.txt", "一面问了事务隔离级别和索引选择。".encode(), "text/plain")},
        headers=_headers(token),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "done"
    visible = json.dumps([payload for _, payload in events], ensure_ascii=False)
    assert "已收入" in visible
    assert "主体" not in visible
    status_texts = [payload.get("text", "") for name, payload in events if name == "status"]
    assert "正在收录面经" in status_texts
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    rows = db.execute(
        "select title, body, source_type from knowledge_items where candidate_id=?",
        (candidate_id,),
    ).fetchall()
    db.close()
    assert len(rows) == 1
    assert rows[0][2] == "upload"
    assert "事务隔离" in rows[0][1]
