"""通用定价页模型价格拉取：输入一个定价页 URL，输出该页的结构化模型价格。

与站点采集链路（adapters/ai 的 PriceRecord 体系）相互独立：站点采集面向中转站
new-api 接口并按配置的模型清单过滤；本模块面向厂商官方定价页，目标是"页面上的
全部模型价格"，不预设模型清单。

流水线：抓取 → 确定性解析（Markdown 表格 / HTML 表格 / JSON 价格表，任一命中即停）
→ Headless 渲染兜底（JS 空壳页）→ AI 兜底（模型名与档位价格数字必须在页面文本中
字面出现，防幻觉，结果一律标记 candidate）。确定性解析"出了结果但可疑"（模型名带
档位/促销注释、合并行、同模型重复档价）时同样交给 AI 复核：AI 有产出就以 AI 为准，
AI 没产出则把可疑记录整页降级为 candidate——宁可多人工复核，不静默采脏价。页面超过
max_input_chars 时先剥掉 script/style 再按行分块，逐段提取后按模型键合并。同一模型的
多档价格（高峰/空闲时段、不同上下文档）逐档保留：AI 档位带 name（含页面里的时段定义），
静态解析把时段/档位列收进 context；AI 兜底的基准档取标准档（如高峰时段），静态解析按
页面行序取第一档。

币种按页面如实标注（「元」→ CNY、`$`/美元 → USD），单位统一折算成 /1M tokens，
不做汇率折算。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from llm_price_monitor.adapters import parse_base_price_entries
from llm_price_monitor.ai import (
    AIExtractionError,
    ai_content,
    json_content,
    request_with_model_fallback,
)
from llm_price_monitor.browser_fetch import fetch_page_html
from llm_price_monitor.catalog.normalize import model_key
from llm_price_monitor.config import AIConfig, PriceMonitorError
from llm_price_monitor.evidence import clip, redact_text
from llm_price_monitor.units import number_or_none
from llm_price_monitor.useragent import DEFAULT_BROWSER_USER_AGENT

DEFAULT_TIMEOUT = 45.0

# 与站点采集一致的判空单元格集合；破折号是定价页最常见的空值写法
_EMPTY_CELLS = {"", "—", "-", "–", "/", "n/a", "na", "null", "none"}
_FREE_PATTERN = re.compile(r"免费|free", re.IGNORECASE)
_NUMBER_PATTERN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_CNY_PATTERN = re.compile(r"元|¥|￥|人民币|rmb|\bcny\b", re.IGNORECASE)
_USD_PATTERN = re.compile(r"\$|美元|usd|dollar", re.IGNORECASE)


def detect_currency(*texts: str | None) -> str | None:
    """从表头/单位文本里判断币种：人民币标识优先于美元（国内定价页默认口径）。"""
    joined = " ".join(text for text in texts if text)
    if _CNY_PATTERN.search(joined):
        return "CNY"
    if _USD_PATTERN.search(joined):
        return "USD"
    return None


def unit_scale(header_text: str) -> float:
    """页面标价换算成"每百万 tokens"要乘的系数；识别不出按国内定价页惯例的百万口径。"""
    text = header_text.replace(" ", "")
    if re.search(r"百万|1M|/M(?![A-Za-z])|million", text, re.IGNORECASE):
        return 1.0
    if re.search(r"千|1K|每千|thousand", text, re.IGNORECASE):
        return 1000.0
    if re.search(r"每token|/token|per-token", text, re.IGNORECASE):
        return 1_000_000.0
    return 1.0


def price_value(cell: str) -> tuple[float | None, bool]:
    """单元格 → (数值, 是否可解析)。免费按 0 处理；空/破折号视为没有这个价格。"""
    text = cell.strip()
    if text.casefold() in _EMPTY_CELLS:
        return None, False
    if _FREE_PATTERN.search(text):
        return 0.0, True
    match = _NUMBER_PATTERN.search(text.replace(" ", ""))
    if match is None:
        return None, False
    return number_or_none(match.group(0).replace(",", "")), True


# 国内定价页惯用「模型名（200K）」把上下文档位写进名称单元格（如 GLM-4.7-Flash（200K）），
# 尾部的括号限定符不是模型名的一部分，剥掉后才能与厂商目录的模型键对上
_TRAILING_QUALIFIER_PATTERN = re.compile(r"[（(][^（）()]*[)）]\s*$")


def clean_model_name(name: str) -> str:
    """去掉模型名尾部的括号限定符（上下文档位等），保留原始大小写。"""
    return _TRAILING_QUALIFIER_PATTERN.sub("", name).strip()


def _match_column(header: str) -> str | None:
    """表头单元格 → 语义列名。按表头名匹配而非列号（部分厂商的模型表会多一列「输入模态」）。"""
    h = re.sub(r"\s+", "", header).casefold()
    if not h or "输入模态" in h or "modality" in h:
        return None
    if re.search(r"缓存存储|storage|小时", h):
        return None  # 存储费按小时计，不是缓存读/写单价
    if "模型名称" in h or h in {"模型", "名称", "model", "modelname"}:
        return "model"
    if "上下文" in h or "context" in h or "时段" in h or "档位" in h:
        return "context"
    if "缓存命中" in h or "缓存读" in h or "cacheread" in h:
        return "cache_read"
    has_price_word = bool(re.search(r"单价|价格|price|cost", h))
    if ("输入" in h or "input" in h or "prompt" in h) and (has_price_word or h in {"输入", "input"}):
        return "input"
    if ("输出" in h or "output" in h or "completion" in h) and (has_price_word or h in {"输出", "output"}):
        return "output"
    return None


def _cell(cells: list[str], columns: list[str | None], name: str) -> str:
    if name not in columns:
        return ""
    index = columns.index(name)
    return cells[index] if index < len(cells) else ""


def _records_from_grid(
    header: list[str],
    rows: list[list[str]],
    *,
    source_url: str,
) -> list[dict[str, Any]] | None:
    """一张表 → 价格记录。同一模型多行（不同上下文档）第一行做基准，全部档位进 tiers（与站点价的 tiers 约定一致）。"""
    columns = [_match_column(cell) for cell in header]
    if "model" not in columns or ("input" not in columns and "output" not in columns):
        return None
    currency = detect_currency(*header)
    scale = unit_scale(" ".join(header))
    unit = f"{currency or '未知'}/1M tokens"

    records: list[dict[str, Any]] = []
    by_model: dict[str, dict[str, Any]] = {}
    for cells in rows:
        name = clean_model_name(_cell(cells, columns, "model"))
        if not name:
            continue
        input_value, has_input = price_value(_cell(cells, columns, "input"))
        output_value, has_output = price_value(_cell(cells, columns, "output"))
        # 既没有输入价也没有输出价的行是表头重复或说明行，跳过
        if not has_input and not has_output:
            continue
        cache_value, _ = price_value(_cell(cells, columns, "cache_read"))
        context = _cell(cells, columns, "context").strip()
        tier = {
            key: value
            for key, value in (
                ("context", context or None),
                ("input_price", input_value * scale if input_value is not None else None),
                ("output_price", output_value * scale if output_value is not None else None),
            )
            if value is not None
        }
        key = model_key(name)
        record = by_model.get(key)
        if record is None:
            record = {
                "model": name,
                "model_key": key,
                "input_price": input_value * scale if input_value is not None else None,
                "output_price": output_value * scale if output_value is not None else None,
                "cache_read_price": cache_value * scale if cache_value is not None else None,
                "currency": currency,
                "unit": unit,
                "tiers": [tier] if tier else [],
                "source_url": source_url,
                "quote": redact_text(" | ".join(cell.strip() for cell in cells)),
            }
            by_model[key] = record
            records.append(record)
            continue
        if tier:
            record["tiers"].append(tier)
    return records or None


def _split_markdown_row(line: str) -> list[str]:
    r"""一行 Markdown 表格 → 单元格列表；`\|` 转义不切分。"""
    placeholder = "\x00"
    line = line.strip().removeprefix("|").removesuffix("|").replace("\\|", placeholder)
    return [cell.replace(placeholder, "|").strip() for cell in line.split("|")]


def parse_markdown_tables(text: str, source_url: str) -> list[dict[str, Any]] | None:
    """Markdown 定价页（Mintlify 系文档站直接提供 .md）→ 价格记录。"""
    lines = text.splitlines()
    records: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        if not lines[index].lstrip().startswith("|"):
            index += 1
            continue
        block: list[str] = []
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            block.append(lines[index])
            index += 1
        if len(block) < 2:
            continue
        header = _split_markdown_row(block[0])
        rows = [_split_markdown_row(line) for line in block[1:]]
        # 跳过 |---|---| 分隔行
        rows = [row for row in rows if not all(re.fullmatch(r":?-{2,}:?", cell) for cell in row if cell)]
        parsed = _records_from_grid(header, rows, source_url=source_url)
        if parsed:
            records.extend(parsed)
    return records or None


class _GridCollector(HTMLParser):
    """收集 HTML 里的 <table> 行列文本（支持一层嵌套）；列映射复用 Markdown 同款逻辑。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[tuple[list[str], list[list[str]]]] = []
        # 每层嵌套一个 [header, rows, current_row, current_cell]
        self._stack: list[list[Any]] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "table":
            self._stack.append([None, [], None, None])
        elif self._stack:
            frame = self._stack[-1]
            if tag == "tr" and frame[2] is None:
                frame[2] = []
            elif tag in {"td", "th"} and frame[2] is not None:
                frame[3] = []

    def handle_data(self, data: str) -> None:
        if self._stack and self._stack[-1][3] is not None:
            self._stack[-1][3].append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        frame = self._stack[-1]
        if tag in {"td", "th"} and frame[3] is not None:
            frame[2].append("".join(frame[3]).strip())
            frame[3] = None
        elif tag == "tr" and frame[2] is not None:
            if frame[0] is None:
                frame[0] = frame[2]
            else:
                frame[1].append(frame[2])
            frame[2] = None
        elif tag == "table":
            header, rows = frame[0], frame[1]
            self._stack.pop()
            if header and rows:
                self.tables.append((header, rows))


