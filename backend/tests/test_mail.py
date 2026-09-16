from __future__ import annotations

import smtplib
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path

import pytest
from pycore.core.config import ConfigManager
from sqlalchemy import select
from src.config.settings import load_settings_from_dict
from src.db.models import Candidate, NudgeLog
from src.db.session import close_db, configure_engine_from_settings, get_db_context, init_db
from src.services import mail as mail_mod
from src.services.mail import (
    LLM_KEY_INVALID_EMAIL_BODY,
    LLM_KEY_INVALID_EMAIL_SUBJECT,
    make_email_nudge_log,
    send_llm_key_invalid_email,
    send_nudge_email,
)
from src.utils.crypto import new_id

TEST_SECRET = "test-secret-key-not-for-production"
_CORS = [
    "http://localhost:5199",
    "http://127.0.0.1:5199",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
]
PLACEHOLDER_PASSWORD = "smtp-test-placeholder"


def _base_settings(tmp_path: Path, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "debug": True,
        "secret_key": TEST_SECRET,
        "host": "127.0.0.1",
        "port": 8099,
        "cors_origins": _CORS,
        "database_path": str(tmp_path / "offer_coming.db"),
        "upload_dir": str(tmp_path / "uploads"),
        "resume_max_bytes": 2048,
        "session_ttl_days": 30,
        "timezone": "Asia/Shanghai",
        "llm_api_key": "",
        "smtp_host": "",
        "smtp_from": "",
    }
    data.update(overrides)
    return data


def _load(tmp_path: Path, **overrides: object) -> None:
    ConfigManager.reset()
    load_settings_from_dict(_base_settings(tmp_path, **overrides))
    configure_engine_from_settings()


def _configured(tmp_path: Path, **overrides: object) -> None:
    values: dict[str, object] = {
        "smtp_host": "smtp.test.example",
        "smtp_port": 587,
        "smtp_user": "smtp-user",
        "smtp_password": PLACEHOLDER_PASSWORD,
        "smtp_from": "xiaoao@example.com",
        "smtp_use_tls": True,
    }
    values.update(overrides)
    _load(tmp_path, **values)


class FakeSMTP:
    last: FakeSMTP | None = None
    fail_connect = False
    fail_auth = False
    raise_unexpected = False

    def __init__(self, host: str, port: int, *args: object, **kwargs: object) -> None:
        if type(self).fail_connect:
            raise ConnectionRefusedError("connection refused")
        self.host = host
        self.port = port
        self.started_tls = False
        self.login_user: str | None = None
        self.sent: list[EmailMessage] = []
        FakeSMTP.last = self

    def ehlo(self, name: str = "") -> tuple[int, bytes]:
        return (250, b"ok")

    def starttls(self, *args: object, **kwargs: object) -> tuple[int, bytes]:
        self.started_tls = True
        return (220, b"ready")

    def login(self, user: str, password: str) -> tuple[int, bytes]:
        if type(self).fail_auth:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Error")
        self.login_user = user
        assert password == PLACEHOLDER_PASSWORD
        return (235, b"ok")

    def send_message(self, msg: EmailMessage, *args: object, **kwargs: object) -> dict[str, tuple[int, bytes]]:
        if type(self).raise_unexpected:
            raise RuntimeError("transport exploded")
        self.sent.append(msg)
        return {}

    def quit(self) -> tuple[int, bytes]:
        return (221, b"bye")

    def close(self) -> None:
        return None


class FakeSMTPSSL(FakeSMTP):
    pass


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeSMTP]]:
    FakeSMTP.last = None
    FakeSMTP.fail_connect = False
    FakeSMTP.fail_auth = False
    FakeSMTP.raise_unexpected = False
    monkeypatch.setattr(mail_mod.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail_mod.smtplib, "SMTP_SSL", FakeSMTPSSL)
    yield FakeSMTP
    FakeSMTP.last = None
    FakeSMTP.fail_connect = False
    FakeSMTP.fail_auth = False
    FakeSMTP.raise_unexpected = False


def test_unconfigured_returns_reason_without_raising(tmp_path: Path, fake_smtp: type[FakeSMTP]) -> None:
    _load(tmp_path)
    sent_ok, reason = send_nudge_email(
        to_email="seeker@example.com",
        subject="催促",
        body="待办",
    )
    assert sent_ok is False
    assert "尚未配置" in reason
    assert "smtp_host" in reason
    assert "smtp_from" in reason
    assert FakeSMTP.last is None


def test_send_matches_nudge_todo_and_tone(tmp_path: Path, fake_smtp: type[FakeSMTP]) -> None:
    _configured(tmp_path)
    tone_level = 3
    todo = "今日面试题未完成"
    subject = f"小凹催促 · 语气档{tone_level}"
    body = f"待办：{todo}\n语气档：{tone_level}"
    sent_ok, reason = send_nudge_email(
        to_email="seeker@example.com",
        subject=subject,
        body=body,
    )
    assert sent_ok is True
    assert reason == ""
    client = FakeSMTP.last
    assert client is not None
    assert client.started_tls is True
    assert client.host == "smtp.test.example"
    assert client.port == 587
    assert len(client.sent) == 1
    message = client.sent[0]
    assert message["From"] == "xiaoao@example.com"
    assert message["To"] == "seeker@example.com"
    assert message["Subject"] == subject
    content = message.get_content()
    assert todo in content
    assert f"语气档：{tone_level}" in content


