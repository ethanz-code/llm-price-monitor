"""并发锁与站点健康档案：合并锁、落库复查锁、传输抖动容忍与 collect_status / site_collect_health 合并。"""
from __future__ import annotations

import re
import threading
import time
from typing import Any

from llm_price_monitor import tasklog
from llm_price_monitor.config import MonitorConfig
from llm_price_monitor.store import Store

from .events import SectionScan

# 测试采集与全局采集并行后，两路扫描可能同时读-改-写 collect_status / site_collect_health
# 两个整文档，后写者会覆盖先写者的站点条目；进程内互斥让合并串行，双方条目都不丢
_MERGE_LOCK = threading.Lock()

# 事件/历史/快照落库的复查-写入原子锁：并行采集各自拿旧快照比对，若不加锁，
# 后复查的一路看不到先落库一路的结果，同一变化会写成两条事件（见 _persist_scan_results）
_PERSIST_LOCK = threading.Lock()

_TRANSPORT_ERROR_RE = re.compile(
    r"ssl\b|\beof\b|timed out|timeout|connection|disconnect"
    r"|nodename nor servname|getaddrinfo|name or service not known|no address associated",
    re.IGNORECASE,
)

# 渠道状态连续多少轮传输抖动后才升级为错误：状态 5 分钟一轮，3 轮约 15 分钟。
# 秒级/分钟级的线路抖动每轮都刷错误卡片等于噪声，持续宕机仍会在容忍窗口内报警
STATUS_TRANSPORT_TOLERANCE = 3
_TRANSPORT_STREAK_DOC = "status_transport_streak"


def _merge_collect_status(store: Store, config: MonitorConfig, site_status: dict[str, dict[str, Any]]) -> None:
    """把本轮各站点的价格采集状态并入 collect_status：单站点采集不能冲掉其他站点的状态。"""
    with _MERGE_LOCK:
        previous_status = store.get_document("collect_status")
        merged_status = dict(previous_status) if isinstance(previous_status, dict) else {}
        for spec in config.sites:
            if spec.id in site_status:
                merged_status[spec.id] = site_status[spec.id]
            elif not spec.enabled:
                merged_status[spec.id] = {"status": "disabled", "error": None, "checked_at": None}
        store.set_document("collect_status", merged_status)


def _merge_site_health(store: Store, section: str, entries: dict[str, dict[str, Any] | None]) -> None:
    """把一类采集的逐站结果并入 site_collect_health：entry 为 None 表示该站本轮正常，清掉旧记录。"""
    with _MERGE_LOCK:
        doc = dict(store.get_document("site_collect_health") or {})
        for site_id, entry in entries.items():
            site_doc = dict(doc.get(site_id) or {})
            if entry is None:
                site_doc.pop(section, None)
            else:
                site_doc[section] = entry
            if site_doc:
                doc[site_id] = site_doc
            else:
                doc.pop(site_id, None)
        store.set_document("site_collect_health", doc)


def _bump_transport_streak(store: Store | None, site_id: str) -> int:
    """渠道状态传输抖动的连续失败轮数 +1，返回新值；无库可记时按首轮处理。"""
    if store is None:
        return 1
    with _MERGE_LOCK:
        doc = dict(store.get_document(_TRANSPORT_STREAK_DOC) or {})
        streak = int(doc.get(site_id) or 0) + 1
        doc[site_id] = streak
        store.set_document(_TRANSPORT_STREAK_DOC, doc)
    return streak


def _reset_transport_streak(store: Store | None, site_id: str) -> None:
    """站点采集恢复正常（或换成了非传输类失败）后清零抖动计数。"""
    if store is None:
        return
    with _MERGE_LOCK:
        doc = dict(store.get_document(_TRANSPORT_STREAK_DOC) or {})
        if site_id in doc:
            del doc[site_id]
            store.set_document(_TRANSPORT_STREAK_DOC, doc)


def is_transport_error(message: str) -> bool:
    """网络传输层故障（SSL 握手中断、超时、连接被重置等）：多为环境抖动，不值得进健康档案惊动用户。"""
    return bool(_TRANSPORT_ERROR_RE.search(message))


def _merge_price_health(store: Store, config: MonitorConfig, site_status: dict[str, dict[str, Any]]) -> None:
    """价格采集的逐站异常进健康档案：采集失败红、需认证/无数据黄，正常或停用清除。"""
    entries: dict[str, dict[str, Any] | None] = {}
    for spec in config.sites:
        value = site_status.get(spec.id)
        status = str(value.get("status")) if value else ""
        if status == "error":
            message = str(value.get("error") or "价格采集失败")
            entries[spec.id] = (
                None
                if is_transport_error(message)
                else {
                    "level": "error",
                    "message": message,
                    "time": float(value.get("checked_at") or time.time()),
                }
            )
        elif status in {"auth_required", "no_data"}:
            fallback = "需要登录才能看到价格" if status == "auth_required" else "本轮没抓到任何价格数据"
            entries[spec.id] = {
                "level": "warn",
                "message": str(value.get("error") or fallback),
                "time": float(value.get("checked_at") or time.time()),
            }
        else:
            entries[spec.id] = None  # 正常或停用：不保留旧异常
    _merge_site_health(store, "price", entries)


def _section_health_entries(config: MonitorConfig, scan: SectionScan, fallback: str) -> dict[str, dict[str, Any] | None]:
    """渠道状态/公告采集共用的逐站异常条目：本轮业务报错的进档案，传输层抖动与其余（正常/停用/未配置接口）清除。"""
    errored = {str(item.get("site_id")): str(item.get("error") or fallback) for item in scan.errors}
    errored = {site_id: message for site_id, message in errored.items() if not is_transport_error(message)}
    return {
        spec.id: ({"level": "error", "message": errored[spec.id], "time": time.time()} if spec.id in errored else None)
        for spec in config.sites
    }
