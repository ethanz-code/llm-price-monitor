"""访客 IP 归属地：省份名归一、批量解析容错与 store 侧的缓存/聚合。"""
from llm_price_monitor import visitor_geo
from llm_price_monitor.store import Store

# conftest 会把 visitor_geo.resolve_regions 换成离线假实现，模块导入时先留一份真身
_real_resolve_regions = visitor_geo.resolve_regions


def test_normalize_region():
    assert visitor_geo.normalize_region("中国", "广东省") == "广东"
    assert visitor_geo.normalize_region("中国", "内蒙古") == "内蒙古"
    assert visitor_geo.normalize_region("中国", "内蒙古自治区") == "内蒙古"
    assert visitor_geo.normalize_region("中国", "香港特别行政区") == "香港"
    assert visitor_geo.normalize_region("中国", "北京市") == "北京"
    assert visitor_geo.normalize_region("United States", "California") == "海外"
    assert visitor_geo.normalize_region("中国", "") == "未知"


def test_resolve_regions_offline_ips_marked_local():
    results = _real_resolve_regions(["127.0.0.1", "192.168.1.7"])
    assert results["192.168.1.7"]["province"] == "本地网络"
    assert results["127.0.0.1"]["province"] == "本地网络"  # 内网/环回都不出外网请求，直接标记


def test_store_geo_cache_and_region_dist(tmp_path, monkeypatch):
    store = Store(tmp_path / "monitor.db")
    store.add_visit(path="/overview", ip="1.2.3.4", user_agent="ua", browser="Chrome", os="macOS", device="desktop")
    store.add_visit(path="/history", ip="5.6.7.8", user_agent="ua", browser="Chrome", os="macOS", device="desktop")

    ips = store.pending_geo_ips(cutoff=0)
    assert set(ips) == {"1.2.3.4", "5.6.7.8"}

    monkeypatch.setattr(
        visitor_geo,
        "resolve_regions",
        lambda batch: {"1.2.3.4": {"province": "广东", "country": "中国", "city": "深圳"}},
    )
    resolved = visitor_geo.resolve_regions(ips)
    store.save_ip_geo(resolved, [ip for ip in ips if ip not in resolved])

    # 解析过的 IP 不再进待解析队列；失败的等 fail_ttl 过后再重试
    assert store.pending_geo_ips(cutoff=0) == []
    assert store.pending_geo_ips(cutoff=0, fail_ttl=0) == ["5.6.7.8"]

    regions = {item["name"]: item for item in store.region_dist(cutoff=0)}
    assert regions["中国"]["pv"] == 1  # 地图按国家级聚合
    assert regions["未知"]["pv"] == 1
    assert regions["未知"]["uv"] == 1
