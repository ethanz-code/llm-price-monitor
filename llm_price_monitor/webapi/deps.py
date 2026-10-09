"""路由模块共享的小工具：配置加载、登录态判断、官方价目录读取与汇率取值。

按域拆分的 APIRouter（webapi/routes/）跨域要用的小工具统一放这里，
保持各路由模块之间不互相导入。
"""
from __future__ import annotations

from typing import Any

import ipaddress

from fastapi import HTTPException, Request

from llm_price_monitor.catalog import fx
from llm_price_monitor.config import MonitorConfig, config_from_store
from llm_price_monitor.store import Store
from llm_price_monitor.webapi import auth


def load_config(store: Store) -> MonitorConfig:
    """从数据库加载运行配置；坏配置以 500 暴露给端点。"""
    try:
        return config_from_store(store)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"加载数据库配置失败: {exc}") from exc


_TRUSTED_PEER_NETS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def _trusted_peer(host: str) -> bool:
    """直连方是否为可信内网组件（本机反代 / Docker 网络里的 Next 容器）。

    API 从不对外发布（部署只绑容器网络/本机，见 docker-compose.md），直连方只可能
    是自家基础设施，因此内网对端的转发头可信；公网对端与解析不了的地址一律不信任，
    防止伪造头冒充他人 IP。不用 ipaddress.is_private——它把 TEST-NET/CGNAT 等
    IANA 保留段也算私有，会让文档段地址冒充内网对端。
    """
    text = host.strip().removeprefix("::ffff:")
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return False
    return any(addr in net for net in _TRUSTED_PEER_NETS)


def client_ip(request: Request) -> str:
    """访客 IP：仅当直连方是可信内网组件（回环反代或容器网络里的 Next 中间件）时才信任
    转发头——优先 x-real-ip（部署文档的 Nginx 写入真实 IP），否则取 XFF 最后一跳
    （追加链的末端由可信代理写入）；公网直连一律用 socket 地址，防止伪造头冒充他人 IP。"""
    peer = request.client.host if request.client else ""
    if not _trusted_peer(peer):
        return peer
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")
    return forwarded[-1].strip() or peer


def is_admin(store: Store, request: Request) -> bool:
    """会话 cookie 有效即为管理员；首次启动（无管理员账号）时人人未登录。"""
    return auth.session_username(store, request.cookies.get(auth.SESSION_COOKIE)) is not None


def catalog_models(report: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """从已存的官方价目录文档提取 (模型表, 元信息)；无文档时为空表。"""
    if not isinstance(report, dict):
        return {}, {}
    models = report.get("models")
    meta = {k: report.get(k) for k in ("generated_at", "generated_at_iso", "usd_cny_rate", "rate_source", "source", "source_url")}
    return (models if isinstance(models, dict) else {}), meta


def require_catalog_rate(report: Any) -> tuple[float, str]:
    """折扣/总览端点用：目录快照汇率优先，缺失时实时拉取；两者都失败抛 503。"""
    snapshot_rate = report.get("usd_cny_rate") if isinstance(report, dict) else None
    rate, source = fx.resolve_rate(snapshot_rate)
    if rate is None:
        raise HTTPException(status_code=503, detail="厂商价快照没有汇率，且实时汇率获取失败")
    return rate, source
