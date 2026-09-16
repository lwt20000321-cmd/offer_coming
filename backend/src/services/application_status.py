"""投递状态文案与归一。表里展示中文标签；出题/已挂逻辑用 normalized_status。"""

from __future__ import annotations

import re
from typing import Literal

NormalizedStatus = Literal["waiting_interview", "rejected", "other"]

APPLICATION_STATUS_LABELS: tuple[str, ...] = (
    "简历筛选中",
    "简历挂",
    "待测评",
    "已测评",
    "一面",
    "二面",
    "三面",
    "等待面试",
    "已挂",
)

STATUS_PROGRESS_ASK = (
    "现在这条是什么进度？请选：简历筛选中、简历挂、待测评、已测评、一面、二面、三面、等待面试、已挂。"
)

# 先匹配更具体的说法，避免「一面」吃掉「三面」，或「挂了」吃掉「简历挂」。
_CANONICAL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("简历筛选中", ("简历筛选中", "筛选中", "初筛中", "简历初筛")),
    ("简历挂", ("简历挂",)),
    ("待测评", ("待测评", "等测评")),
    ("已测评", ("已测评", "测完了", "测完")),
    ("三面", ("三面",)),
    ("二面", ("二面",)),
    ("一面", ("一面",)),
    ("等待面试", ("等待面试", "等面试", "约面")),
    ("已挂", ("已挂", "挂了", "被拒", "淘汰", "offer 黄了")),
)

_REJECTED_LABELS = frozenset({"已挂", "简历挂"})
_WAITING_LABELS = frozenset({"等待面试", "一面", "二面", "三面"})

_APPLY_RES = (
    re.compile(r"我投了"),
    re.compile(r"我投递了"),
    re.compile(r"已经投了"),
    re.compile(r"刚投了"),
    re.compile(r"投了(这个|这家|该|一份|一个)"),
    re.compile(r"投递了(这个|这家|该|一份|一个)"),
    re.compile(r"申请了(这个|这家|该)"),
    re.compile(r"请补录"),
    re.compile(r"补录"),
    re.compile(r"加入到?(?:我的)*投递"),
    re.compile(r"加到(?:我的)*投递"),
    re.compile(r"写进(?:我的)*投递"),
    re.compile(r"补录到?(?:我的)*投递"),
    re.compile(r"(填|写)进去"),
)

_URL_RE = re.compile(r"https?://\S+", re.I)
_ROLE_COMPANY_PAREN = re.compile(
    r"(?P<role>[\u4e00-\u9fffA-Za-z0-9·\-／/]{2,40})[（(](?P<company>[^）)]{2,40})[）)]"
)
_COMPANY_PIPE = re.compile(
    r"(?m)^[\s]*([^\n|｜]{2,40})\s*[|｜]"
)
_ROLE_AFTER_BRACKET = re.compile(r"【[^】]{1,40}】\s*([^\n]{2,40})")
_COMPANY_OF_JOB = re.compile(r"把?([\u4e00-\u9fffA-Za-z0-9（）()]{2,20})的岗")
_NOT_COMPANY = frozenset(
    {
        "售后服务",
        "校招",
        "应届生",
        "实习",
        "社招",
        "全职",
        "兼职",
        "热招",
        "社招/校招",
    }
)
_HOST_COMPANY = (
    ("bambulab", "Bambu Lab（创想三维）"),
    ("hikvision", "海康威视"),
    ("intsig", "合合信息（IntSig）"),
    ("wondershare", "万兴科技（Wondershare）"),
)
_SKIP_HOST_LABELS = frozenset({"www", "jobs", "campus", "careers", "hr", "zhaopin", "example"})

_MISSING_ROW_RES = (
    re.compile(r"没(有)?看到"),
    re.compile(r"看不到"),
    re.compile(r"尚未写入"),
    re.compile(r"补录进去"),
    re.compile(r"(填|写)进去"),
    re.compile(r"进(投递)?表"),
)


def canonical_status_from_text(text: str) -> str | None:
    blob = text or ""
    for label, aliases in _CANONICAL_RULES:
        if any(alias in blob for alias in aliases):
            return label
    return None


def normalize_application_status(status_text: str) -> NormalizedStatus:
    label = canonical_status_from_text(status_text) or (status_text or "")
    if label in _REJECTED_LABELS or any(
        marker in (status_text or "") for marker in ("已挂", "挂了", "被拒", "淘汰", "offer 黄了", "简历挂")
    ):
        return "rejected"
    if label in _WAITING_LABELS:
        return "waiting_interview"
    return "other"


def looks_like_apply_intent(text: str) -> bool:
    blob = text or ""
    return any(pattern.search(blob) for pattern in _APPLY_RES)


def looks_like_missing_row_complaint(text: str) -> bool:
    blob = text or ""
    return any(pattern.search(blob) for pattern in _MISSING_ROW_RES)


def looks_like_jd_body(text: str) -> bool:
    blob = _URL_RE.sub(" ", text or "")
    return len(re.sub(r"\s+", "", blob)) >= 40


def company_hint_from_url(url: str) -> str:
    host = ""
    match = re.search(r"https?://([^/]+)", url or "", re.I)
    if match:
        host = match.group(1).lower()
    for key, name in _HOST_COMPANY:
        if key in host:
            return name
    label = host.split(".")[0]
    if label and label not in _SKIP_HOST_LABELS:
        return label
    return ""


def parse_application_fields(text: str) -> dict[str, str]:
    blob = text or ""
    company = ""
    role = ""
    match = _ROLE_COMPANY_PAREN.search(blob)
    if match and "投递时间" not in match.group("role"):
        role = match.group("role").strip()
        company = match.group("company").strip()
        if company in _NOT_COMPANY:
            role = f"{role}（{company}）" if role else company
            company = ""
    if not company:
        pipe = _COMPANY_PIPE.search(blob)
        if pipe:
            company = pipe.group(1).strip()
    if not role:
        headed = _ROLE_AFTER_BRACKET.search(blob)
        if headed:
            role = headed.group(1).strip()
    if not company:
        named = _COMPANY_OF_JOB.search(blob)
        if named:
            company = named.group(1).strip()
    return {"company_name": company, "role_title": role}
