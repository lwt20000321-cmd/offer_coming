"""EXT-003：按 D-003-A 用用户 SMTP 发送催促邮件。失败只返回中文原因，不抛到调度。"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from socket import gaierror

from pycore.core.logger import get_logger

from src.config.settings import AppSettings, get_settings
from src.db.models import NudgeLog
from src.utils.crypto import new_id

logger = get_logger()

LLM_KEY_INVALID_EMAIL_SUBJECT = "请更新小凹的百炼 Key"
LLM_KEY_INVALID_EMAIL_BODY = (
    "你的百炼 Key 已失效，今日催促和出题发不出来，请到小凹「我的key」更新。"
)
LLM_KEY_INVALID_DEDUP_PREFIX = "llm_key_invalid:"

_AUTH_FAIL = "邮件没发出去：认证失败。"
_AUTH_NO_PERMISSION = "邮件没发出去：认证失败。发件邮箱未开通 SMTP 或授权码无效。"
_CONNECT_FAIL = "邮件没发出去：连接失败。"
_REJECT_FAIL = "邮件没发出去：收件被拒绝。"
_GENERIC_FAIL = "邮件没发出去：发送过程出错。"
_SMTP_TIMEOUT_SECONDS = 20
_NETEASE_HOST_SUFFIXES = ("163.com", "126.com", "yeah.net")

_CONNECT_ERRORS = (
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
    ConnectionError,
    TimeoutError,
    gaierror,
    OSError,
    ssl.SSLError,
)


def _missing_smtp_fields(settings: AppSettings) -> list[str]:
    missing: list[str] = []
    if not (settings.smtp_host or "").strip():
        missing.append("smtp_host")
    if not settings.smtp_port:
        missing.append("smtp_port")
    if not (settings.smtp_user or "").strip():
        missing.append("smtp_user")
    if not (settings.smtp_password or ""):
        missing.append("smtp_password")
    if not (settings.smtp_from or "").strip():
        missing.append("smtp_from")
    return missing


def make_email_nudge_log(
    *,
    candidate_id: str,
    beijing_hour_key: str,
    tone_level: int,
    sent_ok: bool,
    error_text: str,
) -> NudgeLog:
    """构造 channel=email 的催促日志，sent_ok 与本次 SMTP 结果一致。"""
    return NudgeLog(
        id=new_id("n"),
        candidate_id=candidate_id,
        beijing_hour_key=beijing_hour_key,
        channel="email",
        tone_level=tone_level,
        sent_ok=sent_ok,
        error_text=error_text or None,
    )


def _is_netease_host(host: str) -> bool:
    lowered = host.lower()
    return any(lowered == suffix or lowered.endswith("." + suffix) for suffix in _NETEASE_HOST_SUFFIXES)


def _smtp_credentials(settings: AppSettings) -> tuple[str, str, str]:
    host = settings.smtp_host.strip()
    user = settings.smtp_user.strip()
    password = (settings.smtp_password or "").strip()
    if _is_netease_host(host):
        user = user.lower()
    return host, user, password


def _deliver(settings: AppSettings, message: EmailMessage) -> None:
    host, user, password = _smtp_credentials(settings)
    port = settings.smtp_port
    if port is None:
        raise OSError("smtp_port missing")
    use_tls = bool(settings.smtp_use_tls)
    context = ssl.create_default_context()
    if use_tls:
        client = smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT_SECONDS)
    else:
        client = smtplib.SMTP_SSL(
            host, port, timeout=_SMTP_TIMEOUT_SECONDS, context=context
        )
    try:
        client.ehlo()
        if use_tls:
            client.starttls(context=context)
            client.ehlo()
        if _is_netease_host(host):
            # 163/126 会广告 AUTH PLAIN，但 PLAIN 常直接 535；Python login 默认优先 PLAIN。
            client.esmtp_features["auth"] = "LOGIN"
        client.login(user, password)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except Exception:
            logger.warning("SMTP QUIT 失败，尝试关闭", host=host)
            try:
                client.close()
            except Exception:
                logger.warning("SMTP 连接未能正常关闭", host=host)


def llm_key_invalid_dedup_key(beijing_date: str) -> str:
    return f"{LLM_KEY_INVALID_DEDUP_PREFIX}{beijing_date}"


def send_llm_key_invalid_email(*, to_email: str) -> tuple[bool, str]:
    """D-009-B 固定模板。未配置 SMTP 时不假装已发送。"""
    return send_nudge_email(
        to_email=to_email,
        subject=LLM_KEY_INVALID_EMAIL_SUBJECT,
        body=LLM_KEY_INVALID_EMAIL_BODY,
    )


def send_nudge_email(*, to_email: str, subject: str, body: str) -> tuple[bool, str]:
    """返回 (sent_ok, error_text)。配置缺失或投递失败不抛异常、不假装成功。"""
    try:
        settings = get_settings()
        missing = _missing_smtp_fields(settings)
        if missing:
            reason = f"邮件发不出去：尚未配置 {'、'.join(missing)}。"
            logger.info("SMTP 未配置，跳过发信", detail=reason)
            return False, reason

        from_addr = settings.smtp_from.strip()
        host = settings.smtp_host.strip()
        if _is_netease_host(host):
            from_addr = from_addr.lower()
        message = EmailMessage()
        message["From"] = from_addr
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)

        _deliver(settings, message)
        logger.info("SMTP 催促邮件已接受投递", smtp_host=host)
        return True, ""
    except smtplib.SMTPAuthenticationError as exc:
        smtp_code = getattr(exc, "smtp_code", None)
        raw_error = getattr(exc, "smtp_error", b"")
        if isinstance(raw_error, bytes):
            error_text = raw_error.decode("utf-8", "replace")
        else:
            error_text = str(raw_error or "")
        logger.warning(
            "SMTP 认证失败",
            smtp_host=_safe_host(),
            smtp_code=smtp_code,
        )
        if smtp_code == 550 or "no permission" in error_text.lower():
            return False, _AUTH_NO_PERMISSION
        return False, _AUTH_FAIL
    except smtplib.SMTPRecipientsRefused:
        logger.warning("SMTP 收件被拒绝", smtp_host=_safe_host())
        return False, _REJECT_FAIL
    except smtplib.SMTPSenderRefused:
        logger.warning("SMTP 发件被拒绝", smtp_host=_safe_host())
        return False, _REJECT_FAIL
    except smtplib.SMTPDataError:
        logger.warning("SMTP 拒收数据", smtp_host=_safe_host())
        return False, _REJECT_FAIL
    except _CONNECT_ERRORS:
        logger.warning("SMTP 连接失败", smtp_host=_safe_host())
        return False, _CONNECT_FAIL
    except smtplib.SMTPException:
        logger.warning("SMTP 投递失败", smtp_host=_safe_host())
        return False, _GENERIC_FAIL
    except Exception:
        logger.warning("SMTP 发送过程出错", smtp_host=_safe_host())
        return False, _GENERIC_FAIL


def _safe_host() -> str:
    try:
        return (get_settings().smtp_host or "").strip()
    except Exception:
        return ""
