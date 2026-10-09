"""采集链路的传输层退避重试与直连优先代理兜底：对端服务器/线路间歇性掐断 TLS、
连接重置、超时这类偶发抖动，一次失败不该丢掉整轮采集。

只包采集用的 httpx.Client 构建入口（build_client），重试发生在 transport 层，
价格/状态/公告三类采集与其适配器无感知。重试只对幂等的 GET/HEAD 生效：
token 续签、AI 兜底等 POST 不盲重试，避免续签凭证被消费两次。

代理分两档：
- 标准代理环境变量（HTTPS_PROXY 等）语义是全量接管，配了就全部流量走它；
- 未配环境变量但系统设置里配了 settings.fallback_proxy 时，采集走「直连优先、
  被墙兜底」：直连失败（连接类错误或 403/451）的域名记入失败记忆（egress.py），
  TTL 内直接走代理，TTL 过期放行一次短连接超时的直连重探。代理地址在构建
  client 时捕获，面板改动从下一轮采集生效。
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlsplit

import httpx

from llm_price_monitor import egress, tasklog

# 传输层错误最多重试次数；总尝试 = 1 + RETRY_ATTEMPTS。3 次退避约 10s 窗口：
# 2 次（1s/2s）只够盖住秒级抖动，实测对端会持续掐十几秒才恢复
RETRY_ATTEMPTS = 3
# 指数退避间隔（秒）；第 N 次重试取 RETRY_BACKOFF[min(N-1, 末位)]，超长自动取末位
RETRY_BACKOFF: tuple[float, ...] = (1.0, 3.0, 6.0)

# 代理环境变量，按 https > all > http 取第一个非空值（大小写都认，与 httpx 约定一致）
_PROXY_ENV_KEYS = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy")

# 直连返回这些状态码视为「出口 IP 被目标站拒收」，同样切备用代理重试
_FALLBACK_STATUS_CODES = frozenset({403, 451})
# 失败记忆过期的直连重探把连接超时压到这个值，黑洞式被墙不至于白等整个采集周期
_REPROBE_CONNECT_TIMEOUT_SECONDS = 8.0
# 本地构造的请求错误 / 连接池耗尽 / 无效 URL 与出口线路无关，切代理救不了
_NO_FALLBACK_ERRORS = (httpx.LocalProtocolError, httpx.PoolTimeout, httpx.UnsupportedProtocol)


def _env_proxy() -> str | None:
    """采集目标全是外部站点，读标准代理环境变量即可，不处理 NO_PROXY 例外。"""
    for key in _PROXY_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def _force_short_connect(request: httpx.Request) -> httpx.Request:
    """给请求注入短连接超时（直连重探用）；read/write 等其余超时保持请求原值。"""
    timeout = dict(request.extensions.get("timeout") or {})
    timeout["connect"] = _REPROBE_CONNECT_TIMEOUT_SECONDS
    request.extensions = {**request.extensions, "timeout": timeout}
    return request


class RetryingTransport(httpx.BaseTransport):
    """包一层底层 transport：传输层异常时按退避间隔重试幂等请求。"""

    def __init__(
        self,
        wrapped: httpx.BaseTransport,
        attempts: int = RETRY_ATTEMPTS,
        backoff: tuple[float, ...] = RETRY_BACKOFF,
    ) -> None:
        self._wrapped = wrapped
        self._attempts = attempts
        self._backoff = backoff or RETRY_BACKOFF

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method.upper() not in {"GET", "HEAD"}:
            return self._wrapped.handle_request(request)
        for attempt in range(self._attempts + 1):
            try:
                return self._wrapped.handle_request(request)
            except httpx.TransportError as exc:
                # 本地构造的请求错误重试也不会好；重试额度用完则按原样抛给采集层
                if isinstance(exc, httpx.LocalProtocolError) or attempt >= self._attempts:
                    raise
                delay = self._backoff[min(attempt, len(self._backoff) - 1)]
                tasklog.emit(
                    f"传输层抖动（{exc}），{delay:g}s 后重试（{attempt + 1}/{self._attempts}）",
                    "warn",
                )
                time.sleep(delay)
        raise RuntimeError("unreachable")  # 循环内必 return 或 raise，类型收窄用


class FallbackTransport(httpx.BaseTransport):
    """直连优先、代理兜底：仅对幂等的 GET/HEAD 生效（与重试同一原则，避免续签等
    POST 被消费两次）。直连整体失败（内层退避重试耗尽）或返回 403/451 时记入失败
    记忆并换备用代理重试一次；记忆 TTL 内的请求直接走代理，过期后放行一次短连接
    超时的直连重探，成功即切回直连。无头浏览器经 egress 共用这份记忆（只读）。
    """

    def __init__(self, direct: httpx.BaseTransport, proxied: httpx.BaseTransport) -> None:
        self._direct = direct
        self._proxied = proxied

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method.upper() not in {"GET", "HEAD"}:
            return self._direct.handle_request(request)
        host = urlsplit(str(request.url)).hostname or ""
        decision = egress.plan(host)
        if decision.use_proxy:
            return self._proxied.handle_request(request)
        try:
            response = self._direct.handle_request(
                _force_short_connect(request) if decision.reprobe else request
            )
        except httpx.TransportError as exc:
            if isinstance(exc, _NO_FALLBACK_ERRORS):
                raise
            egress.mark_direct_failed(host)
            tasklog.emit(f"[{host}] 直连失败（{exc}），改走备用代理重试", "warn")
            return self._proxied.handle_request(request)
        if response.status_code in _FALLBACK_STATUS_CODES:
            egress.mark_direct_failed(host)
            tasklog.emit(
                f"[{host}] 直连返回 HTTP {response.status_code}，疑似出口 IP 被目标站拒收，改走备用代理重试",
                "warn",
            )
            response.close()
            return self._proxied.handle_request(request)
        egress.mark_direct_ok(host)
        return response


def build_client(
    *,
    timeout: float | None = None,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """采集用 httpx.Client：默认跟随重定向，传输层带退避重试；transport 可注入替换（测试用）。"""
    if transport is not None:
        return httpx.Client(
            timeout=timeout,
            headers=headers,
            follow_redirects=follow_redirects,
            transport=transport,
        )
    env_proxy = _env_proxy()
    if env_proxy:
        # 环境代理语义是全量接管，失败记忆兜底没有用武之地
        return httpx.Client(
            timeout=timeout,
            headers=headers,
            follow_redirects=follow_redirects,
            transport=RetryingTransport(httpx.HTTPTransport(proxy=env_proxy)),
        )
    fallback_proxy = egress.fallback_proxy()
    if not fallback_proxy:
        return httpx.Client(
            timeout=timeout,
            headers=headers,
            follow_redirects=follow_redirects,
            transport=RetryingTransport(httpx.HTTPTransport()),
        )
    return httpx.Client(
        timeout=timeout,
        headers=headers,
        follow_redirects=follow_redirects,
        transport=FallbackTransport(
            direct=RetryingTransport(httpx.HTTPTransport()),
            proxied=RetryingTransport(httpx.HTTPTransport(proxy=fallback_proxy)),
        ),
    )
