"""站点配置端点：增删改查（写入前做结构校验）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import httpx

from llm_price_monitor.ai import infer_token_fields
from llm_price_monitor.config import DEPRECATED_SITE_FIELDS, SiteSpec, config_from_store, sites_from_raw
from llm_price_monitor.store import Store
from llm_price_monitor.token_refresh import refresh_site_token


class SiteBody(BaseModel):
    config: dict[str, Any]


def _validated_site_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict) or not str(config.get("id") or "").strip():
        raise ValueError("站点必须提供非空 id")
    config = {key: value for key, value in config.items() if key not in DEPRECATED_SITE_FIELDS}
    unknown = sorted(set(config) - set(SiteSpec.__dataclass_fields__))
    if unknown:
        raise ValueError(f"站点配置包含未知字段: {', '.join(unknown)}")
    sites_from_raw([config])  # 结构校验：network / models / headers 等
    return config


def _apply_token_sample(store: Store, config: dict[str, Any]) -> str | None:
    """续签配置带响应案例时，保存现场用 AI 分析出新 token 的字段路径并写入配置。

    案例（response_sample）只用于分析，不落库；分析失败不阻塞保存，返回提示语，续签时
    会退回按常见结构自动探测。返回 None 表示一切正常。
    """
    refresh = config.get("token_refresh")
    if not isinstance(refresh, dict):
        return None
    sample = refresh.pop("response_sample", None)
    if not isinstance(sample, str) or not sample.strip():
        return None
    try:
        fields = infer_token_fields(config_from_store(store).ai, sample)
    except Exception as exc:  # noqa: BLE001 - AI 失败只降级不拦保存
        refresh.pop("access_token_field", None)
        refresh.pop("refresh_token_field", None)
        return f"响应案例分析失败（{exc}），续签时会按常见结构自动找 token"
    refresh.update(fields)
    return None


def build_router(store: Store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sites")
    def list_sites() -> dict[str, Any]:
        return {"sites": store.list_site_configs(), "collect_status": store.get_document("collect_status") or {}}

    @router.post("/api/sites")
    def create_site(body: SiteBody) -> dict[str, Any]:
        try:
            config = _validated_site_config(body.config)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        site_id = str(config["id"]).strip()
        if store.get_site_config(site_id) is not None:
            raise HTTPException(status_code=409, detail=f"站点已存在: {site_id}")
        warning = _apply_token_sample(store, config)
        store.upsert_site(site_id, config)
        return {"site": config, **({"warning": warning} if warning else {})}

    @router.put("/api/sites/{site_id}")
    def update_site(site_id: str, body: SiteBody) -> dict[str, Any]:
        old_config = store.get_site_config(site_id)
        if old_config is None:
            raise HTTPException(status_code=404, detail=f"站点不存在: {site_id}")
        try:
            config = _validated_site_config(body.config)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        new_id = str(config["id"]).strip()
        if new_id != site_id and store.get_site_config(new_id) is not None:
            raise HTTPException(status_code=409, detail=f"目标站点 id 已存在: {new_id}")
        if new_id != site_id:
            store.rename_site(site_id, new_id)
        warning = _apply_token_sample(store, config)
        store.upsert_site(new_id, config)
        # 分组过滤从无到有或口径变化时，把库里未选中分组的历史状态数据一并清掉（不可逆）
        cleaned: dict[str, int] | None = None
        new_groups = _status_groups(config)
        if new_groups and new_groups != _status_groups(old_config):
            cleaned = store.prune_status_history(new_id, new_groups)
        response: dict[str, Any] = {"site": config}
        if warning:
            response["warning"] = warning
        if cleaned:
            response["cleaned"] = cleaned
        return response

    @router.post("/api/sites/test-token-refresh")
    def test_token_refresh(body: SiteBody) -> dict[str, Any]:
        """用当前填写的续签配置真实调用一次续签接口，验证地址、凭证和响应结构都能对上。

        成功返回完整的新 token，由前端回填编辑表单，保存后生效；这里不写库。
        注意部分站点的 refresh_token 是一次性的，测试会消耗掉一次换新，
        所以必须回填后再保存，否则下次续签会拿着已失效的旧 token 去撞。
        """
        try:
            spec: SiteSpec = sites_from_raw([{k: v for k, v in body.config.items() if k in SiteSpec.__dataclass_fields__}])[0]
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not spec.token_refresh.get("url"):
            raise HTTPException(status_code=400, detail="先填续签接口地址再测试")
        settings = config_from_store(store).settings
        try:
            with httpx.Client(timeout=settings.timeout, headers={"user-agent": settings.user_agent}) as http:
                access_token, refresh_token = refresh_site_token(spec, http, settings.timeout, settings.user_agent)
        except Exception as exc:  # noqa: BLE001 - 测试端点把任何失败都转成可读提示
            raise HTTPException(status_code=400, detail=f"续签测试失败：{exc}") from exc

        return {
            "ok": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "refresh_token_rotated": refresh_token != spec.token_refresh.get("refresh_token"),
        }

    @router.delete("/api/sites/{site_id}")
    def delete_site(site_id: str, purge: bool = False) -> dict[str, Any]:
        """purge 为真时同时清理该站点的历史价格、事件与状态数据（删除弹窗里的可选项）。"""
        if not store.delete_site(site_id, purge=purge):
            raise HTTPException(status_code=404, detail=f"站点不存在: {site_id}")
        return {"deleted": site_id, "purged": purge}

    return router


def _status_groups(config: dict[str, Any]) -> list[str]:
    groups = (config.get("status") or {}).get("groups")
    return [str(item) for item in groups] if isinstance(groups, list) else []
