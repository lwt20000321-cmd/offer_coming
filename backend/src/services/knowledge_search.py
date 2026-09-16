"""EXT-004：百炼 Chat Completions + enable_search。不对小红书主机发 GET。"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from pycore.core.logger import get_logger

from src.config.settings import AppSettings, get_settings
from src.services.llm_client import (
    LlmClient,
    current_candidate_api_key,
    current_candidate_id,
    should_skip_llm_http,
)

logger = get_logger()

BLOCKED_FETCH_HOSTS = ("xiaohongshu.com", "xhslink.com")
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_XHS_ONLY_RE = re.compile(
    r"^(在)?小红书上(有|能搜到|可以搜到)(相关)?(帖|帖子|面经|笔记).{0,8}$"
)


def host_is_blocked(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(host == item or host.endswith("." + item) for item in BLOCKED_FETCH_HOSTS)


def body_is_unusable(body: str) -> bool:
    text = (body or "").strip()
    if not text:
        return True
    compact = re.sub(r"\s+", "", text)
    if _XHS_ONLY_RE.match(compact):
        return True
    if compact in {"小红书上有帖", "小红书上有相关帖", "小红书上有帖子"}:
        return True
    return False


def parse_search_payload(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {"items": [], "failed": True, "fail_reason": "没有检索正文"}
    match = _JSON_FENCE_RE.search(text)
    candidate = match.group(1) if match else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {"items": [], "failed": True, "fail_reason": "检索结果无法核对"}
    try:
        data = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return {"items": [], "failed": True, "fail_reason": "检索结果无法核对"}
    if not isinstance(data, dict):
        return {"items": [], "failed": True, "fail_reason": "检索结果无法核对"}
    return data


def usable_items(payload: dict[str, Any], limit: int) -> list[dict[str, str]]:
    if payload.get("failed") is True:
        return []
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return []
    kept: list[dict[str, str]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        body = str(item.get("body") or "").strip()
        if body_is_unusable(body):
            continue
        title = str(item.get("title") or "").strip() or body[:40]
        hint = str(item.get("source_hint") or "").strip()
        kept.append({"title": title, "body": body, "source_hint": hint})
        if len(kept) >= limit:
            break
    return kept


class KnowledgeSearchService:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.llm = LlmClient(self.settings)

    async def search_public_experiences(
        self,
        *,
        company_name: str,
        role_title: str,
        prompt: str,
        api_key: str | None = None,
        candidate_id: str | None = None,
    ) -> dict[str, Any]:
        company = (company_name or "").strip()
        role = (role_title or "").strip()
        if not company or not role:
            logger.info("岗位公司或名称不足，跳过公开检索")
            return {
                "ok": False,
                "items": [],
                "fail_reason": "还认不出公司和岗位，没法查找面经。",
            }
        api_key = (api_key or current_candidate_api_key() or "").strip()
        candidate_id = candidate_id or current_candidate_id()
        if not api_key:
            logger.warning("无求职者 Key，知识库检索按失败处理")
            return {
                "ok": False,
                "items": [],
                "fail_reason": "这次没找到可核对的面经，你可以在对话里发文件或粘贴。",
                "mocked": True,
            }
        if should_skip_llm_http():
            logger.warning("llm_allow_mock 跳过公开检索 HTTP，按失败处理")
            return {
                "ok": False,
                "items": [],
                "fail_reason": "这次没找到可核对的面经，你可以在对话里发文件或粘贴。",
                "mocked": True,
            }
        user = (
            f"{prompt.strip()}\n\n"
            f"请查找「{company} {role}」已公开的面试经验摘要，不要编造。"
            "不要输出小红书帖文伪造成功入库。"
        )
        response = await self.llm.chat_completions(
            api_key=api_key,
            candidate_id=candidate_id,
            model=self.settings.llm_knowledge_model,
            messages=[{"role": "user", "content": user}],
            temperature=self.settings.llm_knowledge_temperature,
            enable_search=True,
            search_options={"forced_search": True},
            enable_thinking=False,
        )
        message = (response.get("choices") or [{}])[0].get("message") or {}
        content = str(message.get("content") or "")
        payload = parse_search_payload(content)
        items = usable_items(payload, self.settings.knowledge_search_max_items)
        if not items:
            reason = str(payload.get("fail_reason") or "").strip() or (
                "这次没找到可核对的面经，你可以在对话里发文件或粘贴。"
            )
            logger.info("公开面经检索没有可核对正文", company=company, role=role)
            return {"ok": False, "items": [], "fail_reason": reason, "mocked": False}
        logger.info("公开面经检索抽出可核对正文", company=company, role=role, count=len(items))
        return {"ok": True, "items": items, "fail_reason": None, "mocked": False}
