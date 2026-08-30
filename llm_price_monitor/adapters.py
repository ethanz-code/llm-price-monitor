"""价格采集适配器：直接请求配置的价格接口并计算价格记录。

不启动浏览器、不读 DOM；New API/One API 响应走确定性计价，
其余响应在配置了 AI 时交给 AIPriceExtractor。
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from llm_price_monitor.ai import AIExtractionError, AIPriceExtractor
from llm_price_monitor.config import AIConfig, ModelTarget, PriceMonitorError, SiteSpec
from llm_price_monitor.evidence import is_preferred_response_url, payload_hash, redact_url
from llm_price_monitor.tracker import (
    GroupRatioUnavailableError,
    PriceRecord,
    looks_like_newapi_pricing,
    newapi_price_record,
)

_ENV_VALUE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class Adapter(Protocol):
    def collect(
        self,
        spec: SiteSpec,
        client: httpx.Client,
        timeout: float,
        user_agent: str,
        ai: AIConfig | None = None,
    ) -> list[PriceRecord]: ...


def headers(spec: SiteSpec, user_agent: str) -> dict[str, str]:
    result = {**spec.request_headers, "user-agent": user_agent}
    if spec.auth_token:
        secret = spec.auth_token
        if not secret:
            raise PriceMonitorError("配置文件 auth_token 未配置")
        result[spec.auth_header] = f"{spec.auth_prefix}{secret}"
    if spec.cookie:
        cookie = spec.cookie
        if not cookie:
            raise PriceMonitorError("配置文件 cookie 未配置")
        result["cookie"] = cookie
    return result


def expand_header_value(value: str) -> str:
    return _ENV_VALUE_PATTERN.sub(lambda match: os.getenv(match.group(1), ""), value)


def auth_required_records(spec: SiteSpec, message: str, source_url: str | None = None) -> list[PriceRecord]:
    targets = list(spec.models) or [ModelTarget("*")]
    source_url = source_url or spec.model_list_url or ""
    metadata = {"pricing_kind": "auth_required", "currency": spec.currency, "error": message}
    return [
        PriceRecord(
            target.name,
            None,
            None,
            f"{spec.currency}/1M tokens",
            source_url,
            time.time(),
            metadata,
            "unavailable",
            True,
        )
        for target in targets
    ]


def network_pricing_records(
    spec: SiteSpec,
    captured: list[dict[str, Any]],
    resolved_aliases: dict[str, tuple[str, ...]] | None = None,
) -> list[PriceRecord]:
    """只用 HTTP JSON 响应计算价格；支持 New API 和字段映射响应。"""
    response_source_url = str(captured[0].get("url") or spec.model_list_url or "") if captured else (spec.model_list_url or "")
    ordered = sorted(
        captured,
        key=lambda item: (
            not is_preferred_response_url(
                str(item.get("url", "")), spec.preferred_response_url_patterns
            ),
        ),
    )
    records: dict[str, PriceRecord] = {}
    attempted_groups: dict[str, set[str | None]] = {target.name: set() for target in spec.models}
    failures: dict[str, list[str]] = {target.name: [] for target in spec.models}
    for response in ordered:
        payload = response.get("payload")
        if not isinstance(payload, dict):
            continue
        for target in spec.models:
            names = {
                name.casefold()
                for name in (target.name, *target.aliases, *(resolved_aliases or {}).get(target.name, ()))
            }
            item = next(
                (
                    value for value in payload.get("data", [])
                    if isinstance(value, dict) and str(value.get("model_name", "")).casefold() in names
                ),
                None,
            )
            if not isinstance(item, dict):
                continue
            if target.group:
                groups = [target.group]
            else:
                enabled = [name for name in item.get("enable_groups", []) if isinstance(name, str) and name]
                groups = enabled or [None]
            for selected_group in dict.fromkeys(groups):
                record_key = f"{target.name}:{selected_group or ''}"
                if record_key in records:
                    continue
                try:
                    record = newapi_price_record(
                        payload,
                        target.name,
                        str(response.get("url") or spec.model_list_url or ""),
                        group=selected_group,
                        aliases=tuple(dict.fromkeys((*target.aliases, *(resolved_aliases or {}).get(target.name, ())))),
                        ratio_base_price=spec.ratio_base_price,
                        currency=spec.currency,
                    )
                except GroupRatioUnavailableError:
                    # 站点未公开该分组倍率，无法计价，直接不输出该分组。
                    continue
                except ValueError as exc:
                    attempted_groups[target.name].add(selected_group)
                    failures[target.name].append(str(exc))
                    continue
                attempted_groups[target.name].add(selected_group)
                metadata = dict(record.metadata or {})
                metadata.update({
                    "adapter": "browser_network",
                    "network_evidence": [{
                        "source": "model_list",
                        "url": redact_url(str(response.get("url", ""))),
                        "resource_type": response.get("resource_type"),
                        "status": response.get("status"),
                        "payload_sha256": payload_hash(payload),
                    }],
                    "page_evidence": [],
                })
                record.metadata = metadata
                records[record_key] = record
    for target in spec.models:
        groups = attempted_groups[target.name] or {target.group}
        for selected_group in groups:
            record_key = f"{target.name}:{selected_group or ''}"
            if record_key in records:
                continue
            reason = "; ".join(dict.fromkeys(failures[target.name])) or "未捕获可识别的 one-api/new-api 定价 JSON 响应"
            records[record_key] = PriceRecord(
                target.name,
                None,
                None,
                f"{spec.currency}/1M tokens",
                response_source_url,
                time.time(),
                {
                    "adapter": "network",
                    "pricing_kind": "unavailable",
                    "group": selected_group,
                    "currency": spec.currency,
                    "network_evidence": [],
                    "page_evidence": [],
                    "notes": reason,
                },
                "unavailable",
            )
    return list(records.values())


class NetworkAdapter:
    def collect(
        self,
        spec: SiteSpec,
        client: httpx.Client,
        timeout: float,
        user_agent: str,
        ai: AIConfig | None = None,
    ) -> list[PriceRecord]:
        """直接请求配置的价格接口；不启动浏览器。"""
        if not spec.models:
            raise PriceMonitorError("不会自动检测所有模型；请在 models 中配置目标模型和 model_list_url/network.url")
        network = spec.network
        endpoint = network.get("url") if isinstance(network, dict) else None
        if not isinstance(endpoint, str) or not endpoint.strip():
            if not spec.model_list_url:
                raise PriceMonitorError(f"站点 {spec.id} 未配置 network.url 或 model_list_url")
            parsed = urlsplit(spec.model_list_url)
            endpoint = f"{parsed.scheme}://{parsed.netloc}/api/pricing"
        endpoint = urljoin(spec.model_list_url or endpoint, endpoint)
        method = str(network.get("method", "GET")).strip().upper() if isinstance(network, dict) else "GET"
        if method not in {"GET", "POST", "PUT", "PATCH"}:
            raise PriceMonitorError(f"站点 {spec.id} 的 network.method 不支持: {method}")
        params = network.get("params", {}) if isinstance(network, dict) else {}
        request_headers = network.get("headers", {}) if isinstance(network, dict) else {}
        if not isinstance(params, dict) or not isinstance(request_headers, dict):
            raise PriceMonitorError(f"站点 {spec.id} 的 network.params 和 network.headers 必须是对象")
        request_headers_dict = headers(spec, user_agent)
        request_headers_dict.update({str(key): expand_header_value(str(value)) for key, value in request_headers.items()})
        if spec.cookies and "cookie" not in request_headers_dict:
            request_headers_dict["cookie"] = "; ".join(f"{key}={value}" for key, value in spec.cookies.items())
        body = network.get("body") if isinstance(network, dict) else None
        body_type = str(network.get("body_type", "json")).strip().lower() if isinstance(network, dict) else "json"
        if body_type not in {"json", "form", "raw"}:
            raise PriceMonitorError(f"站点 {spec.id} 的 network.body_type 必须是 json、form 或 raw")
        kwargs: dict[str, Any] = {"params": params, "headers": request_headers_dict, "timeout": timeout}
        if body is not None:
            if body_type == "json":
                kwargs["json"] = body
            elif body_type == "form":
                kwargs["data"] = body
            else:
                kwargs["content"] = str(body)
        response = client.request(method, endpoint, **kwargs)
        if response.status_code in {401, 403}:
            return auth_required_records(spec, f"网络价格接口返回 HTTP {response.status_code}", str(response.url))
        response.raise_for_status()
        payload: Any = None
        response_is_json = True
        try:
            payload = response.json()
        except ValueError:
            response_is_json = False
        response_path = network.get("response_path") if isinstance(network, dict) else None
        if response_path and not response_is_json:
            raise PriceMonitorError(f"站点 {spec.id} 的 network.response_path 只能用于 JSON 响应")
        if response_path:
            for part in str(response_path).strip(".").split("."):
                if isinstance(payload, dict) and part in payload:
                    payload = payload[part]
                elif isinstance(payload, list) and part.isdigit() and int(part) < len(payload):
                    payload = payload[int(part)]
                else:
                    raise PriceMonitorError(f"站点 {spec.id} 的 network.response_path 不存在: {response_path}")
        captured = {
            "url": str(response.url),
            "status": response.status_code,
            "resource_type": "fetch",
            "source": "model_list",
            "preferred_response": True,
            "payload": payload,
            "content_type": response.headers.get("content-type", ""),
        }
        if not response_is_json:
            captured["text"] = response.text
            if ai is not None and ai.enabled and ai.base_url and ai.pick_model():
                return AIPriceExtractor(ai).extract(
                    spec, response.text, [captured], client=client,
                    expected_models=[target.name for target in spec.models],
                )
            raise PriceMonitorError(f"站点 {spec.id} 返回 HTML/文本响应，且未配置可用 AI")
        if looks_like_newapi_pricing(payload):
            direct_records = network_pricing_records(spec, [captured])
            # Always let AI inspect New API/One API model names when enabled.
            # It resolves informal configured labels to evidence-backed
            # model_name aliases; pricing itself remains deterministic.
            if ai is not None and ai.enabled and ai.base_url and ai.pick_model():
                try:
                    ai_records = AIPriceExtractor(ai).extract(
                        spec, "", [captured], client=client,
                        expected_models=[target.name for target in spec.models],
                    )
                    aliases: dict[str, tuple[str, ...]] = {}
                    for record in ai_records:
                        metadata = record.metadata or {}
                        values = [metadata.get("observed_model"), *(metadata.get("aliases") or [])]
                        aliases[record.model] = tuple(
                            value.strip() for value in values
                            if isinstance(value, str) and value.strip()
                        )
                    resolved_records = network_pricing_records(spec, [captured], aliases)
                    alias_metadata = {
                        record.model: record.metadata or {}
                        for record in ai_records
                    }
                    for resolved in resolved_records:
                        details = dict(resolved.metadata or {})
                        ai_details = alias_metadata.get(resolved.model, {})
                        if ai_details.get("observed_model"):
                            details["observed_model"] = ai_details["observed_model"]
                        if ai_details.get("aliases"):
                            details["aliases"] = ai_details["aliases"]
                        resolved.metadata = details
                    if any(record.price_status != "unavailable" for record in resolved_records):
                        return resolved_records
                except AIExtractionError:
                    pass
            return direct_records
        if ai is not None and ai.enabled and ai.base_url and ai.pick_model():
            return AIPriceExtractor(ai).extract(
                spec,
                "",
                [captured],
                client=client,
                expected_models=[target.name for target in spec.models],
            )
        raise PriceMonitorError(f"站点 {spec.id} 不是可识别的 New API/One API 响应，且未配置可用 AI")


# Backwards-compatible import name for callers using the pre-network adapter.
BrowserAdapter = NetworkAdapter

ADAPTERS: dict[str, Adapter] = {"browser": NetworkAdapter(), "network": NetworkAdapter()}
