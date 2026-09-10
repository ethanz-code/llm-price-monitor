"""FastAPI 应用组装：种子导入、鉴权中间件与按域挂载的 APIRouter（webapi/routes/）。

路径语义与 CLI 一致：var/ 下的数据文件、config/default-seed.json 均相对启动时的
工作目录解析，`price-web` 应在仓库根目录运行。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from llm_price_monitor.browser_setup import ensure_browser_ready
from llm_price_monitor.catalog import jsonio
from llm_price_monitor.config import DEFAULT_SCHEDULE_MINUTES, config_from_store
from llm_price_monitor import ai
from llm_price_monitor.store import Store
from llm_price_monitor.webapi import auth, routes, scheduler, tasks
from llm_price_monitor.webapi.deps import is_admin

DEFAULT_CONFIG = Path("config/default-seed.json")  # 首次启动（空库）的种子文件，此后数据库是唯一真相源
CATALOG_FILE = Path("var/catalog.json")
DB_PATH = Path("var/monitor.db")  # 可用 PRICE_MONITOR_DB 覆盖（测试用它把库隔离到临时目录）
AI_CACHE_FILE = Path("var/price-ai-cache.json")
LATEST_FILE = Path("var/price-latest.json")
HISTORY_FILE = Path("var/price-history.jsonl")
EVENTS_FILE = Path("var/price-events.jsonl")


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
            "retention_price_days",
            "retention_visit_days",
            "retention_status_days",
            "schedule",
        )
        if key in raw
    }
    return doc


def _ai_seed_document(raw: dict[str, Any]) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    doc = {
        key: raw[key]
        for key in ("enabled", "base_url", "model", "models", "timeout", "max_input_chars", "max_tokens", "enable_thinking")
        if key in raw
    }
    if raw.get("api_key"):
        doc["api_key"] = raw["api_key"]
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

    if CATALOG_FILE.exists():
        store.set_document("catalog", jsonio.read_json(CATALOG_FILE))
    if AI_CACHE_FILE.exists():
        store.set_document("ai_cache", jsonio.read_json(AI_CACHE_FILE))
    if latest_path.exists():
        latest = jsonio.read_json(latest_path)
        if isinstance(latest, dict):
            store.replace_latest({key: row for key, row in latest.items() if isinstance(row, dict)})
    store.append_history(_read_jsonl_rows(history_path))
    store.append_events(_read_jsonl_rows(events_path))
    store.set_document("seeded", {"at": time.time()})


def _ensure_schedule_defaults(store: Store) -> None:
    """settings 里缺 schedule（从未在设置页保存过调度间隔）时补默认值，让设置页能显示数字；已有则不动。"""
    settings = store.get_document("settings") or {}
    if "schedule" in settings:
        return
    settings["schedule"] = dict(DEFAULT_SCHEDULE_MINUTES)
    store.set_document("settings", settings)


def _seed_admin_from_env(store: Store) -> None:
    """兼容旧部署：设置了 PRICE_WEB_PASSWORD 且库里还没有管理员账号时，把环境凭据落库。"""
    if auth.get_admin(store) is not None or not os.getenv("PRICE_WEB_PASSWORD"):
        return
    auth.set_admin(store, os.getenv("PRICE_WEB_USERNAME", "admin"), os.getenv("PRICE_WEB_PASSWORD", ""))


def create_app(config_path: Path = DEFAULT_CONFIG) -> FastAPI:
    app = FastAPI(title="llm-price-monitor", docs_url=None, redoc_url=None)
    app.state.config_path = config_path
    # 创建时才解析路径：create_app 常在测试中被 monkeypatch.chdir 包裹，模块级常量会绑错目录
    store = Store(Path(os.getenv("PRICE_MONITOR_DB") or DB_PATH))
    _seed_store(store, config_path)
    _ensure_schedule_defaults(store)
    _seed_admin_from_env(store)
    tasks.attach_store(store)  # 历史任务连同日志落 SQLite，重启后仍可查看
    app.state.store = store

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 管理员专属的读路径（其余 GET 公开浏览）；写方法一律需要管理员。
    # /api/tasks/{task_id} 为动态路径，另行按前缀匹配
    admin_get_paths = {"/api/settings", "/api/sites", "/api/docs/readme", "/api/tasks", "/api/analytics/summary", "/api/analytics/logs", "/api/ai-logs"}
    admin_get_prefixes = ("/api/tasks/",)

    @app.middleware("http")
    async def _admin_gate(request: Request, call_next: Any) -> Response:
        """读接口公开浏览；写操作与管理设置读取需要管理员会话。

        首次启动（尚未创建管理员账号）只放行 /api/setup，其余写接口一律 401。
        """
        needs_admin = (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            or request.url.path in admin_get_paths
            or request.url.path.startswith(admin_get_prefixes)
        ) and request.url.path not in routes.auth.PUBLIC_WRITE_PATHS
        if needs_admin:
            has_admin = auth.get_admin(store) is not None
            # 首次启动只放行 /api/setup；其余写接口一律 401
            if not has_admin and request.url.path != routes.auth.SETUP_PATH:
                return Response(status_code=401)
            if has_admin and not is_admin(store, request):
                return Response(status_code=401)
        return await call_next(request)

    app.include_router(routes.auth.build_router(store))
    app.include_router(routes.catalog.build_router(store))
    app.include_router(routes.collect.build_router(store))
    app.include_router(routes.sites.build_router(store))
    app.include_router(routes.settings.build_router(store))
    app.include_router(routes.data.build_router(store))
    app.include_router(routes.status.build_router(store))
    app.include_router(routes.geo.build_router(store))
    app.include_router(routes.analytics.build_router(store))
    app.include_router(routes.assistant.build_router(store))
    app.include_router(routes.ai_logs.build_router(store))
    ai.ai_log_hook = store.add_ai_log  # 大模型调用统一落日志
    ensure_browser_ready(store)
    scheduler.start_scheduler(store)

    return app


def main() -> None:
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
            # 热重载要求 import string；父进程环境会被子进程继承
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
