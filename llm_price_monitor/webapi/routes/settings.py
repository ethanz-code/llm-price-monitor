"""系统设置端点：读取、保存与外链实测（AI / WxPusher）。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from llm_price_monitor import wxpusher
from llm_price_monitor.ai import ping_model, provider_error_detail
from llm_price_monitor.config import ai_from_raw, schedule_from_raw, settings_from_raw
from llm_price_monitor.store import Store
from llm_price_monitor.webapi.seed import MODES, apply_seed


class SettingsBody(BaseModel):
    settings: dict[str, Any] | None = None
    ai: dict[str, Any] | None = None


class SeedBody(BaseModel):
    mode: Literal["skip_existing", "overwrite"]


class SettingsTestBody(BaseModel):
    target: Literal["ai", "wxpusher"]
    settings: dict[str, Any] | None = None
    ai: dict[str, Any] | None = None


def mask_api_key(key: str) -> str:
    """密钥掩码：只保留末 4 位，用于设置接口的响应体（完整密钥不出后端）。"""
    return f"••••{key[-4:]}" if len(key) > 4 else "••••••"


def masked_ai(doc: dict[str, Any] | None) -> dict[str, Any]:
    """ai 配置文档的响应视图：api_key 替换为掩码。"""
    ai = dict(doc or {})
    key = str(ai.get("api_key") or "")
    if key:
        ai["api_key"] = mask_api_key(key)
    return ai


def merge_ai_preserving_mask(stored: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    """合并 ai 配置：入参 api_key 等于当前掩码时视为“未修改”，换回库中的完整密钥；传 null/空串表示清除。"""
    incoming = dict(incoming)
    stored_key = str((stored or {}).get("api_key") or "")
    if stored_key and incoming.get("api_key") == mask_api_key(stored_key):
        incoming["api_key"] = stored_key
    return {**(stored or {}), **incoming}


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        """管理员读取系统设置（AI / 通知等）；api_key 只回掩码，完整密钥不出后端。"""
        return {
            "settings": store.get_document("settings") or {},
            "ai": masked_ai(store.get_document("ai")),
        }

    @router.put("/api/settings")
    def update_settings(body: SettingsBody) -> dict[str, Any]:
        """合并保存系统设置；保存前用配置构建器做类型校验，非法输入返回 400。"""
        try:
            if body.settings is not None:
                merged = {**(store.get_document("settings") or {}), **body.settings}
                settings_from_raw(merged, resolve_env=False)
                if "schedule" in merged:
                    schedule_from_raw(merged["schedule"])  # 调度间隔校验：非法输入 400
                store.set_document("settings", merged)
            if body.ai is not None:
                merged_ai = merge_ai_preserving_mask(store.get_document("ai"), body.ai)
                ai_from_raw(merged_ai, cache=None)
                store.set_document("ai", merged_ai)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"settings": store.get_document("settings") or {}, "ai": masked_ai(store.get_document("ai"))}

    @router.post("/api/seed")
    def reseed(request: Request, body: SeedBody) -> dict[str, Any]:
        """手动重新导入种子文件：skip_existing 只补库中缺失项，overwrite 用种子值覆盖同名配置。"""
        seed_path: Path = request.app.state.config_path
        if not seed_path.exists():
            raise HTTPException(status_code=400, detail=f"种子文件不存在：{seed_path}")
        try:
            raw = json.loads(seed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"种子文件 {seed_path} 不是合法 JSON：{exc}") from exc
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail=f"种子文件 {seed_path} 格式不正确")
        try:
            return apply_seed(store, raw, body.mode)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/settings/test")
    def test_settings(body: SettingsTestBody) -> dict[str, Any]:
        """用表单当前值合并覆盖已存配置，实测一条外部链路（先测后存）；失败返回 400 与原因。"""
        merged_settings = {**(store.get_document("settings") or {}), **(body.settings or {})}
        merged_ai = merge_ai_preserving_mask(store.get_document("ai"), body.ai or {})

        started = time.monotonic()

        def elapsed_ms() -> int:
            return round((time.monotonic() - started) * 1000)

        try:
            if body.target == "ai":
                ai_config = ai_from_raw(merged_ai, cache=None)
                if not ai_config.base_url or not ai_config.models:
                    raise ValueError("请先填写 AI Base URL 和模型列表")
                model = ai_config.models[0]
                reply = ping_model(ai_config, model)
                return {"ok": True, "elapsed_ms": elapsed_ms(), "model": model, "reply": reply}
            token = str(merged_settings.get("wxpusher_app_token") or "").strip()
            if not token:
                raise ValueError("请先填写 WxPusher App Token")
            wxpusher.send_wxpusher(
                app_token=token,
                content="【LLM 价格监控】这是一条测试推送，收到即表示通知配置有效。",
                summary="测试推送",
                uid=str(merged_settings.get("wxpusher_uid") or "").strip() or None,
            )
            return {"ok": True, "elapsed_ms": elapsed_ms()}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="目标服务返回了非 JSON 响应，请检查地址是否正确") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=400, detail=f"目标服务返回了错误：{provider_error_detail(exc.response)}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=400, detail=f"连接目标服务失败: {exc}") from exc

    return router