def parse_html_tables(text: str, source_url: str) -> list[dict[str, Any]] | None:
    """HTML 定价页（静态含 <table>）→ 价格记录；标准库解析，不引入 bs4/lxml。"""
    collector = _GridCollector()
    try:
        collector.feed(text)
    except Exception:
        return None  # 残缺 HTML 交给Headless 渲染或 AI 兜底
    records: list[dict[str, Any]] = []
    for header, rows in collector.tables:
        parsed = _records_from_grid(header, rows, source_url=source_url)
        if parsed:
            records.extend(parsed)
    return records or None


def parse_json_entries(text: str, source_url: str) -> list[dict[str, Any]] | None:
    """JSON / 压缩 JS 里的基准价表（models.dev 同构结构）→ 价格记录。

    `parse_base_price_entries` 的单位约定是 USD/1M tokens；页面文本带人民币
    标识时按 CNY 修正。
    """
    entries = parse_base_price_entries(text)
    if not entries:
        return None
    currency = detect_currency(text[:20000]) or "USD"
    unit = f"{currency}/1M tokens"
    records: list[dict[str, Any]] = []
    for entry in entries:
        models = entry.get("models") or []
        for name in models:
            records.append({
                "model": str(name),
                "model_key": model_key(str(name)),
                "input_price": entry.get("input"),
                "output_price": entry.get("output"),
                "cache_read_price": entry.get("cache_read"),
                "currency": currency,
                "unit": unit,
                "tiers": [],
                "source_url": source_url,
                "quote": redact_text(json.dumps(entry, ensure_ascii=False)),
            })
    return records or None


