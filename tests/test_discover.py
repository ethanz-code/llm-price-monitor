"""price-discover 测试：链接归一、域名转 id、README 表格解析、pricing 响应判读，
以及 probe 落盘链路（--take 只预览不落盘 / 全量落盘 / 重测增量合并）与 harvest 源选择。"""
import asyncio
import json
from pathlib import Path

import pytest

import llm_price_monitor.discover as discover_mod
from llm_price_monitor.discover import (
    SOURCE_KEYS,
    Candidate,
    build_importable,
    classify_pricing,
    harvest_html_links,
    harvest_awesome_api_proxy,
    harvest_markdown_links,
    harvest_welfare,
    harvest_zuiquan,
    host_to_id,
    normalize_host,
    origin_of,
    parse_fofa,
    parse_hunter,
    parse_quake,
)


def test_origin_of_strips_affiliate_paths_and_noise():
    assert origin_of("https://x-llm.net/sign-up?aff=4KHa") == "https://x-llm.net"
    assert origin_of("http://wawapii.com/") == "http://wawapii.com"
    assert origin_of("https://www.github.com/daheiai") is None
    assert origin_of("https://t.me/somegroup") is None
    assert origin_of("not a url") is None


def test_host_to_id_strips_infra_prefix_and_tld_and_dedupes():
    taken: set[str] = set()
    assert host_to_id("shannonapi.xyz", taken) == "shannonapi"
    assert host_to_id("api.gpt.ge", taken) == "gpt"
    assert host_to_id("www.bkbk.baby", taken) == "bkbk"
    assert host_to_id("uo.mentoe.com", taken) == "uo-mentoe"
    # 撞名追加序号
    assert host_to_id("api.gpt.ge", taken) == "gpt-2"


def test_harvest_awesome_api_proxy_parses_table_rows():
    readme = "\n".join(
        [
            "# 最全 AI API 中转站导航",
            "| # | 站点 | 域名 | 描述 | 状态 | 7天可用率 | 评价净值 |",
            "| ---: | --- | --- | --- | :---: | ---: | ---: |",
            "| 1 | [瓦瓦AI](http://wawapii.com/) | wawapii.com | 🎁 主打稳定<br>长期在线 | 在线 | 99.7% | +167 |",
            "| 2 | [可乐AI](https://code28.ccwu.cc/sign-up?aff=oqw4) | code28.ccwu.cc | 低倍率 | 离线 | 100% | -4 |",
            "| 3 | 导航本身 [最全API导航](https://zuiquanapi.com/) | - | 不是站点行 | 在线 | 100% | +1 |",
        ]
    )
    rows = harvest_awesome_api_proxy(readme)
    assert [r.host for r in rows] == ["wawapii.com", "code28.ccwu.cc"]
    assert rows[0].name == "瓦瓦AI"
    assert rows[0].meta["description"] == "主打稳定 长期在线"  # 描述列清洗进 meta
    assert rows[0].meta["rating"] == 167
    assert rows[1].meta["rating"] == -4


def test_harvest_zuiquan_parses_escaped_payload_entries():
    """zuiquanapi RSC payload 是双层 JSON 转义的结构化条目，解开转义拿 name/description。"""
    payload = (
        r'前缀[]{\"id\":204,\"subcategory_id\":5,\"name\":\"aitokensflux\",\"url\":\"https://aitokensflux.com/register?aff=INx2\",'
        r'\"domain\":\"\",\"logo\":\"?\",\"tag\":\"第三方\",\"description\":\"aitokensflux 主打\\\"稳定好用\\\"，美元人民币 1:1 结算。\",'
        r'\"monitor_tier\":\"basic\",\"is_promoted\":0,\"rank_score\":\"0.438846\",\"sort_order\":30,'
        r'\"active_subcategory_id\":null,\"has_active_sponsorship\":0,\"created_at\":\"2026-06-14 16:06:23\"}'
        r',[{\"other\":1}]'
    )
    rows = harvest_zuiquan(payload)
    assert len(rows) == 1
    row = rows[0]
    assert row.host == "aitokensflux.com"
    assert row.name == "aitokensflux"
    assert row.meta["description"] == 'aitokensflux 主打"稳定好用"，美元人民币 1:1 结算。'


