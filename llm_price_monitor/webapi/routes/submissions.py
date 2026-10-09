"""站点提交端点：访客申请把某个中转站加入监控清单，入库并尽力推送 WxPusher 通知。"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from llm_price_monitor import wxpusher
from llm_price_monitor.config import config_from_store
from llm_price_monitor.store import Store
from llm_price_monitor.webapi.deps import client_ip

# 单 IP 每分钟最多提交次数：超量拒绝，防刷库
SUBMIT_MAX_PER_MINUTE = 3

_SUBMISSION_STATUSES = {"new", "done"}


class SiteSubmissionBody(BaseModel):
    name: str
    url: str
    models: str | None = None
    contact: str | None = None


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    # 进程内限速状态：随应用生命周期存续，重启即重置
    submit_hits: dict[str, list[float]] = {}

    @router.post("/api/site-submissions")
    def submit_site(body: SiteSubmissionBody, request: Request) -> dict[str, bool]:
        """访客提交站点：入库保存；配置了 WxPusher 时尽力推送通知，推送失败不影响入库。"""
        name = body.name.strip()
        url = body.url.strip()
        if not name or len(name) > 100:
            raise HTTPException(status_code=400, detail="请填写站点名称（100 字以内）")
        if not url.startswith(("http://", "https://")) or len(url) > 500:
            raise HTTPException(status_code=400, detail="请填写以 http(s):// 开头的站点地址")
        models = (body.models or "").strip() or None
        if models and len(models) > 500:
            raise HTTPException(status_code=400, detail="模型列表最长 500 字")
        contact = (body.contact or "").strip() or None
        if contact and len(contact) > 100:
            raise HTTPException(status_code=400, detail="联系方式最长 100 字")
        now = time.time()
        ip = client_ip(request)
        recent = [t for t in submit_hits.get(ip, []) if now - t < 60]
        if len(recent) >= SUBMIT_MAX_PER_MINUTE:
            raise HTTPException(status_code=429, detail="提交过于频繁，请稍后再试")
        recent.append(now)
        submit_hits[ip] = recent
        store.add_site_submission(name=name, url=url, models=models, contact=contact, ip=ip)
        settings = config_from_store(store).settings
        if settings.wxpusher_app_token:
            try:
                wxpusher.send_wxpusher(
                    app_token=settings.wxpusher_app_token,
                    content=(
                        f"【LLM 价格监控】收到新的站点提交\n站点：{name}\n地址：{url}\n"
                        f"模型：{models or '未填'}\n联系方式：{contact or '未留'}"
                    ),
                    summary=f"新站点提交：{name}",
                    uid=settings.wxpusher_uid,
                )
            except Exception:
                pass  # 尽力而为：推送失败不阻塞访客提交
        return {"ok": True}

    # 管理端接口挂在 /api/admin 前缀下：公开写路径集合按路径放行，管理接口必须与之分开
    @router.get("/api/admin/site-submissions")
    def list_submissions(limit: int = 100, offset: int = 0, status: str | None = None) -> dict[str, Any]:
        """提交列表（管理员）：按时间倒序分页，可按状态过滤。"""
        if status and status not in _SUBMISSION_STATUSES:
            raise HTTPException(status_code=400, detail="未知的状态筛选")
        rows, total = store.list_site_submissions(
            limit=max(1, min(limit, 500)), offset=max(0, offset), status=status
        )
        return {"submissions": rows, "total": total}

    @router.post("/api/admin/site-submissions/{submission_id}/status")
    def mark_submission(submission_id: int, body: dict[str, Any]) -> dict[str, bool]:
        """标记处理状态（管理员）：done 表示已核验/已接入，new 表示重新打开。"""
        status = str(body.get("status") or "")
        if status not in _SUBMISSION_STATUSES:
            raise HTTPException(status_code=400, detail="状态只能是 new 或 done")
        if not store.set_site_submission_status(submission_id, status):
            raise HTTPException(status_code=404, detail="没有这条提交")
        return {"ok": True}

    return router
