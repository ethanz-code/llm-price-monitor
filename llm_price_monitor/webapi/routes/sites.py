"""站点配置端点：增删改查（写入前做结构校验）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from llm_price_monitor.config import DEPRECATED_SITE_FIELDS, SiteSpec, sites_from_raw
from llm_price_monitor.store import Store


class SiteBody(BaseModel):
    config: dict[str, Any]


def _validated_site_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict) or not str(config.get("id") or "").strip():
        raise ValueError("站点必须提供非空 id")
    config = {key: value for key, value in config.items() if key not in DEPRECATED_SITE_FIELDS}
    unknown = sorted(set(config) - set(SiteSpec.__dataclass_fields__))
    if unknown:
        raise ValueError(f"站点配置包含未知字段: {', '.join(unknown)}")
    sites_from_raw([config])  # 结构校验：network / models / headers 等
    return config


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sites")
    def list_sites() -> dict[str, Any]:
        return {"sites": store.list_site_configs(), "collect_status": store.get_document("collect_status") or {}}

    @router.post("/api/sites")
    def create_site(body: SiteBody) -> dict[str, Any]:
        try:
            config = _validated_site_config(body.config)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        site_id = str(config["id"]).strip()
        if store.get_site_config(site_id) is not None:
            raise HTTPException(status_code=409, detail=f"站点已存在: {site_id}")
        store.upsert_site(site_id, config)
        return {"site": config}

    @router.put("/api/sites/{site_id}")
    def update_site(site_id: str, body: SiteBody) -> dict[str, Any]:
        if store.get_site_config(site_id) is None:
            raise HTTPException(status_code=404, detail=f"站点不存在: {site_id}")
        try:
            config = _validated_site_config(body.config)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        new_id = str(config["id"]).strip()
        if new_id != site_id and store.get_site_config(new_id) is not None:
            raise HTTPException(status_code=409, detail=f"目标站点 id 已存在: {new_id}")
        if new_id != site_id:
            store.delete_site(site_id)
        store.upsert_site(new_id, config)
        return {"site": config}

    @router.delete("/api/sites/{site_id}")
    def delete_site(site_id: str, purge: bool = False) -> dict[str, Any]:
        """purge 为真时同时清理该站点的历史价格、事件与状态数据（删除弹窗里的可选项）。"""
        if not store.delete_site(site_id, purge=purge):
            raise HTTPException(status_code=404, detail=f"站点不存在: {site_id}")
        return {"deleted": site_id, "purged": purge}

    return router
