"""厂商定价源端点：管理台配置国内厂商定价页、触发抓取与覆盖检测。

读路径在 app.py 的 admin_get_paths/admin_get_prefixes 里登记（管理员可见）；
写方法由全局管理员中间件兜住，这里不重复鉴权。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from llm_price_monitor.catalog import vendor_sources
from llm_price_monitor.store import Store
from llm_price_monitor.webapi import tasks
from llm_price_monitor.webapi.jobs import catalog_refresh_job, vendor_source_refresh_job


class VendorSourceBody(BaseModel):
    vendor: str
    url: str
    enabled: bool = True
    region: str = "cn"


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    def _summary(record: dict[str, Any]) -> dict[str, Any]:
        """列表用的源摘要：不含 models 明细（详情走 GET /{vendor}）。"""
        return {key: value for key, value in record.items() if key != "models"}

    @router.get("/api/vendor-sources")
    def list_sources() -> dict[str, Any]:
        sources = vendor_sources.load_sources(store)
        return {"sources": [_summary(record) for _, record in sorted(sources.items())]}

    @router.get("/api/vendor-sources/detection")
    def detection() -> dict[str, Any]:
        """models.dev 对国内厂商国内价的覆盖检测记录（推荐添加清单）。"""
        full = store.get_document("catalog_all")
        providers = full.get("providers") if isinstance(full, dict) else None
        if not isinstance(providers, list) or not providers:
            raise HTTPException(status_code=404, detail="厂商清单还没有生成，请先刷新一次厂商定价目录")
        return {"records": vendor_sources.detect_vendor_coverage(providers, vendor_sources.load_sources(store))}

    @router.get("/api/vendor-sources/{vendor}")
    def get_source(vendor: str) -> dict[str, Any]:
        record = vendor_sources.load_sources(store).get(vendor)
        if record is None:
            raise HTTPException(status_code=404, detail=f"厂商定价源不存在: {vendor}")
        return record

    @router.post("/api/vendor-sources")
    def create_source(body: VendorSourceBody) -> dict[str, Any]:
        try:
            vendor, _url = vendor_sources.validate_source(body.vendor, body.url)
            region = vendor_sources.validate_region(body.region)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if vendor in vendor_sources.load_sources(store):
            raise HTTPException(status_code=409, detail=f"厂商定价源已存在: {vendor}")
        record = vendor_sources.upsert_source(store, body.vendor, body.url, body.enabled, region)
        return {"source": _summary(record)}

    @router.put("/api/vendor-sources/{vendor}")
    def update_source(vendor: str, body: VendorSourceBody) -> dict[str, Any]:
        if vendor not in vendor_sources.load_sources(store):
            raise HTTPException(status_code=404, detail=f"厂商定价源不存在: {vendor}")
        try:
            normalized_vendor, _url = vendor_sources.validate_source(body.vendor, body.url)
            region = vendor_sources.validate_region(body.region)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if normalized_vendor != vendor:
            raise HTTPException(status_code=400, detail="请求体里的厂商名与路径不一致（不支持改名，请删除后重建）")
        record = vendor_sources.upsert_source(store, vendor, body.url, body.enabled, region)
        revert_task_id = None
        if not body.enabled:
            # 停用后目录里上次合并的国内价会残留，自动触发目录刷新恢复 models.dev 基准
            revert_task_id = _submit_catalog_refresh_silently(store)
        return {"source": _summary(record), **({"revert_task_id": revert_task_id} if revert_task_id else {})}

    @router.delete("/api/vendor-sources/{vendor}")
    def remove_source(vendor: str) -> dict[str, Any]:
        if not vendor_sources.delete_source(store, vendor):
            raise HTTPException(status_code=404, detail=f"厂商定价源不存在: {vendor}")
        # 删除后目录里已合并的国内价无法就地撤销，自动触发目录刷新重建
        revert_task_id = _submit_catalog_refresh_silently(store)
        return {"deleted": vendor, **({"revert_task_id": revert_task_id} if revert_task_id else {})}

    @router.post("/api/vendor-sources/{vendor}/refresh")
    def refresh_source(vendor: str) -> dict[str, str]:
        if vendor not in vendor_sources.load_sources(store):
            raise HTTPException(status_code=404, detail=f"厂商定价源不存在: {vendor}")
        try:
            task_id = tasks.submit("vendor-source-refresh", vendor_source_refresh_job(store, vendor))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"task_id": task_id}

    return router


def _submit_catalog_refresh_silently(store: Store) -> str | None:
    """后台提交目录刷新以恢复 models.dev 基准；冲突（刷新进行中）时静默跳过。"""
    try:
        return tasks.submit("catalog-refresh", catalog_refresh_job(store))
    except RuntimeError:
        return None
