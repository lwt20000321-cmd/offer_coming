from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from src.config.settings import get_settings
from src.services.agent import (
    AgentService,
    load_all_prompts,
    mark_conversation_busy,
    release_conversation,
    _clip_user_reply,
    _should_clip_user_reply,
    _slim_snapshot,
    _visible_chat_history,
)
from src.services.llm_client import LlmUnavailableError
from src.services.llm_probe import LlmKeyProbeResult
from src.services.application_status import (
    STATUS_PROGRESS_ASK,
    canonical_status_from_text,
    looks_like_apply_intent,
    looks_like_missing_row_complaint,
)
from src.services.tools import normalize_application_status
from src.utils.crypto import beijing_today

FAKE_USER_KEY = "sk-test-agent-user-key"
FAKE_OPERATOR_KEY = "sk-test-operator-must-not-be-used"


@pytest.fixture(autouse=True)
def _candidate_key_and_mock_llm(tmp_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    object.__setattr__(get_settings(), "llm_allow_mock", True)

    async def ok_probe(api_key: str, settings: Any = None) -> LlmKeyProbeResult:
        return LlmKeyProbeResult(verdict="ok", http_status=200)

    monkeypatch.setattr("src.services.candidate.probe_llm_api_key", ok_probe)


def _start(client: TestClient, email: str = "agent@example.com") -> tuple[str, str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email, "llm_api_key": FAKE_USER_KEY},
        files={"resume": ("resume.txt", b"Python backend intern with SQLite", "text/plain")},
    )
    assert response.status_code == 201
    data = response.json()["data"]
    current = client.get(
        "/api/candidates/current",
        headers={"Authorization": f"Bearer {data['session_token']}"},
    )
    candidate_id = current.json()["data"]["id"]
    return data["session_token"], data["conversation_id"], candidate_id


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
    }


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


def test_prompts_are_read_from_files() -> None:
    from src.services.prompt_context import SHARED_PROMPT_KEYS

    prompts = load_all_prompts()
    assert "小凹" in prompts["xiaoao_system.md"]
    assert "简历" in prompts["exam_points.md"]
    assert "五道" in prompts["questions.md"] or "五题" in prompts["questions.md"]
    assert "点评" in prompts["review.md"]
    assert "tone_level" in prompts["nudge.md"]
    assert "心情变好" in prompts["counseling.md"]
    assert "诡秘" in prompts["counseling.md"]
    assert "{{recent_event}}" in prompts["counseling.md"]
    assert "{{current_emotion}}" in prompts["counseling.md"]
    assert "{{ready_for_action}}" in prompts["counseling.md"]
    assert "{{session_ended}}" in prompts["counseling.md"]
    for text in prompts.values():
        for key in SHARED_PROMPT_KEYS:
            assert "{{" + key + "}}" in text


def test_status_text_normalization() -> None:
    assert normalize_application_status("已挂") == "rejected"
    assert normalize_application_status("挂了") == "rejected"
    assert normalize_application_status("被拒") == "rejected"
    assert normalize_application_status("淘汰") == "rejected"
    assert normalize_application_status("offer 黄了") == "rejected"
    assert normalize_application_status("简历挂") == "rejected"
    assert normalize_application_status("等待面试") == "waiting_interview"
    assert normalize_application_status("等面试") == "waiting_interview"
    assert normalize_application_status("约面") == "waiting_interview"
    assert normalize_application_status("一面") == "waiting_interview"
    assert normalize_application_status("二面") == "waiting_interview"
    assert normalize_application_status("三面") == "waiting_interview"
    assert normalize_application_status("简历筛选中") == "other"
    assert normalize_application_status("待测评") == "other"
    assert normalize_application_status("已测评") == "other"
    assert normalize_application_status("还在沟通中") == "other"
    assert canonical_status_from_text("现在一面") == "一面"
    assert looks_like_apply_intent("我投递了这个岗位")
    assert looks_like_apply_intent("加入到我的我的投递里去")
    assert looks_like_apply_intent("补录进去了吗")
    assert not looks_like_apply_intent("为什么在我的投递里没有看到")
    assert looks_like_missing_row_complaint("为什么在我的投递里没有看到")
    from src.services.application_status import parse_application_fields

    parsed = parse_application_fields(
        "产品经理（顺丰科技）投递时间：2026-09-15\n顺丰科技 | 应届生\n加入到我的投递里去"
    )
    assert parsed["company_name"] == "顺丰科技"
    assert parsed["role_title"] == "产品经理"


