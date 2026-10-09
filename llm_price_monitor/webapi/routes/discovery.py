"""站点发现端点：把 price-discover 的产出展示成「新站发现」，并提供管理操作。

- GET /api/discovery（公开访客接口）：以候选池（var/discovery/candidates.json，导航源收录的
  站点与简介）为主表，探测档案（probed.json，只来自 CLI probe）按 URL 联表补充价格接口
  状态——没探过的标 unknown，探过但失联的不进列表只留计数。价格接口只是展示字段，
  不作为筛选门槛。
- POST /api/discovery/refresh（管理员）：后台任务拉 zuiquanapi 源合并候选池，只拿站点与
  简介不探测，秒级完成。
- POST /api/discovery/import（管理员）：把勾选的候选站写入站点库（默认停用，只写站点
  入口地址；公告等采集细节启用前在编辑里配，公告地址缺省时采集会自动推导）。
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from llm_price_monitor import discover
from llm_price_monitor.store import Store
from llm_price_monitor.webapi import tasks
from llm_price_monitor.webapi.jobs import discovery_refresh_job


class DiscoveryImportBody(BaseModel):
    hosts: list[str] = Field(min_length=1, max_length=500, description="候选站域名列表（/api/discovery 的 host 字段）")
    enabled: bool = False


def _row_online(row: dict[str, Any]) -> bool:
    return bool(row.get("online", row.get("pricing_ok")))


def _load_discovery() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str]:
    """读候选池与探测档案，返回（候选列表, 档案按 URL 索引, 池子更新时间）；档案缺失按从未探测处理。"""
    pool_file = discover.OUT_DIR / "candidates.json"
    if not pool_file.exists():
        raise HTTPException(status_code=404, detail="还没跑过站点发现，点「刷新发现」拉取站点清单")
    try:
        payload = json.loads(pool_file.read_text(encoding="utf-8"))
        pool = [cand for cand in payload.get("candidates") or [] if isinstance(cand, dict) and cand.get("url")]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"发现数据文件损坏，重新刷新即可：{exc}") from exc
    generated_at = str(payload.get("generated_at") or "")
    archive: dict[str, dict[str, Any]] = {}
    probed_file = discover.OUT_DIR / "probed.json"
    if probed_file.exists():
        try:
            rows = json.loads(probed_file.read_text(encoding="utf-8")).get("results") or []
            archive = {str(row.get("url") or ""): row for row in rows if isinstance(row, dict) and row.get("url")}
        except Exception:
            archive = {}  # 档案损坏不拦展示：按全部未探测处理，重跑 CLI probe 可重建
    return pool, archive, generated_at


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    def library_hosts() -> dict[str, str]:
        """库内站点：归一化域名 → 站点 id。配置兼容新结构 network.url 与存量 model_list_url。"""
        hosts: dict[str, str] = {}
        for config in store.list_site_configs():
            raw_url = config.get("network", {}).get("url") if isinstance(config.get("network"), dict) else None
            host = urlsplit(str(raw_url or config.get("model_list_url") or "")).hostname or ""
            if host:
                hosts[discover.normalize_host(host)] = str(config.get("id") or "")
        return hosts

    @router.get("/api/discovery")
    def discovery() -> dict[str, Any]:
        pool, archive, generated_at = _load_discovery()
        in_library = library_hosts()

        stations: list[dict[str, Any]] = []
        online = dead = 0
        for cand in pool:
            url = str(cand["url"])
            host = urlsplit(url).hostname or ""
            if not host:
                continue
            archive_row = archive.get(url)
            if archive_row is None:
                probe = {}  # 没探过：探测字段全部留空，pricing_state 标 unknown
            elif _row_online(archive_row):
                online += 1  # 探过且在线：展示探测结果
                probe = archive_row
            else:
                dead += 1  # 探过但失联：不进列表只留计数
                continue
            meta = cand.get("meta") or {}
            stations.append(
                {
                    "host": host,
                    "name": str(cand.get("name") or ""),
                    "url": url,
                    "sources": cand.get("sources") or [],
                    "new_api": bool(probe.get("new_api")),
                    "models": int(probe.get("models") or 0),
                    "pricing_state": "unknown"
                    if archive_row is None
                    else ("auth" if archive_row.get("auth_required") else ("public" if archive_row.get("pricing_ok") else "none")),
                    "system_name": str(probe.get("system_name") or ""),
                    "description": str(meta.get("description") or "").strip(),
                    "imported_id": in_library.get(discover.normalize_host(host)),
                }
            )
        return {
            "generated_at": generated_at,
            "summary": {
                "total": len(stations) + dead,
                "online": online,
                "unprobed": len([s for s in stations if s["pricing_state"] == "unknown"]),
                "pricing_public": len([s for s in stations if s["pricing_state"] == "public"]),
                "pricing_auth": len([s for s in stations if s["pricing_state"] == "auth"]),
                "dead": dead,
                "imported": len([s for s in stations if s["imported_id"]]),
            },
            # 未监控的排前面（运营最关心），同组里探明模型多的在前
            "stations": sorted(stations, key=lambda s: (bool(s["imported_id"]), -s["models"], s["host"])),
        }

    @router.post("/api/discovery/refresh")
    def discovery_refresh() -> dict[str, str]:
        # 任务体与后台调度共用（jobs.discovery_refresh_job）：手动点「刷新发现」与定时项同路
        try:
            return {"task_id": tasks.submit("discovery-refresh", discovery_refresh_job(store))}
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/discovery/import")
    def discovery_import(body: DiscoveryImportBody) -> dict[str, Any]:
        pool, _, _ = _load_discovery()
        pool_by_host: dict[str, dict[str, Any]] = {urlsplit(str(cand["url"])).hostname or "": cand for cand in pool}
        in_library = library_hosts()
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
            origin = str(cand["url"]).rstrip("/")
            site_id = discover.host_to_id(urlsplit(origin).hostname or raw_host, taken)
            # 最小导入：只登记站点入口地址（standard 采集的必填项）与停用态；
            # 公告地址不写，采集时自动按 站点/api/status、/api/notice 推导
            store.upsert_site(
                site_id,
                {
                    "id": site_id,
                    "network": {"url": f"{origin}/api/pricing"},
                    "enabled": bool(body.enabled),
                },
            )
            in_library[target] = site_id
            imported.append(site_id)
        return {"imported": imported, "skipped": skipped, "missing": missing}

    return router
