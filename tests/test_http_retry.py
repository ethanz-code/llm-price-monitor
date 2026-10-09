"""传输层退避重试：幂等请求遇抖动重试、额度用尽抛出、POST 不重试、留痕日志。"""
import httpx
import pytest

from llm_price_monitor import egress, tasklog
from llm_price_monitor.http_retry import (
    _REPROBE_CONNECT_TIMEOUT_SECONDS,
    RETRY_ATTEMPTS,
    FallbackTransport,
    RetryingTransport,
    build_client,
)


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
    assert sleeps == [1.0, 3.0, 6.0]


def test_retry_recovers_on_second_attempt_with_ladder_backoff(monkeypatch):
    calls: list[int] = []
    sleeps: list[float] = []
    monkeypatch.setattr("llm_price_monitor.http_retry.time.sleep", sleeps.append)
    with _client(_flaky_handler(2, calls)) as client:
        response = client.get("https://demo.test/api/status")
    assert response.status_code == 200
    assert len(calls) == 3
    assert sleeps == [1.0, 3.0]


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
    assert logs == [("传输层抖动（SSL: UNEXPECTED_EOF_WHILE_READING），1s 后重试（1/3）", "warn")]


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


# ---------- 直连优先、代理兜底（FallbackTransport） ----------


@pytest.fixture(autouse=True)
def _clean_egress():
    """兜底用例从干净的失败记忆出发，不接线 provider（需要时用例里单独接）。"""
    egress._failures.clear()
    egress.configure_provider(None)
    yield
    egress._failures.clear()
    egress.configure_provider(None)


def _fallback_client(direct_handler, proxied_handler) -> httpx.Client:
    return httpx.Client(
        timeout=5,
        transport=FallbackTransport(
            direct=RetryingTransport(httpx.MockTransport(direct_handler)),
            proxied=RetryingTransport(httpx.MockTransport(proxied_handler)),
        ),
    )


def test_fallback_switches_to_proxy_after_direct_connect_error(monkeypatch):
    calls = {"direct": [], "proxy": []}

    def direct(request):
        calls["direct"].append(1)
        raise httpx.ConnectError("connection reset", request=request)

    def proxied(request):
        calls["proxy"].append(1)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr("llm_price_monitor.http_retry.time.sleep", lambda _s: None)
    with _fallback_client(direct, proxied) as client:
        response = client.get("https://demo.test/api/status")
    assert response.status_code == 200
    assert len(calls["direct"]) == 1 + RETRY_ATTEMPTS  # 直连先走完整退避重试，耗尽才兜底
    assert len(calls["proxy"]) == 1
    assert egress.plan("demo.test").use_proxy  # 失败记入记忆，TTL 内后续直接走代理


def test_fallback_on_403_then_reprobe_recovers_direct(monkeypatch):
    calls = {"direct": [], "proxy": []}

    def direct(request):
        calls["direct"].append(1)
        return httpx.Response(403) if len(calls["direct"]) == 1 else httpx.Response(200)

    def proxied(request):
        calls["proxy"].append(1)
        return httpx.Response(200)

    now = [1000.0]
    monkeypatch.setattr(egress, "_now", lambda: now[0])
    with _fallback_client(direct, proxied) as client:
        assert client.get("https://demo.test/p").status_code == 200  # 403 → 兜底
        assert len(calls["direct"]) == 1 and len(calls["proxy"]) == 1
        assert client.get("https://demo.test/p").status_code == 200  # 记忆内：直连不再被碰
        assert len(calls["direct"]) == 1 and len(calls["proxy"]) == 2

        now[0] += egress.REPROBE_SECONDS + 1
        assert client.get("https://demo.test/p").status_code == 200  # 重探直连成功 → 洗白
        assert len(calls["direct"]) == 2
        assert not egress.plan("demo.test").use_proxy and not egress.plan("demo.test").reprobe


def test_no_fallback_on_ordinary_4xx():
    calls = {"direct": [], "proxy": []}

    def direct(request):
        calls["direct"].append(1)
        return httpx.Response(404)

    def proxied(request):
        calls["proxy"].append(1)
        return httpx.Response(200)

    with _fallback_client(direct, proxied) as client:
        assert client.get("https://demo.test/missing").status_code == 404
    assert len(calls["direct"]) == 1 and len(calls["proxy"]) == 0
    assert not egress.plan("demo.test").use_proxy


def test_post_is_not_fallbacked():
    calls = {"direct": [], "proxy": []}

    def direct(request):
        calls["direct"].append(1)
        raise httpx.ConnectError("reset", request=request)

    def proxied(request):
        calls["proxy"].append(1)
        return httpx.Response(200)

    with _fallback_client(direct, proxied) as client, pytest.raises(httpx.ConnectError):
        client.post("https://demo.test/api/refresh")
    assert len(calls["direct"]) == 1 and len(calls["proxy"]) == 0


def test_reprobe_injects_short_connect_timeout(monkeypatch):
    seen = []

    def direct(request):
        seen.append(dict((request.extensions or {}).get("timeout") or {}))
        return httpx.Response(200)

    def proxied(request):
        return httpx.Response(200)

    now = [1000.0]
    monkeypatch.setattr(egress, "_now", lambda: now[0])
    egress.mark_direct_failed("demo.test")
    now[0] += egress.REPROBE_SECONDS + 1
    with _fallback_client(direct, proxied) as client:
        assert client.get("https://demo.test/p").status_code == 200
    assert seen[0]["connect"] == _REPROBE_CONNECT_TIMEOUT_SECONDS  # 重探只压短连接超时
    assert seen[0]["read"] == 5  # 其余超时保持请求原值


def test_build_client_transport_variants(monkeypatch):
    # 清掉本机可能存在的代理环境变量，避免污染分支判定
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)
    # 未配任何代理：普通重试传输层
    with build_client() as client:
        assert type(client._transport) is RetryingTransport
    # 配了备用代理：包 FallbackTransport
    egress.configure_provider(lambda: "http://172.17.0.1:7890")
    with build_client() as client:
        assert isinstance(client._transport, FallbackTransport)
    # 环境代理是全量接管语义：优先于备用代理，无失败记忆层
    monkeypatch.setenv("HTTPS_PROXY", "http://env.test:7890")
    with build_client() as client:
        assert type(client._transport) is RetryingTransport