def test_send_message_sse_events(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    token, conversation_id, _ = _start(client)
    _patch_public_jd(monkeypatch)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "https://jobs.example.com/1 这个岗位"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(response.text)
    names = [name for name, _ in events]
    assert "status" in names
    assert "delta" in names
    assert "done" in names
    assert names[-1] == "done"
    first_status = next(payload for name, payload in events if name == "status")
    assert first_status["stage"] == "thinking"
    assert first_status["text"] == "小凹正在思考..."
    done = events[-1][1]
    assert done["message"]["role"] == "assistant"
    assert "id" in done["snapshot"]
    assert "today" in done["snapshot"]
    assert "counseling_active" in done["snapshot"]["today"]


def test_second_send_is_conflict_json(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, _ = _start(client)
    mark_conversation_busy(conversation_id)
    try:
        response = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "第二句"},
            headers=_headers(token),
        )
    finally:
        release_conversation(conversation_id)
    assert response.status_code == 409
    assert "application/json" in response.headers["content-type"]
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "CONFLICT"
    assert "等它说完" in body["error"]
    assert "event:" not in response.text


def test_empty_content_is_validation_json(client: TestClient) -> None:
    token, conversation_id, _ = _start(client)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "   "},
        headers=_headers(token),
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert "写出内容" in response.json()["error"]
    assert "application/json" in response.headers["content-type"]


def test_fetch_jd_persists_application_and_exam_points(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, candidate_id = _start(client)
    seen = _patch_public_jd(monkeypatch)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "请看 https://jobs.example.com/python 这个岗位"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert any(item.startswith("GET https://jobs.example.com/python") for item in seen)
    events = _sse_events(response.text)
    assert events[-1][0] == "done"
    message = events[-1][1]["message"]
    assert message["message_type"] == "jd_summary"
    assert "对照" in message["content"]

    db = sqlite3.connect(tmp_env / "offer_coming.db")
    row = db.execute(
        "select jd_url, exam_points, company_name from applications where candidate_id=?",
        (candidate_id,),
    ).fetchone()
    db.close()
    assert row is not None
    assert row[0] == "https://jobs.example.com/python"
    assert "对照" in row[1]
    assert row[2] == "示例公司"

    listed = client.get("/api/applications", headers={"Authorization": f"Bearer {token}"})
    apps = listed.json()["data"]["applications"]
    assert len(apps) == 1
    assert apps[0]["exam_points"]
    assert "jd_url" in apps[0]
    assert "normalized_status" in apps[0]


def test_jd_fetch_failure_error_event_and_notice(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, _ = _start(client)
    _patch_public_jd(monkeypatch, status_code=403, text="no")
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "https://jobs.example.com/blocked"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["error_code"] == "JD_FETCH_FAILED"
    assert "链接" in events[-1][1]["error"]

    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    rows = messages.json()["data"]["messages"]
    assert rows[-1]["message_type"] == "error_notice"
    assert "链接" in rows[-1]["content"]

    listed = client.get("/api/applications", headers={"Authorization": f"Bearer {token}"})
    assert listed.json()["data"]["applications"] == []


def test_update_application_status_normalization(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, candidate_id = _start(client)
    _patch_public_jd(monkeypatch)
    first = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "https://jobs.example.com/python"},
        headers=_headers(token),
    )
    assert first.status_code == 200

    waiting = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "这个岗位等待面试，deadline 2026-09-20"},
        headers=_headers(token),
    )
    assert waiting.status_code == 200
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    status_text, normalized, deadline = db.execute(
        "select status_text, normalized_status, deadline from applications where candidate_id=?",
        (candidate_id,),
    ).fetchone()
    assert normalized == "waiting_interview"
    assert "等待面试" in status_text
    assert deadline == "2026-09-20"

    rejected = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "这个岗位已挂了"},
        headers=_headers(token),
    )
    assert rejected.status_code == 200
    status_text, normalized = db.execute(
        "select status_text, normalized_status from applications where candidate_id=?",
        (candidate_id,),
    ).fetchone()
    assert normalized == "rejected"
    assert "已挂" in status_text

    other = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "进度还在沟通中"},
        headers=_headers(token),
    )
    assert other.status_code == 200
    status_text, normalized = db.execute(
        "select status_text, normalized_status from applications where candidate_id=?",
        (candidate_id,),
    ).fetchone()
    db.close()
    assert normalized == "other"
    assert "还在沟通" in status_text


