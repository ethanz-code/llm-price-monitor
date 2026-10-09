"""访客行为端点：页面访问埋点与统计（管理员）、访客建议。"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from llm_price_monitor import wxpusher
from llm_price_monitor.config import config_from_store
from llm_price_monitor import visitor_geo
from llm_price_monitor.store import Store
from llm_price_monitor.ua import parse_user_agent
from llm_price_monitor.webapi.deps import client_ip

# 单 IP 每分钟最多写入的访问记录数：超量静默丢弃，防伪造来源刷库
TRACK_MAX_PER_MINUTE = 60


class FeedbackBody(BaseModel):
    content: str
    contact: str | None = None


class TrackBody(BaseModel):
    path: str


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    # 进程内限速/去重状态：随应用生命周期存续，重启即重置
    feedback_hits: dict[str, list[float]] = {}
    track_hits: dict[str, list[float]] = {}
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
        ip = client_ip(request)
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
        ip = client_ip(request)
        user_agent = (request.headers.get("user-agent") or "")[:500]
        now = time.time()
        key = (ip, path)
        if now - visit_hits.get(key, 0.0) < 30:
            return {"ok": True}
        visit_hits[key] = now
        recent = [t for t in track_hits.get(ip, []) if now - t < 60]
        if len(recent) >= TRACK_MAX_PER_MINUTE:
            track_hits[ip] = recent
            return {"ok": True}  # 超量静默丢弃：响应不区分，避免给刷库者探测信号
        recent.append(now)
        track_hits[ip] = recent
        for stale_key, stale_ts in list(visit_hits.items()):
            if now - stale_ts > 300:
                visit_hits.pop(stale_key, None)
        ua = parse_user_agent(user_agent)
        store.add_visit(path=path, ip=ip, user_agent=user_agent, browser=ua.browser, os=ua.os, device=ua.device)
        if now - visit_purge_state["last"] > 3600:
            visit_purge_state["last"] = now
            retention_days = config_from_store(store).settings.retention_visit_days
            store.purge_visits(now - retention_days * 86400)
        return {"ok": True}

    @router.get("/api/analytics/summary")
    def analytics_summary() -> dict[str, Any]:
        """访问统计聚合（管理员）：KPI、按天趋势、设备/浏览器/系统分布、省份分布与热门榜单。

        先补齐尚未解析归属地的访客 IP（批量解析 + 持久缓存），解析失败不影响返回。
        """
        cutoff = time.time() - 30 * 86400
        try:
            ips = store.pending_geo_ips(cutoff=cutoff, limit=200)
            if ips:
                resolved = visitor_geo.resolve_regions(ips)
                failed = [ip for ip in ips if ip not in resolved]
                store.save_ip_geo(resolved, failed)
        except Exception:
            pass  # 归属地解析只影响地图，失败时照常返回统计
        summary = store.visit_summary()
        summary["regions"] = store.region_dist(cutoff=cutoff)
        return {**summary, "retained_days": config_from_store(store).settings.retention_visit_days}

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
