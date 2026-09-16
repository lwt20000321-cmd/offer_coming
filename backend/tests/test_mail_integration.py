"""T-010：催促邮件双通道真实验收。

测试时钟 + POST /internal/scheduler/tick。隔离库，不碰运行时业务库。
SMTP 从 backend/.env 经 ConfigManager 读取后复制到内存配置；不改用户 .env。
禁止把 smtp_password、授权码、完整密钥写入断言信息、日志或失败文本。
出题/催促走规则模板。求职者不进入辅导。无浏览器，不占 5175。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from pycore.core.config import ConfigManager
from src.config.settings import get_settings, load_app_config, load_settings_from_dict
from src.db.session import configure_engine_from_settings
from src.services.scheduler import reset_clock, set_clock, set_scheduler_loop_enabled

TZ = ZoneInfo("Asia/Shanghai")
RESUME_TEXT = (
    "刘文韬，Python 后端实习生。"
    "独立做过校园二手书交易平台 BookSwap，使用 FastAPI 和 SQLite。"
)
PROGRESS = "已约下周一面"
DEADLINE = "2026-09-20"
FAIL_CANDIDATE_EMAIL = "t010.undeliverable@example.invalid"


@pytest.fixture(autouse=True)
def _isolate_scheduler_loop() -> Iterator[None]:
    set_scheduler_loop_enabled(False)
    yield
    reset_clock()
    set_scheduler_loop_enabled(True)


def _assert_no_secret(text: str | None, secret: str) -> None:
    if secret and text and secret in text:
        pytest.fail("可见文本中出现了敏感配置，已省略原文。")


def _recipient_domain(email: str) -> str:
    if "@" not in email:
        return "unknown"
    return email.rsplit("@", 1)[1]


def _smtp_ready(settings: Any) -> bool:
    return bool(
        (settings.smtp_host or "").strip()
        and settings.smtp_port
        and (settings.smtp_user or "").strip()
        and settings.smtp_password
        and (settings.smtp_from or "").strip()
    )


def _live_payload(tmp_path: Path) -> dict[str, Any]:
    ConfigManager.reset()
    load_app_config()
    payload = get_settings().model_dump()
    payload["database_path"] = str(tmp_path / "t010.db")
    payload["upload_dir"] = str(tmp_path / "uploads")
    payload["debug"] = True
    return payload


@pytest.fixture
def live_env(tmp_path: Path) -> Iterator[Path]:
    payload = _live_payload(tmp_path)
    ConfigManager.reset()
    load_settings_from_dict(payload)
    configure_engine_from_settings()
    yield tmp_path
    ConfigManager.reset()


@pytest.fixture
def fail_env(tmp_path: Path) -> Iterator[Path]:
    payload = _live_payload(tmp_path)
    payload["database_path"] = str(tmp_path / "t010-fail.db")
    payload["smtp_host"] = ""
    payload["smtp_port"] = None
    payload["smtp_password"] = ""
    payload["smtp_from"] = ""
    ConfigManager.reset()
    load_settings_from_dict(payload)
    configure_engine_from_settings()
    yield tmp_path
    ConfigManager.reset()


@pytest.fixture
def live_client(live_env: Path) -> Iterator[TestClient]:
    import src.main as main_mod

    application = main_mod.build_app()
    main_mod._app = application
    with TestClient(application) as test_client:
        test_client.timeout = httpx.Timeout(110.0)
        yield test_client
    main_mod._app = None


@pytest.fixture
def fail_client(fail_env: Path) -> Iterator[TestClient]:
    import src.main as main_mod

    application = main_mod.build_app()
    main_mod._app = application
    with TestClient(application) as test_client:
        test_client.timeout = httpx.Timeout(110.0)
        yield test_client
    main_mod._app = None


def _use_scheduler_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.services.questions._llm_configured", lambda: False)
    monkeypatch.setattr("src.services.nudge._llm_configured", lambda: False)


def _start(client: TestClient, email: str) -> tuple[str, str, str]:
    response = client.post(
        "/api/candidates",
        data={"email": email},
        files={"resume": ("resume.txt", RESUME_TEXT.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    return data["session_token"], data["conversation_id"], data["candidate"]["id"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _iso(day: int, hour: int) -> str:
    current = datetime(2026, 9, day, hour, 0, 0, tzinfo=TZ)
    set_clock(lambda value=current: value)
    return current.isoformat()


def _tick(client: TestClient, day: int, hour: int):
    response = client.post("/internal/scheduler/tick", params={"at": _iso(day, hour)})
    assert response.status_code == 200, response.text
    return response


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def _insert_application(
    db_path: Path,
    candidate_id: str,
    *,
    app_id: str,
    company: str,
    role: str,
    normalized_status: str,
    status_text: str,
    progress: str = PROGRESS,
    deadline: str | None = DEADLINE,
) -> None:
    conn = _connect(db_path)
    conn.execute(
        """
        insert into applications (
            id, candidate_id, company_name, role_title, jd_url, jd_text,
            exam_points, progress_text, status_text, normalized_status,
            deadline, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            app_id,
            candidate_id,
            company,
            role,
            "https://example.com/job/1",
            "",
            "结合 BookSwap 考查接口设计",
            progress,
            status_text,
            normalized_status,
            deadline,
            "2026-09-13T01:00:00+00:00",
            "2026-09-13T01:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()


def _seed_waiting(db_path: Path, candidate_id: str) -> None:
    _insert_application(
        db_path,
        candidate_id,
        app_id="a_t010_wait",
        company="星云科技",
        role="后端开发",
        normalized_status="waiting_interview",
        status_text="等待面试",
    )


def _messages(client: TestClient, token: str, conversation_id: str) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/conversations/{conversation_id}/messages",
        headers=_headers(token),
    )
    assert response.status_code == 200
    rows = response.json()["data"]["messages"]
    assert isinstance(rows, list)
    return rows