def test_counseling_toggle_and_same_turn_questions(
    client: TestClient, tmp_env: Path
) -> None:
    token, conversation_id, candidate_id = _start(client)
    anxious = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "我很焦虑，今天不想面"},
        headers=_headers(token),
    )
    assert anxious.status_code == 200
    events = _sse_events(anxious.text)
    assert events[-1][0] == "done"
    assert events[-1][1]["message"]["message_type"] == "counseling"
    assert events[-1][1]["snapshot"]["today"]["counseling_active"] is True

    prompt = "请把简历里某段项目经历讲深一层"
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        "insert into question_sets (id, candidate_id, beijing_date, status, created_at) "
        "values (?, ?, ?, ?, '2026-09-13T02:00:00')",
        ("qs_t004", candidate_id, beijing_today(), "in_progress"),
    )
    db.execute(
        "insert into questions (id, question_set_id, seq, kind, prompt, answer, target_application_id) "
        "values (?, ?, 1, 'common', ?, null, null)",
        ("q_t004_1", "qs_t004", prompt),
    )
    db.execute(
        "insert into questions (id, question_set_id, seq, kind, prompt, answer, target_application_id) "
        "values (?, ?, 2, 'role', '结合岗位讲设计', null, null)",
        ("q_t004_2", "qs_t004"),
    )
    db.execute(
        "insert into questions (id, question_set_id, seq, kind, prompt, answer, target_application_id) "
        "values (?, ?, 3, 'role', '结合考查点讲风险', null, null)",
        ("q_t004_3", "qs_t004"),
    )
    db.commit()
    db.close()

    better = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "心情变好了"},
        headers=_headers(token),
    )
    assert better.status_code == 200
    done = _sse_events(better.text)[-1][1]
    assert done["snapshot"]["today"]["counseling_active"] is False
    assert prompt in done["message"]["content"]


def test_save_answers_ready_for_review(
    client: TestClient, tmp_env: Path
) -> None:
    token, conversation_id, candidate_id = _start(client)
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        "insert into question_sets (id, candidate_id, beijing_date, status, created_at) "
        "values (?, ?, ?, ?, '2026-09-13T02:00:00')",
        ("qs_ans", candidate_id, beijing_today(), "in_progress"),
    )
    for seq in (1, 2, 3):
        db.execute(
            "insert into questions (id, question_set_id, seq, kind, prompt, answer, target_application_id) "
            "values (?, ?, ?, ?, ?, null, null)",
            (f"q_ans_{seq}", "qs_ans", seq, "common" if seq == 1 else "role", f"题{seq}"),
        )
    db.commit()
    db.close()

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "第一题：做过 SQLite。第二题：会拆服务。第三题：会讲取舍。"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    done = _sse_events(response.text)[-1][1]
    assert done["message"]["message_type"] == "review"
    assert "缺点" in done["message"]["content"]

    db = sqlite3.connect(tmp_env / "offer_coming.db")
    answers = [
        row[0]
        for row in db.execute(
            "select answer from questions where question_set_id='qs_ans' order by seq"
        ).fetchall()
    ]
    status, review = db.execute(
        "select status, review from question_sets where id='qs_ans'"
    ).fetchone()
    db.close()
    assert answers == ["做过 SQLite。", "会拆服务。", "会讲取舍。"]
    assert status == "completed"
    assert review

    current = client.get(
        "/api/question-sets/current",
        headers={"Authorization": f"Bearer {token}"},
    )
    payload = current.json()["data"]
    assert payload["status"] == "completed"
    assert payload["review"]
    assert all(item["answer"] for item in payload["questions"])