def test_harvest_welfare_ignores_entries_without_url():
    payload = '{"sites": [{"name": "A", "homeUrl": "https://api.a.example"}, {"name": "B", "homeUrl": null}]}'
    rows = harvest_welfare(payload)
    assert [r.host for r in rows] == ["api.a.example"]


def test_classify_pricing_handles_new_api_shapes():
    assert classify_pricing({"success": True, "data": [{"model_name": "gpt-4o"}, {"model_name": "o4"}]}) == (2, True)
    assert classify_pricing({"success": True, "data": {"gpt-4o": {}}}) == (1, True)
    assert classify_pricing({"success": False, "message": "无权进行此操作，未登录且未提供 access token"}) == (0, False)
    assert classify_pricing("not json") == (0, False)


@pytest.mark.parametrize("bad", [None, 3])
def test_classify_pricing_rejects_non_dict(bad):
    assert classify_pricing(bad) == (0, False)


def test_normalize_host_strips_infra_prefixes_case_insensitively():
    assert normalize_host("WWW.Cun.ai") == "cun.ai"
    assert normalize_host("api.artbloom.tech") == "artbloom.tech"
    assert normalize_host("downstream.jbbtoken.cn") == "downstream.jbbtoken.cn"  # 业务子域保留
    assert normalize_host("newapi.dragon3api.com") == "dragon3api.com"


def _cand(host: str) -> "object":
    from llm_price_monitor.discover import Candidate

    return Candidate(host=host, url=f"https://{host}", name=host, sources=["t"])


def test_build_importable_excludes_existing_library_hosts():
    probed = [
        {"name": "a", "url": "https://aihub365.cn", "sources": ["t"], "note": "", "new_api": True, "pricing_ok": True, "models": 5, "auth_required": False, "error": ""},
        {"name": "b", "url": "https://fresh.example.com", "sources": ["t"], "note": "", "new_api": True, "pricing_ok": True, "models": 3, "auth_required": False, "error": ""},
    ]
    cands = {_cand("aihub365.cn").host: _cand("aihub365.cn"), _cand("fresh.example.com").host: _cand("fresh.example.com")}
    configs = build_importable(cands, probed, exclude_hosts={"aihub365.cn"})
    assert [c["id"] for c in configs] == ["fresh-example"]


def test_origin_of_cleans_escaped_payload_urls():
    # zuiquanapi 页面 JSON 转义存储：反斜杠必须剥掉，否则生成 https://x.y\/api/pricing 废链
    assert origin_of("https://forapi.ai\\/register?aff=x") == "https://forapi.ai"
    assert origin_of(r"http://www.nmoon.cc/\ ") == "http://www.nmoon.cc"


def test_harvest_markdown_links_filters_noise_and_keeps_name():
    readme = (
        "### 站点\n\n"
        "- [甲API](https://jia.example.com/register?aff=1)\n"
        "- [项目主页](https://github.com/some/repo)\n"
        "- [乙](http://yi.example.cn/)\n"
    )
    rows = harvest_markdown_links(readme, source="nav-repo")
    assert [(r.host, r.name) for r in rows] == [("jia.example.com", "甲API"), ("yi.example.cn", "乙")]
    assert rows[0].sources == ["nav-repo"]


def test_harvest_html_links_skips_own_host_and_cleans_backslash():
    html = '<a href="https://a.example.com/">a</a> <a href="https://nav.example.com/">nav</a>'
    rows = harvest_html_links(html, source="nav-site", own_host="nav.example.com")
    assert [r.host for r in rows] == ["a.example.com"]
    assert harvest_html_links(r"https://b.example.com\/x", source="s", own_host="s.com")[0].host == "b.example.com"


# ---------- probe 落盘链路与 harvest 源选择（run_probe / run_harvest） ----------


def _write_candidates(out_dir: Path, *cands: Candidate) -> None:
    payload = {"generated_at": "x", "count": len(cands), "candidates": [cand.__dict__ for cand in cands]}
    (out_dir / "candidates.json").write_text(json.dumps(payload), encoding="utf-8")


