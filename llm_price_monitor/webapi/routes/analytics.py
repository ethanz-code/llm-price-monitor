"""访客行为端点：页面访问埋点与统计（管理员）、访客建议。"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from llm_price_monitor import wxpusher
from llm_price_monitor.config import config_from_store
from llm_price_monitor.store import Store
from llm_price_monitor.ua import parse_user_agent

# 访问记录自动保留 90 天，每小时顺带清一次过期数据
VISIT_RETENTION_DAYS = 90


class FeedbackBody(BaseModel):
    content: str
    contact: str | None = None


class TrackBody(BaseModel):
    path: str


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    # 进程内限速/去重状态：随应用生命周期存续，重启即重置
    feedback_hits: dict[str, list[float]] = {}
    visit_purge_state = {"last": 0.0}
    # 同 IP + 路径 30 秒内只记一次，避免刷新与重复预取虚高 PV
    visit_hits: dict[tuple[str, str], float] = {}

    @router.post("/api/feedback")
    def submit_feedback(body: FeedbackBody, request: Request) -> dict[str, bool]:
        """访客提建议：入库保存；配置了 WxPusher 时尽力推送通知，推送失败不影响入库。"""
        content = body.content.strip()
        if not content:
            raise HTTPException(status_code=400, detail="建议内容不能为空")
        if len(content) > 1000:
            raise HTTPException(status_code=400, detail="建议内容最长 1000 字")
        contact = (body.contact or "").strip() or None
        if contact and len(contact) > 100:
            raise HTTPException(status_code=400, detail="联系方式最长 100 字")
        now = time.time()
        ip = request.client.host if request.client else ""
        recent = [t for t in feedback_hits.get(ip, []) if now - t < 60]
        if len(recent) >= 3:
            raise HTTPException(status_code=429, detail="提交过于频繁，请稍后再试")
        recent.append(now)
        feedback_hits[ip] = recent
        store.append_feedback(content, contact)
        settings = config_from_store(store).settings
        if settings.wxpusher_app_token:
            try:
                wxpusher.send_wxpusher(
                    app_token=settings.wxpusher_app_token,
                    content=f"【LLM 价格监控】收到新的建议\n联系方式：{contact or '未留'}\n\n{content}",
                    summary=content[:100],
                    uid=settings.wxpusher_uid,
                )
            except Exception:
                pass  # 尽力而为：推送失败不阻塞访客提交
        return {"ok": True}

    @router.post("/api/analytics/track")
    def track_visit(body: TrackBody, request: Request) -> dict[str, bool]:
        """记录页面访问（由 Next 中间件服务端上报）：路径 + 客户端 IP + UA，公开写接口。"""
        path = body.path.split("?", 1)[0].split("#", 1)[0]
        if not path.startswith("/") or path.startswith("/api") or len(path) > 200:
            raise HTTPException(status_code=400, detail="非法路径")
        forwarded = (request.headers.get("x-forwarded-for") or request.headers.get("x-client-ip") or "").split(",")[0].strip()
        ip = forwarded or (request.client.host if request.client else "")
        user_agent = (request.headers.get("user-agent") or "")[:500]
        now = time.time()
        key = (ip, path)
        if now - visit_hits.get(key, 0.0) < 30:
            return {"ok": True}
        visit_hits[key] = now
        for stale_key, stale_ts in list(visit_hits.items()):
            if now - stale_ts > 300:
                visit_hits.pop(stale_key, None)
        ua = parse_user_agent(user_agent)
        store.add_visit(path=path, ip=ip, user_agent=user_agent, browser=ua.browser, os=ua.os, device=ua.device)
        if now - visit_purge_state["last"] > 3600:
            visit_purge_state["last"] = now
            store.purge_visits(now - VISIT_RETENTION_DAYS * 86400)
        return {"ok": True}

    @router.get("/api/analytics/summary")
    def analytics_summary() -> dict[str, Any]:
        """访问统计聚合（管理员）：KPI、按天趋势、设备/浏览器/系统分布与热门榜单。"""
        return {**store.visit_summary(), "retained_days": VISIT_RETENTION_DAYS}

    @router.get("/api/analytics/logs")
    def analytics_logs(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        """访问明细（管理员）：按时间倒序分页。"""
        visits, total = store.list_visits(limit=max(1, min(limit, 500)), offset=max(0, offset))
        return {"visits": visits, "total": total}

    @router.post("/api/analytics/clear")
    def analytics_clear() -> dict[str, bool]:
        """清空全部访问记录（管理员）。"""
        store.clear_visits()
        return {"ok": True}

    return router
