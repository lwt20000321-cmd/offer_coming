"""时间、令牌哈希与求职者 Key 加解密。"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.config.settings import get_settings

_HKDF_SALT = b"offer_coming.llm_api_key.v1"
_HKDF_INFO = b"candidate-llm-api-key"


def _fernet_from_secret(secret_key: str) -> Fernet:
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    ).derive(secret_key.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_llm_api_key(plaintext: str, secret_key: str) -> str:
    return _fernet_from_secret(secret_key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_llm_api_key(ciphertext: str, secret_key: str) -> str:
    return _fernet_from_secret(secret_key).decrypt(ciphertext.encode("ascii")).decode("utf-8")


def utc_now() -> datetime:
    return datetime.now(UTC)


def beijing_today() -> str:
    settings = get_settings()
    return datetime.now(ZoneInfo(settings.timezone)).date().isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str, secret_key: str) -> str:
    payload = f"{secret_key}:{token}".encode()
    return hashlib.sha256(payload).hexdigest()


def session_expiry(ttl_days: int, now: datetime | None = None) -> datetime:
    current = now or utc_now()
    return current + timedelta(days=ttl_days)
