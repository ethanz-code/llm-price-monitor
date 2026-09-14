"""渠道状态采集：GET status.url，解析为自由结构 JSON，与价格采集同周期顺带执行。

解析三层：结构化 JSON 直接采用；HTML/文本先提取内嵌 JSON（script 变量、JSON.parse 字面量）；
仍失败且配置了 AI 时交给大模型抽取。状态变化由调用方对 data 做结构 diff 生成事件，
不强制归一化 schema——各站点保留各自字段，图表后续按实际字段配置。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from llm_price_monitor.adapters import build_request_kwargs, http_error_message, resolve_endpoint
from llm_price_monitor.timeline import TIMELINE_KEYS
from llm_price_monitor.ai import (
    AIExtractionError,
    _fit_text,
    ai_content,
    ai_request,
    json_content,
)
from llm_price_monitor.config import AIConfig, PriceMonitorError, SiteSpec
from llm_price_monitor.evidence import payload_hash, redact_url

# 内嵌 JSON 候选起点：赋值/传参后的第一个 { 或 [（window.__X__ = {...}、JSON.parse({...}) 等）
_JSON_START_PATTERN = re.compile(r"[=:=(]\s*([\[{])")
# 状态语义键名片段：决定内嵌 JSON 候选的优先级
_STATUS_KEY_HINTS = ("status", "state", "channel", "health", "available", "online", "可用", "渠道", "状态")
_MAX_JSON_CANDIDATES = 50
# 渠道条目的名字候选键：与前端 channelStatus.ts 的 NAME_KEYS 口径一致
_CHANNEL_NAME_KEYS = ("name", "channel", "model", "id", "title", "key")


def filter_status_groups(data: dict[str, Any], groups: list[str]) -> dict[str, Any]:
    """按配置的分组名过滤状态数据：带名字的渠道条目只保留名字对得上的（忽略大小写）；
    一个都没匹配上时保留原数据，避免配置写错把整份状态清空。"""
    result, matched = prune_status_groups(data, groups)
    return result if matched else data


def _status_key_names(data: dict[str, Any], depth: int = 2) -> list[str]:
    names: list[str] = []
    for key, value in data.items():
        names.append(str(key))
        if depth <= 0:
            continue
        if isinstance(value, dict):
            names.extend(_status_key_names(value, depth - 1))
        elif isinstance(value, list):
            for item in value[:10]:
                if isinstance(item, dict):
                    names.extend(_status_key_names(item, depth - 1))
    return names


def _looks_status_like(data: dict[str, Any]) -> bool:
    keys = json.dumps(_status_key_names(data), ensure_ascii=False).casefold()
    return any(hint in keys for hint in _STATUS_KEY_HINTS)


def _status_from_text(text: str) -> dict[str, Any] | None:
    """从 HTML/JS 文本中提取内嵌 JSON 对象；优先带状态语义键的候选，否则取最大候选。"""
    candidates: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    for match in _JSON_START_PATTERN.finditer(text):
        if len(candidates) >= _MAX_JSON_CANDIDATES:
            break
        try:
            value, _ = decoder.raw_decode(text, match.start(1))
        except ValueError:
            continue
        if isinstance(value, dict) and value:
            candidates.append(value)
    if not candidates:
        return None
    for candidate in candidates:
        if _looks_status_like(candidate):
            return candidate
    return max(candidates, key=lambda item: len(json.dumps(item, ensure_ascii=False)))


def ai_extract_status(
    config: AIConfig, text: str, url: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """AI 兜底：把 HTML/JS 文本整理成状态 JSON 对象；页面原文不变时直接复用缓存结果。"""
    ai_model = config.pick_model()
    if not config.base_url or not ai_model:
        raise PriceMonitorError("配置文件 ai.base_url 或 ai.model/ai.models 未配置")
    if not config.api_key:
        raise PriceMonitorError("配置文件 ai.api_key 未配置")
    cache_key = payload_hash({"version": 1, "kind": "status", "url": url, "text": text})
    cached = config.cache.cache_get(cache_key) if config.cache is not None else None
    if isinstance(cached, dict):
        return cached
    system = (
        "你是渠道状态数据抽取器。从网页 HTML 或内嵌 JS 中提取渠道/服务的状态信息，"
        "只能使用 user 消息中的内容，禁止凭常识补全或猜测。"
        "必须只返回一个 JSON 对象，不要 Markdown，不要解释。"
    )
    user = f"""请从以下网页内容中提取渠道/服务状态数据。

输出一个 JSON 对象，结构不做强制约定，保留原始字段语义，推荐形如：
{{"items": [{{"name": "渠道或模型名", "status": "up|down|degraded|unknown", "……": "原始字段"}}]}}