def _nudges(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in messages if item["message_type"] == "nudge"]


def _counseling_active(db_path: Path, candidate_id: str) -> int:
    conn = _connect(db_path)
    flag = conn.execute(
        "select counseling_active from candidates where id=?",
        (candidate_id,),
    ).fetchone()[0]
    conn.close()
    return int(flag)


def _nudge_logs(
    db_path: Path, candidate_id: str, hour_key: str
) -> dict[str, sqlite3.Row]:
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        select channel, sent_ok, error_text, tone_level
        from nudge_logs
        where candidate_id=? and beijing_hour_key=?
        """,
        (candidate_id, hour_key),
    ).fetchall()
    conn.close()
    return {row["channel"]: row for row in rows}


def _hour_log_count(db_path: Path, candidate_id: str, hour_key: str) -> int:
    conn = _connect(db_path)
    count = conn.execute(
        """
        select count(*) from nudge_logs
        where candidate_id=? and beijing_hour_key=?
        """,
        (candidate_id, hour_key),
    ).fetchone()[0]
    conn.close()
    return int(count)


def _assert_paired_logs(
    db_path: Path,
    candidate_id: str,
    hour_key: str,
    *,
    email_sent_ok: bool,
    secret: str,
) -> None:
    by_channel = _nudge_logs(db_path, candidate_id, hour_key)
    assert set(by_channel) == {"chat", "email"}, f"{hour_key} 应成对写入 chat 与 email"
    assert int(by_channel["chat"]["sent_ok"]) == 1
    assert int(by_channel["email"]["sent_ok"]) == int(email_sent_ok)
    assert by_channel["chat"]["tone_level"] == by_channel["email"]["tone_level"]
    _assert_no_secret(by_channel["email"]["error_text"], secret)
    if not email_sent_ok:
        error_text = by_channel["email"]["error_text"] or ""
        assert error_text
        assert "邮件" in error_text or "smtp" in error_text.lower() or "配置" in error_text


def _complete_today_questions(db_path: Path, beijing_date: str = "2026-09-13") -> None:
    conn = _connect(db_path)
    row = conn.execute(
        "select id from question_sets where beijing_date=?",
        (beijing_date,),
    ).fetchone()
    assert row is not None
    set_id = row[0]
    conn.execute(
        "update questions set answer='已作答，接口对账与库存同步。' where question_set_id=?",
        (set_id,),
    )
    conn.execute(
        "update question_sets set status='completed' where id=?",
        (set_id,),
    )
    conn.commit()
    conn.close()


def _require_live_smtp() -> tuple[str, str]:
    settings = get_settings()
    if not _smtp_ready(settings):
        pytest.skip("SMTP 未配置，AC-015 与依赖收件箱的双通道条目未跑真实发信。")
    email = (settings.smtp_user or "").strip()
    if "@" not in email:
        pytest.skip("smtp_user 不是可用收件邮箱，无法按 AC-015 发到同一账号。")
    return email, settings.smtp_password


def test_dual_channel_nudge_real_smtp_and_idempotency(
    live_client: TestClient,
    live_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-007 / AC-009 / AC-012 / AC-015：10:00 温柔双通道；整点成对；重跑不重复；完成后停催。"""
    _use_scheduler_templates(monkeypatch)
    to_email, secret = _require_live_smtp()
    domain = _recipient_domain(to_email)
    db_path = live_env / "t010.db"
    token, conversation_id, candidate_id = _start(live_client, to_email)
    _seed_waiting(db_path, candidate_id)
    assert _counseling_active(db_path, candidate_id) == 0

    _tick(live_client, 13, 10)
    morning = _nudges(_messages(live_client, token, conversation_id))
    assert morning, "10:00 应出现对话催促"
    morning_text = morning[-1]["content"]
    _assert_no_secret(morning_text, secret)
    assert "慢慢" in morning_text or "不着急" in morning_text or "陪你" in morning_text
    assert "立刻做，别再拖" not in morning_text
    assert any(marker in morning_text for marker in ("1.", "题"))
    assert "投递进度 / deadline" not in morning_text
    assert "考查点" not in morning_text
    _assert_paired_logs(
        db_path, candidate_id, "2026-09-13T10", email_sent_ok=True, secret=secret
    )
    ten_email = _nudge_logs(db_path, candidate_id, "2026-09-13T10")["email"]
    assert int(ten_email["sent_ok"]) == 1
    assert ten_email["error_text"] is None
    print(
        f"AC-015 recipient_domain={domain} sent_ok=true "
        "inbox_visual=not_opened smtp_accepted=true"
    )

    _tick(live_client, 13, 10)
    after_retry = _nudges(_messages(live_client, token, conversation_id))
    assert len(after_retry) == 1
    assert _hour_log_count(db_path, candidate_id, "2026-09-13T10") == 2

    _tick(live_client, 13, 11)
    hourly = _nudges(_messages(live_client, token, conversation_id))
    assert len(hourly) == 2, "未答完时应在下一个整点再催"
    eleven_text = hourly[-1]["content"]
    _assert_no_secret(eleven_text, secret)
    assert "投递进度 / deadline" not in eleven_text
    assert "考查点" not in eleven_text
    assert any(marker in eleven_text for marker in ("1.", "题"))
    _assert_paired_logs(
        db_path, candidate_id, "2026-09-13T11", email_sent_ok=True, secret=secret
    )
    print(
        f"AC-012 recipient_domain={domain} hour=11 sent_ok=true "
        "inbox_visual=not_opened"
    )

    _complete_today_questions(db_path)
    before_complete = {item["id"] for item in hourly}
    _tick(live_client, 13, 12)
    after_complete = _nudges(_messages(live_client, token, conversation_id))
    new_after_done = [item for item in after_complete if item["id"] not in before_complete]
    assert new_after_done == [], "待办完成后下一个整点不应再发该日未完成催促"
    assert _hour_log_count(db_path, candidate_id, "2026-09-13T12") == 0
    assert _counseling_active(db_path, candidate_id) == 0


