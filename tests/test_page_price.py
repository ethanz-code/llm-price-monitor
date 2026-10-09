"""通用定价页拉取脚本（page_price）：三种确定性解析、无头兜底与 AI 兜底的证据校验。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from llm_price_monitor import page_price
from llm_price_monitor.page_price import (
    detect_currency,
    fetch_page_prices,
    parse_html_tables,
    parse_json_entries,
    parse_markdown_tables,
    price_value,
    resolve_ai_config,
    unit_scale,
)

# 仿 docs.bigmodel.cn/cn/guide/start/pricing.md 的真实结构：旗舰模型表多一列「输入模态」、
# 「免费」单元格、同一模型多上下文档、Accordion 内嵌第二张表
BIGMODEL_MD = """# API 定价

除特别说明外，模型价格统一按照“元/百万 Tokens”展示。

## 旗舰模型

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


def by_key(records: list[dict], name: str) -> dict:
    return next(record for record in records if record["model_key"] == name)


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


def test_parse_markdown_bigmodel_structure() -> None:
    records = parse_markdown_tables(BIGMODEL_MD, "https://docs.bigmodel.cn/cn/guide/start/pricing.md")
    assert records is not None
    keys = {record["model_key"] for record in records}
    assert keys == {"glm5.3", "glm5.3flash", "glm5.1", "glm4.7flash", "glmz1air"}

    flagship = by_key(records, "glm5.3")
    assert flagship["input_price"] == 8.0
    assert flagship["output_price"] == 28.0
    assert flagship["cache_read_price"] == 2.0
    assert flagship["currency"] == "CNY"
    assert flagship["unit"] == "CNY/1M tokens"
    # 「输入模态」列不该混进价格；缓存存储列（按小时计费）不参与
    assert flagship["quote"] == "GLM-5.3 | 1M | 8 | 28 | 限时免费 | 2 | 文本"

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


def test_fetch_falls_back_to_headless_for_js_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=JS_SHELL_PAGE)

    rendered_calls: list[str] = []

    def fake_fetch_page_html(url: str, config: dict, user_agent: str | None = None, extra_headers: dict | None = None) -> str:
        rendered_calls.append(url)
        return HTML_PAGE

    monkeypatch.setattr(page_price, "fetch_page_html", fake_fetch_page_html)
    result = fetch_page_prices("https://example.com/pricing", transport=httpx.MockTransport(handler))
    assert rendered_calls == ["https://example.com/pricing"]
    assert result["method"] == "headless-html"
    assert len(result["models"]) == 2


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
