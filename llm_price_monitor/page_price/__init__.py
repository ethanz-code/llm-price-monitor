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

包结构（原单文件 page_price.py 拆分，公有接口与私有名字全部在包命名空间再导出，
`from llm_price_monitor.page_price import X` 与 `llm_price_monitor.page_price.X`
——含测试 monkeypatch——继续可用）：
- `parsing`：币种识别、单位折算、价格单元格取数、模型名清洗与上下文窗口解析
- `tiers`：标准档挑选与基准价（输入/输出/缓存命中）三件套组合
- `tables`：四种确定性表格解析（Markdown / HTML / JSON / 组件属性）
- `ai_fallback`：AI 兜底抽取、超长页分块与逐档证据校验
- `pipeline`：fetch_page_prices 主流水线
- `cli`：命令行入口
"""
from __future__ import annotations

import httpx

from llm_price_monitor.browser_fetch import fetch_page_html
from llm_price_monitor.config import AIConfig

# 抓取超时秒数；定义在子模块导入之前：pipeline 的 fetch_page_prices 默认参数经
# 包属性回查这里，保持与旧单文件模块一致的取值来源。
DEFAULT_TIMEOUT = 45.0

from .parsing import (  # noqa: E402
    _CJK_PATTERN,
    _CONTEXT_RANGE_PATTERN,
    _CONTEXT_TOKEN_PATTERN,
    _CONTEXT_TOKEN_SCALE,
    _CNY_PATTERN,
    _EMPTY_CELLS,
    _FREE_PATTERN,
    _NUMBER_PATTERN,
    _OUTPUT_LABEL_PATTERN,
    _TRAILING_QUALIFIER_PATTERN,
    _USD_PATTERN,
    _context_tokens,
    clean_model_name,
    detect_currency,
    parse_context_limit,
    price_value,
    unit_scale,
)
from .tiers import (  # noqa: E402
    _CACHE_HIT_TIER_PATTERN,
    _STANDARD_TIER_PATTERN,
    _compose_baseline,
    _pick_baseline_tier,
)
from .tables import (  # noqa: E402
    _COMPONENT_COLUMNS,
    _COMPONENT_ROWS,
    _GridCollector,
    _JS_OBJECT_KEY,
    _NON_MODEL_HEADER_CELL,
    _PARSERS,
    _PRICE_MARKED_CELL,
    _TRANSPOSED_TIER_LABEL,
    _cell,
    _cell_category,
    _extract_bracketed,
    _loads_js_array,
    _match_column,
    _parse_text,
    _records_from_categorized_rows,
    _records_from_grid,
    _records_from_transposed_grid,
    _split_markdown_row,
    parse_component_tables,
    parse_html_tables,
    parse_json_entries,
    parse_markdown_tables,
)
from .ai_fallback import (  # noqa: E402
    _AI_CHUNK_CAP,
    _AI_EXTRACT_MAX_TOKENS,
    _AI_PAGE_BUDGET_SECONDS,
    _AI_SYSTEM_PROMPT,
    _SUSPICIOUS_NAME_PATTERN,
    _VisibleTextHarvester,
    _ai_extract,
    _number_in_text,
    _parse_ai_tiers,
    _records_from_ai_payload,
    _records_suspicious,
    _slim_html_for_ai,
    _split_for_ai,
)
from .pipeline import fetch_page_prices  # noqa: E402
from .cli import main, resolve_ai_config  # noqa: E402

__all__ = [
    "DEFAULT_TIMEOUT",
    "AIConfig",
    "clean_model_name",
    "detect_currency",
    "fetch_page_prices",
    "main",
    "parse_component_tables",
    "parse_context_limit",
    "parse_html_tables",
    "parse_json_entries",
    "parse_markdown_tables",
    "price_value",
    "resolve_ai_config",
    "unit_scale",
]