def _probed_row(url: str, *, ok: bool = True, models: int = 10) -> dict:
    return {
        "name": url, "url": url, "sources": [], "note": "",
        "new_api": ok, "pricing_ok": ok, "models": models if ok else 0,
        "auth_required": False, "error": "",
    }


def test_run_probe_take_preview_keeps_full_results(tmp_path: Path, monkeypatch):
    """--take 部分探测只预览：只探测前 N 个候选，全量结果文件必须原样保留，
    防止小样本静默覆盖上一轮全量 probed/importable（曾出过的事故，此为回归守卫）。"""
    out = tmp_path / "discovery"
    out.mkdir()
    monkeypatch.setattr(discover_mod, "OUT_DIR", out)
    _write_candidates(
        out,
        Candidate("a-llm.example", "https://a-llm.example", "A"),
        Candidate("b-llm.example", "https://b-llm.example", "B"),
        Candidate("c-llm.example", "https://c-llm.example", "C"),
    )
    sentinel_probed = '{"generated_at": "sentinel", "results": []}'
    sentinel_importable = '[{"id": "sentinel"}]'
    (out / "probed.json").write_text(sentinel_probed, encoding="utf-8")
    (out / "importable.json").write_text(sentinel_importable, encoding="utf-8")

    seen: list[list[str]] = []

    async def fake_probe(candidates, concurrency, timeout, proxy):
        seen.append([cand.host for cand in candidates])
        return [_probed_row(cand.url) for cand in candidates]

    monkeypatch.setattr(discover_mod, "probe", fake_probe)
    asyncio.run(discover_mod.run_probe(take=2, concurrency=2, timeout=1.0, proxy=None, retry_failed=False))

    assert seen == [["a-llm.example", "b-llm.example"]]  # 只探测前 take 个
    assert (out / "probed.json").read_text(encoding="utf-8") == sentinel_probed
    assert (out / "importable.json").read_text(encoding="utf-8") == sentinel_importable


def test_run_probe_full_run_writes_results(tmp_path: Path, monkeypatch):
    """全量探测（take=0）正常落盘：明细含失败项，importable 只含可用站且默认停用。"""
    out = tmp_path / "discovery"
    out.mkdir()
    monkeypatch.setattr(discover_mod, "OUT_DIR", out)
    _write_candidates(out, Candidate("a-llm.example", "https://a-llm.example", "A"))

    async def fake_probe(candidates, concurrency, timeout, proxy):
        return [_probed_row("https://a-llm.example"), _probed_row("https://dead.example", ok=False)]

    monkeypatch.setattr(discover_mod, "probe", fake_probe)
    monkeypatch.setattr(discover_mod, "existing_site_hosts", lambda: {})
    asyncio.run(discover_mod.run_probe(take=0, concurrency=2, timeout=1.0, proxy=None, retry_failed=False))

    results = json.loads((out / "probed.json").read_text(encoding="utf-8"))["results"]
    assert {row["url"] for row in results} == {"https://a-llm.example", "https://dead.example"}
    importable = json.loads((out / "importable.json").read_text(encoding="utf-8"))
    assert importable == [
        {
            "id": "a-llm",
            "network": {"url": "https://a-llm.example/api/pricing"},
            "notice": {"url": "https://a-llm.example/api/status"},
            "enabled": False,
        }
    ]


def test_run_probe_retry_failed_merges_stale_ok(tmp_path: Path, monkeypatch):
    """--retry-failed 只重测失败项：上轮已通过的保留原样，与捞回结果合并落盘。"""
    out = tmp_path / "discovery"
    out.mkdir()
    monkeypatch.setattr(discover_mod, "OUT_DIR", out)
    _write_candidates(
        out,
        Candidate("a-llm.example", "https://a-llm.example", "A"),
        Candidate("b-llm.example", "https://b-llm.example", "B"),
    )
    (out / "probed.json").write_text(
        json.dumps({
            "generated_at": "x",
            "results": [_probed_row("https://a-llm.example"), _probed_row("https://b-llm.example", ok=False)],
        }),
        encoding="utf-8",
    )

    async def fake_probe(candidates, concurrency, timeout, proxy):
        assert [cand.host for cand in candidates] == ["b-llm.example"]  # 只重测上轮失败项
        return [_probed_row("https://b-llm.example")]

    monkeypatch.setattr(discover_mod, "probe", fake_probe)
    monkeypatch.setattr(discover_mod, "existing_site_hosts", lambda: {})
    asyncio.run(discover_mod.run_probe(take=0, concurrency=2, timeout=1.0, proxy=None, retry_failed=True))

    results = json.loads((out / "probed.json").read_text(encoding="utf-8"))["results"]
    assert {row["url"] for row in results} == {"https://a-llm.example", "https://b-llm.example"}
    assert all(row["pricing_ok"] for row in results)  # 捞回项与保留项都可用


