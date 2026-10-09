"""通用定价页拉取脚本（page_price）：三种确定性解析、Headless 兜底与 AI 兜底的证据校验。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from llm_price_monitor import page_price
from llm_price_monitor.page_price import (
    detect_currency,
    fetch_page_prices,
    parse_component_tables,
    parse_context_limit,
    parse_html_tables,
    parse_json_entries,
    parse_markdown_tables,
    price_value,
    resolve_ai_config,
    unit_scale,
)

# 仿 docs.bigmodel.cn/cn/guide/start/pricing.md 的真实结构：首个模型表多一列「输入模态」、
# 「免费」单元格、同一模型多上下文档、Accordion 内嵌第二张表
BIGMODEL_MD = """# API 定价

除特别说明外，模型价格统一按照“元/百万 Tokens”展示。

## 主力模型

| 模型名称           | 上下文 | 输入单价（元/百万 Tokens） | 输出单价（元/百万 Tokens） | 缓存存储（元/百万 Tokens/小时） | 缓存命中（元/百万 Tokens） | 输入模态        |
| ------------------ | ---- | --------- | --------- | ---------- | --------- | ----------- |
| GLM-5.3            | 1M   | 8         | 28        | 限时免费    | 2         | 文本        |
| GLM-5.3-Flash      | 1M   | 0.8       | 2.8       | 限时免费    | 0.23      | 图片、文本  |

## 文本模型

| 模型名称   | 上下文             | 输入单价（元/百万 Tokens） | 输出单价（元/百万 Tokens） | 缓存存储（元/百万 Tokens/小时） | 缓存命中（元/百万 Tokens） |
| ------- | --------------- | --------- | --------- | ---------- | --------- |
| GLM-5.1 | 输入长度 [0, 32K)  | 6         | 24        | 限时免费    | 1.3       |
| GLM-5.1 | 输入长度 ≥32K     | 8         | 28        | 限时免费    | 2         |
| GLM-4.7-Flash | 200K      | 免费      | 免费      | 限时免费    | 0         |

<Accordion title="更多文本模型">
  | 模型名称            | 上下文 | 输入单价（元/百万 Tokens） | 输出单价（元/百万 Tokens） | 缓存命中（元/百万 Tokens） |
  | ---------------- | --- | --------- | --------- | --------- |
  | GLM-Z1-Air（128K） | 128K | 0.5      | 0.5       | 0.1       |
</Accordion>
"""

HTML_PAGE = """<html><body><h1>模型定价</h1>
<table>
  <tr><th>模型名称</th><th>输入单价（元/百万 Tokens）</th><th>输出单价（元/百万 Tokens）</th><th>缓存命中（元/百万 Tokens）</th></tr>
  <tr><td>deepseek-flash</td><td>1</td><td>4</td><td>0.02</td></tr>
  <tr><td>deepseek-v4-pro</td><td>4.5</td><td>13.5</td><td>0.15</td></tr>
</table>
<table>
  <tr><th>产品名称</th><th>说明</th><th>价格</th></tr>
  <tr><td>知识库</td><td>按量付费</td><td>0.5元/次</td></tr>
</table>
</body></html>"""

JSON_PAGE = json.dumps({
    "data": [
        {"provider": "deepseek", "models": ["deepseek-chat", "deepseek-reasoner"], "input": 0.27, "output": 1.1, "cache_read": 0.07},
    ]
})

# 纯文本价目：md/html/json 三种解析都拿不到，只能走 AI 兜底
PLAIN_PAGE = """DeepSeek 价格表（2026年9月）
deepseek-flash：输入 1 元/百万 tokens，输出 4 元/百万 tokens，缓存命中 0.02 元
deepseek-v4-pro：输入 4.5 元/百万 tokens，输出 13.5 元/百万 tokens
"""

JS_SHELL_PAGE = "<!doctype html><html><head><script>var app=document.getElementById('app')</script></head><body><div id=\"app\"></div></body></html>"

# 仿 platform.kimi.com 定价页迁到组件表格后的 .md 原文：价格埋在 DocTable 的
# columns/rows 属性里，开头还有组件定义处的空 columns=[]/rows=[]（不该误报）
DOCTABLE_MD = """# 模型推理价格说明

export const DocTable = ({columns = [], rows = []}) => {
  return <div className="doc-table-wrap">...</div>;
};

## 模型定价

### K3 系列模型

<DocTable
  columns={[
{ title: "模型", width: "12%" },
{ title: "计费单位", width: "10%" },
{ title: "缓存写入（TTL 5min）", width: "13%" },
{ title: "缓存写入（TTL 1h）", width: "13%" },
{ title: "输入价格（缓存命中）", width: "16%" },
{ title: "输入价格（缓存未命中）", width: "16%" },
{ title: "输出价格", width: "12%" },
{ title: "上下文窗口", width: "14%" },
]}
  rows={[
["kimi-k3", "1M tokens", "¥20.00", "¥40.00", "¥2.00", "¥20.00", "¥100.00", "1,048,576 tokens"],
]}
/>

### K2 系列模型