def test_smtp_disabled_copy_still_writes_chat_nudge(
    fail_client: TestClient,
    fail_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-016：配置副本关闭必要 SMTP 字段后，对话催促仍在并有中文失败说明。"""
    _use_scheduler_templates(monkeypatch)
    assert not _smtp_ready(get_settings())
    db_path = fail_env / "t010-fail.db"
    token, conversation_id, candidate_id = _start(fail_client, FAIL_CANDIDATE_EMAIL)
    _seed_waiting(db_path, candidate_id)
    assert _counseling_active(db_path, candidate_id) == 0

    _tick(fail_client, 13, 10)
    nudges = _nudges(_messages(fail_client, token, conversation_id))
    assert nudges, "邮件失败时对话催促仍应按规则出现"
    text = nudges[-1]["content"]
    assert "邮件" in text
    assert "发不出去" in text or "尚未配置" in text
    assert "smtp_host" in text or "尚未配置" in text
    assert "投递进度 / deadline" not in text
    _assert_paired_logs(
        db_path, candidate_id, "2026-09-13T10", email_sent_ok=False, secret="unused-secret"
    )
    email_row = _nudge_logs(db_path, candidate_id, "2026-09-13T10")["email"]
    assert int(email_row["sent_ok"]) == 0
    error_text = email_row["error_text"] or ""
    assert "尚未配置" in error_text or "发不出去" in error_text
