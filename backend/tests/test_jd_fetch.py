from __future__ import annotations

import httpx
import pytest
from src.config.settings import get_settings
from src.services.jd_fetch import create_jd_http_client, extract_readable_text, fetch_jd


def _allow_public_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    import ipaddress

    def fake_host_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr("src.services.jd_fetch._host_ips", fake_host_ips)


@pytest.mark.asyncio
async def test_jd_http_client_disables_env_trust(tmp_env) -> None:
    client = create_jd_http_client(get_settings())
    assert client.trust_env is False
    await client.aclose()


def test_extract_readable_text_strips_html() -> None:
    html = "<html><head><style>p{}</style></head><body><h1>后端</h1><p>要求 Python</p></body></html>"
    text = extract_readable_text(html, "text/html")
    assert "Python" in text
    assert "<p>" not in text
    assert "p{}" not in text


@pytest.mark.asyncio
async def test_fetch_jd_issues_get_and_returns_text(tmp_env, monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_hosts(monkeypatch)
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(
            200,
            text="<html><body><p>招聘 Python 后端，需要项目经验</p></body></html>",
            headers={"content-type": "text/html"},
        )

    def factory(settings=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr("src.services.jd_fetch.create_jd_http_client", factory)
    result = await fetch_jd("https://jobs.example.com/backend")
    assert result["ok"] is True
    assert "Python" in result["text"]
    assert seen == [("GET", "https://jobs.example.com/backend")]


@pytest.mark.asyncio
async def test_fetch_jd_blocks_loopback(tmp_env, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"不应请求内网地址 {request.url}")

    def factory(settings=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("src.services.jd_fetch.create_jd_http_client", factory)
    result = await fetch_jd("http://127.0.0.1/secret")
    assert result["ok"] is False
    assert "内网" in result["error"]


@pytest.mark.asyncio
async def test_fetch_jd_blocks_private_network(tmp_env, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"不应请求内网地址 {request.url}")

    def factory(settings=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("src.services.jd_fetch.create_jd_http_client", factory)
    result = await fetch_jd("http://10.0.0.8/jd")
    assert result["ok"] is False
    assert "内网" in result["error"]


@pytest.mark.asyncio
async def test_fetch_jd_non_2xx_is_failure(tmp_env, monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_hosts(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    def factory(settings=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("src.services.jd_fetch.create_jd_http_client", factory)
    result = await fetch_jd("https://jobs.example.com/blocked")
    assert result["ok"] is False
    assert "读不到" in result["error"]


@pytest.mark.asyncio
async def test_fetch_jd_timeout(tmp_env, monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_public_hosts(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    def factory(settings=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("src.services.jd_fetch.create_jd_http_client", factory)
    result = await fetch_jd("https://jobs.example.com/slow")
    assert result["ok"] is False
    assert "超时" in result["error"]


@pytest.mark.asyncio
async def test_fetch_jd_blocks_redirect_to_loopback(
    tmp_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allow_public_hosts(monkeypatch)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    def factory(settings=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr("src.services.jd_fetch.create_jd_http_client", factory)
    result = await fetch_jd("https://jobs.example.com/redirect")
    assert result["ok"] is False
    assert "内网" in result["error"]
    assert requested == ["https://jobs.example.com/redirect"]