def test_foreign_conversation_is_forbidden(client: TestClient) -> None:
    token_a, conversation_a, _ = _start(client, email="a@example.com")
    token_b, _conversation_b, _ = _start(client, email="b@example.com")
    response = client.post(
        f"/api/conversations/{conversation_a}/messages",
        json={"content": "你好"},
        headers=_headers(token_b),
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "FORBIDDEN"


def test_missing_candidate_key_sse_llm_key_missing(
    client: TestClient, tmp_env: Path
) -> None:
    token, conversation_id, candidate_id = _start(client, email="nokey@example.com")
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    db.execute(
        "update candidates set llm_api_key_ciphertext = null, llm_key_status = 'missing' "
        "where id=?",
        (candidate_id,),
    )
    db.commit()
    db.close()
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "你好，帮我看看岗位"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["error_code"] == "LLM_KEY_MISSING"
    assert "我的key" in events[-1][1]["error"]
    assert not any(name == "done" for name, _ in events)
    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    rows = messages.json()["data"]["messages"]
    assistant = [item for item in rows if item["role"] == "assistant"]
    assert assistant
    assert assistant[-1]["message_type"] == "error_notice"
    assert "我的key" in assistant[-1]["content"]
    assert assistant[-1]["message_type"] != "chat" or "对照" not in assistant[-1]["content"]


def test_invalid_key_401_sse_and_no_operator_fallback(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, conversation_id, candidate_id = _start(client, email="badkey@example.com")
    settings = get_settings()
    object.__setattr__(settings, "llm_allow_mock", False)
    object.__setattr__(settings, "llm_api_key", FAKE_OPERATOR_KEY)
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("Authorization"))
        return httpx.Response(
            401,
            json={"error": {"code": "invalid_api_key", "message": "invalid"}},
        )

    def factory(current=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("src.services.llm_client.create_llm_http_client", factory)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "你好，帮我规划一下"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["error_code"] == "LLM_KEY_INVALID"
    assert "我的key" in events[-1][1]["error"]
    assert not any(name == "done" for name, _ in events)
    assert calls
    assert all(item == f"Bearer {FAKE_USER_KEY}" for item in calls)
    assert all(FAKE_OPERATOR_KEY not in (item or "") for item in calls)
    assert len(calls) == 1
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    status = db.execute(
        "select llm_key_status from candidates where id=?",
        (candidate_id,),
    ).fetchone()[0]
    db.close()
    assert status == "invalid"
    messages = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    rows = messages.json()["data"]["messages"]
    assistant = [item for item in rows if item["role"] == "assistant"]
    assert assistant[-1]["message_type"] == "error_notice"
    assert not any(
        item["role"] == "assistant" and item["message_type"] not in {"error_notice"}
        for item in rows
    )


def test_clip_user_reply_caps_length() -> None:
    text = "结论已经有了。" + ("补充说明很长。" * 80)
    clipped = _clip_user_reply(text, 80)
    assert len(clipped) <= 80
    assert clipped.endswith(("。", "…"))


def test_clip_skips_five_question_stems() -> None:
    stems = "今日五题：\n\n" + "\n".join(
        f"{i}. {'这是一道足够长的面试题干，用来确认不会被字符上限截掉。' * 3}"
        for i in range(1, 6)
    )
    assert not _should_clip_user_reply(stems, "chat")
    assert not _should_clip_user_reply(stems, "questions")
    assert not _should_clip_user_reply("点评很长。" * 40, "review")
    assert _should_clip_user_reply("闲聊补充说明很长。" * 40, "chat")


def test_orchestrator_prompt_keeps_five_questions_complete() -> None:
    text = load_all_prompts()["orchestrator.md"]
    assert "180 字" in text
    assert "250 字" in text
    assert "不得中途截断" in text
    assert "call_interview_agent" in text
    assert "不做完整模拟面试" in text
    assert "1～3 句" in text


def test_slim_snapshot_drops_application_bodies() -> None:
    slim = _slim_snapshot(
        {
            "beijing_time": "2026-09-15T03:00:00+08:00",
            "counseling_active": False,
            "resume_parse_ok": False,
            "resume_excerpt": "x" * 1500,
            "applications": [
                {
                    "normalized_status": "waiting_interview",
                    "exam_points": "很长考查点" * 40,
                }
            ],
            "question_set": {"status": "pending"},
            "today": {},
        }
    )
    assert "applications" not in slim
    assert "resume_excerpt" not in slim
    assert slim["application_count"] == 1
    assert slim["waiting_interview_count"] == 1
    assert slim["question_set_status"] == "pending"
    dumped = json.dumps(slim, ensure_ascii=False)
    assert "很长考查点" not in dumped


def test_looks_like_five_question_ask() -> None:
    from src.services.questions import looks_like_five_question_ask

    assert looks_like_five_question_ask("又截断了")
    assert looks_like_five_question_ask("今日五题")
    assert looks_like_five_question_ask("把五题再发一遍")
    assert not looks_like_five_question_ask("第一题我这样答")
    assert not looks_like_five_question_ask("把顺丰加入投递")


def test_visible_chat_history_excludes_current_and_truncates() -> None:
    class _Row:
        def __init__(self, id: str, role: str, content: str) -> None:
            self.id = id
            self.role = role
            self.content = content

    rows = [
        _Row("m1", "user", "我更想去星云科技"),
        _Row("m2", "assistant", "记下了。"),
        _Row("m3", "user", "刚才那家公司叫什么"),
    ]
    visible = _visible_chat_history(
        rows, exclude_id="m3", max_messages=16, max_chars=400
    )
    assert visible == [
        {"role": "user", "content": "我更想去星云科技"},
        {"role": "assistant", "content": "记下了。"},
    ]
    long_row = _Row("m9", "assistant", "啊" * 80)
    clipped = _visible_chat_history(
        [long_row], exclude_id=None, max_messages=16, max_chars=20
    )
    assert len(clipped[0]["content"]) <= 20
    assert clipped[0]["content"].endswith("…")


def test_second_turn_sends_prior_chat_to_orchestrator(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[dict[str, Any]]] = []
    original = AgentService._complete

    async def wrapped(self: AgentService, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        if kwargs.get("role") == "orchestrator" and messages and messages[-1].get("role") == "user":
            captured.append(
                [item for item in messages if item.get("role") in {"user", "assistant"}]
            )
        return await original(self, messages, **kwargs)

    monkeypatch.setattr(AgentService, "_complete", wrapped)
    token, conversation_id, _ = _start(client, email="history@example.com")
    first = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "我更想去星云科技"},
        headers=_headers(token),
    )
    assert first.status_code == 200
    captured.clear()
    second = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "刚才那家公司叫什么"},
        headers=_headers(token),
    )
    assert second.status_code == 200
    assert captured
    blob = json.dumps(captured[0], ensure_ascii=False)
    assert "我更想去星云科技" in blob
    assert "刚才那家公司叫什么" in blob