<DocTable
  columns={[
{ title: "模型", width: "24%" },
{ title: "计费单位", width: "12%" },
{ title: "输入价格（缓存命中）", width: "16%" },
{ title: "输入价格（缓存未命中）", width: "16%" },
{ title: "输出价格", width: "14%" },
{ title: "上下文窗口", width: "14%" },
]}
  rows={[
["kimi-k2.7-code", "1M tokens", "¥1.30", "¥6.50", "¥27.00", "262,144 tokens"],
["kimi-k2.7-code-highspeed", "1M tokens", "¥2.60", "¥13.00", "¥54.00", "262,144 tokens"],
]}
/>
"""


def by_key(records: list[dict], name: str) -> dict:
    return next(record for record in records if record["model_key"] == name)


def test_parse_component_tables_kimi_doctable() -> None:
    """Mintlify DocTable 组件属性表：表头取 title、行取数组，缓存命中列语义照旧。"""
    records = parse_component_tables(DOCTABLE_MD, "https://platform.kimi.com/docs/pricing/chat.md")
    keys = [record["model_key"] for record in records]
    assert keys == ["kimik3", "kimik2.7code", "kimik2.7codehighspeed"]
    k3 = records[0]
    # 「输入价格（缓存命中）」进 cache_read、「（缓存未命中）」进 input——与 HTML 表同款列匹配
    assert k3["input_price"] == 20.0 and k3["output_price"] == 100.0
    assert k3["cache_read_price"] == 2.0
    assert k3["context"] == "1,048,576 tokens"


def test_parse_component_tables_skips_empty_props_and_garbage() -> None:
    assert parse_component_tables("export const T = ({columns = [], rows = []}) => <div/>;", "https://x") is None
    assert parse_component_tables("columns=[{broken", "https://x") is None


def test_fetch_probes_md_sibling_when_html_has_no_tables() -> None:
    """页面组件化后静态解析落空 → 探测同名 .md 恢复出价，不用走渲染/AI。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".md"):
            return httpx.Response(200, text=DOCTABLE_MD)
        return httpx.Response(200, text=JS_SHELL_PAGE)

    result = fetch_page_prices("https://platform.kimi.com/docs/pricing/chat", transport=httpx.MockTransport(handler))
    assert result["method"] == "md-source-component"
    assert [record["model_key"] for record in result["models"]] == ["kimik3", "kimik2.7code", "kimik2.7codehighspeed"]


def test_fetch_skips_md_probe_when_static_parse_hits() -> None:
    """静态解析命中就不发探测请求：健康页零额外成本。"""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=HTML_PAGE)

    result = fetch_page_prices("https://example.com/pricing", transport=httpx.MockTransport(handler))
    assert result["method"] == "static-html"
    assert all(not url.endswith(".md") for url in requested)


def test_unit_helpers() -> None:
    assert detect_currency("输入单价（元/百万 Tokens）") == "CNY"
    assert detect_currency("Input Price (USD / 1M tokens)") == "USD"
    assert detect_currency("上下文") is None
    assert unit_scale("输入单价（元/百万 Tokens）") == 1.0
    assert unit_scale("输入单价（元/千 Tokens）") == 1000.0
    assert unit_scale("input ($ / token)") == 1_000_000.0
    assert price_value("免费") == (0.0, True)
    assert price_value("0.8 元") == (0.8, True)
    assert price_value("1,234.5") == (1234.5, True)
    assert price_value("—") == (None, False)
    assert price_value("限时免费")[0] == 0.0


def test_parse_context_limit() -> None:
    """上下文列原文 → limit 数字：单值/斜杠对/输出标注三种形态，非上下文文本一律 None。"""
    assert parse_context_limit("1M") == {"context": 1048576, "output": None}
    assert parse_context_limit("1,048,576 tokens") == {"context": 1_048_576, "output": None}
    assert parse_context_limit("128k") == {"context": 131072, "output": None}
    assert parse_context_limit("200K/32K") == {"context": 204800, "output": 32768}
    assert parse_context_limit("256K；最大输出 32K") == {"context": 262144, "output": 32768}
    assert parse_context_limit("1M（输出 64K）") == {"context": 1048576, "output": 65536}
    assert parse_context_limit("输入长度 [0, 32K)") is None  # 输入价格分档不是上下文窗口
    assert parse_context_limit("8K~1M") is None  # 可取值区间读不出单一上限，不猜
    assert parse_context_limit("00:00 ~ 24:00") is None  # 时段定义不是上下文窗口
    assert parse_context_limit("高峰时段") is None
    assert parse_context_limit("") is None
    assert parse_context_limit(None) is None


def test_parse_markdown_captures_description_and_context() -> None:
    """智谱式「模型名称|简介|上下文|输入|输出」表：简介与上下文进记录级字段。"""
    md = (
        "| 模型名称 | 简介 | 上下文 | 输入单价（元/百万 Tokens） | 输出单价（元/百万 Tokens） |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| GLM-5.3 | 旗舰模型，适合复杂任务 | 1M | 8 | 28 |\n"
        "| GLM-5.3-Flash | 轻量快速档 | 128K | 0.8 | 2.8 |\n"
    )
    records = parse_markdown_tables(md, "https://docs.bigmodel.cn/pricing")
    glm = by_key(records, "glm5.3")
    assert glm["description"] == "旗舰模型，适合复杂任务"
    assert glm["context"] == "1M"
    flash = by_key(records, "glm5.3flash")
    assert flash["description"] == "轻量快速档"
    assert flash["tiers"] == [{"context": "128K", "input_price": 0.8, "output_price": 2.8}]


def test_fetch_ai_captures_context_and_description() -> None:
    transport = _transport_with_ai(PLAIN_PAGE, [
        {"model": "deepseek-flash", "input": 1, "output": 4, "currency": "CNY",
         "context": "64K/8K", "description": "快速低价模型",
         "quote": "deepseek-flash：输入 1 元/百万 tokens"},
    ])
    result = fetch_page_prices("https://example.com/pricing", ai_config=AI_CONFIG, transport=transport)
    flash = result["models"][0]
    assert flash["context"] == "64K/8K"
    assert flash["description"] == "快速低价模型"


