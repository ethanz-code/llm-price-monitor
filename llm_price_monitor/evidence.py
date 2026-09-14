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
    # 模型卡片没识别出来时保留原始 DOM 文本：动态渲染站点常没有可匹配的价格特征
    if not text and raw_text.strip():
        text = clip(" ".join(raw_text.split()), 12000)
    if not text:
        return None
    text = annotate_positional_price_arrays(text, expected_models, full_text=raw_text)
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


# 压缩 JS 里模型价格行按位置排列，没有字段名，AI 只能猜，容易把官方参考价当成售价。
# 由确定性代码先判读成带字段名的结论行，AI 只负责采信与填字段：
#   3 个数字：官方输入/输出/缓存读取（官方参考价 = 上游美元价 × 7，实付基础价 = 官方价 ÷ 7）
#   6 个数字：官方三项 + 站内实付基础价三项
#   7 个数字：官方四项（含缓存创建）+ 站内实付基础价三项
_POSITIONAL_PRICE_ROW = re.compile(
    r'["\']?(?P<model>[A-Za-z0-9][A-Za-z0-9._/-]{2,63})["\']?\s*,\s*'
    r"(?P<numbers>-?\d*\.?\d+(?:\s*,\s*-?\d*\.?\d+){2,6})"
)
_POSITIONAL_NUMBER = re.compile(r"-?\d*\.?\d+")
_GROUP_ROW = re.compile(
    r'\[\s*"(?P<name>[A-Za-z0-9_.\-]{2,32})"\s*,\s*'
    r'(?:[A-Za-z_$][\w$.]*|"[^"]{0,80}")\s*,\s*"[^"]{0,80}"\s*,\s*"[^"]{0,80}"\s*,\s*'
    r"(?P<ratio>0?\.\d+|0|1(?:\.\d+)?)\s*,"
)
# 页面可能在校验/覆写逻辑里改掉分组倍率（如 gptFullGroup[4] = .3），以覆写值为准。
_GROUP_VAR_BINDING = re.compile(
    r'(\w+)\s*=\s*\w+\s*\.find\(\s*\w+\s*=>\s*\w+\s*\[0\]\s*===\s*"([A-Za-z0-9_.\-]+)"\s*\)'
)
_GROUP_RATIO_OVERRIDE = re.compile(r"(\w+)\s*\[4\]\s*=\s*(0?\.\d+|0|1(?:\.\d+)?)")


def _trim_number(value: float) -> str:
    return f"{round(value, 6):g}"


def _price_triplet_row(model: str, numbers: list[str]) -> tuple[list[str], list[str]] | None:
    """位置数组 → (官方三项, 站内实付基础价三项)；无法判读返回 None。"""
    if len(numbers) == 3:
        official = numbers
        return official, [_trim_number(float(item) / 7) for item in official]
    if len(numbers) == 6:
        return numbers[0:3], numbers[3:6]
    if len(numbers) == 7:
        # 官方四项（输入/输出/缓存创建/缓存读取）+ 实付三项
        return [numbers[0], numbers[1], numbers[3]], numbers[4:7]
    return None


def _group_rows(text: str) -> list[tuple[str, str, int, int]]:
    """提取分组行 → (分组名, 倍率文本, 起始位置, 结束位置)；倍率已应用 JS 覆写。"""
    rows = [
        (match.group("name"), match.group("ratio"), match.start(), match.end())
        for match in _GROUP_ROW.finditer(text)
    ]
    if not rows:
        return []
    overrides = {
        binding.group(1): binding.group(2)
        for binding in _GROUP_VAR_BINDING.finditer(text)
    }
    ratios = {
        overrides.get(match.group(1), match.group(1)): match.group(2)
        for match in _GROUP_RATIO_OVERRIDE.finditer(text)
    }
    bounds = [row[2] for row in rows] + [len(text)]
    return [
        (name, ratios.get(name, ratio), start, bounds[index + 1])
        for index, (name, ratio, start, _) in enumerate(rows)
    ]


def annotate_positional_price_arrays(text: str, expected_models: list[str], *, full_text: str | None = None) -> str:
    """把位置型价格数组翻译成带字段名的结论行，供 AI 直接采信。

    分组倍率与 JS 覆写从 full_text（未裁剪的整页文本）提取，避免裁剪丢掉倍率覆写。
    """
    annotations: list[str] = []
    seen: set[str] = set()
    for match in _POSITIONAL_PRICE_ROW.finditer(text):
        model = match.group("model")
        if model.casefold() in seen:
            continue
        if expected_models and not any(
            contains_model_alias(model, target) or target.casefold() in model.casefold()
            for target in expected_models
        ):
            continue
        numbers = [item.strip() for item in _POSITIONAL_NUMBER.findall(match.group("numbers"))]
        parsed = _price_triplet_row(model, numbers)
        if parsed is None:
            continue
        official, base = parsed
        seen.add(model.casefold())
        annotations.append(
            f"[位置型价格数组解读] 模型 {model}：官方参考价 输入={official[0]} / 输出={official[1]} / 缓存读取={official[2]}；"
            f"站内实付基础价 输入={base[0]} / 输出={base[1]} / 缓存读取={base[2]}。"
            "官方参考价不得填入 input_price/output_price。"
        )
        model_annotations = [
            f"[分组实付价结论] 模型 {model} @ {name}（倍率 {ratio}x）：输入={final[0]} / 输出={final[1]} / 缓存读取={final[2]}"
            for name, ratio, final in _grouped_rows_for(full_text or text, model, base)
        ]
        annotations.extend(model_annotations or [
            f"[分组实付价结论] 模型 {model}：未识别到分组倍率，input_price/output_price 填 null 并标 rule_only。"
        ])
    if not annotations:
        return text
    return text + "\n" + "\n".join(annotations)


def _grouped_rows_for(
    full_text: str,
    model: str,
    base: list[str],
) -> list[tuple[str, str, list[str]]]:
    """按分组倍率折算实付价；只保留白名单命中该模型（或无白名单）的分组。"""
    rows: list[tuple[str, str, list[str]]] = []
    for name, ratio, start, end in _group_rows(full_text):
        group_text = full_text[start:end]
        if '["' in group_text and not contains_model_alias(group_text, model):
            continue
        try:
            ratio_value = float(ratio)
            final = [_trim_number(float(item) * ratio_value) for item in base]
        except ValueError:
            continue
        rows.append((name, _trim_number(ratio_value), final))
    return rows


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
