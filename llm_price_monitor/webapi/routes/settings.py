"""系统设置端点：读取、保存与外链实测（AI / WxPusher），以及模型池批量体检。"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
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

# 模型池体检：单模型 15 秒内没答完按失败计；单次请求上限 40 个，前端分批调
PROBE_TIMEOUT_SECONDS = 15.0
PROBE_MAX_BATCH = 40

# 备用代理实测：204 端点判连通（轻、稳、大陆直连必失败，正好证明代理在工作），
# 出口 IP 尽力而为（拿不到不影响连通结论）
PROXY_TEST_TIMEOUT_SECONDS = 12.0
# 侧栏状态条的探测超时：给页面加载用的，比手动测试更短
PROXY_STATUS_TIMEOUT_SECONDS = 8.0
_PROXY_TEST_URL = "https://www.gstatic.com/generate_204"
_PROXY_EXIT_IP_URL = "https://api.ipify.org?format=json"


def probe_proxy(
    proxy_url: str, *, transport: httpx.BaseTransport | None = None, timeout: float = PROXY_TEST_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """从服务器出发经代理实测出口：连通判定 + 尽力获取出口 IP；失败抛 ValueError。
    transport 仅供测试注入请求替身，注入时不带 proxy 参数（两者互斥）。"""
    client_kwargs: dict[str, Any] = {"timeout": timeout, "trust_env": False}
    if transport is not None:
        client_kwargs["transport"] = transport
    else:
        client_kwargs["proxy"] = proxy_url
    try:
        with httpx.Client(**client_kwargs) as client:
            started = time.monotonic()
            response = client.get(_PROXY_TEST_URL)
            response.raise_for_status()
            elapsed = round((time.monotonic() - started) * 1000)
            try:
                exit_ip = str(client.get(_PROXY_EXIT_IP_URL).json().get("ip") or "") or None
            except (httpx.HTTPError, ValueError):
                exit_ip = None
    except httpx.HTTPError as exc:
        raise ValueError(f"经代理连不通外网（检查代理客户端是否在跑、地址是否可达）：{exc}") from exc
    return {"ok": True, "elapsed_ms": elapsed, "exit_ip": exit_ip}


class SettingsBody(BaseModel):
    settings: dict[str, Any] | None = None
    ai: dict[str, Any] | None = None


class SeedBody(BaseModel):
    mode: Literal["skip_existing", "overwrite"]


class SettingsTestBody(BaseModel):
    target: Literal["ai", "wxpusher", "proxy"]
    settings: dict[str, Any] | None = None
    ai: dict[str, Any] | None = None


class ModelsProbeBody(BaseModel):
    models: list[str]
    reset: bool = False


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


def track_monitor_model_removals(store: Store, incoming: dict[str, Any]) -> None:
    """保存监控清单时记录被移除的模型（monitor_models_dismissed）：
    目录刷新自动补模型时跳过它们，否则手动删掉的模型下轮又会被加回来。"""
    stored = store.get_document("settings") or {}
    old = stored.get("monitor_models") or []
    new = incoming.get("monitor_models")
    if not isinstance(new, list) or not all(isinstance(item, str) for item in new):
        return  # 形状不对交给 settings_from_raw 报 400
    dismissed = {str(item) for item in stored.get("monitor_models_dismissed") or []}
    dismissed |= {str(item) for item in old if item not in new}
    dismissed -= {item for item in new}
    incoming["monitor_models_dismissed"] = sorted(dismissed)


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

    @router.get("/api/settings/proxy-status")
    def get_proxy_status() -> dict[str, Any]:
        """管理员查看采集备用代理的实时连通状态；每次调用实发探测，结果不缓存。"""
        proxy_url = str((store.get_document("settings") or {}).get("fallback_proxy") or "").strip()
        if not proxy_url:
            return {"configured": False}
        try:
            result = probe_proxy(proxy_url, timeout=PROXY_STATUS_TIMEOUT_SECONDS)
        except ValueError as exc:
            return {"configured": True, "ok": False, "error": str(exc)}
        return {"configured": True, "ok": True, "elapsed_ms": result["elapsed_ms"], "exit_ip": result.get("exit_ip")}

    @router.put("/api/settings")
    def update_settings(body: SettingsBody) -> dict[str, Any]:
        """合并保存系统设置；保存前用配置构建器做类型校验，非法输入返回 400。"""
        try:
            if body.settings is not None:
                if "monitor_models" in body.settings:
                    track_monitor_model_removals(store, body.settings)
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
            if body.target == "proxy":
                proxy_url = str(merged_settings.get("fallback_proxy") or "").strip()
                if not proxy_url:
                    raise ValueError("请先填写备用代理地址")
                result = probe_proxy(proxy_url)
                return {"ok": True, "elapsed_ms": result["elapsed_ms"], "exit_ip": result.get("exit_ip")}
            token = str(merged_settings.get("wxpusher_app_token") or "").strip()
            if not token:
                raise ValueError("请先填写 WxPusher App Token")
            wxpusher.send_wxpusher(
                app_token=token,
                content="【大橘】这是一条测试推送，收到即表示通知配置有效。",
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

    @router.get("/api/settings/test-models")
    def get_models_probe() -> dict[str, Any]:
        """回看上一轮模型池体检结果（documents.model_probe）；没测过返回空。"""
        doc = store.get_document("model_probe")
        if not doc:
            return {"tested_at": None, "results": {}}
        return {"tested_at": doc.get("tested_at"), "results": doc.get("results", {})}

    @router.post("/api/settings/test-models")
    def probe_models(body: ModelsProbeBody) -> dict[str, Any]:
        """批量实测模型池：对每个模型发一次最小对话请求，逐个返回可用状态与报错原文。

        用已保存的配置（密钥完整不出后端）；结果合并进 documents.model_probe 供面板回看。
        不写 ai_logs——体检是探针流量，混进日志会污染按口径统计的成功率。
        """
        models = list(dict.fromkeys(item.strip() for item in body.models if item.strip()))
        if not models:
            raise HTTPException(status_code=400, detail="模型列表为空，请先填写 AI 模型列表")
        if len(models) > PROBE_MAX_BATCH:
            raise HTTPException(status_code=400, detail=f"单次最多测 {PROBE_MAX_BATCH} 个模型，请分批调用")
        try:
            ai_config = ai_from_raw(store.get_document("ai") or {}, cache=None)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not ai_config.base_url:
            raise HTTPException(status_code=400, detail="请先填写并保存 AI Base URL 再体检模型池")

        def probe_one(model: str) -> dict[str, Any]:
            started = time.monotonic()
            elapsed = lambda: round((time.monotonic() - started) * 1000)
            try:
                reply = ping_model(ai_config, model, timeout=PROBE_TIMEOUT_SECONDS)
                return {"model": model, "ok": True, "reply": reply[:80], "duration_ms": elapsed()}
            except httpx.HTTPStatusError as exc:
                return {"model": model, "ok": False, "error": provider_error_detail(exc.response), "duration_ms": elapsed()}
            except httpx.HTTPError as exc:
                return {"model": model, "ok": False, "error": f"连接失败：{exc}", "duration_ms": elapsed()}
            except ValueError as exc:
                return {"model": model, "ok": False, "error": f"返回了非 JSON 响应：{exc}", "duration_ms": elapsed()}
            except Exception as exc:  # 单个模型异常不炸整批
                return {"model": model, "ok": False, "error": str(exc), "duration_ms": elapsed()}

        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(probe_one, models))

        doc = {"tested_at": time.time(), "results": {}} if body.reset else (store.get_document("model_probe") or {"tested_at": time.time(), "results": {}})
        merged: dict[str, Any] = dict(doc.get("results") or {})
        for item in results:
            merged[item["model"]] = item
        store.set_document("model_probe", {"tested_at": time.time(), "results": merged})
        return {"results": results}

    return router
