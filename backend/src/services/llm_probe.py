"""保存路径的百炼 Key 探测（D-007-C）。

只用调用方传入的求职者 Key 作为 Authorization。禁止读取运营 llm_api_key。
"""

from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pycore.core.logger import get_logger

from src.config.settings import AppSettings, get_settings

logger = get_logger()

ProbeVerdict = Literal["ok", "invalid", "unreachable"]


@dataclass(frozen=True)
class LlmKeyProbeResult:
    verdict: ProbeVerdict
    http_status: int | None = None


def _error_blob(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    err = payload.get("error")
    if isinstance(err, dict):
        parts = [err.get("code"), err.get("type"), err.get("message")]
        return " ".join(str(part) for part in parts if part)
    return " ".join(
        str(payload.get(key) or "") for key in ("code", "type", "message")
    )


def _is_invalid_key(status_code: int, payload: Any) -> bool:
    if status_code == 401:
        return True
    return "invalid_api_key" in _error_blob(payload).lower()


def _read_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}


def _classify(status_code: int, payload: Any) -> LlmKeyProbeResult:
    if _is_invalid_key(status_code, payload):
        logger.info("百炼 Key 探测拒绝", http_status=status_code)
        return LlmKeyProbeResult(verdict="invalid", http_status=status_code)
    if 200 <= status_code < 300:
        logger.info("百炼 Key 探测成功", http_status=status_code)
        return LlmKeyProbeResult(verdict="ok", http_status=status_code)
    logger.info("百炼 Key 探测暂时不可达", http_status=status_code)
    return LlmKeyProbeResult(verdict="unreachable", http_status=status_code)


async def probe_llm_api_key(
    api_key: str,
    settings: AppSettings | None = None,
) -> LlmKeyProbeResult:
    current = settings or get_settings()
    base = current.llm_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(
        timeout=current.llm_connect_timeout_seconds,
        connect=current.llm_connect_timeout_seconds,
    )
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
            models_resp = await client.get(f"{base}/models", headers=headers)
            models_payload = _read_json(models_resp)
            if _is_invalid_key(models_resp.status_code, models_payload):
                logger.info("百炼 Key 探测拒绝", http_status=models_resp.status_code)
                return LlmKeyProbeResult(
                    verdict="invalid", http_status=models_resp.status_code
                )
            if models_resp.status_code == 404:
                chat_resp = await client.post(
                    f"{base}/chat/completions",
                    headers=headers,
                    json={
                        "model": current.llm_orchestrator_model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                    },
                )
                return _classify(chat_resp.status_code, _read_json(chat_resp))
            return _classify(models_resp.status_code, models_payload)
    except httpx.TimeoutException:
        logger.info("百炼 Key 探测超时")
        return LlmKeyProbeResult(verdict="unreachable", http_status=None)
    except httpx.HTTPError as exc:
        logger.info("百炼 Key 探测网络失败", detail=type(exc).__name__)
        return LlmKeyProbeResult(verdict="unreachable", http_status=None)