def test_parse_markdown_bigmodel_structure() -> None:
    records = parse_markdown_tables(BIGMODEL_MD, "https://docs.bigmodel.cn/cn/guide/start/pricing.md")
    assert records is not None
    keys = {record["model_key"] for record in records}
    assert keys == {"glm5.3", "glm5.3flash", "glm5.1", "glm4.7flash", "glmz1air"}

    top = by_key(records, "glm5.3")
    assert top["input_price"] == 8.0
    assert top["output_price"] == 28.0
    assert top["cache_read_price"] == 2.0
    assert top["currency"] == "CNY"
    assert top["unit"] == "CNY/1M tokens"
    # 「输入模态」列不该混进价格；缓存存储列（按小时计费）不参与
    assert top["quote"] == "GLM-5.3 | 1M | 8 | 28 | 限时免费 | 2 | 文本"

    flash = by_key(records, "glm5.3flash")
    assert (flash["input_price"], flash["output_price"], flash["cache_read_price"]) == (0.8, 2.8, 0.23)

    # 同一模型多上下文档：第一行做基准，全部档位进 tiers（与站点价约定一致）
    tiered = by_key(records, "glm5.1")
    assert (tiered["input_price"], tiered["output_price"]) == (6.0, 24.0)
    assert tiered["tiers"] == [
        {"context": "输入长度 [0, 32K)", "input_price": 6.0, "output_price": 24.0},
        {"context": "输入长度 ≥32K", "input_price": 8.0, "output_price": 28.0},
    ]

    # 「免费」按 0 计
    free = by_key(records, "glm4.7flash")
    assert free["input_price"] == 0.0 and free["output_price"] == 0.0

    # Accordion 内嵌表也能解析；名称里的「（128K）」限定符已剥掉
    assert by_key(records, "glmz1air")["input_price"] == 0.5


def test_parse_html_tables() -> None:
    records = parse_html_tables(HTML_PAGE, "https://api-docs.example.com/pricing")
    assert records is not None
    # 第二张表（产品/说明/价格）没有模型列，整表跳过
    assert [record["model_key"] for record in records] == ["deepseekflash", "deepseekv4pro"]
    pro = by_key(records, "deepseekv4pro")
    assert (pro["input_price"], pro["output_price"], pro["cache_read_price"]) == (4.5, 13.5, 0.15)
    assert pro["currency"] == "CNY"


def test_parse_html_broken_markup_returns_none() -> None:
    assert parse_html_tables("<html><table><tr><td>残缺", "https://x") is None


# 仿 api-docs.deepseek.com/zh-cn/quick_start/pricing 的真实结构：模型名当列头的
# 转置规格表，规格行、价格区（计费类别 × 时段，rowspan 让时段行缺类别格）、
# 上下文行 colspan 合并、并发行是纯数字（无币种标识，不当价格）
DEEPSEEK_TRANSPOSED_HTML = """<html><body><table>
  <tr><th>模型</th><th>deepseek-flash(1)</th><th>deepseek-v4-pro</th></tr>
  <tr><td>BASE URL (OpenAI 格式)</td><td colspan="2">https://api.deepseek.com</td></tr>
  <tr><td>模型版本</td><td>DeepSeek-V4.1-Flash</td><td>DeepSeek-V4-Pro-0813</td></tr>
  <tr><td>上下文长度</td><td colspan="2">1M</td></tr>
  <tr><td>并发限制(3)</td><td>2500</td><td>500</td></tr>
  <tr><td>价格(2)</td><td>百万tokens输入（缓存命中）</td><td>空闲时段</td><td>0.02元</td><td>0.15元</td></tr>
  <tr><td>高峰时段</td><td>0.04元</td><td>0.30元</td></tr>
  <tr><td>百万tokens输入（缓存未命中）</td><td>空闲时段</td><td>1元</td><td>4.5元</td></tr>
  <tr><td>高峰时段</td><td>2元</td><td>9.0元</td></tr>
  <tr><td>百万tokens输出</td><td>空闲时段</td><td>4元</td><td>13.5元</td></tr>
  <tr><td>高峰时段</td><td>8元</td><td>27.0元</td></tr>
</table></body></html>"""


def test_parse_html_transposed_grid_composes_baseline() -> None:
    """转置规格表：价格按列对齐到模型，基准组合输入取缓存未命中高峰、输出/缓存取高峰档。"""
    records = parse_html_tables(DEEPSEEK_TRANSPOSED_HTML, "https://api-docs.deepseek.com/zh-cn/quick_start/pricing")
    assert records is not None
    assert [record["model_key"] for record in records] == ["deepseekflash", "deepseekv4pro"]
    pro = by_key(records, "deepseekv4pro")
    assert (pro["input_price"], pro["output_price"], pro["cache_read_price"]) == (9.0, 27.0, 0.3)
    assert pro["currency"] == "CNY"
    assert pro["context"] == "1M"
    assert "price_status" not in pro  # 静态解析不走 AI，不带待复核标记
    assert len(pro["tiers"]) == 6
    assert ("百万tokens输入（缓存未命中） 高峰时段", 9.0, None, None) in [
        (tier["name"], tier["input_price"], tier["output_price"], tier["cache_read_price"])
        for tier in pro["tiers"]
    ]
    flash = by_key(records, "deepseekflash")
    assert (flash["input_price"], flash["output_price"], flash["cache_read_price"]) == (2.0, 8.0, 0.04)


