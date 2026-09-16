"""EXT-001 百炼 Chat Completions 传输。使用 httpx 且 trust_env=False。禁止 dashscope SDK。

Bearer 只能是调用方传入的求职者 Key。禁止读取 settings.llm_api_key。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from pycore.core.logger import get_logger

from src.config.settings import AppSettings, get_settings
from src.db.models import Candidate
from src.utils.crypto import decrypt_llm_api_key

logger = get_logger()

_api_key_var: ContextVar[str | None] = ContextVar(
    "offer_coming_candidate_api_key", default=None
)
_candidate_id_var: ContextVar[str | None] = ContextVar(
    "offer_coming_candidate_id", default=None
)

LLM_KEY_MISSING_TEXT = "请到「我的key」粘贴百炼 Key，才能继续和小凹对话。"
LLM_KEY_INVALID_TEXT = "这把 Key 百炼不接受，请到「我的key」更换。"
LLM_QUOTA_TEXT = "你的百炼额度不够了，请充值或到「我的key」换一把。"
LLM_UNAVAILABLE_TEXT = "小凹这会儿连不上，请稍后再发这句"
SCHEDULER_KEY_NOTICE = (
    "你的百炼 Key 已失效，今日催促和出题发不出来，请到小凹「我的key」更新。"
)


class LlmClientError(Exception):
    error_code = "LLM_UNAVAILABLE"
    user_message = LLM_UNAVAILABLE_TEXT

    def __init__(self, user_message: str | None = None) -> None:
        self.user_message = user_message or type(self).user_message
        super().__init__(self.user_message)


class LlmKeyMissingError(LlmClientError):
    error_code = "LLM_KEY_MISSING"
    user_message = LLM_KEY_MISSING_TEXT


class LlmKeyInvalidError(LlmClientError):
    error_code = "LLM_KEY_INVALID"
    user_message = LLM_KEY_INVALID_TEXT


class LlmQuotaExceededError(LlmClientError):
    error_code = "LLM_QUOTA_EXCEEDED"
    user_message = LLM_QUOTA_TEXT


class LlmUnavailableError(LlmClientError):
    error_code = "LLM_UNAVAILABLE"
    user_message = LLM_UNAVAILABLE_TEXT


class LlmMockSkippedError(LlmClientError):
    error_code = "LLM_UNAVAILABLE"
    user_message = LLM_UNAVAILABLE_TEXT


def create_llm_http_client(settings: AppSettings | None = None) -> httpx.AsyncClient:
    current = settings or get_settings()
    timeout = httpx.Timeout(
        timeout=current.llm_timeout_seconds,
        connect=current.llm_connect_timeout_seconds,
    )
    return httpx.AsyncClient(trust_env=False, timeout=timeout)


@contextmanager
def candidate_llm_context(api_key: str, candidate_id: str) -> Iterator[None]:
    token_key = _api_key_var.set(api_key)
    token_id = _candidate_id_var.set(candidate_id)
    try:
        yield
    finally:
        _api_key_var.reset(token_key)
        _candidate_id_var.reset(token_id)


def current_candidate_api_key() -> str | None:
    return _api_key_var.get()


def current_candidate_id() -> str | None:
    return _candidate_id_var.get()


def resolved_candidate_api_key(candidate: Candidate) -> str:
    cipher = (candidate.llm_api_key_ciphertext or "").strip()
    if not cipher:
        raise LlmKeyMissingError()
    try:
        key = decrypt_llm_api_key(cipher, get_settings().secret_key).strip()
    except Exception:
        logger.info("求职者 Key 密文无法解密", candidate_id=candidate.id)
        raise LlmKeyMissingError() from None
    if not key:
        raise LlmKeyMissingError()
    return key


def candidate_llm_key_unusable(candidate: Candidate) -> bool:
    if (candidate.llm_key_status or "") == "invalid":
        return True
    return not (candidate.llm_api_key_ciphertext or "").strip()


def should_skip_llm_http() -> bool:
    return bool(get_settings().llm_allow_mock)


def _error_blob(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    err = payload.get("error")
    if isinstance(err, dict):
        parts = [err.get("code"), err.get("type"), err.get("message")]
        return " ".join(str(part) for part in parts if part)
    return " ".join(str(payload.get(key) or "") for key in ("code", "type", "message"))


def _read_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}


def _is_invalid_key(status_code: int, payload: Any) -> bool:
    if status_code == 401:
        return True
    return "invalid_api_key" in _error_blob(payload).lower()


def _is_quota(status_code: int, payload: Any) -> bool:
    if status_code != 429:
        return False
    blob = _error_blob(payload).lower()
    return any(token in blob for token in ("quota", "insufficient", "exceeded", "额度"))


@dataclass
class LlmStreamEvent:
    kind: Literal["content", "tool_delta", "complete"]
    text: str = ""
    response: dict[str, Any] | None = None


@dataclass
class _StreamAssembler:
    content: str = ""
    role: str = "assistant"
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def ingest(self, payload: dict[str, Any]) -> LlmStreamEvent | None:
        if payload.get("error"):
            return None
        choices = payload.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return None
        choice = choices[0]
        if choice.get("finish_reason"):
            self.finish_reason = str(choice["finish_reason"])
        delta = choice.get("delta") or {}
        if not isinstance(delta, dict):
            delta = {}
        if delta.get("role"):
            self.role = str(delta["role"])
        tool_deltas = delta.get("tool_calls") or []
        content_piece = str(delta.get("content") or "")
        # 模型内部思考不写进用户气泡，继续用「小凹正在思考...」
        if tool_deltas:
            for item in tool_deltas:
                if isinstance(item, dict):
                    self._merge_tool_call(item)
            return LlmStreamEvent(kind="tool_delta")
        if content_piece:
            self.content += content_piece
            return LlmStreamEvent(kind="content", text=content_piece)
        return None

    def _merge_tool_call(self, item: dict[str, Any]) -> None:
        try:
            index = int(item.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        while len(self.tool_calls) <= index:
            self.tool_calls.append(
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                }
            )
        target = self.tool_calls[index]
        if item.get("id"):
            target["id"] = str(item["id"])
        if item.get("type"):
            target["type"] = str(item["type"])
        function = item.get("function") or {}
        if not isinstance(function, dict):
            return
        current = target["function"]
        if function.get("name"):
            current["name"] = str(current.get("name") or "") + str(function["name"])
        if function.get("arguments"):
            current["arguments"] = str(current.get("arguments") or "") + str(
                function["arguments"]
            )

    def as_response(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = self.tool_calls
            if not self.content:
                message["content"] = None
        return {
            "choices": [
                {
                    "message": message,
                    "finish_reason": self.finish_reason
                    or ("tool_calls" if self.tool_calls else "stop"),
                }
            ]
        }


def _build_chat_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    stream: bool,
    thinking: bool,
    tools: list[dict[str, Any]] | None,
    temperature: float | None,
    max_tokens: int | None,
    enable_search: bool,
    search_options: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "enable_thinking": thinking,
    }
    if tools:
        payload["tools"] = tools
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if enable_search:
        payload["enable_search"] = True
        payload["search_options"] = search_options or {"forced_search": True}
    return payload


def _raise_for_llm_http(status: int, body: Any) -> None:
    if _is_invalid_key(status, body if isinstance(body, dict) else {}):
        raise LlmKeyInvalidError()
    if _is_quota(status, body if isinstance(body, dict) else {}):
        raise LlmQuotaExceededError()
    if status >= 400:
        raise LlmUnavailableError()


async def _iter_sse_payloads(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip("\r")
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                yield parsed


class LlmClient:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()

    async def chat_completions(
        self,
        *,
        api_key: str | None = None,
        candidate_id: str | None = None,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        enable_search: bool = False,
        search_options: dict[str, Any] | None = None,
        enable_thinking: bool | None = None,
    ) -> dict[str, Any]:
        key = (api_key or "").strip()
        cid = candidate_id or current_candidate_id()
        if not key:
            logger.warning("未提供求职者百炼 Key，拒绝调用", candidate_id=cid)
            raise LlmKeyMissingError()
        chosen = (model or "").strip()
        if not chosen:
            logger.warning("调用未指定 model，拒绝退回废弃的 llm_model")
            raise RuntimeError("未指定模型名称")
        if self.settings.llm_allow_mock:
            logger.info(
                "llm_allow_mock 跳过 HTTP",
                status_code=0,
                model=chosen,
                candidate_id=cid,
            )
            raise LlmMockSkippedError()
        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        thinking = (
            self.settings.llm_enable_thinking if enable_thinking is None else enable_thinking
        )
        payload: dict[str, Any] = {
            "model": chosen,
            "messages": messages,
            "stream": stream,
            "enable_thinking": thinking,
        }
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if enable_search:
            payload["enable_search"] = True
            payload["search_options"] = search_options or {"forced_search": True}
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        retries = 0
        max_retries = self.settings.llm_max_retries
        while True:
            try:
                async with create_llm_http_client(self.settings) as client:
                    response = await client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                logger.info(
                    "百炼 Chat Completions 超时",
                    status_code=None,
                    model=chosen,
                    candidate_id=cid,
                )
                raise LlmUnavailableError() from exc
            except httpx.HTTPError as exc:
                logger.info(
                    "百炼 Chat Completions 网络失败",
                    status_code=None,
                    model=chosen,
                    candidate_id=cid,
                    detail=type(exc).__name__,
                )
                raise LlmUnavailableError() from exc
            status = response.status_code
            body = _read_json(response)
            logger.info(
                "已请求百炼 Chat Completions",
                status_code=status,
                model=chosen,
                candidate_id=cid,
            )
            if _is_invalid_key(status, body):
                raise LlmKeyInvalidError()
            if _is_quota(status, body):
                raise LlmQuotaExceededError()
            if status in {429, 500, 502, 503, 504} and retries < max_retries:
                retries += 1
                logger.info(
                    "百炼暂时不可用，准备重试",
                    status_code=status,
                    model=chosen,
                    candidate_id=cid,
                    retries=retries,
                )
                await asyncio.sleep(2**retries)
                continue
            if status >= 400:
                logger.info(
                    "百炼 Chat Completions 失败",
                    status_code=status,
                    model=chosen,
                    candidate_id=cid,
                )
                raise LlmUnavailableError()
            if not isinstance(body, dict):
                raise LlmUnavailableError()
            return body

    async def iter_chat_completions(
        self,
        *,
        api_key: str | None = None,
        candidate_id: str | None = None,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        enable_search: bool = False,
        search_options: dict[str, Any] | None = None,
        enable_thinking: bool | None = None,
    ) -> AsyncIterator[LlmStreamEvent]:
        key = (api_key or "").strip()
        cid = candidate_id or current_candidate_id()
        if not key:
            logger.warning("未提供求职者百炼 Key，拒绝调用", candidate_id=cid)
            raise LlmKeyMissingError()
        chosen = (model or "").strip()
        if not chosen:
            logger.warning("调用未指定 model，拒绝退回废弃的 llm_model")
            raise RuntimeError("未指定模型名称")
        if self.settings.llm_allow_mock:
            logger.info(
                "llm_allow_mock 跳过 HTTP",
                status_code=0,
                model=chosen,
                candidate_id=cid,
            )
            raise LlmMockSkippedError()
        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        thinking = (
            self.settings.llm_enable_thinking if enable_thinking is None else enable_thinking
        )
        payload = _build_chat_payload(
            model=chosen,
            messages=messages,
            stream=True,
            thinking=thinking,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_search=enable_search,
            search_options=search_options,
        )
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        retries = 0
        max_retries = self.settings.llm_max_retries
        while True:
            assembler = _StreamAssembler()
            try:
                async with create_llm_http_client(self.settings) as client:
                    async with client.stream(
                        "POST", url, headers=headers, json=payload
                    ) as response:
                        status = response.status_code
                        if status >= 400:
                            raw = await response.aread()
                            try:
                                body = json.loads(raw.decode("utf-8") or "{}")
                            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                                body = {}
                            logger.info(
                                "已请求百炼 Chat Completions",
                                status_code=status,
                                model=chosen,
                                candidate_id=cid,
                            )
                            if status in {429, 500, 502, 503, 504} and retries < max_retries:
                                retries += 1
                                logger.info(
                                    "百炼暂时不可用，准备重试",
                                    status_code=status,
                                    model=chosen,
                                    candidate_id=cid,
                                    retries=retries,
                                )
                                await asyncio.sleep(2**retries)
                                continue
                            _raise_for_llm_http(status, body)
                            raise LlmUnavailableError()
                        logger.info(
                            "已请求百炼 Chat Completions",
                            status_code=status,
                            model=chosen,
                            candidate_id=cid,
                        )
                        async for parsed in _iter_sse_payloads(response):
                            if parsed.get("error"):
                                _raise_for_llm_http(status, parsed)
                                raise LlmUnavailableError()
                            event = assembler.ingest(parsed)
                            if event is not None:
                                yield event
            except LlmClientError:
                raise
            except httpx.TimeoutException as exc:
                logger.info(
                    "百炼 Chat Completions 超时",
                    status_code=None,
                    model=chosen,
                    candidate_id=cid,
                )
                raise LlmUnavailableError() from exc
            except httpx.HTTPError as exc:
                logger.info(
                    "百炼 Chat Completions 网络失败",
                    status_code=None,
                    model=chosen,
                    candidate_id=cid,
                    detail=type(exc).__name__,
                )
                raise LlmUnavailableError() from exc
            yield LlmStreamEvent(kind="complete", response=assembler.as_response())
            return

