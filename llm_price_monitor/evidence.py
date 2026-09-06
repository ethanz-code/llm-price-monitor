"""网络/页面证据的捕获、脱敏与按目标模型筛选。

AI 抽取的证据都从这里来：先脱敏（token/cookie 等），再按目标模型别名
预筛 payload，把价格证据片段截取成可交给 AI 的短文本。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

from llm_price_monitor.matching import model_alias_pattern, model_aliases, contains_model_alias

SENSITIVE_EVIDENCE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "apikey",
    "access-token",
    "access_token",
    "refresh-token",
    "refresh_token",
    "client-secret",
    "client_secret",
    "secret",
}

PRICE_EVIDENCE_PATTERN = re.compile(
    r"(?:"
    r"[\"']?[A-Za-z_][A-Za-z0-9_]*(?:price|pricing|cost|rate)[A-Za-z0-9_]*[\"']?\s*[:=：]?\s*[$¥￥]?\s*\d+(?:\.\d+)?"
    r"|(?:input|output|prompt|completion|price|pricing|cost|rate|费用|价格|定价|单价|倍率)"
    r"(?:[_\s-]*(?:price|token|cost|rate))?[\"']?\s*[:=：]?\s*[$¥￥]?\s*\d+(?:\.\d+)?"
    r"|(?:输入|输出)\s*Token\s*[:=：]?\s*[$¥￥]?\s*\d+(?:\.\d+)?"
    r")",
    re.IGNORECASE,
)

_PAGE_MODEL_ID_PATTERN = re.compile(r"\b[a-z][a-z0-9._-]*/[a-z][a-z0-9._-]*\b", re.IGNORECASE)
_PAGE_DISPLAY_MODEL_PATTERN = re.compile(
    r"(?<![\w/$.])(?:[A-Z][A-Za-z0-9.+-]*\s*){1,3}:\s*[A-Z][A-Za-z0-9.+-]*(?:\s+[A-Z][A-Za-z0-9.+-]*){0,6}",
    re.IGNORECASE,
)


def payload_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    half = max(1, (limit - 80) // 2)
    return value[:half] + "\n...[evidence truncated]...\n" + value[-half:]


def redact_text(value: str) -> str:
    return re.sub(
        r"(?i)(\b(?:authorization|cookie|set-cookie|x-api-key|api-key|access_token|refresh_token|client_secret|secret)\b\s*[:=]\s*)([^\s,;]+)",
        r"\1[REDACTED]",
        value,
    )


def redact_url(value: str) -> str:
    return re.sub(
        r"(?i)([?&](?:token|access_token|refresh_token|api_key|apikey|secret|key)=)[^&#]+",
        r"\1[REDACTED]",
        value,
    )


def is_preferred_response_url(url: str, patterns: tuple[str, ...] = ("price", "model")) -> bool:
    parts = urlsplit(url)
    # 域名经常包含 model（例如 modelflare），只检查接口 path/query。
    normalized = re.sub(r"[^a-z0-9]+", " ", f"{parts.path}?{parts.query}".casefold()).strip()
    return any(
        (
            re.sub(r"[^a-z0-9]+", " ", pattern.casefold()).strip() in normalized
            or (pattern.casefold().strip() == "price" and "pricing" in normalized)
        )
        for pattern in patterns if pattern.strip()
    )


def contains_price_evidence(value: str) -> bool:
    """Require a price-like label or currency amount, not just arbitrary numbers."""
    return PRICE_EVIDENCE_PATTERN.search(value) is not None


def sanitize_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, child in value.items():
            if str(key).lower() in SENSITIVE_EVIDENCE_KEYS:
                clean[str(key)] = "[REDACTED]"
            else:
                clean[str(key)] = sanitize_evidence(child)
        return clean
    if isinstance(value, list):
        return [sanitize_evidence(child) for child in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def filter_target_payload(value: Any, model: str) -> Any:
    """递归保留 JSON 中包含目标模型的对象，去掉同响应中的其他模型。"""
    if isinstance(value, dict):
        direct_model = any(
            contains_model_alias(str(key), model)
            or (not isinstance(child, (dict, list)) and contains_model_alias(str(child), model))
            for key, child in value.items()
        )
        if direct_model:
            return value
        retained: dict[str, Any] = {}
        for key, child in value.items():
            if contains_model_alias(str(key), model):
                retained[str(key)] = child
                continue
            child_text = json.dumps(child, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            if contains_model_alias(child_text, model):
                filtered = filter_target_payload(child, model)
                if filtered is not None:
                    retained[str(key)] = filtered
        if retained:
            return retained
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return value if contains_model_alias(serialized, model) else None
    if isinstance(value, list):
        filtered_items = [item for item in (filter_target_payload(child, model) for child in value) if item is not None]
        return filtered_items or None
    if contains_model_alias(str(value), model):
        return value
    return None


def first_target_payload(value: Any, model: str) -> Any:
    """保留响应中第一个命中的目标模型对象，避免把分页结果整体交给 AI。"""
    if isinstance(value, dict):
        direct_model = any(
            contains_model_alias(str(key), model)
            or (not isinstance(child, (dict, list)) and contains_model_alias(str(child), model))
            for key, child in value.items()
        )
        if direct_model:
            return value
        for key, child in value.items():
            filtered = first_target_payload(child, model)
            if filtered is not None:
                return {str(key): filtered}
        return None
    if isinstance(value, list):
        for child in value:
            filtered = first_target_payload(child, model)
            if filtered is not None:
                return [filtered]
        return None
    return value if contains_model_alias(str(value), model) else None


def candidate_payload(value: Any, expected_models: list[str]) -> Any:
    """仅按模型名相似性预筛 JSON，字段语义仍交给 AI 判断。"""
    if not expected_models:
        return None
    if isinstance(value, dict):
        direct_model = any(
            contains_model_alias(str(key), model)
            or (not isinstance(child, (dict, list)) and any(contains_model_alias(str(child), model) for model in expected_models))
            for key, child in value.items()
            for model in expected_models
        )
        if direct_model:
            return value
        retained: dict[str, Any] = {}
        for key, child in value.items():
            filtered = candidate_payload(child, expected_models)
            if filtered is not None:
                retained[str(key)] = filtered
            elif key in {"group_ratio", "pricing_version", "currency", "unit"}:
                retained[str(key)] = child
        return retained or None
    if isinstance(value, list):
        filtered_items = [item for item in (candidate_payload(child, expected_models) for child in value) if item is not None]
        return filtered_items or None
    return value if any(contains_model_alias(str(value), model) for model in expected_models) else None


def decode_response_body(body: bytes, content_type: str = "") -> str:
    """Decode captured response bytes without corrupting UTF-8 pages."""
    charset_match = re.search(r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\s\"']+)", content_type, re.IGNORECASE)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        text = body.decode(charset, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    return repair_mojibake(text)


def repair_mojibake(text: str) -> str:
    """Repair common UTF-8-as-Windows-1252 fragments from upstream SSR data."""
    markers = ("Ã", "Â", "â", "ð", "ï", "æ", "å", "ç")
    if not any(marker in text for marker in markers):
        return text
    # Some terminals render the NBSP byte in a mojibake sequence as a normal
    # space, e.g. ``æ ¼`` instead of ``æ\xa0¼``.
    text = re.sub(r"([ÃÂâðïæåç]) (?=[\u0080-\u024f\u02c6-\u02dc\u2000-\u20ff])", lambda match: f"{match.group(1)}\u00a0", text)

    def repair(fragment: re.Match[str]) -> str:
        value = fragment.group(0)
        try:
            raw = bytearray()
            for char in value:
                codepoint = ord(char)
                if 0x80 <= codepoint <= 0x9F:
                    raw.append(codepoint)
                else:
                    raw.extend(char.encode("cp1252"))
            candidate = bytes(raw).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return value
        return candidate if any("\u4e00" <= char <= "\u9fff" for char in candidate) else value

    return re.sub(r"[\u0080-\u024f\u02c6-\u02dc\u2000-\u20ff \t]+", repair, text)


def join_page_sources(page_sources: list[dict[str, Any]]) -> str:
    return "\n--- verification source ---\n".join(
        f"[{source['source']}] {source.get('quote', source.get('text', ''))}" for source in page_sources
    )


def structure_page_source(source: dict[str, str], expected_models: list[str]) -> dict[str, Any] | None:
    raw_text = str(source.get("text", ""))
    text = target_page_text(raw_text, expected_models)
    if not text and contains_price_evidence(str(source.get("text", ""))):
        text = clip(" ".join(str(source.get("text", "")).split()), 12000)
    # 兼容历史证据数据；当前 network 适配器不会采集或读取页面文档。
    if not text and raw_text.strip():
        text = clip(" ".join(raw_text.split()), 12000)
    if not text:
        return None
    model = next(
        (target for target in expected_models if contains_model_alias(text, target)),
        expected_models[0] if expected_models else None,
    )
    return {
        "source": str(source.get("source") or "model_list"),
        "url": redact_url(str(source.get("url", ""))),
        "target_model": model,
        "quote": text,
    }


def first_model_segment(value: str, model: str, expected_models: list[str], limit: int = 2800) -> str:
    """截取首次命中的模型段，到下一个目标模型段之前结束。"""
    lowered = value.casefold()
    matches = [
        (lowered.find(alias.casefold()), alias)
        for alias in model_aliases(model)
        if lowered.find(alias.casefold()) >= 0
    ]
    if not matches:
        return ""
    start = min(position for position, _ in matches)
    boundaries: list[int] = []
    for candidate in expected_models:
        aliases = model_aliases(candidate)
        for alias in aliases:
            position = lowered.find(alias.casefold(), start + len(alias))
            if position == start:
                continue
            if position >= 0:
                boundaries.append(position)
    end = min(boundaries) if boundaries else len(value)
    return clip(value[start:end].strip(), limit)


def page_model_card(page_text: str, target: str) -> str:
    lines = [" ".join(line.split()) for line in page_text.splitlines() if line.strip()]
    model_heading = re.compile(r"^(?=[a-z][a-z0-9./_-]*[a-z])[a-z][a-z0-9._/-]{2,}$", re.IGNORECASE)
    aliases = model_aliases(target)
    target_pattern = [model_alias_pattern(alias) for alias in aliases]
    line_positions = [
        index for index, line in enumerate(lines)
        if any(
            pattern.search(line)
            and line == alias
            for pattern, alias in zip(target_pattern, aliases)
        )
    ]
    if line_positions:
        start = line_positions[0]
        end = next((index for index in range(start + 1, len(lines)) if model_heading.fullmatch(lines[index])), len(lines))
        return " ".join(lines[start:end]).strip()
    normalized = " ".join(lines)
    positions = []
    for alias in aliases:
        positions.extend(match.start() for match in model_alias_pattern(alias).finditer(normalized))
    if not positions:
        return ""
    model_start = min(positions)
    current_model_end = max(
        position + len(alias)
        for alias in aliases
        for position in [normalized.casefold().find(alias.casefold(), model_start)]
        if position == model_start
    )
    boundaries = [
        match.start()
        for match in _PAGE_MODEL_ID_PATTERN.finditer(normalized, model_start + 1)
        if match.start() >= current_model_end
    ]
    boundaries.extend(match.start() for match in _PAGE_DISPLAY_MODEL_PATTERN.finditer(normalized, current_model_end))
    end = min(boundaries) if boundaries else len(normalized)
    return normalized[model_start:end].strip()


def target_page_text(page_text: str, expected_models: list[str]) -> str:
    if not expected_models:
        return " ".join(page_text.split())
    lines = [" ".join(line.split()) for line in page_text.splitlines() if line.strip()]
    price_legend = [
        line
        for line in lines
        if re.search(r"(?:input|output|cache|price|pricing|输入|输出|缓存|价格)", line, re.IGNORECASE)
        and not re.search(r"(?:[$¥€]|\\d)", line)
    ]
    legend = " ".join(dict.fromkeys(price_legend))[:1200]
    cards = []
    for target in expected_models:
        card = page_model_card(page_text, target)
        if card and legend:
            card = f"页面共享价格字段说明：{legend}\n目标模型卡片：{card}"
        if card and card not in cards:
            cards.append(card)
    return "\n--- target model ---\n".join(cards)
