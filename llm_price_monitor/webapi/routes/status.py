"""站点附属时序数据端点：渠道状态与站点公告（各自的时间线与变化事件）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from llm_price_monitor.store import Store


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/status")
    def status_data(
        limit: int = 200,
        site_id: str | None = None,
        per_site: int | None = None,
        since: float | None = None,
        max_records: int | None = None,
    ) -> dict[str, Any]:
        """渠道状态时序（自由结构 JSON）；per_site 按站点分组各取最近 N 条，since 只取该 Unix 时间之后的快照。
        max_records 限制返回行数：窗口内记录更多时由存储层按 id 均匀抽样（total 仍返回全量行数）。"""
        records, total = store.read_status(
            site_id=site_id, limit=limit, per_site=per_site, since=since, max_records=max_records
        )
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

    return router
