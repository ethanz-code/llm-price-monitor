"""FastAPI 应用：只读数据端点 + 采集/官方价刷新后台任务 + session 登录鉴权。

路径语义与 CLI 一致：var/ 下的数据文件、config/price-monitor.json 均相对启动时的
工作目录解析，`price-web` 应在仓库根目录运行。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import asdict, replace
from pathlib import Path
from collections.abc import Callable
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from llm_price_monitor.webapi import auth, tasks
from llm_price_monitor.config import (
    MonitorConfig,
    SiteSpec,
    ai_from_raw,
    config_from_store,
    settings_from_raw,
    sites_from_raw,
)
from llm_price_monitor.env import load_env_files
from llm_price_monitor.official import fx, jsonio
from llm_price_monitor.official import search as official_search
from llm_price_monitor.official.discount import build_discount, summarize
from llm_price_monitor.official.fetch import fetch_official
from llm_price_monitor.report import attach_official_discounts, run_once, summary_row
from llm_price_monitor.store import Store

DEFAULT_CONFIG = Path("config/price-monitor.json")
OFFICIAL_FILE = Path("var/official-prices.json")
DB_PATH = Path("var/monitor.db")
AI_CACHE_FILE = Path("var/price-ai-cache.json")
LATEST_FILE = Path("var/price-latest.json")
HISTORY_FILE = Path("var/price-history.jsonl")
EVENTS_FILE = Path("var/price-events.jsonl")


class CollectBody(BaseModel):
    persist: bool = False
    site_id: str | None = None
    dry_run: bool = False


class RefreshBody(BaseModel):
    vendors: list[str] | None = None


class SettingsBody(BaseModel):
    settings: dict[str, Any] | None = None
    ai: dict[str, Any] | None = None


class SiteBody(BaseModel):
    config: dict[str, Any]


class CredentialsBody(BaseModel):
    username: str
    password: str


def _settings_seed_document(raw: dict[str, Any]) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    doc = {
        key: raw[key]
        for key in (
            "timeout",
            "user_agent",
            "random_user_agent",
            "user_agent_platforms",
            "user_agent_chrome_versions",
            "user_agent_version_window",
        )
        if key in raw
    }
    webhook = raw.get("webhook") or (os.getenv(str(raw["webhook_env"]).strip()) if raw.get("webhook_env") else None)
    if webhook:
        doc["webhook"] = webhook
    tavily = raw.get("tavily_api_key") or os.getenv("TAVILY_API_KEY")
    if tavily:
        doc["tavily_api_key"] = tavily
    return doc


def _ai_seed_document(raw: dict[str, Any]) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    doc = {
        key: raw[key]
        for key in ("enabled", "base_url", "model", "models", "timeout", "max_input_chars", "max_tokens", "enable_thinking", "dry_run")
        if key in raw
    }
    api_key = raw.get("api_key") or (os.getenv(str(raw["api_key_env"]).strip()) if raw.get("api_key_env") else None)
    if api_key:
        doc["api_key"] = api_key
    return doc


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _seed_store(store: Store, config_path: Path) -> None:
    """首次启动（空库）把配置文件与 var/ 存量文件导入数据库；此后数据库是唯一真相源。"""
    if store.get_document("seeded") is not None:
        return
    raw: dict[str, Any] = {}
    if config_path.exists():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"配置文件 {config_path} 格式不正确")

    store.replace_sites([item for item in raw.get("sites", []) if isinstance(item, dict)])
    store.set_document("settings", _settings_seed_document(raw.get("settings", {})))
    store.set_document("ai", _ai_seed_document(raw.get("ai", {})))
    config_from_store(store)  # 立即校验导入结果，坏配置在启动期报错

    # 旧配置的 settings 里可自定义数据文件路径；未配置时用标准名
    settings_raw = raw.get("settings", {}) if isinstance(raw.get("settings"), dict) else {}
    latest_path = Path(str(settings_raw.get("latest_file") or LATEST_FILE))
    history_path = Path(str(settings_raw.get("history_file") or HISTORY_FILE))
    events_path = Path(str(settings_raw.get("event_file") or EVENTS_FILE))

    if OFFICIAL_FILE.exists():
        store.set_document("official_prices", jsonio.read_json(OFFICIAL_FILE))
    if AI_CACHE_FILE.exists():
        store.set_document("ai_cache", jsonio.read_json(AI_CACHE_FILE))
    if latest_path.exists():
        latest = jsonio.read_json(latest_path)
        if isinstance(latest, dict):
            store.replace_latest({key: row for key, row in latest.items() if isinstance(row, dict)})
    store.append_history(_read_jsonl_rows(history_path))
    store.append_events(_read_jsonl_rows(events_path))
    store.set_document("seeded", {"at": time.time()})


def _seed_admin_from_env(store: Store) -> None:
    """兼容旧部署：设置了 PRICE_WEB_PASSWORD 且库里还没有管理员账号时，把环境凭据落库。"""
    if auth.get_admin(store) is not None or not os.getenv("PRICE_WEB_PASSWORD"):
        return
    auth.set_admin(store, os.getenv("PRICE_WEB_USERNAME", "admin"), os.getenv("PRICE_WEB_PASSWORD", ""))


def create_app(config_path: Path = DEFAULT_CONFIG) -> FastAPI:
    app = FastAPI(title="llm-price-monitor", docs_url=None, redoc_url=None)
    app.state.config_path = config_path
    store = Store(DB_PATH)
    _seed_store(store, config_path)
    _seed_admin_from_env(store)
    app.state.store = store

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _config() -> MonitorConfig:
        try:
            return config_from_store(store)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=f"加载数据库配置失败: {exc}") from exc

    def _official_models() -> tuple[dict[str, Any], dict[str, Any]]:
        """返回 (官方价模型表, 元信息)；数据库无官方价文档时为空表。"""
        report = store.get_document("official_prices")
        if not report:
            return {}, {}
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

    def _is_admin(request: Request) -> bool:
        """会话 cookie 有效即为管理员；首次启动（无管理员账号）时人人未登录。"""
        return auth.session_username(store, request.cookies.get(auth.SESSION_COOKIE)) is not None

    # 管理员专属的读路径（其余 GET 公开浏览）；写方法一律需要管理员
    ADMIN_GET_PATHS = {"/api/settings", "/api/sites"}
    SETUP_PATH = "/api/setup"
    # 登录/登出/首次设置本身必须是公开写接口，否则永远进不了门
    PUBLIC_WRITE_PATHS = {SETUP_PATH, "/api/auth/login", "/api/auth/logout"}

    @app.middleware("http")
    async def _admin_gate(request: Request, call_next: Any) -> Response:
        """读接口公开浏览；写操作与管理设置读取需要管理员会话。

        首次启动（尚未创建管理员账号）只放行 /api/setup，其余写接口一律 401。
        """
        needs_admin = (
            request.method in {"POST", "PUT", "PATCH", "DELETE"} or request.url.path in ADMIN_GET_PATHS
        ) and request.url.path not in PUBLIC_WRITE_PATHS
        if needs_admin:
            has_admin = auth.get_admin(store) is not None
            # 首次启动只放行 /api/setup；其余写接口一律 401
            if not has_admin and request.url.path != SETUP_PATH:
                return Response(status_code=401)
            if has_admin and not _is_admin(request):
                return Response(status_code=401)
        return await call_next(request)

    @app.post("/api/setup")
    def setup(body: CredentialsBody, response: Response) -> dict[str, bool]:
        """首次设置：创建管理员账号并直接登录；账号已存在时拒绝。"""
        if auth.get_admin(store) is not None:
            raise HTTPException(status_code=409, detail="管理员账号已存在，请直接登录")
        try:
            username = auth.validate_username(body.username)
            auth.validate_password(body.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        auth.set_admin(store, username, body.password)
        response.set_cookie(
            auth.SESSION_COOKIE,
            auth.issue_session(store, username),
            max_age=auth.SESSION_TTL,
            httponly=True,
            samesite="lax",
        )
        return {"is_admin": True}

    @app.post("/api/auth/login")
    def login(body: CredentialsBody, response: Response) -> dict[str, bool]:
        """账号密码登录，签发 30 天有效期的 HttpOnly 会话 cookie。"""
        admin = auth.get_admin(store)
        if admin is None:
            raise HTTPException(status_code=400, detail="尚未创建管理员账号，请先完成首次设置")
        if body.username.strip() != admin["username"] or not auth.verify_password(body.password, admin["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码不正确")
        response.set_cookie(
            auth.SESSION_COOKIE,
            auth.issue_session(store, admin["username"]),
            max_age=auth.SESSION_TTL,
            httponly=True,
            samesite="lax",
        )
        return {"is_admin": True}

    @app.post("/api/auth/logout")
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"is_admin": False}

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        """管理员读取系统设置（AI / Tavily / webhook 等，密钥为明文）。"""
        return {
            "settings": store.get_document("settings") or {},
            "ai": store.get_document("ai") or {},
        }

    @app.put("/api/settings")
    def update_settings(body: SettingsBody) -> dict[str, Any]:
        """合并保存系统设置；保存前用配置构建器做类型校验，非法输入返回 400。"""
        try:
            if body.settings is not None:
                merged = {**(store.get_document("settings") or {}), **body.settings}
                settings_from_raw(merged, resolve_env=False)
                store.set_document("settings", merged)
            if body.ai is not None:
                merged_ai = {**(store.get_document("ai") or {}), **body.ai}
                ai_from_raw(merged_ai, resolve_env=False, cache=None)
                store.set_document("ai", merged_ai)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"settings": store.get_document("settings") or {}, "ai": store.get_document("ai") or {}}

    def _validated_site_config(config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(config, dict) or not str(config.get("id") or "").strip():
            raise ValueError("站点必须提供非空 id")
        unknown = sorted(set(config) - set(SiteSpec.__dataclass_fields__))
        if unknown:
            raise ValueError(f"站点配置包含未知字段: {', '.join(unknown)}")
        sites_from_raw([config])  # 结构校验：network / models / headers 等
        return config

    @app.get("/api/sites")
    def list_sites() -> dict[str, Any]:
        return {"sites": store.list_site_configs()}

    @app.post("/api/sites")
    def create_site(body: SiteBody) -> dict[str, Any]:
        try:
            config = _validated_site_config(body.config)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        site_id = str(config["id"]).strip()
        if store.get_site_config(site_id) is not None:
            raise HTTPException(status_code=409, detail=f"站点已存在: {site_id}")
        store.upsert_site(site_id, config)
        return {"site": config}

    @app.put("/api/sites/{site_id}")
    def update_site(site_id: str, body: SiteBody) -> dict[str, Any]:
        if store.get_site_config(site_id) is None:
            raise HTTPException(status_code=404, detail=f"站点不存在: {site_id}")
        try:
            config = _validated_site_config(body.config)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        new_id = str(config["id"]).strip()
        if new_id != site_id and store.get_site_config(new_id) is not None:
            raise HTTPException(status_code=409, detail=f"目标站点 id 已存在: {new_id}")
        if new_id != site_id:
            store.delete_site(site_id)
        store.upsert_site(new_id, config)
        return {"site": config}

    @app.delete("/api/sites/{site_id}")
    def delete_site(site_id: str) -> dict[str, Any]:
        if not store.delete_site(site_id):
            raise HTTPException(status_code=404, detail=f"站点不存在: {site_id}")
        return {"deleted": site_id}

    @app.get("/api/tasks")
    def list_tasks() -> dict[str, Any]:
        return {"tasks": tasks.recent()}

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/meta")
    def meta(request: Request) -> dict[str, Any]:
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
            "is_admin": _is_admin(request),
            "needs_setup": auth.get_admin(store) is None,
        }

    @app.get("/api/latest")
    def latest() -> dict[str, Any]:
        return store.latest_all()

    @app.get("/api/history")
    def history(limit: int = 500, site_id: str | None = None, model: str | None = None) -> dict[str, Any]:
        records, total = store.read_history(limit=limit, site_id=site_id, model=model)
        return {"records": records, "total": total}

    @app.get("/api/events")
    def events(limit: int = 200, site_id: str | None = None, kind: str | None = None) -> dict[str, Any]:
        events, total = store.read_events(limit=limit, site_id=site_id, kind=kind)
        return {"events": events, "total": total}

    @app.get("/api/official")
    def official() -> dict[str, Any]:
        report = store.get_document("official_prices")
        if not report:
            raise HTTPException(status_code=404, detail="官方价数据不存在，请先在管理面板触发刷新")
        return report

    @app.get("/api/discount")
    def discount() -> dict[str, Any]:
        models, meta = _official_models()
        if not models:
            raise HTTPException(status_code=404, detail="官方价文件不存在或为空，请先获取官方价")
        latest_rows = [row for row in store.latest_all().values() if isinstance(row, dict)]
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
        for row in store.latest_all().values():
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
        if body.dry_run:
            config = replace(config, ai=replace(config.ai, dry_run=True))

        def _run() -> dict[str, Any]:
            report = run_once(config, store=store, persist=body.persist and not body.dry_run)
            output = attach_official_discounts(asdict(report), store.get_document("official_prices"))
            return {
                "records": [summary_row(row) for row in output["records"]],
                "events": [event["kind"] for event in report.events],
                "errors": report.errors,
                "persisted": body.persist and not body.dry_run,
                "official_prices": output["official_prices"],
                "ai_previews": report.ai_previews,
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
            raise HTTPException(status_code=400, detail="系统设置中 AI 未启用或未配置（Base URL 与模型列表），无法提取官方价")
        vendors = [official_search.VendorSpec(name) for name in body.vendors] if body.vendors else None

        def _run() -> dict[str, Any]:
            output = fetch_official(
                config,
                previous=store.get_document("official_prices"),
                tavily_key=config.settings.tavily_api_key,
                vendors=vendors,
            )
            store.set_document("official_prices", output)
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
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument("--with-frontend", action="store_true", help="同时以生产模式拉起 web/ 下的 Next.js 服务（需先 npm run build）")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="开发模式：uvicorn 热重载（改 Python 自动重启）+ Next.js dev 前端热加载，隐含 --with-frontend；与 --config 互斥",
    )
    args = parser.parse_args()

    if args.dev and args.config != str(DEFAULT_CONFIG):
        raise SystemExit("--dev 模式不支持 --config（热重载子进程只按默认路径加载配置）")

    frontend: subprocess.Popen | None = None
    if args.with_frontend or args.dev:
        web_dir = Path("web")
        if args.dev:
            command = ["npm", "run", "dev", "--", "-p", str(args.frontend_port)]
        else:
            if not (web_dir / ".next" / "BUILD_ID").exists():
                raise SystemExit("web/.next 不存在，请先在 web/ 下执行 npm run build")
            command = ["npm", "run", "start", "--", "-p", str(args.frontend_port)]
        # 前端 /api 反代目标跟随本次后端地址，保证 --port 换端口时整条链路一致
        env = {**os.environ, "PRICE_WEB_API_URL": f"http://{args.host}:{args.port}"}
        frontend = subprocess.Popen(command, cwd=web_dir, env=env)
        print(f"前端已启动: http://localhost:{args.frontend_port}（API: http://{args.host}:{args.port}）", flush=True)

    try:
        if args.dev:
            # 热重载要求 import string；.env 已在父进程注入 os.environ，子进程会继承
            uvicorn.run(
                "llm_price_monitor.webapi.app:create_app",
                factory=True,
                host=args.host,
                port=args.port,
                reload=True,
                reload_dirs=["llm_price_monitor"],
            )
        else:
            uvicorn.run(create_app(Path(args.config)), host=args.host, port=args.port)
    finally:
        if frontend:
            frontend.terminate()
