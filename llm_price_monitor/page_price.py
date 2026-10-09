"""通用定价页模型价格拉取：输入一个定价页 URL，输出该页的结构化模型价格。

与站点采集链路（adapters/ai 的 PriceRecord 体系）相互独立：站点采集面向中转站
new-api 接口并按配置的模型清单过滤；本模块面向厂商官方定价页，目标是"页面上的
全部模型价格"，不预设模型清单。

流水线：抓取 → 确定性解析（Markdown 表格 / HTML 表格 / JSON 价格表 / 组件属性
表格，任一命中即停）→ 同名 .md 原文探测（页面把表格做成客户端组件时）→
Headless 渲染兜底（JS 空壳页）→ AI 兜底（模型名与档位价格数字必须在页面文本中
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
import ast
import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
# 阿里云百炼惯用「模型ID/中文注解」同格写法（如 qwen3.8-max/Batch调用半价），
# 注解段带 CJK 才当注解剥掉——ZHIPU/GLM-5.3 这类路径式模型名不含 CJK，保留原样
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def clean_model_name(name: str) -> str:
    """剥掉模型名单元格里的注解（斜杠/换行后的中文说明、尾部括号限定符），保留原始大小写。

    阿里云百炼把「Batch调用半价」这类注解跟在模型 ID 后面，分隔既有斜杠也有换行；
    注解段带 CJK 才剥——ZHIPU/GLM-5.3 这类路径式模型名不含 CJK，保留原样。
    """
    text = name.strip()
    segments = re.split(r"/|[\r\n]+", text)
    for index, segment in enumerate(segments[1:], start=1):
        if _CJK_PATTERN.search(segment):
            text = segments[0]
            break
    return _TRAILING_QUALIFIER_PATTERN.sub("", text).strip()


# 上下文窗口文本的取数与单位：K/M 按二进制口径（与 Kimi 官方标注 1,048,576 tokens 一致）
_CONTEXT_TOKEN_PATTERN = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(K|M|B|万|亿)?", re.IGNORECASE)
_CONTEXT_TOKEN_SCALE = {"K": 1024, "M": 1024**2, "B": 1024**3, "万": 10_000, "亿": 10_000**2}
# 显式输出上限标注：标注后的数字是输出上限而不是上下文
_OUTPUT_LABEL_PATTERN = re.compile(r"最大输出|输出上限|输出|max(?:imum)?[\s-]*output", re.IGNORECASE)
# "8K~1M"这类可取值区间读不出单一上限，宁缺勿猜
_CONTEXT_RANGE_PATTERN = re.compile(r"\d[^/／]*[-~～–—]\s*\d")


def _context_tokens(text: str) -> int | None:
    """一段文本里第一个「数字+单位」→ token 数；读不出 ≥1 的整数返回 None。"""
    match = _CONTEXT_TOKEN_PATTERN.search(text)
    if match is None:
        return None
    value = float(match.group(1).rstrip(",.、；;，").replace(",", ""))
    scale = _CONTEXT_TOKEN_SCALE.get((match.group(2) or "").upper(), 1)
    tokens = int(value * scale)
    return tokens if tokens >= 1 else None


def parse_context_limit(text: str | None) -> dict[str, int | None] | None:
    """定价页上下文列原文 → {"context": 上限, "output": 输出上限或 None}；解析不出返回 None。

    认三种形态：单值（"1M"、"1,048,576 tokens"）、斜杠对（"200K/32K"）、
    输出标注（"256K；最大输出 32K"）。时段名（"高峰时段"）、区间（"8K~1M"）、
    输入价格分档（"[0, 32k)"）这些不是上下文窗口的文本一律返回 None，不猜。
    """
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw or raw.casefold() in _EMPTY_CELLS or not re.search(r"\d", raw):
        return None
    if _CONTEXT_RANGE_PATTERN.search(raw):
        return None
    parts = [part for part in re.split(r"[/／]", raw) if part.strip()]
    if len(parts) >= 2:
        context = _context_tokens(parts[0])
        if context is None:
            return None
        return {"context": context, "output": _context_tokens(parts[1])}
    label = _OUTPUT_LABEL_PATTERN.search(raw)
    if label:
        context = _context_tokens(raw[: label.start()])
        if context is None:
            return None
        return {"context": context, "output": _context_tokens(raw[label.end() :])}
    context = _context_tokens(raw)
    if context is None:
        return None
    return {"context": context, "output": None}


def _match_column(header: str) -> str | None:
    """表头单元格 → 语义列名。按表头名匹配而非列号（部分厂商的模型表会多一列「输入模态」）。"""
    h = re.sub(r"\s+", "", header).casefold()
    if not h or "输入模态" in h or "modality" in h:
        return None
    if re.search(r"缓存存储|storage|小时", h):
        return None  # 存储费按小时计，不是缓存读/写单价
    if "模型名称" in h or "模型id" in h or "modelid" in h or h in {"模型", "名称", "model", "modelname"}:
        return "model"
    if "简介" in h:
        return "description"
    if "上下文" in h or "context" in h or "时段" in h or "档位" in h or re.search(r"token(数|范围|长度)", h):
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
    # 币种标注常在单元格不在表头（阿里云「24元」），表头读不出来时看首行
    currency = detect_currency(*header, *(rows[0] if rows else []))
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
                # 记录级上下文/简介取首次出现的行（同模型多行是档位拆分，这两列不随档位变）
                "context": context or None,
                "description": re.sub(r"\s+", " ", _cell(cells, columns, "description")).strip() or None,
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


# 转置规格表（DeepSeek 官方定价页形态）的识别特征：价格单元格必须带币种标识，
# 免得把并发数、上下文窗口、BASE URL 当成价格；列头单元格出现属性列词汇说明
# 那是普通价目表，不是「模型当列头」的转置表
_PRICE_MARKED_CELL = re.compile(r"元|¥|￥|\$|美元|usd", re.IGNORECASE)
_NON_MODEL_HEADER_CELL = re.compile(r"输入|输出|价格|上下文|缓存|并发|简介|描述|时段|版本|modality", re.IGNORECASE)
_TRANSPOSED_TIER_LABEL = re.compile(
    r"空闲|高峰|标准|默认|正常|时段|peak|standard|default|normal|off-peak|idle", re.IGNORECASE
)


def _cell_category(cell: str) -> str | None:
    """定价行的说明单元格 → 计费类别。

    缓存命中优先于输入/输出；输出必须先于输入判——「输出（输入<=32k）」这类
    档位注解里同时含两个词，谁在句首才算谁。
    """
    text = cell.strip()
    if not text:
        return None
    if _CACHE_HIT_TIER_PATTERN.search(text):
        return "cache_read"
    lowered = text.casefold()
    if "输出" in text or "output" in lowered:
        return "output"
    if "输入" in text or "input" in lowered:
        return "input"
    return None


def _records_from_transposed_grid(
    header: list[str],
    rows: list[list[str]],
    *,
    source_url: str,
) -> list[dict[str, Any]] | None:
    """转置规格表 → 价格记录：模型名当列头（第一行「模型 | 名A | 名B」），价格按行排布。

    价格单元格按出现顺序对齐到模型列；合并单元格让部分行缺类别格，沿用上一行
    的计费类别。基准价组合复用 AI 档位同款逻辑（输入取缓存未命中标准档、输出取
    标准档、缓存命中价取命中档），全部档位进 tiers。
    """
    head = re.sub(r"\s+", "", header[0]).casefold() if header else ""
    if "模型" not in head and head not in {"model", "modelname"}:
        return None
    # 模型名可能不从第 1 列开始：表头标签格 colspan 横跨时（真实 DeepSeek 页「模型」
    # 占 3 列），rowspan/colspan 展开会把标签重复填进中间列——与表头标签同文的格子
    # 与补齐空格都不是模型列
    name_columns: list[tuple[int, str]] = []
    for index, cell in enumerate(header[1:], start=1):
        text = clean_model_name(cell.strip())
        if not text or re.sub(r"\s+", "", cell).casefold() == head:
            continue
        if _PRICE_MARKED_CELL.search(cell) or _NON_MODEL_HEADER_CELL.search(text):
            return None  # 列头混着属性词或价格，不是转置规格表
        name_columns.append((index, text))
    if not name_columns:
        return None
    names = [name for _, name in name_columns]

    every_cell = [cell for row in rows for cell in row]
    currency = detect_currency(*every_cell)
    scale = unit_scale(" ".join(every_cell))
    unit = f"{currency or '未知'}/1M tokens"

    per_model: list[list[dict[str, Any]]] = [[] for _ in names]
    contexts: dict[int, str] = {}
    descriptions: dict[int, str] = {}
    quote_parts: list[str] = []
    category: str | None = None
    category_text = ""
    name_column_set = {index for index, _ in name_columns}
    for row in rows:
        price_cells: list[float] = []
        for cell in row:
            if not _PRICE_MARKED_CELL.search(cell):
                continue
            value, _has = price_value(cell)
            if value is not None:
                price_cells.append(value)
        labels = [cell for cell in row if not _PRICE_MARKED_CELL.search(cell)]
        if not price_cells:
            # 无价格的行只认上下文/简介：值取模型列（colspan 合并展开后同值即全模型适用），其余不猜
            label_text = " ".join(cell for index, cell in enumerate(row) if index not in name_column_set)
            values = [row[index].strip() if index < len(row) else "" for index, _ in name_columns]
            distinct = list(dict.fromkeys(v for v in values if v))
            if len(distinct) == 1:
                values = distinct * len(names)
            elif len(values) != len(names) or any(not v for v in values):
                continue
            if "上下文" in label_text or "context" in label_text.casefold():
                contexts = {index: value for index, value in enumerate(values)}
            elif "简介" in label_text or "描述" in label_text or "description" in label_text.casefold():
                descriptions = {index: value for index, value in enumerate(values)}
            continue
        if len(price_cells) != len(names):
            continue  # 价格个数与模型列数对不上的行（colspan 错位）不猜
        row_category = next(((text, _cell_category(text)) for text in labels if _cell_category(text)), None)
        if row_category is not None:
            category_text, category = row_category
        tier_text = next((text for text in labels if _TRANSPOSED_TIER_LABEL.search(text)), "") or category_text
        if category is None:
            continue  # 计费类别读不出（无类别格可沿用），不猜
        quote_parts.append(" | ".join(cell.strip() for cell in row))
        for model_index, value in enumerate(price_cells):
            per_model[model_index].append({
                "name": " ".join(filter(None, dict.fromkeys((category_text, tier_text.strip())))) or None,
                "standard": bool(_STANDARD_TIER_PATTERN.search(tier_text)),
                "input": value * scale if category == "input" else None,
                "output": value * scale if category == "output" else None,
                "cache_read": value * scale if category == "cache_read" else None,
            })

    records: list[dict[str, Any]] = []
    for index, (name, tiers) in enumerate(zip(names, per_model)):
        if not tiers:
            continue
        baseline = _compose_baseline(tiers)
        records.append({
            "model": name,
            "model_key": model_key(name),
            "input_price": baseline["input"],
            "output_price": baseline["output"],
            "cache_read_price": baseline["cache_read"],
            "context": contexts.get(index),
            "description": descriptions.get(index),
            "currency": currency,
            "unit": unit,
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
            "quote": redact_text(" | ".join(quote_parts)),
        })
    return records or None


def _records_from_categorized_rows(
    header: list[str],
    rows: list[list[str]],
    *,
    source_url: str,
) -> list[dict[str, Any]] | None:
    """计费类别行表（百度千帆形态）→ 价格记录：输入/输出各自占一行，价格在「在线推理」这类服务价列。

    表形：模型列 + 类别列（子项如「输入（输入<=32k）」）+ 服务价列 + 单位列，rowspan
    合并的模型格已由 _GridCollector 展开补齐。基准价取该模型第一条输入行与第一条
    输出行；全部行进 tiers，档位注解原文保留（交给 parse_context_limit 判废，不猜）。
    """
    columns = [_match_column(cell) for cell in header]
    if "model" not in columns:
        return None
    model_index = columns.index("model")
    # 类别列：除模型列外，「输入/输出」开头格最多的那列
    category_index, category_hits = -1, 0
    for index in range(len(header)):
        if index == model_index:
            continue
        hits = sum(1 for row in rows if index < len(row) and _cell_category(row[index]) in {"input", "output"})
        if hits > category_hits:
            category_index, category_hits = index, hits
    if category_hits < 2:
        return None
    # 价格列：表头带单价/价格/在线的服务价列优先，否则不猜
    price_index = next(
        (
            index
            for index, head in enumerate(header)
            if index not in (model_index, category_index) and re.search(r"单价|价格|在线|售价|price", head, re.IGNORECASE)
        ),
        -1,
    )
    if price_index < 0:
        return None
    # 只认按 token 计价的表：预付费包月（元/个/月）、按次计费这类不是 token 单价，不进价目
    unit_index = header.index("单位") if "单位" in header else -1
    token_priced = "token" in " ".join(header).casefold() or (
        unit_index >= 0 and any("token" in row[unit_index].casefold() for row in rows)
    )
    if not token_priced:
        return None
    currency = detect_currency(*header, *(cell for row in rows for cell in row)) or "CNY"
    unit = f"{currency}/1M tokens"
    _CATEGORY_FIELD = {"input": "input_price", "output": "output_price", "cache_read": "cache_read_price"}

    by_model: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    for row in rows:
        name = clean_model_name(row[model_index] if model_index < len(row) else "")
        if not name:
            continue
        category = _cell_category(row[category_index])
        if category is None:
            continue
        value, has = price_value(row[price_index] if price_index < len(row) else "")
        if not has:
            continue  # 批量推理 '-' 之类的空价格：该行不进档位
        scale = unit_scale(row[unit_index] if 0 <= unit_index < len(row) else "")
        tier = {
            "name": row[category_index].strip(),
            "input_price": value * scale if category == "input" else None,
            "output_price": value * scale if category == "output" else None,
            "cache_read_price": value * scale if category == "cache_read" else None,
        }
        key = model_key(name)
        record = by_model.get(key)
        if record is None:
            record = {
                "model": name,
                "model_key": key,
                "input_price": None,
                "output_price": None,
                "cache_read_price": None,
                "currency": currency,
                "unit": unit,
                "tiers": [],
                "source_url": source_url,
                "quotes": [],
            }
            by_model[key] = record
            records.append(record)
        field = _CATEGORY_FIELD[category]
        if record[field] is None:
            record[field] = value * scale  # 基准价：该模型此类别第一条行（文档序即最基础档）
        record["tiers"].append(tier)
        record["quotes"].append(" | ".join(cell.strip() for cell in row))
    records = [
        {**record, "quote": redact_text(" | ".join(dict.fromkeys(record.pop("quotes"))))}
        for record in records
        if record["input_price"] is not None or record["output_price"] is not None or record["cache_read_price"] is not None
    ]
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
    """收集 HTML 里的 <table> 行列文本（支持一层嵌套）；列映射复用 Markdown 同款逻辑。

    colspan/rowspan 合并单元格按锚点值展开补齐成矩形（百度千帆的模型列下推多行、
    阿里云的说明列横跨等都靠这个对齐），空单元格与被合并覆盖的格子都能区分。
    """

    # colspan/rowspan 防御上限：正常价目表远够，坏页面防止内存爆掉
    _MAX_SPAN = 1000

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[tuple[list[str], list[list[str]]]] = []
        # 每层嵌套一个表格 frame dict：row 当前行号、buffer 当前单元格文本、
        # span 待落格的 (colspan, rowspan)、filled (行,列)→文本（含合并展开）、nrows 行数、width 列数
        self._stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "table":
            self._stack.append({"row": -1, "buffer": None, "span": (1, 1), "filled": {}, "nrows": 0, "width": 0})
        elif self._stack:
            frame = self._stack[-1]
            if tag == "tr":
                frame["row"] += 1
                frame["buffer"] = None
            elif tag in {"td", "th"} and frame["row"] >= 0:
                raw = {key: value for key, value in attrs}
                frame["span"] = (self._span(raw.get("colspan")), self._span(raw.get("rowspan")))
                frame["buffer"] = []

    @classmethod
    def _span(cls, raw: Any) -> int:
        try:
            value = int(str(raw))
        except (TypeError, ValueError):
            return 1
        return min(max(value, 1), cls._MAX_SPAN)

    def handle_data(self, data: str) -> None:
        if self._stack and self._stack[-1]["buffer"] is not None:
            self._stack[-1]["buffer"].append(data)

    def _place_cell(self, frame: dict[str, Any]) -> None:
        """把当前单元格文本按 (colspan, rowspan) 落进 filled 矩阵，列号跳过已被占用的槽。"""
        text = "".join(frame["buffer"]).strip()
        colspan, rowspan = frame["span"]
        frame["span"] = (1, 1)
        row = frame["row"]
        col = 0
        while (row, col) in frame["filled"]:
            col += 1  # 跳过本行已被上方 rowspan 覆盖或已放置的列
        for r in range(rowspan):
            for c in range(col, col + colspan):
                frame["filled"][(row + r, c)] = text
        frame["nrows"] = max(frame["nrows"], row + rowspan)
        frame["width"] = max(frame["width"], col + colspan)
        frame["buffer"] = None

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        frame = self._stack[-1]
        if tag in {"td", "th"} and frame["buffer"] is not None:
            self._place_cell(frame)
        elif tag == "tr" and frame["row"] >= 0:
            if frame["buffer"] is not None:
                self._place_cell(frame)  # 残缺 HTML 缺 </td>：行尾把开着的格子落掉，不丢文本
            frame["buffer"] = None
        elif tag == "table":
            self._stack.pop()
            if frame["nrows"] < 2:
                return
            materialized = [
                [frame["filled"].get((row, col), "") for col in range(frame["width"])]
                for row in range(frame["nrows"])
            ]
            header, rows = materialized[0], materialized[1:]
            if any(cell for cell in header) and rows:
                self.tables.append((header, rows))


def parse_html_tables(text: str, source_url: str) -> list[dict[str, Any]] | None:
    """HTML 定价页（静态含 <table>）→ 价格记录；标准库解析，不引入 bs4/lxml。"""
    collector = _GridCollector()
    try:
        collector.feed(text)
    except Exception:
        return None  # 残缺 HTML 交给Headless 渲染或 AI 兜底
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for header, rows in collector.tables:
        # 转置规格表（模型当列头）与计费类别行表（百度千帆形态）都没有标准的
        # 输入/输出列，通用列映射落空后按这两种口径再试
        parsed = (
            _records_from_grid(header, rows, source_url=source_url)
            or _records_from_categorized_rows(header, rows, source_url=source_url)
            or _records_from_transposed_grid(header, rows, source_url=source_url)
        )
        for record in parsed or []:
            # 同模型出现在多张表（促销/原价对照、分区复述）：先到先得，与 AI 合并同规则
            if record["model_key"] in seen:
                continue
            seen.add(record["model_key"])
            records.append(record)
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


def _extract_bracketed(text: str, start: int) -> str | None:
    """从 text[start]（应为 '['）起取配对方括号内的原文；字符串字面量里的括号不参与配对。"""
    depth = 0
    in_string = False
    quote = ""
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_string = False
            continue
        if ch in {'"', "'"}:
            in_string, quote = True, ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


# JS 对象字面量的键可不带引号（{ title: "模型" }），转成 JSON 前先补引号
_JS_OBJECT_KEY = re.compile(r"([{,]\s*)([A-Za-z_]\w*)\s*:")


def _loads_js_array(raw: str) -> Any | None:
    """宽松解析 JS 数组字面量（键可不带引号、允许尾逗号）；不是合法结构返回 None，不猜。"""
    quoted = _JS_OBJECT_KEY.sub(lambda m: f'{m.group(1)}"{m.group(2)}":', raw)
    try:
        return json.loads(quoted)
    except ValueError:
        pass
    try:
        # JS 的尾逗号 JSON 不收，Python 字面量语法恰好兼容
        return ast.literal_eval(quoted)
    except (ValueError, SyntaxError):
        return None


# JSX 属性形态 rows={[...]}：= 与 [ 之间隔着一个表达式容器 {
_COMPONENT_COLUMNS = re.compile(r"columns\s*=\s*\{?\s*")
_COMPONENT_ROWS = re.compile(r"rows\s*=\s*\{?\s*")


def parse_component_tables(text: str, source_url: str) -> list[dict[str, Any]] | None:
    """组件化定价表（如 Mintlify DocTable 的 columns/rows 属性）→ 价格记录。

    厂商把定价表从 Markdown 迁成 React 组件后，页面 HTML 里不再有 <table>，
    但 .md 原文的组件属性里数据原样还在；取 columns 的 title 当表头、rows 当
    数据行，复用表格解析的列匹配与价格折算。组件定义处的空 columns/rows=[]
    解析不出表头自然跳过，不误报。
    """
    records: list[dict[str, Any]] = []
    for rows_match in _COMPONENT_ROWS.finditer(text):
        columns_match = None
        for candidate in _COMPONENT_COLUMNS.finditer(text, 0, rows_match.start()):
            columns_match = candidate  # 与最近的 columns= 配对（一页多表时逐表配对）
        if columns_match is None:
            continue
        columns_raw = _extract_bracketed(text, columns_match.end()) if text[columns_match.end()] == "[" else None
        rows_raw = _extract_bracketed(text, rows_match.end()) if text[rows_match.end()] == "[" else None
        if not columns_raw or not rows_raw:
            continue
        columns = _loads_js_array(columns_raw)
        rows = _loads_js_array(rows_raw)
        if not isinstance(columns, list) or not isinstance(rows, list):
            continue
        header = [
            str(col.get("title") or "") if isinstance(col, dict) else str(col)
            for col in columns
        ]
        grid = [
            [
                str(cell) if isinstance(cell, (str, int, float)) else ("" if cell is None else json.dumps(cell, ensure_ascii=False))
                for cell in row
            ]
            for row in rows
            if isinstance(row, list)
        ]
        parsed = _records_from_grid(header, grid, source_url=source_url)
        if parsed:
            records.extend(parsed)
    return records or None


_PARSERS = (
    ("md", parse_markdown_tables),
    ("html", parse_html_tables),
    ("json", parse_json_entries),
    ("component", parse_component_tables),
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
    '"context":"上下文窗口原文或null","description":"模型简介原文或null",'
    '"currency":"CNY或USD","quote":"价格所在的原文片段"}]}。'
    "同一模型在页面里有多档价格（如高峰/空闲时段、不同上下文长度、不同并发规格）时，"
    "每档一个元素，name 照页面原文抄写；页面有时间定义说明"
    "（如“高峰时段为北京时间周一至周五 9:00-12:00、14:00-18:00，其余为空闲时段”）"
    "要把定义并进对应档位的 name。适用的标准档位（页面标明空闲时段是折扣价、高峰时段是标准价时，"
    "标准档指高峰时段）把 standard 标为 true，页面没有说明就都不标。"
    "只有一个价的模型 tiers 只放一个元素、name 填 null。"
    "价格数值统一换算成每百万 tokens；页面标注免费的填 0；页面里没有的价格填 null。"
    "context 抄页面上模型简介表或文字里的上下文窗口原文（如 1M、200K/32K、最大输出 8K），"
    "description 抄模型简介原文并压缩到 40 字以内（超长只取核心一句），"
    "页面里没有就填 null，不要编造；这两个字段越长越容易挤爆输出，务必精简。"
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


# 缓存命中档的名称特征：输入按缓存命中/未命中分两行时区分计费类别
_CACHE_HIT_TIER_PATTERN = re.compile(r"缓存命中|命中缓存|cache\s*hit|cached", re.IGNORECASE)


def _compose_baseline(tiers: list[dict[str, Any]]) -> dict[str, Any]:
    """基准价三件套（输入/输出/缓存命中），从档位列表组合而来。

    常见形态每档自带输入+输出（如上下文分档、时段整体半价），沿用单档基准；
    分维表（如 DeepSeek：缓存命中/未命中输入与输出各自一行 × 空闲/高峰）没有
    一档同时带输入和输出，按计费类别各选标准档组合——输入取缓存未命中档、
    缓存命中价取缓存命中档、输出取输出档；页面只标缓存命中输入价时以其兜底。
    """
    cache_tiers = [
        tier for tier in tiers
        if tier["cache_read"] is not None or (tier["name"] and _CACHE_HIT_TIER_PATTERN.search(tier["name"]))
    ]
    if any(tier["input"] is not None and tier["output"] is not None for tier in tiers) or not cache_tiers:
        baseline = _pick_baseline_tier(tiers)
        if baseline["cache_read"] is None:
            cache_only = [
                tier for tier in tiers
                if tier["cache_read"] is not None and tier["input"] is None and tier["output"] is None
            ]
            if cache_only:
                baseline = {**baseline, "cache_read": _pick_baseline_tier(cache_only)["cache_read"]}
        return baseline

    def _pick(group: list[dict[str, Any]]) -> dict[str, Any] | None:
        return _pick_baseline_tier(group) if group else None

    input_tiers = [
        tier for tier in tiers
        if tier["input"] is not None and not (tier["name"] and _CACHE_HIT_TIER_PATTERN.search(tier["name"]))
    ]
    input_source = _pick(input_tiers) or _pick(cache_tiers) or tiers[0]
    cache_source = _pick(cache_tiers) or input_source
    output_source = _pick([tier for tier in tiers if tier["output"] is not None])
    return {
        "input": input_source["input"],
        "output": output_source["output"] if output_source else None,
        "cache_read": cache_source["cache_read"],
    }


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
        # Mintlify 系文档站每个页面都有同名 .md 原文；页面把定价表做成客户端组件时
        # HTML 里没有表格，探测同名 .md 拿结构化原文再解析一遍，比渲染/AI 都稳，
        # 成本只在整条静态链路落空后的一次 GET
        probe_source = urlsplit(final_url)
        if (
            parsed is None
            and not probe_source.path.lower().endswith((".md", ".json", ".txt"))
            and probe_source.path.rstrip("/")
        ):
            probe_url = urlunsplit(probe_source._replace(path=probe_source.path.rstrip("/") + ".md"))
            try:
                probe = client.get(probe_url)
                if probe.status_code == 200 and probe.text:
                    parsed = _parse_text(probe.text, final_url, "md-source")
                    if parsed:
                        text = probe.text  # 后续 AI 复核也优先看这份带表格结构的原文
            except httpx.HTTPError:
                pass  # 探测只是兜底路径，失败照常走渲染/AI
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
