"""模型榜单端点：AA 排名读取与手动刷新。

定时同步由统一调度器（webapi.scheduler）按 settings.schedule.rankings 间隔触发；
手动刷新共用同一个任务体（jobs.rankings_refresh_job）。读取是访客功能，路径
在 app.py 的 public_get_paths 白名单里。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from llm_price_monitor.store import Store
from llm_price_monitor.webapi import tasks
from llm_price_monitor.webapi.jobs import rankings_refresh_job


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/rankings")
    def rankings() -> dict[str, Any]:
        doc = store.get_document("rankings")
        if not doc:
            raise HTTPException(status_code=404, detail="模型榜单不存在（登录后在管理台手动刷新，或等待定时同步）")
        return doc

    @router.post("/api/rankings/refresh")
    def rankings_refresh() -> dict[str, str]:
        try:
            task_id = tasks.submit("rankings-refresh", rankings_refresh_job(store))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"task_id": task_id}

    return router
