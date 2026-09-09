"""只读数据端点：首页总览、最新快照、历史、事件、meta、健康检查与内嵌文档。"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from llm_price_monitor.catalog import fx
from llm_price_monitor.catalog.discount import build_discount
from llm_price_monitor.report import summary_row
from llm_price_monitor.store import Store
from llm_price_monitor.webapi import auth
from llm_price_monitor.webapi.deps import catalog_models, is_admin, load_config, require_catalog_rate


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/overview")
    def overview(request: Request) -> dict[str, Any]:
        """首页数据：最新快照逐条附加官方价折扣；collect_status 仅管理员会话可见。"""
        report = store.get_document("catalog")
        models, meta = catalog_models(report)
        discount_of: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None
        live_rate: dict[str, Any] = {}
        if models:
            rate, rate_source = require_catalog_rate(report)
            live_rate = {"usd_cny_rate": round(rate, 2), "rate_source": rate_source}

            def discount_of(row: dict[str, Any]) -> dict[str, Any] | None:
                entry, _reason = build_discount(summary_row(row), models, rate)
                return entry.as_dict() if entry is not None else None

        records: list[dict[str, Any]] = []
        for row in store.latest_all().values():
            if not isinstance(row, dict):
                continue
            records.append({**row, "discount": discount_of(row) if discount_of else None})
        # 各站点最新公告（每站一条，原样带给前端做单行摘要；没采集到公告的站点无键）
        notices: dict[str, dict[str, Any]] = {}
        for site_id in {str(row.get("site_id")) for row in records if isinstance(row, dict)}:
            notice = store.latest_notice(site_id)
            if isinstance(notice, dict) and notice.get("content"):
                notices[site_id] = {"content": str(notice["content"]), "captured_at": notice.get("captured_at")}
        # 站点信息完整度：监控模型、认证凭证、附加采集地址各 1 分（0–3），给前端智能排序用；
        # network.ratio_url（倍率接口）与价格接口同地址、token_refresh（续签）均不计分
        site_completeness = {
            site.id: sum(
                (
                    bool(site.models),
                    bool(site.auth_token or site.cookie or site.cookies),
                    bool(site.networks),
                )
            )
            for site in load_config(store).sites
        }
        return {
            "records": records,
            "notices": notices,
            "site_completeness": site_completeness,
            # 汇率口径与折扣页一致：优先实时值（拉取失败回落官方价目录缓存并标注）
            "catalog": {
                **meta,
                **live_rate,
                "enabled": bool(models),
            },
            "collect_status": (store.get_document("collect_status") or {}) if is_admin(store, request) else {},
        }

    @router.get("/api/latest")
    def latest() -> dict[str, Any]:
        return store.latest_all()

    def display_rate() -> dict[str, Any]:
        """事件/历史端点附带的展示汇率：快照优先、实时兜底；拿不到就不附，前端回落原币展示。"""
        report = store.get_document("catalog")
        snapshot_rate = report.get("usd_cny_rate") if isinstance(report, dict) else None
        rate, source = fx.resolve_rate(snapshot_rate)
        return {"rate": round(rate, 2), "rate_source": source} if rate else {}

    @router.get("/api/history")
    def history(limit: int = 500, site_id: str | None = None, model: str | None = None) -> dict[str, Any]:
        records, total = store.read_history(limit=limit, site_id=site_id, model=model)
        return {"records": records, "total": total, **display_rate()}

    @router.get("/api/feed")
    def feed(events_limit: int = 200, notice_limit: int = 200) -> dict[str, Any]:
        """统一事件流：价格事件与站点公告事件按时间合并排序，各自的全量总数单独给出。"""
        price_events, price_total = store.read_events(limit=events_limit)
        notice_events, notice_total = store.read_notice_events(limit=notice_limit)
        return {
            "events": sorted([*price_events, *notice_events], key=lambda e: e["detected_at"], reverse=True),
            "price_total": price_total,
            "notice_total": notice_total,
        }

    @router.get("/api/meta")
    def meta(request: Request) -> dict[str, Any]:
        config = load_config(store)
        return {
            "sites": [
                {
                    "id": site.id,
                    "adapter": site.adapter,
                    "models": [target.name for target in site.models],
                    "url": site.network.get("url"),
                    "enabled": site.enabled,
                }
                for site in config.sites
            ],
            "is_admin": is_admin(store, request),
            "needs_setup": auth.get_admin(store) is None,
        }

    @router.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/api/docs/readme")
    def readme() -> dict[str, str]:
        """管理面板文档页用：读仓库根 README.md 原文，找不到时报 404。"""
        # data.py 位于 llm_price_monitor/webapi/routes/ 下，三层向上到包、四层到仓库根
        for candidate in (Path(__file__).resolve().parents[3] / "README.md", Path.cwd() / "README.md"):
            if candidate.is_file():
                return {"markdown": candidate.read_text(encoding="utf-8")}
        raise HTTPException(status_code=404, detail="未找到 README.md")

    return router
