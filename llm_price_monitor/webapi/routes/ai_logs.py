"""AI 请求日志（管理员）：大模型调用的场景、模型、耗时、token 用量与错误记录。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from llm_price_monitor.store import Store

# 列表上限：前端一次拉全量交给表格组件本地分页；保留期内一般 1~2k 条，超长保留期也不会失控
_AI_LOG_LIST_LIMIT = 5000


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/ai-logs")
    def list_ai_logs(
        limit: int = 100,
        offset: int = 0,
        scene: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        rows, total = store.read_ai_logs(limit=max(1, min(limit, _AI_LOG_LIST_LIMIT)), offset=max(0, offset), scene=scene, status=status)
        return {"logs": rows, "total": total}

    @router.get("/api/ai-logs/summary")
    def ai_logs_summary() -> dict[str, Any]:
        """AI 调用统计聚合（管理员）：KPI、按天趋势与场景/模型分布，口径为全部保留记录。"""
        return store.ai_logs_summary()

    @router.get("/api/ai-logs/{log_id}")
    def get_ai_log(log_id: int) -> dict[str, Any]:
        """单条日志全文（含 prompt/回复摘要）：详情弹窗按需取，列表接口不带回。"""
        row = store.read_ai_log(log_id)
        if row is None:
            raise HTTPException(status_code=404, detail="日志不存在或已过期")
        return {"log": row}

    return router