def test_parse_html_transposed_skips_misaligned_price_rows() -> None:
    """价格个数与模型列数对不上的行（colspan 错位）整行不猜，其余行照常入库。"""
    records = parse_html_tables(
        """<html><body><table>
          <tr><th>模型</th><th>deepseek-flash</th><th>deepseek-v4-pro</th></tr>
          <tr><td>百万tokens输入（缓存命中）</td><td>0.02元</td><td>0.15元</td></tr>
          <tr><td>百万tokens输入（缓存未命中）</td><td>1元</td><td>4.5元</td></tr>
          <tr><td>高峰时段</td><td>2元</td></tr>
          <tr><td>百万tokens输出</td><td>4元</td><td>8元</td></tr>
        </table></body></html>""",
        "https://x",
    )
    assert records is not None
    flash = by_key(records, "deepseekflash")
    assert (flash["input_price"], flash["output_price"], flash["cache_read_price"]) == (1.0, 4.0, 0.02)
    assert len(flash["tiers"]) == 3  # 错位的高峰行被丢弃，其余三档保留
    pro = by_key(records, "deepseekv4pro")
    assert (pro["input_price"], pro["output_price"], pro["cache_read_price"]) == (4.5, 8.0, 0.15)


def test_transposed_grid_rejects_attribute_header() -> None:
    """列头是属性列（输入/输出等）的表是普通价目表，转置解读不认。"""
    records = page_price._records_from_transposed_grid(
        ["模型", "输入单价（元/百万 tokens）", "输出单价（元/百万 tokens）"],
        [["deepseek-flash", "1元", "4元"]],
        source_url="https://x",
    )
    assert records is None


# 仿 cloud.baidu.com 千帆价目表：rowspan 把模型列下推多行，输入/输出各占一行，
# 价格在「在线推理」服务列，单位列标元/千tokens（要 ×1000 折成百万口径）
BAIDU_CATEGORIZED_HTML = """<html><body><table>
  <tr><th>模型名称</th><th>版本名称</th><th>子项</th><th>在线推理</th><th>批量推理</th><th>单位</th></tr>
  <tr><td rowspan="4">ERNIE 5.1</td><td rowspan="4">ERNIE-5.1</td><td>输入（输入&lt;=32k）</td><td>0.004</td><td>-</td><td rowspan="4">元/千tokens</td></tr>
  <tr><td>输出（输入&lt;=32k）</td><td>0.018</td><td>-</td></tr>
  <tr><td>输入（32k&lt;输入&lt;=128k）</td><td>0.006</td><td>-</td></tr>
  <tr><td>输出（32k&lt;输入&lt;=128k）</td><td>0.022</td><td>-</td></tr>
</table>
<table>
  <tr><th>模型名称</th><th>版本名称</th><th>子项</th><th>预付费价格（单位：元/个/月）</th></tr>
  <tr><td>ERNIE 5.1</td><td>ERNIE-5.1</td><td>输入</td><td>800</td></tr>
</table>
</body></html>"""


def test_parse_html_categorized_rows_and_token_guard() -> None:
    """计费类别行表：rowspan 展开后按「输入/输出行 × 服务价列」取数，千 tokens 单位折百万；
    按 月/个 计价的预付费表不是 token 单价，整表不认。"""
    records = parse_html_tables(BAIDU_CATEGORIZED_HTML, "https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya")
    assert records is not None
    assert [record["model_key"] for record in records] == ["ernie5.1"]
    ernie = records[0]
    assert (ernie["input_price"], ernie["output_price"]) == (4.0, 18.0)  # 0.004/千 × 1000
    assert ernie["currency"] == "CNY"
    assert len(ernie["tiers"]) == 4
    # 档位名照抄子项原文；基准价取第一条输入行与第一条输出行
    assert (ernie["tiers"][0]["name"], ernie["tiers"][0]["input_price"]) == ("输入（输入<=32k）", 4.0)
    assert (ernie["tiers"][1]["name"], ernie["tiers"][1]["output_price"]) == ("输出（输入<=32k）", 18.0)


def test_clean_model_name_strips_cjk_slash_annotations() -> None:
    """阿里云把中文注解跟在模型 ID 后（斜杠/换行分隔）——剥注解但保留路径式模型名。"""
    assert page_price.clean_model_name("qwen3.8-max-prime/更多详情参考优速模式（Prime）") == "qwen3.8-max-prime"
    assert page_price.clean_model_name("qwen3.7-max/当前能力等同于qwen3.7-max-2026-05-20//Batch调用半价") == "qwen3.7-max"
    assert page_price.clean_model_name("ZHIPU/GLM-5.3") == "ZHIPU/GLM-5.3"
    assert page_price.clean_model_name("deepseek-v4-pro") == "deepseek-v4-pro"


def test_parse_html_model_id_header_with_token_tiers() -> None:
    """「模型 ID（Model ID）」表头 + Token 分档列（阿里云百炼形态）走通用列映射。"""
    html = """<html><body><table>
      <tr><th>模型 ID（Model ID）</th><th>模式</th><th>单次请求的输入Token数</th><th>输入单价（每百万Token）</th><th>输出单价（每百万Token）</th></tr>
      <tr><td>qwen3.8-max</td><td>非思考和思考模式</td><td>0&lt;Token≤1M</td><td>12元</td><td>36元</td></tr>
    </table></body></html>"""
    records = parse_html_tables(html, "https://help.aliyun.com/zh/model-studio/model-pricing")
    assert records is not None
    qwen = records[0]
    assert qwen["model"] == "qwen3.8-max"
    assert (qwen["input_price"], qwen["output_price"]) == (12.0, 36.0)
    assert qwen["currency"] == "CNY"  # 币种在单元格里，表头没有也能识别


def test_parse_json_entries() -> None:
    records = parse_json_entries(JSON_PAGE, "https://example.com/api/pricing")
    assert records is not None
    assert [record["model_key"] for record in records] == ["deepseekchat", "deepseekreasoner"]
    chat = records[0]
    assert (chat["input_price"], chat["output_price"], chat["cache_read_price"]) == (0.27, 1.1, 0.07)
    # 无币种标识时按该解析结构的约定口径标注
    assert chat["currency"] == "USD"


