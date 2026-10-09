"""price-discover 纯函数测试：链接归一、域名转 id、README 表格解析、pricing 响应判读。"""
import pytest

from llm_price_monitor.discover import (
    build_importable,
    classify_pricing,
    harvest_html_links,
    harvest_awesome_api_proxy,
    harvest_markdown_links,
    harvest_welfare,
    host_to_id,
    normalize_host,
    origin_of,
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
            "| 1 | [瓦瓦AI](http://wawapii.com/) | wawapii.com | 主打稳定 | 在线 | 99.7% | +167 |",
            "| 2 | [可乐AI](https://code28.ccwu.cc/sign-up?aff=oqw4) | code28.ccwu.cc | 低倍率 | 离线 | 100% | -4 |",
            "| 3 | 导航本身 [最全API导航](https://zuiquanapi.com/) | - | 不是站点行 | 在线 | 100% | +1 |",
        ]
    )
    rows = harvest_awesome_api_proxy(readme)
    assert [r.host for r in rows] == ["wawapii.com", "code28.ccwu.cc"]
    assert rows[0].name == "瓦瓦AI"
    assert rows[0].meta == {"status": "在线", "uptime7d": "99.7%", "rating": 167}
    assert rows[1].meta["rating"] == -4


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
