"""采集与后台任务端点：全量采集 + 价格/渠道状态/站点公告拆分采集 + 任务状态查询。

全量采集一次跑三类（手动动作）；拆分端点与后台调度器（webapi.scheduler）共用
jobs.py 的任务体，让三类采集能按各自周期独立执行。
"""
from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from llm_price_monitor.config import MonitorConfig
from llm_price_monitor.report import attach_catalog_discounts, run_once, summary_row
from llm_price_monitor.store import Store
from llm_price_monitor.webapi import tasks
from llm_price_monitor.webapi.deps import load_config
from llm_price_monitor.webapi.jobs import notice_scan_job, price_scan_job, status_scan_job


class CollectBody(BaseModel):
    persist: bool = False
    site_id: str | None = None
    site_ids: list[str] | None = None


def _resolve_sites(config: MonitorConfig, body: CollectBody) -> MonitorConfig:
    if body.site_id:
        sites = tuple(site for site in config.sites if site.id == body.site_id)
        if not sites:
            raise HTTPException(status_code=400, detail=f"配置中不存在站点: {body.site_id}")
        # 显式单站请求视作手动测试：扫描会跳过停用站点，这里强制启用
        return replace(config, sites=(replace(sites[0], enabled=True),))
    if body.site_ids is not None:
        wanted = {site_id for site_id in body.site_ids if site_id}
        sites = tuple(site for site in config.sites if site.id in wanted and site.enabled)
        if not sites:
            raise HTTPException(status_code=400, detail="所选站点不存在或均已停用")
        # 批量采集是正式动作，只针对启用中的站点，不改变各自启用状态
        return replace(config, sites=sites)
    return config


def _full_collect_job(config: MonitorConfig, store: Store, persist: bool) -> Callable[[], dict[str, Any]]:
    """全量采集任务体：价格+渠道状态+站点公告一次跑完，结果附加官方折扣供预览。"""
    def _run() -> dict[str, Any]:
        report = run_once(config, store=store, persist=persist)
        output = attach_catalog_discounts(asdict(report), store.get_document("catalog"))
        return {
            "records": [summary_row(row) for row in output["records"]],
            "events": [event["kind"] for event in report.events],
            "errors": report.errors,
            "persisted": persist,
            "catalog": output["catalog"],
            # 公告只在内容新增/更新时产生记录；渠道状态只回传有变化的事件，无变化不展示
            "notices": [
                {"site_id": item["site_id"], "kind": item["kind"], "content": item["content"]}
                for item in report.notice_records
            ],
            # 逐站公告结果（含无变化/无接口等常态），测试弹窗据此给每站一条公告反馈
            "notice_results": [
                {"site_id": item["site_id"], "outcome": item["outcome"], "content": item["content"]}
                for item in report.notice_results
            ],
            "statuses": [
                {"site_id": event["site_id"], "kind": event["kind"], "changes": event["changes"]}
                for event in report.status_events
            ],
        }

    return _run


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    def _submit(kind: str, body: CollectBody, make_job: Callable[[MonitorConfig, Store], Callable[[], dict[str, Any]]]) -> dict[str, str]:
        config = _resolve_sites(load_config(store), body)
        try:
            task_id = tasks.submit(kind, make_job(config, store))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"task_id": task_id}

    @router.post("/api/collect")
    def collect(body: CollectBody) -> dict[str, str]:
        # 单站请求是测试采集，任务列表单独标记，不与正式全量采集混在同一类型里
        kind = "collect-test" if body.site_id else "collect"
        return _submit(kind, body, lambda config, _store: _full_collect_job(config, _store, body.persist))

    @router.post("/api/collect/price")
    def collect_price(body: CollectBody) -> dict[str, str]:
        return _submit("collect-price", body, price_scan_job)

    @router.post("/api/collect/status")
    def collect_status(body: CollectBody) -> dict[str, str]:
        return _submit("collect-status", body, status_scan_job)

    @router.post("/api/collect/notice")
    def collect_notice(body: CollectBody) -> dict[str, str]:
        return _submit("collect-notice", body, notice_scan_job)

    @router.get("/api/tasks")
    def list_tasks() -> dict[str, Any]:
        return {"tasks": tasks.recent()}

    @router.get("/api/tasks/{task_id}")
    def task_status(task_id: str) -> dict[str, Any]:
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task

    return router
