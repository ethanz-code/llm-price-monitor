"""路由模块共享的小工具：配置加载、登录态判断、官方价目录读取与汇率取值。

按域拆分的 APIRouter（webapi/routes/）跨域要用的小工具统一放这里，
保持各路由模块之间不互相导入。
"""
from __future__ import annotations

from typing import Any

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
