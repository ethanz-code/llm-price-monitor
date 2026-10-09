"""站点附属时序数据端点：渠道状态与站点公告（各自的时间线与变化事件）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from llm_price_monitor.store import Store


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/status")
    def status_data(limit: int = 200, site_id: str | None = None) -> dict[str, Any]:
        """渠道状态时序（自由结构 JSON），供状态展示与后续图表配置使用。"""
        records, total = store.read_status(site_id=site_id, limit=limit)
        return {"records": records, "total": total}

    @router.get("/api/status/latest")
    def status_latest() -> dict[str, Any]:
        return store.latest_status_all()

    @router.get("/api/status/events")
    def status_event_list(limit: int = 200, site_id: str | None = None, kind: str | None = None) -> dict[str, Any]:
        status_events, total = store.read_status_events(limit=limit, site_id=site_id, kind=kind)
        return {"events": status_events, "total": total}

    @router.get("/api/notice")
    def notice_data(limit: int = 200, site_id: str | None = None) -> dict[str, Any]:
        """站点公告版本时序；content 为公告正文（Markdown/纯文本），仅在内容变化时新增版本。"""
        records, total = store.read_notice(site_id=site_id, limit=limit)
        return {"records": records, "total": total}

    @router.get("/api/notice/events")
    def notice_event_list(limit: int = 200, site_id: str | None = None, kind: str | None = None) -> dict[str, Any]:
        notice_events, total = store.read_notice_events(limit=limit, site_id=site_id, kind=kind)
        return {"events": notice_events, "total": total}

    return router