def test_fetch_static_markdown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "docs.bigmodel.cn"
        return httpx.Response(200, text=BIGMODEL_MD)

    result = fetch_page_prices(
        "https://docs.bigmodel.cn/cn/guide/start/pricing.md",
        transport=httpx.MockTransport(handler),
    )
    assert result["method"] == "static-md"
    assert result["warnings"] == []
    assert by_key(result["models"], "glm5.3flash")["input_price"] == 0.8


def test_fetch_renders_only_when_headless_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    """空壳页不再自动换 Headless 渲染：只有显式 headless=True 才走浏览器。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=JS_SHELL_PAGE)

    rendered_calls: list[str] = []

    def fake_fetch_page_html(url: str, config: dict, user_agent: str | None = None, extra_headers: dict | None = None) -> str:
        rendered_calls.append(url)
        return HTML_PAGE

    monkeypatch.setattr(page_price, "fetch_page_html", fake_fetch_page_html)

    plain = fetch_page_prices("https://example.com/pricing", transport=httpx.MockTransport(handler))
    assert rendered_calls == []
    assert plain["method"] == "none"
    assert any("没有拿到价格" in warning for warning in plain["warnings"])

    rendered = fetch_page_prices(
        "https://example.com/pricing", transport=httpx.MockTransport(handler), headless=True
    )
    assert rendered_calls == ["https://example.com/pricing"]
    assert rendered["method"] == "headless-html"
    assert len(rendered["models"]) == 2


def _ai_response(models: list[dict]) -> dict:
    return {"choices": [{"message": {"content": json.dumps({"models": models}, ensure_ascii=False)}}]}


def _transport_with_ai(page_text: str, ai_models: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json=_ai_response(ai_models))
        return httpx.Response(200, text=page_text)

    return httpx.MockTransport(handler)


AI_CONFIG = page_price.AIConfig(base_url="https://ai.test/v1", models=("test-model",), api_key="k")


def test_fetch_ai_fallback_extracts_and_validates() -> None:
    transport = _transport_with_ai(PLAIN_PAGE, [
        {"model": "deepseek-flash", "input": 1, "output": 4, "cache_read": 0.02, "currency": "CNY",
         "quote": "deepseek-flash：输入 1 元/百万 tokens"},
        {"model": "deepseek-v4-pro", "input": 4.5, "output": 13.5, "cache_read": None, "currency": "CNY",
         "quote": "deepseek-v4-pro：输入 4.5 元"},
    ])
    result = fetch_page_prices("https://example.com/pricing", ai_config=AI_CONFIG, transport=transport)
    assert result["method"] == "ai"
    assert [record["model_key"] for record in result["models"]] == ["deepseekflash", "deepseekv4pro"]
    flash = result["models"][0]
    assert flash["price_status"] == "candidate"
    assert flash["currency"] == "CNY"
    assert flash["unit"] == "CNY/1M tokens"
    assert result["warnings"] == []


def test_fetch_ai_rejects_hallucinated_prices() -> None:
    transport = _transport_with_ai(PLAIN_PAGE, [
        # 输入价 99 在页面文本里找不到 → 幻觉，丢弃
        {"model": "deepseek-v4-pro", "input": 99, "output": 13.5, "currency": "CNY", "quote": "x"},
        # 模型名不在页面文本里 → 丢弃
        {"model": "gpt-5.6-sol", "input": 1, "output": 2, "currency": "CNY", "quote": "x"},
        # 没有任何价格 → 丢弃
        {"model": "deepseek-flash", "input": None, "output": None, "currency": "CNY", "quote": "x"},
    ])
    result = fetch_page_prices("https://example.com/pricing", ai_config=AI_CONFIG, transport=transport)
    assert result["models"] == []
    assert result["method"] == "none"
    joined = "\n".join(result["warnings"])
    assert "deepseek-v4-pro" in joined and "幻觉" in joined
    assert "gpt-5.6-sol" in joined and "不在页面文本中" in joined
    assert "deepseek-flash" in joined and "没有可用价格" in joined


def test_fetch_without_ai_reports_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PLAIN_PAGE)

    result = fetch_page_prices("https://example.com/pricing", transport=httpx.MockTransport(handler))
    assert result["models"] == []
    assert result["method"] == "none"
    assert any("都没有拿到价格" in warning for warning in result["warnings"])


def test_resolve_ai_config_prefers_cli_args(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRICE_MONITOR_DB", raising=False)
    args = SimpleNamespace(ai_base_url="https://ai.test/v1", ai_api_key="k", ai_model="m", no_ai=False)
    config, note = resolve_ai_config(args)
    assert note is None
    assert config is not None and config.base_url == "https://ai.test/v1"

    # 只有密钥没有地址：提示而不是硬错
    args = SimpleNamespace(ai_base_url="", ai_api_key="k", ai_model="", no_ai=False)
    config, note = resolve_ai_config(args)
    assert config is None and note and "--ai-base-url" in note

    # 库不存在：给出可执行的下一步提示
    monkeypatch.setenv("PRICE_MONITOR_DB", str(tmp_path / "missing" / "monitor.db"))
    args = SimpleNamespace(ai_base_url="", ai_api_key="", ai_model="", no_ai=False)
    config, note = resolve_ai_config(args)
    assert config is None and note and "找不到数据库" in note and "--ai-base-url" in note


def test_cli_exit_code_when_no_models(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(page_price, "fetch_page_prices", lambda *a, **k: {
        "url": "https://x", "final_url": "https://x", "method": "none", "models": [], "warnings": ["空"]
    })
    monkeypatch.setattr("sys.argv", ["price-page", "https://x/pricing"])
    with pytest.raises(SystemExit) as exc_info:
        page_price.main()
    assert exc_info.value.code == 1
    assert "空" in capsys.readouterr().err


def test_cli_writes_out_file(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(page_price, "resolve_ai_config", lambda args: (None, None))
    monkeypatch.setattr(page_price, "fetch_page_prices", lambda *a, **k: {
        "url": "https://x", "final_url": "https://x", "method": "static-md",
        "models": [{"model": "demo", "model_key": "demo", "input_price": 1.0, "output_price": 2.0,
                    "cache_read_price": None, "currency": "CNY", "unit": "CNY/1M tokens",
                    "tiers": [], "source_url": "https://x", "quote": "demo | 1 | 2"}],
        "warnings": [],
    })
    out = tmp_path / "prices.json"
    monkeypatch.setattr("sys.argv", ["price-page", "https://x/pricing", "--out", str(out)])
    page_price.main()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["method"] == "static-md" and len(payload["models"]) == 1
    assert "已写入" in capsys.readouterr().out


# ---------- 多档价格（高峰/空闲时段等） ----------


def test_fetch_ai_fallback_extracts_time_tiers():
    """高峰/空闲两档都进 tiers 且带时段定义；基准取 AI 标记的标准档（高峰）。"""
    page = (
        "DeepSeek 价格表（2026年9月）\n"
        "deepseek-flash：输入（缓存未命中）空闲时段 1 元/百万 tokens、高峰时段 2 元；"
        "输出空闲时段 4 元、高峰时段 8 元；缓存命中空闲时段 0.02 元、高峰时段 0.04 元。\n"
        "高峰时段为北京时间周一至周五 9:00 - 12:00、14:00 - 18:00（其余为空闲时段）。"
    )
    transport = _transport_with_ai(page, [{
        "model": "deepseek-flash",
        "tiers": [
            {"name": "高峰时段（北京时间周一至周五 9:00-12:00、14:00-18:00）", "standard": True,
             "input": 2, "output": 8, "cache_read": 0.04},
            {"name": "空闲时段", "input": 1, "output": 4, "cache_read": 0.02},
        ],
        "currency": "CNY", "quote": "deepseek-flash",
    }])
    result = fetch_page_prices("https://example.com/pricing", ai_config=AI_CONFIG, transport=transport)
    assert result["method"] == "ai" and result["warnings"] == []
    record = result["models"][0]
    # 基准取标准档（高峰时段），空闲档不丢
    assert (record["input_price"], record["output_price"]) == (2.0, 8.0)
    assert record["cache_read_price"] == 0.04
    assert [tier["name"] for tier in record["tiers"]] == [
        "高峰时段（北京时间周一至周五 9:00-12:00、14:00-18:00）", "空闲时段",
    ]
    assert record["tiers"][1] == {"name": "空闲时段", "input_price": 1.0, "output_price": 4.0, "cache_read_price": 0.02}


def test_fetch_ai_standard_tier_by_name_when_unmarked():
    """AI 没标 standard 时，按档位名特征（高峰/标准）挑基准。"""
    page = "demo 模型价格：demo-model 高峰时段 输入 6 元、输出 12 元；空闲时段 输入 3 元、输出 6 元（每百万 tokens）。"
    transport = _transport_with_ai(page, [{
        "model": "demo-model",
        "tiers": [
            {"name": "空闲时段", "input": 3, "output": 6},
            {"name": "高峰时段", "input": 6, "output": 12},
        ],
        "currency": "CNY", "quote": "demo-model",
    }])
    result = fetch_page_prices("https://example.com/pricing", ai_config=AI_CONFIG, transport=transport)
    record = result["models"][0]
    assert (record["input_price"], record["output_price"]) == (6.0, 12.0)
    assert len(record["tiers"]) == 2


def test_fetch_ai_composes_baseline_from_split_categories():
    """输入按缓存命中/未命中分行、输出单列时（DeepSeek 形态），基准按计费类别组合：
    输入取缓存未命中高峰、输出取输出高峰、缓存命中价取缓存命中高峰，tiers 明细保留。"""
    page = (
        "价格 deepseek-flash deepseek-v4-pro\n"
        "百万tokens输入（缓存命中）空闲时段 0.02元 0.15元、高峰时段 0.04元 0.30元\n"
        "百万tokens输入（缓存未命中）空闲时段 1元 4.5元、高峰时段 2元 9.0元\n"
        "百万tokens输出 空闲时段 4元 13.5元、高峰时段 8元 27.0元\n"
        "空闲时段价格为高峰时段价格的一半。"
    )
    transport = _transport_with_ai(page, [{
        "model": "deepseek-flash",
        "tiers": [
            {"name": "输入（缓存命中）空闲时段", "input": 0.02, "output": None, "cache_read": 0.02},
            {"name": "输入（缓存命中）高峰时段", "input": 0.04, "output": None, "cache_read": 0.04},
            {"name": "输入（缓存未命中）空闲时段", "input": 1.0, "output": None, "cache_read": None},
            {"name": "输入（缓存未命中）高峰时段", "input": 2.0, "output": None, "cache_read": None},
            {"name": "输出空闲时段", "input": None, "output": 4.0, "cache_read": None},
            {"name": "输出高峰时段", "input": None, "output": 8.0, "cache_read": None},
        ],
        "currency": "CNY", "quote": "deepseek-flash",
    }])
    result = fetch_page_prices("https://example.com/pricing", ai_config=AI_CONFIG, transport=transport)
    assert result["method"] == "ai" and result["warnings"] == []
    record = result["models"][0]
    # 基准按计费类别组合，不再整档照搬缓存命中价
    assert (record["input_price"], record["output_price"]) == (2.0, 8.0)
    assert record["cache_read_price"] == 0.04
    assert len(record["tiers"]) == 6


def test_fetch_ai_baseline_ignores_misplaced_standard_flag():
    """AI 把缓存命中高峰档误标 standard 时，分维表基准仍按类别各取标准档。"""
    page = (
        "价格 demo-model\n"
        "百万tokens输入（缓存命中）空闲时段 0.02元、高峰时段 0.04元\n"
        "百万tokens输入（缓存未命中）空闲时段 1元、高峰时段 2元\n"
        "百万tokens输出 空闲时段 4元、高峰时段 8元"
    )
    transport = _transport_with_ai(page, [{
        "model": "demo-model",
        "tiers": [
            {"name": "输入（缓存命中）空闲时段", "input": 0.02, "cache_read": 0.02},
            {"name": "输入（缓存命中）高峰时段", "standard": True, "input": 0.04, "cache_read": 0.04},
            {"name": "输入（缓存未命中）空闲时段", "input": 1.0},
            {"name": "输入（缓存未命中）高峰时段", "input": 2.0},
            {"name": "输出空闲时段", "output": 4.0},
            {"name": "输出高峰时段", "output": 8.0},
        ],
        "currency": "CNY", "quote": "demo-model",
    }])
    result = fetch_page_prices("https://example.com/pricing", ai_config=AI_CONFIG, transport=transport)
    record = result["models"][0]
    assert (record["input_price"], record["output_price"]) == (2.0, 8.0)
    assert record["cache_read_price"] == 0.04


def test_fetch_ai_drops_only_hallucinated_tier():
    """单个档位幻觉只丢该档，其余档保留并改取基准。"""
    transport = _transport_with_ai(PLAIN_PAGE, [{
        "model": "deepseek-v4-pro",
        "tiers": [
            {"name": "高峰时段", "input": 99, "output": 27, "cache_read": None},
            {"name": "空闲时段", "input": 4.5, "output": 13.5, "cache_read": None},
        ],
        "currency": "CNY", "quote": "deepseek-v4-pro",
    }])
    result = fetch_page_prices("https://example.com/pricing", ai_config=AI_CONFIG, transport=transport)
    assert len(result["models"]) == 1
    record = result["models"][0]
    assert (record["input_price"], record["output_price"]) == (4.5, 13.5)
    assert [tier["name"] for tier in record["tiers"]] == ["空闲时段"]
    assert any("幻觉" in warning and "高峰时段" in warning for warning in result["warnings"])


def test_parse_html_time_tier_column():
    """静态表格里的「时段」列收进阶梯标签；按页面行序取第一档为基准。"""
    html = """<html><body><table>
      <tr><th>模型名称</th><th>时段</th><th>输入单价（元/百万 Tokens）</th><th>输出单价（元/百万 Tokens）</th></tr>
      <tr><td>demo-model</td><td>空闲时段</td><td>1</td><td>4</td></tr>
      <tr><td>demo-model</td><td>高峰时段</td><td>2</td><td>8</td></tr>
    </table></body></html>"""
    records = parse_html_tables(html, "https://example.com/pricing")
    assert records is not None
    record = records[0]
    assert (record["input_price"], record["output_price"]) == (1.0, 4.0)
    assert record["tiers"] == [
        {"context": "空闲时段", "input_price": 1.0, "output_price": 4.0},
        {"context": "高峰时段", "input_price": 2.0, "output_price": 8.0},
    ]


def test_fetch_page_prices_ai_fallback_uses_rendered_text(monkeypatch):
    """显式 headless 渲染成功但静态解析不出：AI 兜底必须看渲染后的正文，而不是渲染前的 JS 空壳。"""
    from llm_price_monitor.config import AIConfig

    monkeypatch.setattr(page_price, "fetch_page_html", lambda url, headers, user_agent: "<html>claude-x 渲染后价格 $3.00</html>")
    captured: dict[str, str] = {}

    def fake_ai_extract(text: str, source_url: str, ai_config: AIConfig, client: httpx.Client, deadline=None):
        captured["text"] = text
        return [], []

    monkeypatch.setattr(page_price, "_ai_extract", fake_ai_extract)
    ai_config = AIConfig(base_url="https://ai.test/v1", models=("m",), api_key="k", enabled=True)
    result = fetch_page_prices(
        "https://demo.test/pricing",
        ai_config=ai_config,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html>loading…</html>")),
        headless=True,
    )
    assert "渲染后价格" in captured["text"]
    assert "<html>loading…</html>" not in captured["text"]
    assert result["method"] == "none"  # 假抽取不产出记录，只验证喂给 AI 的文本来源


# ---------- 可疑解析的 AI 复核与降级 ----------


def test_records_suspicious_flags_annotations_and_duplicates() -> None:
    assert page_price._records_suspicious([
        {"model": "MiniMax-M3≤ 512k 输入 tokens 永久五折", "model_key": "m3", "input_price": 4.2, "output_price": 16.8},
    ])
    assert page_price._records_suspicious([
        {"model": "mimo-v2.6-pro、mimo-v2.5-pro", "model_key": "mimo", "input_price": None, "output_price": 6.0},
    ])
    assert page_price._records_suspicious([
        {"model": "demo", "model_key": "demo", "input_price": 1.0, "output_price": 2.0},
        {"model": "demo", "model_key": "demo", "input_price": 2.0, "output_price": 4.0},
    ])
    # 正常记录不误报：embedding 只有输入价是合法形态
    assert not page_price._records_suspicious([
        {"model": "glm-5.3", "model_key": "glm53", "input_price": 8.0, "output_price": 28.0},
        {"model": "glm-embedding", "model_key": "glmemb", "input_price": 0.5, "output_price": None},
    ])


SUSPICIOUS_PAGE = """<html><body><table>
  <tr><th>模型名称</th><th>输入单价（元/百万 Tokens）</th><th>输出单价（元/百万 Tokens）</th></tr>
  <tr><td>demo-a、demo-b</td><td>1</td><td>4</td></tr>
  <tr><td>demo-c</td><td>2</td><td>8</td></tr>