规则：
- 保留页面里能确定的原始字段名和取值，不要翻译、换算或推断。
- 状态取值无法判断时保留原文或填 unknown。
- 页面里没有渠道状态信息时返回 {{"items": []}}。

页面地址：{url}

网页内容：
{_fit_text(text, config.max_input_chars)}"""
    url_endpoint, headers, request_body = ai_request(config, ai_model, system, user)
    own = client is None
    client = client or httpx.Client(timeout=config.timeout)
    try:
        response = client.post(url_endpoint, headers=headers, json=request_body, timeout=config.timeout)
        response.raise_for_status()
        result = json_content(ai_content(config.api_format, response.json()))
        if config.cache is not None:
            config.cache.cache_put(cache_key, result)
        return result
    except AIExtractionError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise PriceMonitorError(f"渠道状态 AI 识别请求失败: {exc}") from exc
    finally:
        if own:
            client.close()


def fetch_site_status(
    spec: SiteSpec,
    client: httpx.Client,
    timeout: float,
    user_agent: str,
    ai: AIConfig | None = None,
) -> dict[str, Any]:
    """采集单个站点的渠道状态，返回含来源与解析方式的记录；data 为自由结构。"""
    entry = resolve_endpoint(spec.status, spec=spec, label="status")
    response = client.get(entry.url, **build_request_kwargs(entry, spec, user_agent, timeout))
    if response.is_error:
        raise PriceMonitorError(http_error_message("渠道状态地址", response, auth_hint=True))
    response.raise_for_status()
    data: dict[str, Any] | None = None
    parse_kind = "json"
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        data = payload
    elif isinstance(payload, list):
        data = {"items": payload}
    if data is None:
        data = _status_from_text(response.text)
        parse_kind = "embedded_json"
    if data is None:
        if ai is not None and ai.enabled and ai.base_url and ai.pick_model():
            data = ai_extract_status(ai, response.text, str(response.url), client=client)
            parse_kind = "ai"
        else:
            raise PriceMonitorError(f"站点 {spec.id} 的渠道状态地址返回 HTML/文本，且未配置可用 AI")
    # 配置了 status.groups 时只保留指定分组的渠道条目，采集与事件都只看这些分组
    raw_groups = spec.status.get("groups")
    if isinstance(raw_groups, list) and raw_groups:
        data = filter_status_groups(data, [str(item) for item in raw_groups])
    return {
        "site_id": spec.id,
        "captured_at": time.time(),
        "source_url": redact_url(str(response.url)),
        "http_status": response.status_code,
        "parse": parse_kind,
        "data": data,
    }


# 状态接口里时间戳类字段（滑动窗口游标等）每次轮询必变，diff 时跳过，避免噪音变化刷屏
VOLATILE_TIME_KEYS = {"time", "timestamp", "ts", "datetime", "updated", "last_update"}


def _is_volatile_time_key(key: str) -> bool:
    return key in VOLATILE_TIME_KEYS or key.endswith(("_at", "_time", "_ts"))


def diff_status(previous: Any, current: Any, path: str = "$") -> list[dict[str, Any]]:
    """对状态 data 做路径级结构 diff；列表按下标比较，站点改顺序会呈现为多次 change。"""
    if isinstance(previous, dict) and isinstance(current, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(previous) | set(current), key=str):
            if _is_volatile_time_key(str(key)):
                continue
            if str(key).lower() in TIMELINE_KEYS:
                # 时间线是站点返回的滚动窗口：新点进、旧点出，按下标的 diff 会把整条历史错位报成噪音变化；
                # 检测点明细由 status_records 承载，这里只关心渠道当前状态字段
                continue
            child_path = f"{path}.{key}"
            if key not in previous:
                changes.append({"op": "add", "path": child_path, "new": current[key]})
            elif key not in current:
                changes.append({"op": "remove", "path": child_path, "old": previous[key]})
            else:
                changes.extend(diff_status(previous[key], current[key], child_path))
        return changes
    if isinstance(previous, list) and isinstance(current, list):
        changes = []
        for index in range(max(len(previous), len(current))):
            child_path = f"{path}[{index}]"
            if index >= len(previous):
                changes.append({"op": "add", "path": child_path, "new": current[index]})
            elif index >= len(current):
                changes.append({"op": "remove", "path": child_path, "old": previous[index]})
            else:
                changes.extend(diff_status(previous[index], current[index], child_path))
        return changes
    if previous != current:
        return [{"op": "change", "path": path, "old": previous, "new": current}]
    return []
