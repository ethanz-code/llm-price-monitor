"""传输层退避重试：幂等请求遇抖动重试、额度用尽抛出、POST 不重试、留痕日志。"""
import httpx
import pytest

from llm_price_monitor import tasklog
from llm_price_monitor.http_retry import RETRY_ATTEMPTS, RetryingTransport, build_client


def _client(handler, **kwargs) -> httpx.Client:
    return build_client(transport=RetryingTransport(httpx.MockTransport(handler), **kwargs))


def _flaky_handler(fail_times: int, calls: list[int]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) <= fail_times:
            raise httpx.ConnectError("SSL: UNEXPECTED_EOF_WHILE_READING", request=request)
        return httpx.Response(200, json={"ok": True})
    return handler


def test_retry_recovers_from_transient_ssl_error(monkeypatch):
    calls: list[int] = []
    sleeps: list[float] = []
    monkeypatch.setattr("llm_price_monitor.http_retry.time.sleep", sleeps.append)
    with _client(_flaky_handler(1, calls)) as client:
        response = client.get("https://demo.test/api/status")
    assert response.status_code == 200
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_retry_exhausts_and_raises_with_backoff(monkeypatch):
    calls: list[int] = []
    sleeps: list[float] = []
    monkeypatch.setattr("llm_price_monitor.http_retry.time.sleep", sleeps.append)
    with _client(_flaky_handler(99, calls)) as client, pytest.raises(httpx.ConnectError):
        client.get("https://demo.test/api/status")
    assert len(calls) == 1 + RETRY_ATTEMPTS
    assert sleeps == [1.0, 2.0]


def test_post_is_not_retried(monkeypatch):
    calls: list[int] = []
    sleeps: list[float] = []
    monkeypatch.setattr("llm_price_monitor.http_retry.time.sleep", sleeps.append)
    with _client(_flaky_handler(99, calls)) as client, pytest.raises(httpx.ConnectError):
        client.post("https://demo.test/api/refresh")
    assert len(calls) == 1
    assert sleeps == []


def test_local_protocol_error_is_not_retried(monkeypatch):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.LocalProtocolError("bad request built locally", request=request)

    monkeypatch.setattr("llm_price_monitor.http_retry.time.sleep", lambda *_: None)
    with _client(handler) as client, pytest.raises(httpx.LocalProtocolError):
        client.get("https://demo.test/api/status")
    assert len(calls) == 1


def test_retry_emits_warn_log(monkeypatch):
    logs: list[tuple[str, str]] = []
    tasklog.bind(lambda message, level: logs.append((message, level)))
    try:
        calls: list[int] = []
        monkeypatch.setattr("llm_price_monitor.http_retry.time.sleep", lambda *_: None)
        with _client(_flaky_handler(1, calls)) as client:
            client.get("https://demo.test/api/status")
    finally:
        tasklog.unbind()
    assert logs == [("传输层抖动（SSL: UNEXPECTED_EOF_WHILE_READING），1s 后重试（1/2）", "warn")]


_PROXY_ENV_VARS = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy")


@pytest.fixture
def spy_http_transport(monkeypatch):
    """替身 HTTPTransport：记下收到的 proxy 参数，其余行为不变。"""

    class Spy(httpx.HTTPTransport):
        proxies_seen: list[str | None] = []

        def __init__(self, *, proxy: str | None = None, **kwargs) -> None:
            self.proxies_seen.append(proxy)
            super().__init__(**kwargs)

    monkeypatch.setattr("llm_price_monitor.http_retry.httpx.HTTPTransport", Spy)
    return Spy


def test_default_transport_reads_proxy_env(monkeypatch, spy_http_transport):
    """显式 transport 会绕过 httpx 的环境代理解析，build_client 必须自己读代理环境变量。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    with build_client():
        pass
    assert spy_http_transport.proxies_seen == ["http://127.0.0.1:7897"]


def test_default_transport_prefers_https_over_all_proxy(monkeypatch, spy_http_transport):
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    with build_client():
        pass
    assert spy_http_transport.proxies_seen == ["http://127.0.0.1:7897"]


def test_default_transport_without_proxy_env_stays_direct(monkeypatch, spy_http_transport):
    for key in _PROXY_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    with build_client():
        pass
    assert spy_http_transport.proxies_seen == [None]


def test_injected_transport_bypasses_proxy_env(monkeypatch, spy_http_transport):
    """注入 transport 是测试专用通道，不做任何包装与代理注入。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    with build_client(transport=httpx.MockTransport(handler)) as client:
        client.get("https://demo.test/api/status")
    assert seen == ["https://demo.test/api/status"]
    assert spy_http_transport.proxies_seen == []