</table></body></html>"""


def test_fetch_suspect_parse_prefers_ai_records() -> None:
    """静态解析出合并行（可疑）时交给 AI 复核：AI 有产出就以 AI 为准（标待复核）。"""
    transport = _transport_with_ai(SUSPICIOUS_PAGE, [
        {"model": "demo-a", "input": 1, "output": 4, "currency": "CNY", "quote": "demo-a 输入 1 元"},
        {"model": "demo-b", "input": 2, "output": 8, "currency": "CNY", "quote": "demo-b 输入 2 元"},
    ])
    result = fetch_page_prices("https://example.com/pricing", ai_config=AI_CONFIG, transport=transport)
    assert result["method"] == "ai"
    assert [record["model"] for record in result["models"]] == ["demo-a", "demo-b"]
    assert all(record["price_status"] == "candidate" for record in result["models"])


def test_fetch_suspect_parse_demoted_without_ai() -> None:
    """AI 禁用时可疑静态结果保留但整页降级为待复核，不当可信基准。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SUSPICIOUS_PAGE)

    result = fetch_page_prices(
        "https://example.com/pricing",
        ai_config=page_price.AIConfig(base_url="https://ai.test/v1", models=("m",), api_key="k", enabled=False),
        transport=httpx.MockTransport(handler),
    )
    assert result["method"] == "static-html"
    assert all(record["price_status"] == "candidate" for record in result["models"])
    assert any("待复核" in warning for warning in result["warnings"])


