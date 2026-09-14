"""AI 请求日志（管理员）：大模型调用的场景、模型、耗时、token 用量与错误记录。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from llm_price_monitor.store import Store


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/ai-logs")
    def list_ai_logs(
        limit: int = 100,
        offset: int = 0,
        scene: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        rows, total = store.read_ai_logs(limit=min(limit, 500), offset=offset, scene=scene, status=status)
        return {"logs": rows, "total": total}

    @router.get("/api/ai-logs/summary")
    def ai_logs_summary() -> dict[str, Any]:
        """AI 调用统计聚合（管理员）：KPI、按天趋势与场景/模型分布，口径为全部保留记录。"""
        return store.ai_logs_summary()

    return router
