"""AI 请求专用 httpx.Client。"""
from __future__ import annotations

import httpx

from llm_price_monitor.config import AIConfig

def ai_http_client(config: AIConfig, timeout: float | None = None) -> httpx.Client:
    """AI 请求专用 httpx.Client：默认绕过环境变量代理直连。

    本地 Clash 等环境代理会按固定间隔掐断等响应的长连接（AI 日志里 transport 错误时长
    聚集在 135/150/180s 的固定断点即来自它），AI 端点直连可达，不走 trust_env 的
    http_proxy 等环境变量；base_url 确实需要代理出海时在 ai.proxy 里显式配置。
    """
    return httpx.Client(timeout=timeout or config.timeout, trust_env=False, proxy=config.proxy or None)