def test_apply_phrase_records_even_if_orchestrator_skips_tools(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = AgentService._complete

    async def silent(self: AgentService, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        if kwargs.get("role") == "orchestrator":
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "这条还没写进表，请补录。",
                            "tool_calls": [],
                        }
                    }
                ]
            }
        return await original(self, messages, **kwargs)

    monkeypatch.setattr(AgentService, "_complete", silent)
    _patch_public_jd(monkeypatch)
    token, conversation_id, candidate_id = _start(client, email="apply@example.com")
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "我投递了这个岗位 https://jobs.example.com/python"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "done"
    assert STATUS_PROGRESS_ASK in events[-1][1]["message"]["content"]
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    row = db.execute(
        "select jd_url, status_text from applications where candidate_id=?",
        (candidate_id,),
    ).fetchone()
    db.close()
    assert row is not None
    assert row[0] == "https://jobs.example.com/python"
    assert row[1] == ""


def test_join_my_applications_phrase_records_without_url(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = AgentService._complete

    async def silent(self: AgentService, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        if kwargs.get("role") == "orchestrator":
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "已为你补录顺丰科技岗位。当前一共五岗。",
                            "tool_calls": [],
                        }
                    }
                ]
            }
        return await original(self, messages, **kwargs)

    monkeypatch.setattr(AgentService, "_complete", silent)
    token, conversation_id, candidate_id = _start(client, email="sf-apply@example.com")
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": (
                "产品经理（顺丰科技）投递时间：2026-09-15 16:54:22\n"
                "顺丰科技 | 应届生 面试地点：线上面试\n"
                "加入到我的我的投递里去"
            )
        },
        headers=_headers(token),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "done"
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    row = db.execute(
        "select company_name, role_title from applications where candidate_id=?",
        (candidate_id,),
    ).fetchone()
    db.close()
    assert row is not None
    assert row[0] == "顺丰科技"
    assert row[1] == "产品经理"


