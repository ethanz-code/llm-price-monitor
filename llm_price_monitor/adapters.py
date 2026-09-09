"""价格采集适配器：直接请求配置的价格接口并计算价格记录。

不启动浏览器、不读 DOM；New API/One API 响应走确定性计价，
HTML 页面内嵌价格表按结构特征识别，命中目标模型即确定性计价，
其余响应在配置了 AI 时交给 AIPriceExtractor。
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from llm_price_monitor.ai import AIExtractionError, AIPriceExtractor
from llm_price_monitor.config import AIConfig, ModelTarget, PriceMonitorError, SiteSpec
from llm_price_monitor.evidence import is_preferred_response_url, payload_hash, redact_url
from llm_price_monitor.tracker import (
    GroupRatioUnavailableError,
    PriceRecord,
    looks_like_newapi_pricing,
    looks_like_platform_pricing,
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
    def _expand(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = os.getenv(name)
        if resolved is None:
            raise ValueError(
                f"请求头引用的环境变量未设置: {name}（请在运行进程的环境中提供 {name}，如 docker compose 的 environment 或 shell export）"
            )
        return resolved

    return _ENV_VALUE_PATTERN.sub(_expand, value)


@dataclass(frozen=True)
class EndpointRequest:
    """单个价格数据入口的请求参数（standard 的 network 与附加 networks 共用）。"""

    url: str
    params: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)


def resolve_endpoint(value: Any, *, spec: SiteSpec, label: str) -> EndpointRequest:
    """入口配置（URL 字符串或请求配置对象）→ GET 请求参数；label 形如 "network.url"，用于报错定位。"""
    if isinstance(value, str):
        if not value.strip():
            raise PriceMonitorError(f"站点 {spec.id} 的 {label} 必须是非空 URL")
        return EndpointRequest(url=value.strip())
    if not isinstance(value, dict):
        raise PriceMonitorError(f"站点 {spec.id} 的 {label} 必须是 URL 字符串或请求配置对象")
    url = value.get("url")
    if not isinstance(url, str) or not url.strip():
        raise PriceMonitorError(f"站点 {spec.id} 的 {label}.url 必须是非空 URL")
    params = value.get("params", {})
    extra_headers = value.get("headers", {})
    if not isinstance(params, dict) or not isinstance(extra_headers, dict):
        raise PriceMonitorError(f"站点 {spec.id} 的 {label}.params 和 {label}.headers 必须是对象")
    return EndpointRequest(
        url=url.strip(),
        params=params,
        headers=extra_headers,
    )


def build_request_kwargs(entry: EndpointRequest, spec: SiteSpec, user_agent: str, timeout: float) -> dict[str, Any]:
    """站点级认证/Cookie 头与入口级 headers 合成 httpx 请求参数。"""
    request_headers = headers(spec, user_agent)
    request_headers.update({str(key): expand_header_value(str(value)) for key, value in entry.headers.items()})
    if spec.cookies and "cookie" not in request_headers:
        request_headers["cookie"] = "; ".join(f"{key}={value}" for key, value in spec.cookies.items())
    return {"params": entry.params, "headers": request_headers, "timeout": timeout}


def auth_required_records(spec: SiteSpec, message: str, source_url: str | None = None) -> list[PriceRecord]:
    targets = list(spec.models) or [ModelTarget("*")]
    source_url = source_url or str(spec.network.get("url") or "")
    metadata = {"pricing_kind": "auth_required", "currency": "CNY", "error": message}
    return [
        PriceRecord(
            target.name,
            None,
            None,
            "CNY/1M tokens",
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
    response_source_url = str(captured[0].get("url") or "") if captured else str(spec.network.get("url") or "")
    ordered = sorted(
        captured,
        key=lambda item: (
            not is_preferred_response_url(str(item.get("url", ""))),
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
                for name in (target.name, *(resolved_aliases or {}).get(target.name, ()))
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
            enabled = [name for name in item.get("enable_groups", []) if isinstance(name, str) and name]
            groups = enabled or ["default"]
            for selected_group in dict.fromkeys(groups):
                record_key = f"{target.name}:{selected_group or ''}"
                if record_key in records:
                    continue
                try:
                    record = newapi_price_record(
                        payload,
                        target.name,
                        str(response.get("url") or ""),
                        group=selected_group,
                        aliases=tuple(dict.fromkeys((resolved_aliases or {}).get(target.name, ()))),
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
        groups = attempted_groups[target.name] or {"default"}
        for selected_group in groups:
            record_key = f"{target.name}:{selected_group or ''}"
            if record_key in records:
                continue
            reason = "; ".join(dict.fromkeys(failures[target.name])) or "未捕获可识别的 one-api/new-api 定价 JSON 响应"
            records[record_key] = PriceRecord(
                target.name,
                None,
                None,
                "CNY/1M tokens",
                response_source_url,
                time.time(),
                {
                    "adapter": "network",
                    "pricing_kind": "unavailable",
                    "group": selected_group,
                    "currency": "CNY",
                    "network_evidence": [],
                    "page_evidence": [],
                    "notes": reason,
                },
                "unavailable",
            )
    return list(records.values())

def platform_pricing_records(spec: SiteSpec, captured: list[dict[str, Any]]) -> list[PriceRecord]:
    """确定性解析 platforms/supported_models/final_prices 结构。

    final_prices 里各字段是每 token 单价，×1,000,000 换算为 CNY/1M tokens；
    接口不标币种，按系统约定回落人民币。input/output 齐全为 confirmed，缺一为 candidate。
    """
    response_source_url = str(captured[0].get("url") or "") if captured else str(spec.network.get("url") or "")
    payload = captured[0].get("payload") if captured else None
    if isinstance(payload, dict):
        payload = payload.get("data")
    buckets = payload if isinstance(payload, list) else []
    records: dict[tuple[str, str], PriceRecord] = {}
    for item in buckets:
        platforms = item.get("platforms") if isinstance(item, dict) else None
        if not isinstance(platforms, list):
            continue
        for platform in platforms:
            models = platform.get("supported_models") if isinstance(platform, dict) else None
            if not isinstance(models, list):
                continue
            for model in models:
                if not isinstance(model, dict):
                    continue
                target = next(
                    (t for t in spec.models if t.name.casefold() == str(model.get("name", "")).casefold()),
                    None,
                )
                if target is None:
                    continue
                pricing = model.get("pricing") or {}
                final_prices = pricing.get("final_prices")
                if not isinstance(final_prices, list):
                    continue
                for entry in final_prices:
                    if not isinstance(entry, dict):
                        continue
                    group = str(entry.get("group_name") or "default")
                    def scaled(field: str) -> float | None:
                        value = entry.get(field)
                        return value * 1_000_000 if isinstance(value, (int, float)) and not isinstance(value, bool) else None
                    input_price = scaled("input_price")
                    output_price = scaled("output_price")
                    key = (target.name, group)
                    if key in records:
                        continue
                    metadata = {
                        "adapter": "network",
                        "pricing_kind": "explicit_price",
                        "group": group,
                        "group_ratio": entry.get("rate_multiplier"),
                        "currency": "CNY",
                        "network_evidence": [{
                            "source": "model_list",
                            "url": redact_url(str(captured[0].get("url", ""))),
                            "resource_type": captured[0].get("resource_type"),
                            "status": captured[0].get("status"),
                            "payload_sha256": payload_hash(payload),
                        }],
                        "page_evidence": [],
                        "notes": "platforms 定价接口直读：final_prices 每 token 单价 ×1,000,000 换算为 CNY/1M tokens；证据未标明币种，按人民币回退。",
                    }
                    status = "confirmed" if input_price is not None and output_price is not None else "candidate"
                    records[key] = PriceRecord(
                        target.name,
                        input_price,
                        output_price,
                        "CNY/1M tokens",
                        response_source_url,
                        time.time(),
                        metadata,
                        status,
                    )
    return list(records.values())


class _BrowserPageResponse:
    """无头浏览器抓到的页面伪装成 httpx.Response 的最小接口，让后续 HTML 解析路径直接复用。"""

    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        raise ValueError("无头浏览器页面没有 JSON 载荷")

    def raise_for_status(self) -> None:
        return None


class NetworkAdapter:
    def collect(
        self,
        spec: SiteSpec,
        client: httpx.Client,
        timeout: float,
        user_agent: str,
        ai: AIConfig | None = None,
    ) -> list[PriceRecord]:
        """直接请求配置的价格接口；不启动浏览器。

        全部模型都拿不到价格（unavailable）时，多半是 CDN 质询/接口偶发抖动
        而非站点真没数据，隔 20 秒重试一次，重试成功则用重试结果。
        """
        records = self._collect_once(spec, client, timeout, user_agent, ai)
        if records and all(record.price_status == "unavailable" for record in records):
            time.sleep(20)
            retried = self._collect_once(spec, client, timeout, user_agent, ai)
            if any(record.price_status != "unavailable" for record in retried):
                return retried
        return records

    def _collect_once(
        self,
        spec: SiteSpec,
        client: httpx.Client,
        timeout: float,
        user_agent: str,
        ai: AIConfig | None = None,
    ) -> list[PriceRecord]:
        """单次采集，不重试。"""
        if not spec.models:
            raise PriceMonitorError("不会自动检测所有模型；请在 models 中配置目标模型并在 network.url 配置接口地址")
        network = spec.network if isinstance(spec.network, dict) else {}
        url_value = network.get("url")
        if isinstance(url_value, dict):
            entry = resolve_endpoint(url_value, spec=spec, label="network.url")
        else:
            if not isinstance(url_value, str) or not url_value.strip():
                raise PriceMonitorError(f"站点 {spec.id} 未配置 network.url")
            # 扁平形态：params/headers 等直接挂在 network 下
            entry = resolve_endpoint({**network, "url": url_value}, spec=spec, label="network")
        headless_config = network.get("headless") or {}
        if isinstance(headless_config, dict) and headless_config.get("enabled"):
            from llm_price_monitor.browser_fetch import fetch_page_html

            response: Any = _BrowserPageResponse(entry.url, fetch_page_html(entry.url, headless_config))
        else:
            response = client.get(entry.url, **build_request_kwargs(entry, spec, user_agent, timeout))
        if response.status_code in {401, 403}:
            return auth_required_records(spec, f"网络价格接口返回 HTTP {response.status_code}", str(response.url))
        response.raise_for_status()
        payload: Any = None
        response_is_json = True
        try:
            payload = response.json()
        except ValueError:
            response_is_json = False
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
            # 页面自带结构化价格表时优先确定性解析；页面里没有目标模型时，
            # 自动发现页面引用的 JS chunk 逐个查找（chunk 文件名常带内容哈希，
            # 每次采集重新发现，站点改版也能跟上），仍未命中再交给 AI
            entries = _parse_base_price_entries(response.text)
            evidence_url = str(response.url)
            evidence_status = response.status_code
            evidence_text = response.text
            evidence_source = "model_list"
            if not (entries and any(_entry_matches_targets(item, spec) for item in entries)) and _looks_like_html(response.text):
                chunk_headers = build_request_kwargs(entry, spec, user_agent, timeout)["headers"]
                for src in _CHUNK_SRC_PATTERN.findall(response.text)[:15]:
                    chunk_url = urljoin(entry.url, src)
                    try:
                        chunk_response = client.get(chunk_url, headers=chunk_headers, timeout=timeout)
                    except httpx.HTTPError:
                        continue
                    if chunk_response.status_code != 200:
                        continue
                    parsed = _parse_base_price_entries(chunk_response.text)
                    if parsed and any(_entry_matches_targets(item, spec) for item in parsed):
                        entries = parsed
                        evidence_url = chunk_url
                        evidence_status = chunk_response.status_code
                        evidence_text = chunk_response.text
                        evidence_source = "model_list_chunk"
                        break
            if entries and any(_entry_matches_targets(item, spec) for item in entries):
                entry_evidence = [{
                    "source": evidence_source,
                    "url": redact_url(evidence_url),
                    "resource_type": "fetch",
                    "status": evidence_status,
                    "payload_sha256": payload_hash(evidence_text),
                }]
                # 配置了倍率接口时，基准价 × 端点倍率折算实售价（人民币）；
                # 记录的 source_url 指向倍率接口，倍率证据排在基准价证据前面
                resolve_rate = None
                source_url = str(response.url)
                ratio_url = network.get("ratio_url")
                if isinstance(ratio_url, str) and ratio_url.strip():
                    resolve_rate, rate_evidence = _rate_resolver(
                        spec, client, entry, user_agent, timeout, ratio_url.strip(),
                    )
                    entry_evidence = [rate_evidence[0], *entry_evidence]
                    source_url = ratio_url.strip()
                records = _records_from_base_entries(
                    spec, entries, entry_evidence, source_url, adapter_label="browser_network",
                    resolve_rate=resolve_rate,
                )
                if any(record.price_status == "confirmed" for record in records):
                    return records
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
        if looks_like_platform_pricing(payload):
            # 新版平台分桶定价结构：直读 final_prices，不再依赖 AI 逐分组映射
            platform_records = platform_pricing_records(spec, [captured])
            if platform_records:
                return platform_records
        if ai is not None and ai.enabled and ai.base_url and ai.pick_model():
            return AIPriceExtractor(ai).extract(
                spec,
                "",
                [captured],
                client=client,
                expected_models=[target.name for target in spec.models],
            )
        raise PriceMonitorError(f"站点 {spec.id} 不是可识别的 New API/One API 响应，且未配置可用 AI")



_BASE_ENTRY_PATTERN = re.compile(r'\{category:"[^"]+"[^{}]*\}')
_ENTRY_FIELD_PATTERN = re.compile(r'(\w+):("([^"]*)"|\[[^\]]*\]|-?(?:\d+\.?\d*|\.\d+)|null|true|false)')
_CHUNK_SRC_PATTERN = re.compile(r'src="([^"]+\.js[^"]*)"')


def _looks_like_html(text: str) -> bool:
    """判断响应是否是 HTML 文档；非 HTML 不做 chunk 地址发现。"""
    head = text.lstrip()[:1024].lower()
    return any(mark in head for mark in ("<!doctype html", "<html", "<head", "<body", "<script", "<div"))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_scalar(value: str) -> Any:
    if value.startswith('"'):
        return value[1:-1]
    if value.startswith("["):
        return json.loads(value)
    if value == "null":
        return None
    if value in {"true", "false"}:
        return value == "true"
    return float(value)


def _entries_from_json(payload: Any) -> list[dict[str, Any]]:
    """从 JSON 结构中递归收集与 JS 字面量同构的基准价条目（键序无关、键带引号的标准 JSON）。"""
    entries: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            models = node.get("models")
            if (
                isinstance(models, list) and models
                and isinstance(node.get("provider"), str)
                and _is_number(node.get("input"))
                and _is_number(node.get("output"))
            ):
                entries.append({
                    "category": node.get("category"),
                    "provider": node["provider"],
                    "name": node.get("name"),
                    "models": [str(model) for model in models],
                    "input": float(node["input"]),
                    "output": float(node["output"]),
                    "cache_read": node.get("cache_read") if _is_number(node.get("cache_read")) else None,
                    "cache_create": node.get("cache_create") if _is_number(node.get("cache_create")) else None,
                })
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return entries


def _parse_base_price_entries(text: str) -> list[dict[str, Any]]:
    """解析基准价表条目：先试整体 JSON（标准编码、键序无关），再退回压缩 JS 字面量正则。"""
    stripped = text.lstrip()
    if stripped[:1] in {"{", "["}:
        try:
            entries = _entries_from_json(json.loads(stripped))
        except ValueError:
            entries = []
        if entries:
            return entries
    entries = []
    for match in _BASE_ENTRY_PATTERN.finditer(text):
        fields: dict[str, Any] = {}
        for key, raw, _quoted in _ENTRY_FIELD_PATTERN.findall(match.group(0)):
            fields[key] = _parse_scalar(raw)
        if isinstance(fields.get("models"), list) and {"provider", "input", "output"} <= fields.keys():
            entries.append(fields)
    return entries


def _entry_matches_targets(entry: dict[str, Any], spec: SiteSpec) -> bool:
    names = {target.name.casefold() for target in spec.models}
    return any(str(model).casefold() in names for model in entry.get("models") or [])


def _base_entry_unavailable(
    spec: SiteSpec,
    model: str,
    source_url: str,
    evidence: list[dict[str, Any]],
    reason: str,
    adapter_label: str,
) -> PriceRecord:
    return PriceRecord(
        model,
        None,
        None,
        "CNY/1M tokens",
        source_url,
        time.time(),
        {
            "adapter": adapter_label,
            "pricing_kind": "unavailable",
            "currency": "CNY",
            "network_evidence": evidence,
            "page_evidence": [],
            "notes": reason,
        },
        "unavailable",
    )


def _rate_resolver(
    spec: SiteSpec,
    client: httpx.Client,
    entry: EndpointRequest,
    user_agent: str,
    timeout: float,
    ratio_url: str,
) -> tuple[Callable[[dict[str, Any]], tuple[float | None, str | None]], list[dict[str, Any]]]:
    """倍率接口 JSON → (resolve_rate(entry), 证据)。

    接口行形如 {provider, model_display, rate}；倍率先按模型显示名匹配，
    缺失时回退厂商级。返回的 resolve_rate 交给 _records_from_base_entries 消费。
    """
    request_headers = build_request_kwargs(entry, spec, user_agent, timeout)["headers"]
    response = client.get(ratio_url, headers=request_headers, timeout=timeout)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise PriceMonitorError(f"站点 {spec.id} 的 network.ratio_url 返回非 JSON 响应") from exc
    rows = payload.get("pricing") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise PriceMonitorError(f"站点 {spec.id} 的倍率接口响应不是 pricing 列表形态")
    by_name: dict[str, float] = {}
    by_provider: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rate = row.get("rate")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            continue
        display = row.get("model_display")
        if isinstance(display, str) and display.strip():
            by_name[display.strip().casefold()] = float(rate)
        provider = row.get("provider")
        if isinstance(provider, str) and provider.strip():
            by_provider[provider.strip().casefold()] = float(rate)
    evidence = [{
        "source": "rate_api",
        "url": redact_url(str(response.url)),
        "resource_type": "fetch",
        "status": response.status_code,
        "payload_sha256": payload_hash(response.text),
    }]

    def resolve_rate(entry: dict[str, Any]) -> tuple[float | None, str | None]:
        name = str(entry.get("name") or "").strip().casefold()
        if name in by_name:
            return by_name[name], "model"
        provider = str(entry.get("provider") or "").strip().casefold()
        if provider in by_provider:
            return by_provider[provider], "provider"
        return None, None

    return resolve_rate, evidence


def _records_from_base_entries(
    spec: SiteSpec,
    entries: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    source_url: str,
    *,
    adapter_label: str,
    resolve_rate=None,
    ai_assisted: bool = False,
) -> list[PriceRecord]:
    """基准价表条目 → 价格记录。

    resolve_rate 为空时条目价即站点价（USD）；提供时输出 实售价 = 基准价(USD) × 端点倍率。
    ai_assisted 表示基准价或倍率来自 AI 归一化：记录降级为 candidate 并在 metadata 标注来源。
    """
    records: list[PriceRecord] = []
    for target in spec.models:
        names = {target.name.casefold()}
        entry = next(
            (item for item in entries if any(str(m).casefold() in names for m in item["models"])),
            None,
        )
        if entry is None:
            records.append(_base_entry_unavailable(spec, target.name, source_url, evidence, f"基准价表中未找到模型 {target.name}", adapter_label))
            continue
        rate, rate_source = 1.0, None
        if resolve_rate is not None:
            rate, rate_source = resolve_rate(entry)
            if rate is None:
                records.append(_base_entry_unavailable(spec, target.name, source_url, evidence, "站点未公布该模型的端点倍率", adapter_label))
                continue
        currency = "CNY" if resolve_rate is not None else "USD"
        metadata: dict[str, Any] = {
            "adapter": adapter_label,
            "pricing_kind": "base_times_rate" if resolve_rate is not None else "base_price",
            "currency": currency,
            "base_price": {
                "name": entry.get("name"),
                "provider": entry.get("provider"),
                "models": entry.get("models"),
                "input_usd": entry.get("input"),
                "output_usd": entry.get("output"),
                "cache_read_usd": entry.get("cache_read"),
                "cache_create_usd": entry.get("cache_create"),
            },
            "network_evidence": evidence,
            "page_evidence": [],
        }
        if resolve_rate is not None:
            metadata.update({
                "rate": rate,
                "rate_source": rate_source,
                "formula": "实售价 = 厂商基准价(USD) × 端点倍率；倍率取模型级，缺失时回退厂商级",
            })
        else:
            metadata["formula"] = "价格直接取自站点公布的基准价表（USD）"
        if ai_assisted:
            metadata["extraction"] = "ai"
        records.append(PriceRecord(
            target.name,
            round(float(entry["input"]) * rate, 6),
            round(float(entry["output"]) * rate, 6),
            f"{currency}/1M tokens",
            source_url,
            time.time(),
            metadata,
            "candidate" if ai_assisted else "confirmed",
        ))
    return records

ADAPTERS: dict[str, Adapter] = {
    "standard": NetworkAdapter(),
}
