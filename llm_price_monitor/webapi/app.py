"""FastAPI 应用：只读数据端点 + 采集/官方价刷新后台任务 + 可选 Basic Auth。

路径语义与 CLI 一致：var/ 下的数据文件、config/price-monitor.json 均相对启动时的
工作目录解析，`price-web` 应在仓库根目录运行。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import subprocess
from dataclasses import replace
from pathlib import Path
from collections.abc import Callable
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from llm_price_monitor.webapi import tasks
from llm_price_monitor.config import MonitorConfig, MonitorSettings, load_config
from llm_price_monitor.env import load_env_files
from llm_price_monitor.official import fx, jsonio
from llm_price_monitor.official import search as official_search
from llm_price_monitor.official.discount import build_discount, compute_discounts, summarize
from llm_price_monitor.official_cli import fetch_official
from llm_price_monitor.report import run_once, summary_row

DEFAULT_CONFIG = Path("config/price-monitor.json")


class CollectBody(BaseModel):
    persist: bool = False
    site_id: str | None = None


class RefreshBody(BaseModel):
    vendors: list[str] | None = None


def create_app(config_path: Path = DEFAULT_CONFIG) -> FastAPI:
    app = FastAPI(title="llm-price-monitor", docs_url=None, redoc_url=None)
    app.state.config_path = config_path

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _config() -> MonitorConfig:
        try:
            return load_config(config_path)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"加载配置 {config_path} 失败: {exc}") from exc

    def _settings() -> MonitorSettings:
        return _config().settings

    def _read_json(path: Path) -> dict[str, Any]:
        data = jsonio.read_json(path)
        if not isinstance(data, dict):
            raise HTTPException(status_code=500, detail=f"{path} 格式不正确")
        return data

    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def _official_models() -> tuple[dict[str, Any], dict[str, Any]]:
        """返回 (官方价模型表, 官方价文件自身元信息)；文件缺失时为空表。"""
        path = Path("var/official-prices.json")
        if not path.exists():
            return {}, {}
        report = _read_json(path)
        models = report.get("models")
        meta = {k: report.get(k) for k in ("generated_at", "generated_at_iso", "usd_cny_rate", "rate_source")}
        return (models if isinstance(models, dict) else {}), meta

    def _usd_cny_rate(models: dict[str, Any], meta: dict[str, Any]) -> tuple[float, str]:
        """优先实时汇率；拉取失败时回落到官方价文件缓存的汇率。"""
        try:
            with httpx.Client(follow_redirects=True, timeout=10) as client:
                return fx.get_usd_cny_rate(client)
        except ValueError:
            if meta.get("usd_cny_rate"):
                return float(meta["usd_cny_rate"]), f"{meta.get('rate_source')}（缓存）"
            raise HTTPException(status_code=503, detail="实时汇率获取失败且官方价文件中没有缓存汇率") from None

    @app.middleware("http")
    async def _basic_auth(request: Request, call_next: Any) -> Response:
        password = os.getenv("PRICE_WEB_PASSWORD")
        if password:
            username = os.getenv("PRICE_WEB_USERNAME", "admin")
            header = request.headers.get("Authorization", "")
            ok = False
            if header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(header[6:]).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    ok = False
                else:
                    user, _, supplied = decoded.partition(":")
                    ok = secrets.compare_digest(user, username) and secrets.compare_digest(supplied, password)
            if not ok:
                return Response(status_code=401, headers={"WWW-Authenticate": "Basic realm=llm-price-monitor"})
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        config = _config()
        return {
            "sites": [
                {
                    "id": site.id,
                    "adapter": site.adapter,
                    "models": [target.name for target in site.models],
                    "url": site.network.get("url"),
                    "enabled": site.enabled,
                }
                for site in config.sites
            ],
            "auth_enabled": bool(os.getenv("PRICE_WEB_PASSWORD")),
        }

    @app.get("/api/latest")
    def latest() -> dict[str, Any]:
        return _read_json(Path(_settings().latest_file))

    @app.get("/api/history")
    def history(limit: int = 500, site_id: str | None = None, model: str | None = None) -> dict[str, Any]:
        rows = _read_jsonl(Path(_settings().history_file))
        if site_id:
            rows = [row for row in rows if row.get("site_id") == site_id]
        if model:
            rows = [row for row in rows if row.get("model") == model]
        return {"records": rows[-limit:], "total": len(rows)}

    @app.get("/api/events")
    def events(limit: int = 200, site_id: str | None = None, kind: str | None = None) -> dict[str, Any]:
        rows = _read_jsonl(Path(_settings().event_file))
        if site_id:
            rows = [row for row in rows if row.get("site_id") == site_id]
        if kind:
            rows = [row for row in rows if row.get("kind") == kind]
        return {"events": rows[-limit:], "total": len(rows)}

    @app.get("/api/official")
    def official() -> dict[str, Any]:
        path = Path("var/official-prices.json")
        if not path.exists():
            raise HTTPException(status_code=404, detail="官方价文件不存在，请先运行 fetch-official-prices 或在页面触发刷新")
        return _read_json(path)

    @app.get("/api/discount")
    def discount() -> dict[str, Any]:
        models, meta = _official_models()
        if not models:
            raise HTTPException(status_code=404, detail="官方价文件不存在或为空，请先获取官方价")
        latest_rows = [row for row in _read_json(Path(_settings().latest_file)).values() if isinstance(row, dict)]
        rate, rate_source = _usd_cny_rate(models, meta)
        entries = []
        skipped: list[dict[str, Any]] = []
        for row in latest_rows:
            entry, reason = build_discount(summary_row(row), models, rate)
            if entry is None:
                skipped.append({"site_id": row.get("site_id"), "model": row.get("model"), "reason": reason})
            else:
                entries.append(entry)
        discounts: list[dict[str, Any]] = []
        for entry in entries:
            item = entry.as_dict()
            item.update({"site_id": entry.site_id, "model": entry.model, "group": entry.group})
            discounts.append(item)
        return {
            "usd_cny_rate": round(rate, 2),
            "rate_source": rate_source,
            "official_generated_at": meta.get("generated_at_iso"),
            "discounts": discounts,
            "summary": summarize(entries),
            "skipped": skipped,
        }

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        """首页数据：最新快照逐条附加官方价折扣。"""
        models, meta = _official_models()
        discount_of: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None
        if models:
            rate, _rate_source = _usd_cny_rate(models, meta)

            def discount_of(row: dict[str, Any]) -> dict[str, Any] | None:
                entry, _reason = build_discount(summary_row(row), models, rate)
                return entry.as_dict() if entry is not None else None

        records: list[dict[str, Any]] = []
        for row in _read_json(Path(_settings().latest_file)).values():
            if not isinstance(row, dict):
                continue
            records.append({**row, "discount": discount_of(row) if discount_of else None})
        return {
            "records": records,
            "official": {
                **meta,
                "enabled": bool(models),
            },
        }

    @app.post("/api/collect")
    def collect(body: CollectBody) -> dict[str, str]:
        config = _config()
        if body.site_id:
            sites = tuple(site for site in config.sites if site.id == body.site_id)
            if not sites:
                raise HTTPException(status_code=400, detail=f"配置中不存在站点: {body.site_id}")
            config = replace(config, sites=sites)

        def _run() -> dict[str, Any]:
            report = run_once(config, persist=body.persist)
            return {
                "records": len(report.records),
                "events": [event["kind"] for event in report.events],
                "errors": report.errors,
                "persisted": body.persist,
            }

        try:
            task_id = tasks.submit("collect", _run)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"task_id": task_id}

    @app.post("/api/official/refresh")
    def official_refresh(body: RefreshBody) -> dict[str, str]:
        config = _config()
        if not config.ai.enabled or not config.ai.base_url or not config.ai.pick_model():
            raise HTTPException(status_code=400, detail="配置文件 ai 段未启用或未配置，无法提取官方价")
        output_path = Path("var/official-prices.json")
        vendors = [official_search.VendorSpec(name) for name in body.vendors] if body.vendors else None

        def _run() -> dict[str, Any]:
            output = fetch_official(config, output_path, vendors=vendors)
            return {
                "models_total": len(output["models"]),
                "models_found": sum(1 for entry in output["models"].values() if entry.get("found")),
            }

        try:
            task_id = tasks.submit("official-refresh", _run)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"task_id": task_id}

    @app.get("/api/tasks/{task_id}")
    def task_status(task_id: str) -> dict[str, Any]:
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task

    return app


def main() -> None:
    load_env_files()
    parser = argparse.ArgumentParser(description="启动 llm-price-monitor Web 服务（API + 前端）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--with-frontend", action="store_true", help="同时以生产模式拉起 web/ 下的 Next.js 服务（需先 npm run build）")
    args = parser.parse_args()

    frontend: subprocess.Popen | None = None
    if args.with_frontend:
        web_dir = Path("web")
        if not (web_dir / ".next" / "BUILD_ID").exists():
            raise SystemExit("web/.next 不存在，请先在 web/ 下执行 npm run build")
        frontend = subprocess.Popen(["npm", "run", "start"], cwd=web_dir)
        print(f"前端已启动: http://localhost:3000（API: http://{args.host}:{args.port}）", flush=True)

    try:
        uvicorn.run(create_app(Path(args.config)), host=args.host, port=args.port)
    finally:
        if frontend:
            frontend.terminate()
