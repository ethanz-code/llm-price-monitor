"""站点发现端点：把 price-discover 的产出对外展示成「新站发现」页。

数据来自 var/discovery/{probed,importable}.json（discover CLI 生成，本端点只读不写），
与库内 sites 按归一化域名比对标注哪些已入库。读取是访客功能，路径在 app.py 的
public_get_paths 白名单里；还没跑过发现管线时返回 404，由页面给空态引导。
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException

from llm_price_monitor import discover
from llm_price_monitor.store import Store


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/discovery")
    def discovery() -> dict[str, Any]:
        probed_file = discover.OUT_DIR / "probed.json"
        if not probed_file.exists():
            raise HTTPException(status_code=404, detail="还没跑过站点发现（管理员在服务器上执行 price-discover harvest + probe 生成数据）")
        try:
            payload = json.loads(probed_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"发现数据文件损坏，重新执行 price-discover probe 即可：{exc}") from exc

        # 站点简介来自候选池（导航站收录的描述），probe 明细里另有站名自报（system_name），按 URL 联表
        descriptions: dict[str, str] = {}
        candidates_file = discover.OUT_DIR / "candidates.json"
        if candidates_file.exists():
            try:
                for cand in json.loads(candidates_file.read_text(encoding="utf-8"))["candidates"]:
                    desc = str((cand.get("meta") or {}).get("description") or "").strip()
                    if desc and cand.get("url"):
                        descriptions[str(cand["url"])] = desc
            except Exception:
                pass

        # 入库比对直接用本服务的 store（测试隔离友好），口径与 discover.normalize_host 一致；
        # 站点配置兼容新结构 network.url 与存量 model_list_url 两种字段
        in_library: dict[str, str] = {}
        for config in store.list_site_configs():
            raw_url = config.get("network", {}).get("url") if isinstance(config.get("network"), dict) else None
            host = urlsplit(str(raw_url or config.get("model_list_url") or "")).hostname or ""
            if host:
                in_library[discover.normalize_host(host)] = str(config.get("id") or "")
        stations: list[dict[str, Any]] = []
        for row in payload["results"]:
            if row.get("pricing_ok"):
                state = "available"
            elif row.get("auth_required"):
                state = "auth"
            else:
                state = "dead"
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
                    "state": state,
                    "system_name": str(row.get("system_name") or ""),
                    "description": descriptions.get(url, ""),
                    "imported_id": in_library.get(discover.normalize_host(host)) if state != "dead" else None,
                }
            )
        available = [s for s in stations if s["state"] == "available"]
        auth = [s for s in stations if s["state"] == "auth"]
        dead = [s for s in stations if s["state"] == "dead"]
        return {
            "generated_at": payload.get("generated_at", ""),
            "summary": {
                "total": len(stations),
                "available": len(available),
                "auth": len(auth),
                "dead": len(dead),
                "imported": len([s for s in available if s["imported_id"]]),
            },
            # 对访客有信息量的是可用站（能比价、能接入），失联站只留计数
            "stations": sorted(available, key=lambda s: (-s["models"], s["host"])),
        }

    return router
