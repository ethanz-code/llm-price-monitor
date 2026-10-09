"""采集出口的直连失败记忆与备用代理（直连优先、被墙兜底）。

进程内维护一份域名级失败记忆：某域名直连出现连接类失败（超时/重置/TLS 中断）
或 403/451 时记上一笔，TTL 内后续请求直接走备用代理，不再反复撞墙；TTL 过期后
放行一次直连探测，成功即洗白、失败续期。备用代理地址实时读系统设置
（settings.fallback_proxy，管理面板「系统设置」里维护），面板改完下轮采集生效。

单实例设计（与调度器同一约束），记忆只存进程内存，重启即清零重新学习。
无头浏览器采集共用这份记忆（只读），保证同一站点两条链路的出口选择一致。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

# 直连失败后改走代理的时长（秒）；过期后放行一次直连探测，成功即切回
REPROBE_SECONDS = 3600

_now = time.time
_provider: Callable[[], str | None] | None = None
_failures: dict[str, float] = {}
_lock = threading.Lock()


def configure_provider(provider: Callable[[], str | None] | None) -> None:
    """注入备用代理地址的读取函数（应用启动时接 store.get_document("settings")）；None 表示未接线。"""
    global _provider
    _provider = provider


def fallback_proxy() -> str | None:
    """当前生效的备用代理地址；未配置或读取失败返回 None（采集永不因配置读取挂掉）。"""
    if _provider is None:
        return None
    try:
        value = _provider()
    except Exception:
        return None
    return str(value or "").strip() or None


@dataclass(frozen=True)
class DirectPlan:
    """单个域名的直连决策：use_proxy=直接走代理；reprobe=放行一次直连探测。"""

    use_proxy: bool
    reprobe: bool


def plan(host: str) -> DirectPlan:
    """给定域名该直连还是走代理；失败标记过期时返回 reprobe 让调用方短超时试探。"""
    key = _normalize(host)
    with _lock:
        failed_at = _failures.get(key)
    if failed_at is None:
        return DirectPlan(use_proxy=False, reprobe=False)
    if _now() - failed_at >= REPROBE_SECONDS:
        return DirectPlan(use_proxy=False, reprobe=True)
    return DirectPlan(use_proxy=True, reprobe=False)


def mark_direct_failed(host: str) -> None:
    with _lock:
        _failures[_normalize(host)] = _now()


def mark_direct_ok(host: str) -> None:
    with _lock:
        _failures.pop(_normalize(host), None)


def _normalize(host: str) -> str:
    return str(host or "").strip().lower()