def test_run_harvest_only_selection_and_incremental_merge(tmp_path: Path, monkeypatch):
    """缺省只跑 zuiquanapi 单源，--only/all 按名展开，未知源拒绝；
    候选池与上轮增量合并——本轮没拉到的旧站不会被挤掉。"""
    out = tmp_path / "discovery"
    out.mkdir()
    monkeypatch.setattr(discover_mod, "OUT_DIR", out)
    seen_only: list[set[str]] = []

    async def fake_harvest(proxy, only=None):
        seen_only.append(set(only or ()))
        return [Candidate("fresh.example", "https://fresh.example", "Fresh", ["zuiquanapi"])]

    monkeypatch.setattr(discover_mod, "harvest", fake_harvest)

    asyncio.run(discover_mod.run_harvest(None, None))
    assert seen_only[-1] == {"zuiquanapi"}  # 缺省单源，不再全源跑
    asyncio.run(discover_mod.run_harvest(None, "all"))
    assert seen_only[-1] == set(SOURCE_KEYS)
    asyncio.run(discover_mod.run_harvest(None, "awesome-api-proxy,apisou"))
    assert seen_only[-1] == {"awesome-api-proxy", "apisou"}
    with pytest.raises(SystemExit):
        asyncio.run(discover_mod.run_harvest(None, "nope"))

    # 增量合并：上轮池里的站在本轮源里拉不到，也必须留在池子里
    _write_candidates(out, Candidate("old.example", "https://old.example", "Old", ["github-nav"]))
    asyncio.run(discover_mod.run_harvest(None, None))
    merged = json.loads((out / "candidates.json").read_text(encoding="utf-8"))["candidates"]
    assert {cand["host"] for cand in merged} == {"old.example", "fresh.example"}


# ---------- 网络空间测绘引擎响应解析（sweep） ----------


def test_parse_fofa_handles_list_rows_and_port_schemes():
    payload = {"error": False, "results": [["demo.example.com", "443", "New API"], ["1.2.3.4:8080", "8080", "One API"], ["https://x.example.net", "443", "T"], ["", "443", "空"]]}
    rows = parse_fofa(payload)
    assert [(r.host, r.url) for r in rows] == [
        ("demo.example.com", "https://demo.example.com"),
        ("1.2.3.4", "http://1.2.3.4:8080"),
        ("x.example.net", "https://x.example.net"),
    ]
    assert all(r.sources == ["fofa"] for r in rows)


def test_parse_quake_prefers_http_host_over_ip():
    payload = {"code": 0, "data": [
        {"ip": "1.2.3.4", "port": 443, "hostname": ["a.example.com"], "service": {"http": {"host": "a.example.com", "title": "New API"}}},
        {"ip": "5.6.7.8", "port": 3000, "service": {"http": {"host": "5.6.7.8:3000"}}},
        {"ip": "9.9.9.9", "port": 80},
    ]}
    rows = parse_quake(payload)
    assert [(r.url, r.name) for r in rows] == [("https://a.example.com", "New API"), ("http://5.6.7.8:3000", "")]


def test_parse_hunter_uses_url_field():
    payload = {"code": 200, "data": {"arr": [
        {"url": "https://h.example.com", "http_title": "中转站", "domain": "h.example.com"},
        {"url": "无效"}, {"url": "http://p.example.org:8443", "http_title": ""},
    ]}}
    rows = parse_hunter(payload)
    assert [(r.host, r.url, r.name) for r in rows] == [("h.example.com", "https://h.example.com", "中转站"), ("p.example.org", "http://p.example.org:8443", "")]