_PARSERS = (
    ("md", parse_markdown_tables),
    ("html", parse_html_tables),
    ("json", parse_json_entries),
)


def _parse_text(text: str, source_url: str, prefix: str) -> tuple[list[dict[str, Any]], str] | None:
    for kind, parser in _PARSERS:
        records = parser(text, source_url)
        if records:
            return records, f"{prefix}-{kind}"
    return None


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
_AI_EXTRACT_MAX_TOKENS = 12000
# 整页 AI 提取的总时长上界：超长页分块多、模型池回退会滚很久，没有上界会把抓取
# worker 挂住；到点后已提取的段照常保留（标待复核），没跑的段记警告放弃
_AI_PAGE_BUDGET_SECONDS = 600.0


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
    """价格数字的常见书写形式是否在页面文本中字面出现（与 ai.py 的证据校验同思路）。"""
    candidates = {str(value), f"{value:g}"}
    if value == int(value):
        candidates.add(str(int(value)))
        candidates.add(f"{int(value):,}")
    else:
        candidates.add(f"{value:,.2f}")
        candidates.add(f"{value:,}")
    return any(candidate in text for candidate in candidates)


_AI_SYSTEM_PROMPT = (
    "你是定价页数据抽取助手。从给定的网页文本中提取全部模型的 API 价格，"
    "只输出 JSON 对象，不要输出其他内容："
    '{"models":[{"model":"模型名","tiers":[{"name":"档位名","standard":true,'
    '"input":输入单价,"output":输出单价,"cache_read":缓存命中单价或null}],'
    '"currency":"CNY或USD","quote":"价格所在的原文片段"}]}。'
    "同一模型在页面里有多档价格（如高峰/空闲时段、不同上下文长度、不同并发规格）时，"
    "每档一个元素，name 照页面原文抄写；页面有时间定义说明"
    "（如“高峰时段为北京时间周一至周五 9:00-12:00、14:00-18:00，其余为空闲时段”）"
    "要把定义并进对应档位的 name。适用的标准档位（页面标明空闲时段是折扣价、高峰时段是标准价时，"
    "标准档指高峰时段）把 standard 标为 true，页面没有说明就都不标。"
    "只有一个价的模型 tiers 只放一个元素、name 填 null。"
    "价格数值统一换算成每百万 tokens；页面标注免费的填 0；页面里没有的价格填 null。"
    "不要编造页面里不存在的模型或价格；模型名照页面原文抄写。"
)

