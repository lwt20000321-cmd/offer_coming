"""EXT-002：读取用户给出的岗位 JD。httpx trust_env=False，禁止跟到内网。"""

from __future__ import annotations

import ipaddress
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from pycore.core.logger import get_logger

from src.config.settings import AppSettings, get_settings

logger = get_logger()

# 工具观察截断：避免把整页 HTML 回传给模型（EXT-002）
_JD_TEXT_MAX_CHARS = 8000
_MAX_REDIRECTS = 3


def create_jd_http_client(settings: AppSettings | None = None) -> httpx.AsyncClient:
    current = settings or get_settings()
    timeout = httpx.Timeout(current.jd_fetch_timeout_seconds)
    return httpx.AsyncClient(
        trust_env=False,
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": current.jd_fetch_user_agent},
    )


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        if tag in {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        joined = " ".join(self._chunks)
        lines = [line.strip() for line in joined.splitlines()]
        return "\n".join(line for line in lines if line)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _host_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        as_ip = ipaddress.ip_address(host)
    except ValueError:
        as_ip = None
    if as_ip is not None:
        return [as_ip]
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError("这段岗位链接读不到：解析不到主机。") from exc
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for info in infos:
        raw = str(info[4][0])
        if raw in seen:
            continue
        seen.add(raw)
        ips.append(ipaddress.ip_address(raw))
    if not ips:
        raise ValueError("这段岗位链接读不到：解析不到主机。")
    return ips


def validate_public_http_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("只支持 http 或 https 岗位链接，请换链接或直接把 JD 正文发给我。")
    host = parsed.hostname
    if not host:
        raise ValueError("岗位链接不完整，请换一个可打开的链接。")
    if host.lower() in {"localhost", "metadata.google.internal"}:
        raise ValueError("不能读取内网或本机地址，请换一个公网岗位链接。")
    for ip in _host_ips(host):
        if _is_blocked_ip(ip):
            raise ValueError("不能读取内网或本机地址，请换一个公网岗位链接。")
    return parsed.geturl() if parsed.geturl() else url.strip()


def extract_readable_text(raw: str, content_type: str = "") -> str:
    lowered = content_type.lower()
    looks_html = "html" in lowered or "<html" in raw.lower() or "<body" in raw.lower()
    if looks_html:
        parser = _HTMLTextExtractor()
        try:
            parser.feed(raw)
            parser.close()
        except Exception:
            text = raw
        else:
            text = parser.text()
    else:
        text = raw.strip()
    collapsed = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(collapsed) > _JD_TEXT_MAX_CHARS:
        return collapsed[:_JD_TEXT_MAX_CHARS]
    return collapsed


def _fail(error: str) -> dict[str, Any]:
    return {"ok": False, "text": "", "error": error}


async def fetch_jd(url: str, settings: AppSettings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    try:
        target = validate_public_http_url(url)
    except ValueError as exc:
        logger.info("岗位链接未通过安全校验", host=urlparse(url).hostname)
        return _fail(str(exc))

    redirects = 0
    async with create_jd_http_client(current) as client:
        while True:
            parsed = urlparse(target)
            logger.info("正在请求岗位链接", host=parsed.hostname, scheme=parsed.scheme)
            try:
                response = await client.get(target)
            except httpx.TimeoutException:
                logger.info("读取岗位链接超时", host=parsed.hostname)
                return _fail("这段岗位链接读超时了。请换链接，或直接把 JD 正文发给我。")
            except httpx.HTTPError as exc:
                logger.info("读取岗位链接失败", host=parsed.hostname, detail=type(exc).__name__)
                return _fail("这段岗位链接读不到。请换链接，或直接把 JD 正文发给我。")

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    return _fail("这段岗位链接读不到：重定向没有目标地址。")
                redirects += 1
                if redirects > _MAX_REDIRECTS:
                    return _fail("这段岗位链接跳转次数太多，请换一个直接可打开的链接。")
                nxt = urljoin(target, location)
                try:
                    target = validate_public_http_url(nxt)
                except ValueError as exc:
                    logger.info("岗位链接重定向被拦截", host=urlparse(nxt).hostname)
                    return _fail(str(exc))
                continue

            if response.status_code < 200 or response.status_code >= 300:
                logger.info(
                    "岗位链接返回非成功状态",
                    host=parsed.hostname,
                    status_code=response.status_code,
                )
                return _fail(
                    "这段岗位链接读不到。"
                    "请换一个链接，或直接把 JD 正文发给我，我才能总结考查点。"
                )

            text = extract_readable_text(
                response.text,
                response.headers.get("content-type", ""),
            )
            if not text:
                return _fail("这段岗位链接没有抽到可读文本。请换链接或直接粘贴 JD 正文。")
            logger.info("已读取岗位链接文本", host=parsed.hostname, chars=len(text))
            return {"ok": True, "text": text, "error": None}