def test_unreadable_link_still_records_when_job_info_present(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_public_jd(monkeypatch, status_code=403, text="no")
    token, conversation_id, candidate_id = _start(client, email="hik-apply@example.com")
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": (
                "https://jobs.example.com/blocked 我投了这个。"
                "产品市场专员（海康威视）"
            )
        },
        headers=_headers(token),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "done"
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    row = db.execute(
        "select company_name, role_title, jd_url from applications where candidate_id=?",
        (candidate_id,),
    ).fetchone()
    db.close()
    assert row is not None
    assert row[0] == "海康威视"
    assert row[1] == "产品市场专员"
    assert row[2] == "https://jobs.example.com/blocked"


def test_known_host_records_when_link_unreadable(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_public_jd(monkeypatch, status_code=403, text="no")
    token, conversation_id, candidate_id = _start(client, email="bambu-apply@example.com")
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "https://bambulab.jobs.example.com/x"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    listed = client.get("/api/applications", headers=_headers(token)).json()["data"]["applications"]
    assert listed
    assert "Bambu" in listed[0]["company_name"] or "创想" in listed[0]["company_name"]
    assert listed[0]["jd_url"] == "https://bambulab.jobs.example.com/x"


def test_add_named_job_ignores_old_link_in_history(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = AgentService._complete

    async def silent(self: AgentService, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        if kwargs.get("role") == "orchestrator":
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "好的，我记下了。",
                            "tool_calls": [],
                        }
                    }
                ]
            }
        return await original(self, messages, **kwargs)

    monkeypatch.setattr(AgentService, "_complete", silent)
    _patch_public_jd(monkeypatch, status_code=403, text="no")
    token, conversation_id, candidate_id = _start(client, email="sf-oldurl@example.com")
    first = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "产品经理（顺丰科技）网申进度，先放这儿。"},
        headers=_headers(token),
    )
    assert first.status_code == 200
    blocked = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "https://jobs.example.com/blocked"},
        headers=_headers(token),
    )
    assert blocked.status_code == 200
    add = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "把顺丰的岗加入到投递表中"},
        headers=_headers(token),
    )
    assert add.status_code == 200
    events = _sse_events(add.text)
    assert events[-1][0] == "done"
    listed = client.get("/api/applications", headers=_headers(token)).json()["data"]["applications"]
    names = [item["company_name"] for item in listed]
    assert "顺丰科技" in names
    assert all(item.get("jd_url") != "https://jobs.example.com/blocked" for item in listed)


