"""确定性表格解析：Markdown 表格 / HTML 表格 / JSON 价格表 / 组件属性表格。

一条流水线按 _PARSERS 顺序任一命中即停；列映射按表头名匹配，多形态规格表
（普通价目表、转置规格表、计费类别行表）共用同一套价格原语与档位组合。
"""
from __future__ import annotations

import ast
import json
import re
from html.parser import HTMLParser
from typing import Any

from llm_price_monitor.adapters import parse_base_price_entries
from llm_price_monitor.catalog.normalize import model_key
from llm_price_monitor.evidence import redact_text

from .parsing import (
    clean_model_name,
    detect_currency,
    price_value,
    unit_scale,
)
from .tiers import _CACHE_HIT_TIER_PATTERN, _STANDARD_TIER_PATTERN, _compose_baseline


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


def parse_text_tables(text: str, source_url: str) -> list[dict[str, Any]] | None:
    """全部表格解析器依次尝试（Markdown / HTML / JSON / 组件注入），任一命中即返回条目。

    供 HTML 采集链路在 JS 内嵌条目（{category:...} 形态）解析不出时做确定性兜底：
    普通价目中心页面（表格 + 单元格带币种符号）由这里接住，不再落到 AI 抽价。"""
    result = _parse_text(text, source_url, "table")
    return result[0] if result else None