def test_fetch_big_page_slims_and_chunks_ai() -> None:
    """超长页剥标签分块逐段提取，再按模型键合并。"""
    filler = "<div>导航菜单占位文字</div>" * 40
    # 价格写成纯文本行：静态三种解析都吃不下，强制走分块 AI
    page = (
        "<html><body>" + filler
        + "<p>model-one：输入 1 元/百万 tokens，输出 4 元/百万 tokens</p>" + filler
        + "<p>model-two：输入 2 元/百万 tokens，输出 8 元/百万 tokens</p></body></html>"
    )
    assert len(page) > 200  # 确保超过测试用的 max_input_chars

    seen_chunks: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content.decode())["messages"][1]["content"]
            seen_chunks.append(body)
            models = []
            if "model-one" in body:
                models.append({"model": "model-one", "input": 1, "output": 4, "currency": "CNY", "quote": "输入 1 元"})
            if "model-two" in body:
                models.append({"model": "model-two", "input": 2, "output": 8, "currency": "CNY", "quote": "输入 2 元"})
            return httpx.Response(200, json=_ai_response(models))
        return httpx.Response(200, text=page)

    config = page_price.AIConfig(base_url="https://ai.test/v1", models=("m",), api_key="k", max_input_chars=200)
    result = fetch_page_prices("https://example.com/pricing", ai_config=config, transport=httpx.MockTransport(handler))
    assert result["method"] == "ai"
    assert [record["model"] for record in result["models"]] == ["model-one", "model-two"]
    assert len(seen_chunks) >= 2
    assert all("<div>" not in chunk for chunk in seen_chunks)  # 超长页先剥标签再喂 AI


