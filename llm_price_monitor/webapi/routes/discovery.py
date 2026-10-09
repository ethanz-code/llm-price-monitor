"""站点发现端点：把 price-discover 的产出展示成「新站发现」，并提供管理操作。

- GET /api/discovery（公开访客接口）：读 var/discovery/probed.json，列出「在线」的候选站
  （价格接口只是展示字段：公开可用/需登录/没有，不作为筛选门槛），按归一化域名标注
  「已监控/未监控」，失联站只留计数。旧数据没有 online 字段时回退按 pricing_ok 判在线。
- POST /api/discovery/refresh（管理员）：后台任务拉 zuiquanapi 源 + 探测没测过的候选。
- POST /api/discovery/import（管理员）：把勾选的候选站写入站点库（默认停用）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from llm_price_monitor import discover, tasklog
from llm_price_monitor.store import Store
from llm_price_monitor.webapi import tasks


class DiscoveryImportBody(BaseModel):
    hosts: list[str] = Field(min_length=1, max_length=500, description="候选站域名列表（/api/discovery 的 host 字段）")
    enabled: bool = False


def _row_online(row: dict[str, Any]) -> bool:
    return bool(row.get("online", row.get("pricing_ok")))


def _load_discovery() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """读探测明细与候选池，返回（probed 载荷, 池子按 host 索引）。"""
    probed_file = discover.OUT_DIR / "probed.json"
    pool_file = discover.OUT_DIR / "candidates.json"
    if not probed_file.exists():
        raise HTTPException(status_code=404, detail="还没跑过站点发现，点「刷新发现」生成数据")
    try:
        payload = json.loads(probed_file.read_text(encoding="utf-8"))
        pool = json.loads(pool_file.read_text(encoding="utf-8"))["candidates"] if pool_file.exists() else []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"发现数据文件损坏，重新刷新即可：{exc}") from exc
    by_host = {cand.get("host"): cand for cand in pool if cand.get("host")}
    return payload, by_host


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/discovery")
    def discovery() -> dict[str, Any]:
        payload, pool_by_host = _load_discovery()
        # 入库比对直接用本服务的 store（测试隔离友好），口径与 discover.normalize_host 一致；
        # 站点配置兼容新结构 network.url 与存量 model_list_url 两种字段
        in_library: dict[str, str] = {}
        for config in store.list_site_configs():
            raw_url = config.get("network", {}).get("url") if isinstance(config.get("network"), dict) else None
            host = urlsplit(str(raw_url or config.get("model_list_url") or "")).hostname or ""
            if host:
                in_library[discover.normalize_host(host)] = str(config.get("id") or "")

        # 站点简介来自候选池（导航站收录的描述），按 URL 联表
        descriptions: dict[str, str] = {}
        for cand in pool_by_host.values():
            desc = str((cand.get("meta") or {}).get("description") or "").strip()
            if desc and cand.get("url"):
                descriptions[str(cand["url"])] = desc

        stations: list[dict[str, Any]] = []
        dead = 0
        for row in payload["results"]:
            if not _row_online(row):
                dead += 1
                continue
            url = str(row.get("url") or "")
            host = urlsplit(url).hostname or ""
            stations.append(
                {
                    "host": host,
                    "name": row.get("name") or "",
                    "url": url,
                    "sources": row.get("sources") or [],
                    "new_api": bool(row.get("new_api")),
                    "models": int(row.get("models") or 0),
                    "pricing_state": "auth" if row.get("auth_required") else ("public" if row.get("pricing_ok") else "none"),
                    "system_name": str(row.get("system_name") or ""),
                    "description": descriptions.get(url, ""),
                    "imported_id": in_library.get(discover.normalize_host(host)),
                }
            )
        online = len(stations)
        return {
            "generated_at": payload.get("generated_at", ""),
            "summary": {
                "total": dead + online,
                "online": online,
                "pricing_public": len([s for s in stations if s["pricing_state"] == "public"]),
                "pricing_auth": len([s for s in stations if s["pricing_state"] == "auth"]),
                "dead": dead,
                "imported": len([s for s in stations if s["imported_id"]]),
            },
            # 未监控的排前面（运营最关心），同组里模型多的在前
            "stations": sorted(stations, key=lambda s: (bool(s["imported_id"]), -s["models"], s["host"])),
        }

    @router.post("/api/discovery/refresh")
    def discovery_refresh() -> dict[str, str]:
        def job() -> dict[str, Any]:
            tasklog.emit("开始刷新新站发现：拉取 zuiquanapi 源并探测新候选…")
            stats = asyncio.run(discover.refresh_online())
            tasklog.emit(
                f"新站发现完成：池子 {stats['pool']}（新增 {stats['pool_added']}），"
                f"本轮探测 {stats['probed_now']} 个、在线 {stats['online_now']}，累计在线 {stats['online_total']}，"
                f"待导入 {stats['importable']}"
            )
            return stats

        try:
            return {"task_id": tasks.submit("discovery-refresh", job)}
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/discovery/import")
    def discovery_import(body: DiscoveryImportBody) -> dict[str, Any]:
        payload, pool_by_host = _load_discovery()
        # 探测明细按 host 索引：导入时把已知的接口情报一并写进配置（new-api 系公告接口用 parse=status）
        probed_by_host: dict[str, dict[str, Any]] = {}
        for row in payload["results"]:
            if _row_online(row):
                probed_by_host[urlsplit(str(row.get("url") or "")).hostname or ""] = row
        # 与 GET 同口径：从本服务 store 建归一化域名 → 站点 id 映射（CLI 版 existing_site_hosts
        # 读固定路径，服务/测试环境库不在那）
        in_library: dict[str, str] = {}
        for config in store.list_site_configs():
            raw_url = config.get("network", {}).get("url") if isinstance(config.get("network"), dict) else None
            host = urlsplit(str(raw_url or config.get("model_list_url") or "")).hostname or ""
            if host:
                in_library[discover.normalize_host(host)] = str(config.get("id") or "")
        taken: set[str] = set()
        imported: list[str] = []
        skipped: list[str] = []
        missing: list[str] = []
        for raw_host in body.hosts:
            target = discover.normalize_host(raw_host)
            if target in in_library:
                skipped.append(raw_host)
                continue
            cand = pool_by_host.get(raw_host) or next(
                (cand for host, cand in pool_by_host.items() if discover.normalize_host(host) == target), None
            )
            if cand is None:
                missing.append(raw_host)
                continue
            origin = str(cand.get("url") or f"https://{raw_host}").rstrip("/")
            site_id = discover.host_to_id(urlsplit(origin).hostname or raw_host, taken)
            probed = probed_by_host.get(urlsplit(origin).hostname or "")
            notice: dict[str, Any] = {"url": f"{origin}/api/status"}
            if probed and probed.get("new_api"):
                # new-api 的 /api/status 自带 announcements（parse=status），导入即配好公告采集
                notice["parse"] = "status"
            store.upsert_site(
                site_id,
                {
                    "id": site_id,
                    "network": {"url": f"{origin}/api/pricing"},
                    "notice": notice,
                    "enabled": bool(body.enabled),
                },
            )
            in_library[target] = site_id
            imported.append(site_id)
        return {"imported": imported, "skipped": skipped, "missing": missing}

    return router