# 标准/默认档位的名称特征：页面把折扣档（如空闲时段）标出来时，基准应取标准档
_STANDARD_TIER_PATTERN = re.compile(r"高峰|标准|默认|正常|peak|standard|default", re.IGNORECASE)


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


def _pick_baseline_tier(tiers: list[dict[str, Any]]) -> dict[str, Any]:
    """基准档：AI 标记的标准档优先，其次档位名带高峰/标准等特征，否则第一档。"""
    for tier in tiers:
        if tier["standard"]:
            return tier
    for tier in tiers:
        if tier["name"] and _STANDARD_TIER_PATTERN.search(tier["name"]):
            return tier
    return tiers[0]


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
        baseline = _pick_baseline_tier(tiers)
        currency = str(item.get("currency") or "").upper()
        currency = currency if currency in {"CNY", "USD"} else detect_currency(str(item.get("quote") or ""))
        records.append({
            "model": name,
            "model_key": model_key(name),
            "input_price": baseline["input"],
            "output_price": baseline["output"],
            "cache_read_price": baseline["cache_read"],
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


def fetch_page_prices(
    url: str,
    *,
    ai_config: AIConfig | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    headless: bool = False,
    user_agent: str = DEFAULT_BROWSER_USER_AGENT,
) -> dict[str, Any]:
    """拉取一个定价页并解析出全部模型价格；返回 {"url","final_url","method","models","warnings"}。"""
    warnings: list[str] = []
    with httpx.Client(
        follow_redirects=True,
        timeout=timeout,
        transport=transport,
        headers={"User-Agent": user_agent},
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        final_url = str(response.url)
        text = response.text

        parsed = _parse_text(text, final_url, "static")
        rendered: str | None = None
        if parsed is None and headless:
            try:
                rendered = fetch_page_html(url, {}, user_agent=user_agent)
                parsed = _parse_text(rendered, final_url, "headless")
            except PriceMonitorError as exc:
                warnings.append(str(exc))
        # 解析"有结果但可疑"（档位注释/合并行/重复档价）不能当可信基准：交给 AI 复核，
        # AI 没有产出时整页降级为 candidate，宁可多人工复核也不静默采脏价
        suspect = parsed is not None and _records_suspicious(parsed[0])
        ai_enabled = ai_config is not None and ai_config.enabled
        if (parsed is None or suspect) and ai_enabled:
            try:
                # 渲染出过正文（无论解析成败）就优先给 AI 看渲染后的正文——原始 text 可能是 JS 空壳
                ai_text = rendered or text
                records, ai_warnings = _ai_extract(
                    ai_text, final_url, ai_config, client, deadline=time.monotonic() + _AI_PAGE_BUDGET_SECONDS,
                )
                warnings.extend(ai_warnings)
                if records:
                    parsed = (records, "ai")
                    suspect = False
            except (AIExtractionError, httpx.HTTPError, PriceMonitorError) as exc:
                warnings.append(f"AI 兜底失败：{exc}")
        elif ai_config is not None and not ai_config.enabled and (parsed is None or suspect):
            warnings.append("AI 配置已禁用，跳过 AI 兜底")
        if parsed is not None and suspect:
            for record in parsed[0]:
                record["price_status"] = "candidate"
            warnings.append("确定性解析结果可疑（模型名带档位注释或同模型多价），已整页标为待复核")

    records, method = parsed if parsed else ([], "none")
    if parsed is None:
        warnings.append("静态解析、Headless 渲染与 AI 兜底都没有拿到价格")
    return {
        "url": url,
        "final_url": final_url,
        "method": method,
        "models": records,
        "warnings": warnings,
    }


def resolve_ai_config(args: argparse.Namespace) -> tuple[AIConfig | None, str | None]:
    """CLI AI 配置解析：命令行参数优先，回落 PRICE_MONITOR_DB 库里的 ai document。

    返回 (配置, 说明)；说明非空时写进 warnings。
    """
    if args.ai_base_url or args.ai_api_key or args.ai_model:
        config = AIConfig(
            base_url=args.ai_base_url or "",
            models=(args.ai_model,) if args.ai_model else (),
            api_key=args.ai_api_key,
        )
        if not config.base_url:
            return None, "命令行只给了密钥/模型，没有 --ai-base-url，AI 兜底已跳过"
        return config, None
    if args.no_ai:
        return None, None
    db_path = Path(os.getenv("PRICE_MONITOR_DB") or "var/monitor.db")
    if not db_path.is_file():
        return None, f"找不到数据库 {db_path}（PRICE_MONITOR_DB），AI 兜底已跳过；可用 --ai-base-url/--ai-api-key 显式配置"
    try:
        from llm_price_monitor.config import config_from_store
        from llm_price_monitor.store import Store

        ai = config_from_store(Store(db_path)).ai
    except Exception as exc:  # 库损坏或配置非法：不阻断拉取，只放弃 AI 兜底
        return None, f"读取 AI 配置失败（{exc}），AI 兜底已跳过"
    if not ai.enabled:
        return None, "AI 配置处于禁用状态，AI 兜底已跳过"
    return ai, None


def main() -> None:
    parser = argparse.ArgumentParser(description="通用定价页模型价格拉取：输入 URL，输出结构化模型价格")
    parser.add_argument("url", help="定价页 URL（Markdown / HTML 表格 / JSON 价格表均可）")
    parser.add_argument("--out", help="结果写入的 JSON 文件；省略时打印到 stdout")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="抓取超时秒数（默认 45）")
    parser.add_argument("--headless", action="store_true", help="静态解析为空时尝试Headless 渲染（默认仅 JS 空壳页自动触发）")
    parser.add_argument("--no-ai", action="store_true", help="禁用 AI 兜底")
    parser.add_argument("--ai-base-url", help="AI 兜底的接口地址（默认读 PRICE_MONITOR_DB 库里的 ai 配置）")
    parser.add_argument("--ai-api-key", help="AI 兜底的密钥")
    parser.add_argument("--ai-model", help="AI 兜底使用的模型")
    args = parser.parse_args()

    ai_config, ai_note = resolve_ai_config(args)
    try:
        result = fetch_page_prices(args.url, ai_config=ai_config, timeout=args.timeout, headless=args.headless)
    except httpx.HTTPError as exc:
        result = {"url": args.url, "final_url": args.url, "method": "none", "models": [], "warnings": [f"抓取失败：{exc}"]}
    if ai_note:
        result["warnings"].append(ai_note)

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
        print(f"已写入 {args.out}：{len(result['models'])} 个模型（{result['method']}）")
    else:
        print(payload)
    for warning in result["warnings"]:
        print(f"警告：{warning}", file=sys.stderr)
    if not result["models"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
