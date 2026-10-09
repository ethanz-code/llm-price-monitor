"""AI 兜底抽取与可疑结果判定：超长页分块、逐档证据校验、防幻觉丢弃。"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any

import httpx

from llm_price_monitor.ai import (
    AIExtractionError,
    ai_content,
    json_content,
    request_with_model_fallback,
)
from llm_price_monitor.catalog.normalize import model_key
from llm_price_monitor.config import AIConfig, PriceMonitorError
from llm_price_monitor.evidence import clip, redact_text
from llm_price_monitor.units import number_or_none, price_digit_forms

from .parsing import detect_currency
from .tiers import _compose_baseline

# 确定性解析"有结果但不可信"的特征：模型名里混着档位/促销注释（MiniMax 的
# 「≤ 512k 输入 tokens 永久五折」）、合并行（小米的「a、b」）、零宽字符（火山
# 生图表），以及同键模型出现不同价格（促销价与原价混装）。命中就交给 AI 复核。
_SUSPICIOUS_NAME_PATTERN = re.compile(r"[\u200b≤≥、]|\d折|时段|场景")


def _records_suspicious(records: list[dict[str, Any]]) -> bool:
    seen: dict[str, tuple[Any, Any]] = {}
    for record in records:
        if _SUSPICIOUS_NAME_PATTERN.search(str(record.get("model") or "")):
            return True
        key = str(record.get("model_key") or "")
        prices = (record.get("input_price"), record.get("output_price"))
        if key in seen and seen[key] != prices:
            return True
        seen[key] = prices
    return False


class _VisibleTextHarvester(HTMLParser):
    """剥掉 script/style/noscript 与标签，只留可见文本（块级元素处换行），给超长页分块喂 AI 用。"""

    _SKIP_TAGS = {"script", "style", "noscript"}
    _BREAK_TAGS = {"p", "div", "tr", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _slim_html_for_ai(text: str) -> str:
    harvester = _VisibleTextHarvester()
    try:
        harvester.feed(text)
    except Exception:
        return text  # 残缺 HTML 退回原文，分块照常
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", "".join(harvester.parts))).strip()


# 分块喂 AI 的单段上限：AI 单次回复有 max_tokens 上限，一段塞太多模型会让 JSON
# 输出到一半被截断；30k 字符是"模型数 × 单模型 JSON"能在回复里装下的经验值
_AI_CHUNK_CAP = 30000
# 密集价目页（百炼/硅基那种一张表几百个模型）单段提取的回复预算：默认 4000 tokens
# 装不下几十个模型的 JSON，会输出到一半截断；放大预算配合失败对半拆段双保险
_AI_EXTRACT_MAX_TOKENS = 16000
# 整页 AI 提取的总时长上界：超长页分块多、模型池回退会滚很久，没有上界会把抓取
# worker 挂住；到点后已提取的段照常保留（标待复核），没跑的段记警告放弃
_AI_PAGE_BUDGET_SECONDS = 900.0


def _split_for_ai(text: str, limit: int) -> list[str]:
    """AI 输入分段：不超限原样返回；超限先剥标签再按行边界切块，每段不超 limit。"""
    limit = min(limit, _AI_CHUNK_CAP)
    if len(text) <= limit:
        return [text]
    slimmed = _slim_html_for_ai(text)
    if len(slimmed) <= limit:
        return [slimmed]
    chunks: list[str] = []
    buffer: list[str] = []
    size = 0
    for line in slimmed.splitlines(keepends=True):
        if buffer and size + len(line) > limit:
            chunks.append("".join(buffer))
            buffer, size = [], 0
        buffer.append(line)
        size += len(line)
    if buffer:
        chunks.append("".join(buffer))
    return chunks


def _number_in_text(value: float, text: str) -> bool:
    """价格数字的常见书写形式是否在页面文本中字面出现（形态集与 ai 抽取的证据闸共用）。"""
    return any(form in text for form in price_digit_forms(value))


_AI_SYSTEM_PROMPT = (
    "你是定价页数据抽取助手。从给定的网页文本中提取全部模型的 API 价格，"
    "只输出 JSON 对象，不要输出其他内容："
    '{"models":[{"model":"模型名","tiers":[{"name":"档位名","standard":true,'
    '"input":输入单价,"output":输出单价,"cache_read":缓存命中单价或null}],'
    '"context":"上下文窗口原文或null","description":"模型简介原文或null",'
    '"currency":"CNY或USD","quote":"价格所在的原文片段"}]}。'
    "同一模型在页面里有多档价格（如高峰/空闲时段、不同上下文长度、不同并发规格）时，"
    "每档一个元素，name 照页面原文抄写；页面有时间定义说明"
    "（如“高峰时段为北京时间周一至周五 9:00-12:00、14:00-18:00，其余为空闲时段”）"
    "要把定义并进对应档位的 name。适用的标准档位（页面标明空闲时段是折扣价、高峰时段是标准价时，"
    "标准档指高峰时段）把 standard 标为 true，页面没有说明就都不标。"
    "只有一个价的模型 tiers 只放一个元素、name 填 null。"
    "价目表分列时按列名对号入座：「输入（命中缓存）」是缓存命中价进 cache_read，"
    "输入价必须取「输入（未命中缓存）」列，输出价取「输出」列——命中缓存价通常远低于"
    "输入价，把它当输入价是错位；同一模型有实时推理/批量推理等多行时每行一个档位，"
    "name 照页面原文抄，绝不能把某一行的缓存命中价抄成另一行的输入价。"
    "一行并列多个模型名（如「mimo-a、mimo-b」）要拆成多条记录，每条只写一个模型名；"
    "每条记录的 input 和 output 都照页面该行抄，缺一个整条就不可用，宁可多给也别只给一半。"
    "价格数值统一换算成每百万 tokens；页面标注免费的填 0；页面里没有的价格填 null。"
    "context 抄页面上模型简介表或文字里的上下文窗口原文（如 1M、200K/32K、最大输出 8K），"
    "description 抄模型简介原文并压缩到 40 字以内（超长只取核心一句），"
    "页面里没有就填 null，不要编造；这两个字段越长越容易挤爆输出，务必精简。"
    "不要编造页面里不存在的模型或价格；模型名照页面原文抄写。"
)


def _parse_ai_tiers(item: dict[str, Any]) -> list[dict[str, Any]]:
    """AI 返回的模型条目 → 档位列表；兼容旧版模型级平铺价格形态。"""
    raw_tiers = item.get("tiers")
    tiers: list[dict[str, Any]] = []
    if isinstance(raw_tiers, list):
        for raw in raw_tiers:
            if not isinstance(raw, dict):
                continue
            tiers.append({
                "name": str(raw.get("name") or "").strip() or None,
                "standard": raw.get("standard") is True,
                "input": number_or_none(raw.get("input")),
                "output": number_or_none(raw.get("output")),
                "cache_read": number_or_none(raw.get("cache_read")),
            })
    if not tiers:
        tiers.append({
            "name": None,
            "standard": True,
            "input": number_or_none(item.get("input")),
            "output": number_or_none(item.get("output")),
            "cache_read": number_or_none(item.get("cache_read")),
        })
    return tiers


def _records_from_ai_payload(
    raw_models: Any,
    chunk_text: str,
    source_url: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """一段 AI 返回 → 通过证据校验的记录；档位价格数字必须在该段页面文本中字面出现。"""
    if not isinstance(raw_models, list):
        raise AIExtractionError("AI 没有返回 models 数组")
    normalized_chunk = re.sub(r"[\s_-]+", "", chunk_text).casefold()
    records: list[dict[str, Any]] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("model") or "").strip()
        if not name:
            continue
        if model_key(name) not in normalized_chunk:
            warnings.append(f"AI 返回的模型 {name} 不在页面文本中，已丢弃")
            continue
        tiers: list[dict[str, Any]] = []
        for tier in _parse_ai_tiers(item):
            missing = [
                label
                for label, value in (("输入价", tier["input"]), ("输出价", tier["output"]))
                if value is not None and value != 0 and not _number_in_text(value, chunk_text)
            ]
            if missing:
                warnings.append(f"模型 {name} 的{tier['name'] or '默认'}档{'、'.join(missing)}在页面文本中找不到，疑似幻觉，该档已丢弃")
                continue
            tiers.append(tier)
        if not tiers or all(tier["input"] is None and tier["output"] is None for tier in tiers):
            warnings.append(f"模型 {name} 的 AI 结果没有可用价格，已丢弃")
            continue
        baseline = _compose_baseline(tiers)
        currency = str(item.get("currency") or "").upper()
        currency = currency if currency in {"CNY", "USD"} else detect_currency(str(item.get("quote") or ""))
        records.append({
            "model": name,
            "model_key": model_key(name),
            "input_price": baseline["input"],
            "output_price": baseline["output"],
            "cache_read_price": baseline["cache_read"],
            "context": str(item.get("context") or "").strip() or None,
            "description": re.sub(r"\s+", " ", str(item.get("description") or "")).strip() or None,
            "currency": currency,
            "unit": f"{currency or '未知'}/1M tokens",
            "tiers": [
                {
                    "name": tier["name"],
                    "input_price": tier["input"],
                    "output_price": tier["output"],
                    "cache_read_price": tier["cache_read"],
                }
                for tier in tiers
            ],
            "source_url": source_url,
            "quote": redact_text(str(item.get("quote") or "")),
            "price_status": "candidate",
        })
    return records


def _ai_extract(
    text: str,
    source_url: str,
    ai_config: AIConfig,
    client: httpx.Client,
    deadline: float | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """AI 兜底抽取；返回 (通过证据校验的记录, 丢弃原因)。

    每个模型带档位数组（空闲/高峰时段、不同上下文档等），逐档做证据校验——
    档位价格数字必须在页面文本中字面出现，幻觉档剔除、其余保留；基准档取
    标准档（如高峰时段），保证折扣比较不受折扣档干扰。页面超长时剥标签分块
    逐段提取、按模型键合并（先到先得）；单段失败不放弃整页，全部段失败才告整页失败。
    deadline 是整页提取的总时长上界（time.monotonic() 时刻），到点后没跑的段记警告放弃。
    """
    warnings: list[str] = []
    chunks = _split_for_ai(text, ai_config.max_input_chars)

    def extract_chunk(chunk: str, depth: int = 0) -> list[dict[str, Any]]:
        """单段提取；JSON 输出装不下整段模型时对半拆段重试（大表一个回复塞不下）。"""
        try:
            _model, response = request_with_model_fallback(
                ai_config,
                _AI_SYSTEM_PROMPT,
                clip(chunk, ai_config.max_input_chars),
                max_tokens=max(ai_config.max_tokens, _AI_EXTRACT_MAX_TOKENS),
                client=client,
                scene="定价页价格抽取",
                deadline=deadline,
            )
            payload = json_content(ai_content(ai_config.api_format, response.json()))
            return _records_from_ai_payload(payload.get("models"), chunk, source_url, warnings)
        except (AIExtractionError, httpx.HTTPError, PriceMonitorError) as exc:
            if depth >= 2 or len(chunk) < 6000:
                raise
            warnings.append(f"单段提取失败（{str(exc)[:60]}），对半拆段重试")
            middle = len(chunk) // 2
            cut = chunk.find("\n", middle)
            if cut == -1 or cut > len(chunk) - 200:
                cut = middle
            return extract_chunk(chunk[:cut], depth + 1) + extract_chunk(chunk[cut:], depth + 1)

    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    failed_chunks = 0
    for index, chunk in enumerate(chunks):
        label = f"第 {index + 1}/{len(chunks)} 段" if len(chunks) > 1 else ""
        try:
            chunk_records = extract_chunk(chunk)
        except (AIExtractionError, httpx.HTTPError, PriceMonitorError) as exc:
            warnings.append(f"{label} AI 提取失败：{exc}".strip())
            failed_chunks += 1
            continue
        for record in chunk_records:
            key = str(record.get("model_key") or "")
            if key in seen_keys:
                continue
            seen_keys.add(key)
            records.append(record)
    if not records and failed_chunks and failed_chunks == len(chunks):
        raise AIExtractionError(warnings[0] or "AI 没有提取到任何价格")
    return records, warnings
