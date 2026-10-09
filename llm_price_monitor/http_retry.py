"""采集链路的传输层退避重试：对端服务器/线路间歇性掐断 TLS、连接重置、超时这类
偶发抖动，一次失败不该丢掉整轮采集。

只包采集用的 httpx.Client 构建入口（build_client），重试发生在 transport 层，
价格/状态/公告三类采集与其适配器无感知。重试只对幂等的 GET/HEAD 生效：
token 续签、AI 兜底等 POST 不盲重试，避免续签凭证被消费两次。

代理：显式传 transport 时 httpx 会跳过 trust_env 的环境代理解析，这里必须
自己读代理环境变量传给 HTTPTransport，否则采集会绕过 Clash 等系统代理裸直连。
"""
from __future__ import annotations

import os
import time

import httpx

from llm_price_monitor import tasklog

# 传输层错误最多重试次数；总尝试 = 1 + RETRY_ATTEMPTS
RETRY_ATTEMPTS = 2
# 指数退避间隔（秒）；第 N 次重试取 RETRY_BACKOFF[min(N-1, 末位)]，超长自动取末位
RETRY_BACKOFF: tuple[float, ...] = (1.0, 2.0)

# 代理环境变量，按 https > all > http 取第一个非空值（大小写都认，与 httpx 约定一致）
_PROXY_ENV_KEYS = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy")


def _env_proxy() -> str | None:
    """采集目标全是外部站点，读标准代理环境变量即可，不处理 NO_PROXY 例外。"""
    for key in _PROXY_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


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
    return httpx.Client(
        timeout=timeout,
        headers=headers,
        follow_redirects=follow_redirects,
        transport=RetryingTransport(httpx.HTTPTransport(proxy=_env_proxy())),
    )
