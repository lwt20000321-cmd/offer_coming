"""邮箱规范化。"""

import re

from src.utils.errors import ValidationFailed

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def normalize_email(raw: str | None) -> str:
    if raw is None:
        raise ValidationFailed("请先填写邮箱并上传简历，才能和小凹对话。")
    email = raw.strip().lower()
    if not email or not _EMAIL_RE.match(email):
        raise ValidationFailed("请先填写邮箱并上传简历，才能和小凹对话。")
    return email
