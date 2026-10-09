"""站点认证 token 续签：采集返回"需认证"时调用续签接口换取新 token 并回写配置。

配置形如（存站点 JSON 的 token_refresh 字段）：
    {
        "url": "https://totokens.cc/api/v1/auth/refresh",
        "method": "POST",
        "body": "{\"refresh_token\": \"${refresh_token}\"}",
        "refresh_token": "rt_xxx",
        "access_token_field": "data.access_token"  # 可选；管理面板保存时由 AI 从响应案例分析得出
    }
请求头 = 站点通用请求头（request_headers）打底；响应解析优先用配置的字段路径，
否则自动探测 data.access_token / access_token，响应里带新 refresh_token 时一并换新。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import httpx

from llm_price_monitor.adapters import expand_header_value
from llm_price_monitor.config import PriceMonitorError, SiteSpec
from llm_price_monitor.tracker import PriceRecord


def needs_refresh(collected: list[PriceRecord]) -> bool:
    """任一记录是"需认证"占位就触发续签：无需认证的接口可能照样拿到数据，只看全部会漏掉真过期。"""
    return any(record.requires_auth for record in collected)


def _dig_token(payload: dict[str, Any], configured: Any, fallbacks: tuple[str, ...]) -> str | None:
    """按字段路径取 token：显式配置优先（点号路径，如 data.access_token），否则按常见结构自动探测。"""
    paths: list[str] = []
    if isinstance(configured, str) and configured.strip():
        paths.append(configured.strip())
    paths.extend(fallbacks)
    for path in paths:
        value: Any = payload
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
            if value is None:
                break
        if value:
            return str(value)
    return None


def refresh_site_token(spec: SiteSpec, client: httpx.Client, timeout: float, user_agent: str) -> tuple[str, str]:
    """执行续签请求，返回 (新 access_token, 新 refresh_token)。失败抛 PriceMonitorError。"""
    config = spec.token_refresh
    method = str(config.get("method") or "POST").upper()
    url = str(config["url"]).strip()
    params = {str(k): expand_header_value(str(v)) for k, v in (config.get("params") or {}).items()}
    # 请求头 = 站点通用请求头打底，历史遗留的续签专属 headers 可覆盖
    extra_headers = {
        **{str(k): expand_header_value(str(v)) for k, v in spec.request_headers.items()},
        **{str(k): expand_header_value(str(v)) for k, v in (config.get("headers") or {}).items()},
    }
    body_template = config.get("body")
    kwargs: dict[str, Any] = {"params": params, "headers": extra_headers, "timeout": timeout}
    if body_template is not None and method != "GET":
        kwargs["content"] = body_template.replace("${refresh_token}", str(config.get("refresh_token") or ""))
        kwargs["headers"] = {**extra_headers, **({} if extra_headers else {"content-type": "application/json"})}
    response = client.request(method, url, **kwargs)
    if response.status_code in {401, 403}:
        raise PriceMonitorError(f"续签接口返回 HTTP {response.status_code}，refresh_token 可能已失效")
    response.raise_for_status()
    payload = response.json() if "json" in response.headers.get("content-type", "") else {}
    if not isinstance(payload, dict):
        raise PriceMonitorError("续签接口响应不是 JSON 对象")
    access_token = _dig_token(payload, config.get("access_token_field"), ("data.access_token", "access_token"))
    if not access_token:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        message = payload.get("message") or data.get("message")
        raise PriceMonitorError(f"续签接口响应中没有 access_token{f'：{message}' if message else ''}")
    new_refresh = _dig_token(payload, config.get("refresh_token_field"), ("data.refresh_token", "refresh_token"))
    return str(access_token), str(new_refresh or config.get("refresh_token") or "")


def refreshed_spec(spec: SiteSpec, access_token: str, refresh_token: str) -> SiteSpec:
    """新 token 写入 auth_token 和写死在采集地址 headers 里的认证头，本次采集立即可用。"""
    spec = _with_access_token(spec, access_token)
    return replace(spec, token_refresh={**spec.token_refresh, "refresh_token": refresh_token})


def _retokenize_value(value: str, token: str) -> str:
    """替换认证头的 token 部分，保留 "Bearer " 之类的前缀；Cookie 是 "name=值" 格式，保留 name=。"""
    prefix, _, rest = value.partition(" ")
    if prefix and rest:
        return f"{prefix} {token}"
    name, sep, _ = value.partition("=")
    return f"{name}{sep}{token}" if sep else token


def _synced_endpoint(endpoint: Any, token: str, auth_header: str) -> Any:
    """把 endpoint 配置 headers 里写死的认证头同步成新 token；没有认证头则原样返回。"""
    if not isinstance(endpoint, dict):
        return endpoint
    headers = endpoint.get("headers")
    if not isinstance(headers, dict):
        return endpoint
    auth_names = {auth_header.lower(), "authorization", "cookie"}
    synced = {
        key: (_retokenize_value(value, token) if key.lower() in auth_names and isinstance(value, str) else value)
        for key, value in headers.items()
    }
    return {**endpoint, "headers": synced}


def _with_access_token(spec: SiteSpec, access_token: str) -> SiteSpec:
    return replace(
        spec,
        auth_token=access_token,
        network=_synced_endpoint(spec.network, access_token, spec.auth_header),
        networks=tuple(_synced_endpoint(entry, access_token, spec.auth_header) for entry in spec.networks),
        status=_synced_endpoint(spec.status, access_token, spec.auth_header),
        notice=_synced_endpoint(spec.notice, access_token, spec.auth_header),
    )


def persist_refreshed_config(store: Any, site_id: str, access_token: str, refresh_token: str) -> None:
    """把新 token 写回数据库里的站点 JSON：auth_token 之外，写死的认证请求头也一并替换。

    部分站点把 Authorization / Cookie 直接写死在采集地址 headers 里，采集时它会覆盖
    auth_token / cookie 拼出的同名头，不同步的话续签永远"成功"但请求头还是旧 token。
    价格、渠道状态、公告地址都在同步范围内。
    """
    config = store.get_site_config(site_id)
    if config is None:
        return
    auth_header = str(config.get("auth_header") or "Authorization")
    updated = {
        **config,
        "auth_token": access_token,
        "network": _synced_endpoint(config.get("network") or {}, access_token, auth_header),
        "networks": [_synced_endpoint(entry, access_token, auth_header) for entry in (config.get("networks") or [])],
        "status": _synced_endpoint(config.get("status") or {}, access_token, auth_header),
        "notice": _synced_endpoint(config.get("notice") or {}, access_token, auth_header),
        "token_refresh": {**(config.get("token_refresh") or {}), "refresh_token": refresh_token},
    }
    store.upsert_site(site_id, updated)


def refresh_and_recollect(
    spec: SiteSpec,
    collected: list[PriceRecord],
    *,
    collect: Any,
    client: httpx.Client,
    timeout: float,
    user_agent: str,
    store: Any | None,
) -> tuple[list[PriceRecord], str | None]:
    """需要续签时调用续签接口，成功则回写配置并用新 token 重新采集一次。

    collect 是"给定 spec → 采集记录"的回调（含多地址循环）；返回 (新记录, 失败原因)。
    """
    try:
        access_token, refresh_token = refresh_site_token(spec, client, timeout, user_agent)
    except (PriceMonitorError, httpx.HTTPError, ValueError) as exc:
        return collected, f"token 续签失败：{exc}"
    if store is not None:
        persist_refreshed_config(store, spec.id, access_token, refresh_token)
    try:
        retried = collect(refreshed_spec(spec, access_token, refresh_token))
    except (PriceMonitorError, httpx.HTTPError, ValueError) as exc:
        return collected, f"token 续签成功但重新采集失败：{exc}"
    return retried, None
