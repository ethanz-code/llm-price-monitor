"""官方价目录端点：目录读取、手动刷新与折扣对比。

定时同步由统一的调度器（webapi.scheduler）负责，到点提交 catalog-refresh 任务；
手动刷新共用同一个任务体（jobs.catalog_refresh_job）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from llm_price_monitor.catalog.discount import build_discount, summarize
from llm_price_monitor.report import summary_row
from llm_price_monitor.store import Store
from llm_price_monitor.webapi import tasks
from llm_price_monitor.webapi.deps import catalog_models, require_catalog_rate
from llm_price_monitor.webapi.jobs import catalog_refresh_job


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/catalog")
    def catalog() -> dict[str, Any]:
        report = store.get_document("catalog")
        if not report:
            raise HTTPException(status_code=404, detail="厂商价目录不存在（首次启动会自动同步，也可登录后手动刷新）")
        return report

    @router.get("/api/catalog/all")
    def catalog_all() -> dict[str, Any]:
        report = store.get_document("catalog_all")
        if not report:
            raise HTTPException(status_code=404, detail="全量渠道价目录不存在（登录后在厂商定价页手动刷新即可生成）")
        return report

    @router.post("/api/catalog/refresh")
    def catalog_refresh() -> dict[str, str]:
        try:
            task_id = tasks.submit("catalog-refresh", catalog_refresh_job(store))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"task_id": task_id}

    @router.get("/api/discount")
    def discount() -> dict[str, Any]:
        report = store.get_document("catalog")
        models, meta = catalog_models(report)
        if not models:
            raise HTTPException(status_code=404, detail="厂商价目录不存在或为空，请先刷新厂商价")
        latest_rows = [row for row in store.latest_all().values() if isinstance(row, dict)]
        rate, rate_source = require_catalog_rate(report)
        entries = []
        skipped: list[dict[str, Any]] = []
        for row in latest_rows:
            entry, reason = build_discount(summary_row(row), models, rate)
            if entry is None:
                skipped.append({"site_id": row.get("site_id"), "model": row.get("model"), "reason": reason})
            else:
                entries.append(entry)
        discounts: list[dict[str, Any]] = []
        for entry in entries:
            item = entry.as_dict()
            item.update({"site_id": entry.site_id, "model": entry.model, "group": entry.group})
            discounts.append(item)
        return {
            "usd_cny_rate": round(rate, 2),
            "rate_source": rate_source,
            "official_generated_at": meta.get("generated_at_iso"),
            "discounts": discounts,
            "summary": summarize(entries),
            "skipped": skipped,
        }

    return router