def test_fetch_unseen_vendor_full_pipeline(monkeypatch: pytest.MonkeyPatch):
    """通用性契约：从没见过的虚构厂商页面走完整流水线——JS 空壳→渲染→可疑解析→AI 复核出档位，
    全程不依赖任何厂商名，只认内容特征（注释字符、表头语义、价格证据）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json=_ai_response([
                {"model": "acme-large",
                 "tiers": [
                     {"name": "高峰时段", "standard": True, "input": 2, "output": 8},
                     {"name": "空闲时段", "input": 1, "output": 4},
                 ],
                 "currency": "CNY", "quote": "acme-large"},
            ]))
        return httpx.Response(200, text=JS_SHELL_PAGE)

    monkeypatch.setattr(page_price, "fetch_page_html", lambda url, headers, user_agent: (
        "<html><body><table>"
        "<tr><th>模型名称</th><th>输入单价（元/百万 Tokens）</th><th>输出单价（元/百万 Tokens）</th></tr>"
        "<tr><td>acme-small、acme-mini</td><td>1</td><td>4</td></tr></table>"
        "<p>acme-large 高峰时段 输入 2 元/百万 tokens、输出 8 元；空闲时段 输入 1 元、输出 4 元。</p>"
        "</body></html>"
    ))

    result = fetch_page_prices(
        "https://acme.test/pricing", ai_config=AI_CONFIG,
        transport=httpx.MockTransport(handler), headless=True,
    )
    assert result["method"] == "ai"
    assert [record["model"] for record in result["models"]] == ["acme-large"]
    record = result["models"][0]
    assert record["price_status"] == "candidate"
    # 可疑静态结果被 AI 复核替换：基准取高峰标准档，空闲档进 tiers
    assert (record["input_price"], record["output_price"]) == (2.0, 8.0)
    assert [tier["name"] for tier in record["tiers"]] == ["高峰时段", "空闲时段"]
