"""站点地理位置端点：供首页监控地球把站点摆到真实经纬度上。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from llm_price_monitor.geoip import resolve_site_geo
from llm_price_monitor.store import Store


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/geo")
    def site_geo() -> dict[str, Any]:
        """逐站点解析公网 IP 归属地（带缓存）；失败站点不出现在结果里。"""
        try:
            return {"geo": resolve_site_geo(store.list_site_configs())}
        except Exception as cause:  # noqa: BLE001 —— 定位失败只影响展示，把原因透给前端排查
            raise HTTPException(status_code=500, detail=str(cause)) from cause

    return router