def test_ssl_transport_when_tls_disabled(tmp_path: Path, fake_smtp: type[FakeSMTP]) -> None:
    _configured(tmp_path, smtp_use_tls=False, smtp_port=465)
    sent_ok, reason = send_nudge_email(
        to_email="seeker@example.com",
        subject="小凹催促 · 语气档1",
        body="待办：更新投递进度\n语气档：1",
    )
    assert sent_ok is True
    assert reason == ""
    client = FakeSMTP.last
    assert client is not None
    assert isinstance(client, FakeSMTPSSL)
    assert client.started_tls is False
    assert client.port == 465


def test_auth_failure_writes_auth_clause(tmp_path: Path, fake_smtp: type[FakeSMTP]) -> None:
    _configured(tmp_path)
    FakeSMTP.fail_auth = True
    sent_ok, reason = send_nudge_email(
        to_email="seeker@example.com",
        subject="催促",
        body="待办",
    )
    assert sent_ok is False
    assert "认证失败" in reason
    assert "连接失败" not in reason
    assert PLACEHOLDER_PASSWORD not in reason


def test_connect_failure_writes_connect_clause(tmp_path: Path, fake_smtp: type[FakeSMTP]) -> None:
    _configured(tmp_path)
    FakeSMTP.fail_connect = True
    sent_ok, reason = send_nudge_email(
        to_email="seeker@example.com",
        subject="催促",
        body="待办",
    )
    assert sent_ok is False
    assert "连接失败" in reason
    assert "认证失败" not in reason
    assert FakeSMTP.last is None


def test_transport_exception_does_not_raise(tmp_path: Path, fake_smtp: type[FakeSMTP]) -> None:
    _configured(tmp_path)
    FakeSMTP.raise_unexpected = True
    sent_ok, reason = send_nudge_email(
        to_email="seeker@example.com",
        subject="催促",
        body="待办",
    )
    assert sent_ok is False
    assert reason
    assert PLACEHOLDER_PASSWORD not in reason


@pytest.mark.asyncio
async def test_nudge_log_email_channel_records_sent_ok(
    tmp_path: Path, fake_smtp: type[FakeSMTP]
) -> None:
    _configured(tmp_path)
    tone_level = 2
    sent_ok, error_text = send_nudge_email(
        to_email="seeker@example.com",
        subject=f"小凹催促 · 语气档{tone_level}",
        body=f"待办：跟进 deadline\n语气档：{tone_level}",
    )
    assert sent_ok is True
    await init_db()
    try:
        async with get_db_context() as db:
            candidate = Candidate(
                id=new_id("c"),
                email="seeker@example.com",
                resume_filename="resume.txt",
                resume_path=str(tmp_path / "resume.txt"),
                resume_text="",
                resume_parse_ok=False,
            )
            db.add(candidate)
            db.add(
                make_email_nudge_log(
                    candidate_id=candidate.id,
                    beijing_hour_key="2026-09-13T10",
                    tone_level=tone_level,
                    sent_ok=sent_ok,
                    error_text=error_text,
                )
            )
            await db.flush()
            row = (
                await db.execute(select(NudgeLog).where(NudgeLog.channel == "email"))
            ).scalar_one()
            assert row.sent_ok is True
            assert row.error_text is None
            assert row.tone_level == tone_level
            assert row.channel == "email"
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_nudge_log_email_channel_records_auth_failure(
    tmp_path: Path, fake_smtp: type[FakeSMTP]
) -> None:
    _configured(tmp_path)
    FakeSMTP.fail_auth = True
    sent_ok, error_text = send_nudge_email(
        to_email="seeker@example.com",
        subject="催促",
        body="待办",
    )
    await init_db()
    try:
        async with get_db_context() as db:
            candidate = Candidate(
                id=new_id("c"),
                email="seeker@example.com",
                resume_filename="resume.txt",
                resume_path=str(tmp_path / "resume.txt"),
                resume_text="",
                resume_parse_ok=False,
            )
            db.add(candidate)
            db.add(
                make_email_nudge_log(
                    candidate_id=candidate.id,
                    beijing_hour_key="2026-09-13T11",
                    tone_level=4,
                    sent_ok=sent_ok,
                    error_text=error_text,
                )
            )
            await db.flush()
            row = (
                await db.execute(select(NudgeLog).where(NudgeLog.channel == "email"))
            ).scalar_one()
            assert row.sent_ok is False
            assert row.error_text is not None
            assert "认证失败" in row.error_text
            assert PLACEHOLDER_PASSWORD not in row.error_text
    finally:
        await close_db()


def test_d009b_template_sends_fixed_copy(tmp_path: Path, fake_smtp: type[FakeSMTP]) -> None:
    _configured(tmp_path)
    sent_ok, reason = send_llm_key_invalid_email(to_email="seeker@example.com")
    assert sent_ok is True
    assert reason == ""
    client = FakeSMTP.last
    assert client is not None
    assert len(client.sent) == 1
    message = client.sent[0]
    assert message["Subject"] == LLM_KEY_INVALID_EMAIL_SUBJECT
    assert LLM_KEY_INVALID_EMAIL_BODY in message.get_content()
    assert "我的key" in message.get_content()


def test_d009b_unconfigured_does_not_pretend(tmp_path: Path, fake_smtp: type[FakeSMTP]) -> None:
    _load(tmp_path)
    sent_ok, reason = send_llm_key_invalid_email(to_email="seeker@example.com")
    assert sent_ok is False
    assert "尚未配置" in reason
    assert FakeSMTP.last is None