def test_named_status_is_saved_without_asking(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_public_jd(monkeypatch)
    token, conversation_id, candidate_id = _start(client, email="statusask@example.com")
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "我投递了这个岗位，现在二面 https://jobs.example.com/python"},
        headers=_headers(token),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert STATUS_PROGRESS_ASK not in events[-1][1]["message"]["content"]
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    status_text, normalized = db.execute(
        "select status_text, normalized_status from applications where candidate_id=?",
        (candidate_id,),
    ).fetchone()
    db.close()
    assert status_text == "二面"
    assert normalized == "waiting_interview"


def _silent_orchestrator(monkeypatch: pytest.MonkeyPatch, text: str = "好的") -> None:
    original = AgentService._complete

    async def silent(self: AgentService, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        if kwargs.get("role") == "orchestrator":
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": text,
                            "tool_calls": [],
                        }
                    }
                ]
            }
        return await original(self, messages, **kwargs)

    monkeypatch.setattr(AgentService, "_complete", silent)


def test_message_list_without_after_id_returns_latest(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _silent_orchestrator(monkeypatch)
    token, conversation_id, _candidate_id = _start(client, email="latest-page@example.com")
    for text in ("第一句marker-aaa", "第二句marker-bbb", "第三句marker-ccc"):
        posted = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": text},
            headers=_headers(token),
        )
        assert posted.status_code == 200
        assert _sse_events(posted.text)[-1][0] == "done"

    listed = client.get(
        f"/api/conversations/{conversation_id}/messages",
        params={"limit": 2},
        headers=_headers(token),
    )
    assert listed.status_code == 200
    rows = listed.json()["data"]["messages"]
    blob = "\n".join(item["content"] for item in rows)
    assert "第三句marker-ccc" in blob
    assert "第一句marker-aaa" not in blob
    assert rows[0]["created_at"] <= rows[-1]["created_at"]


def test_interview_unavailable_keeps_recorded_row(
    client: TestClient, tmp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = AgentService._complete

    async def plan(self: AgentService, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        if kwargs.get("role") == "orchestrator":
            if getattr(self, "_exam_dispatched", False):
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "岗位先记下了。",
                                "tool_calls": [],
                            }
                        }
                    ]
                }
            self._exam_dispatched = True
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_exam",
                                    "type": "function",
                                    "function": {
                                        "name": "call_interview_agent",
                                        "arguments": json.dumps({"task": "exam_points"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return await original(self, messages, **kwargs)

    async def boom(self: AgentService, **arguments: Any) -> dict[str, Any]:
        raise LlmUnavailableError()

    monkeypatch.setattr(AgentService, "_complete", plan)
    monkeypatch.setattr(AgentService, "_run_interview", boom)
    _patch_public_jd(monkeypatch)
    token, conversation_id, candidate_id = _start(
        client, email="timeout-apply@example.com"
    )
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": "产品经理（顺丰科技）我投递了这个岗位 https://jobs.example.com/python"
        },
        headers=_headers(token),
    )
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1][0] == "done"
    db = sqlite3.connect(tmp_env / "offer_coming.db")
    row = db.execute(
        "select company_name, jd_url from applications where candidate_id=?",
        (candidate_id,),
    ).fetchone()
    db.close()
    assert row is not None
    assert row[0] == "顺丰科技"
    assert row[1] == "https://jobs.example.com/python"
