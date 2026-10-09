"""geoip 批量归属地：一轮一次请求查完、缓存 TTL 复用、传输失败短退避（全程 mock，不联网）。"""
from __future__ import annotations

import json
import time

from llm_price_monitor import geoip


def _reset_caches() -> None:
    geoip._dns_cache.clear()
    geoip._geo_cache.clear()


def _success(answer_ips: list[str]) -> list[dict]:
    return [
        {"status": "success", "country": "测试国", "city": "测试市", "lat": 30.0, "lon": 110.0}
        for _ in answer_ips
    ]


def test_resolve_site_geo_one_batch_request_per_round(monkeypatch):
    """多个站点首次定位：只发一次批量请求，结果按站点返回。"""
    _reset_caches()
    calls: list[tuple[str, str, list[str]]] = []

    def fake_fetch(url, headers=None, timeout=5.0, data=None, method="GET"):
        assert method == "POST" and "/batch?" in url
        ips = json.loads(data)
        calls.append((method, url, ips))
        return _success(ips)

    monkeypatch.setattr(geoip, "_fetch_json", fake_fetch)
    monkeypatch.setattr(geoip, "_resolve_host", lambda host: f"1.2.3.{host}")
    configs = [{"id": f"s{i}", "source_url": f"https://host{i}.test"} for i in range(5)]

    geo = geoip.resolve_site_geo(configs)

    assert len(calls) == 1 and len(calls[0][2]) == 5
    assert set(geo) == {f"s{i}" for i in range(5)}
    for index, (site_id, point) in enumerate(geo.items()):
        assert point["lat"] == 30.0, (site_id, point)
        assert point["ip"] == f"1.2.3.host{index}.test", (site_id, point)


def test_resolve_site_geo_second_round_reuses_cache(monkeypatch):
    """缓存有效期内再次调用：零网络请求，直接复用归属地。"""
    _reset_caches()
    calls: list = []

    def fake_fetch(url, headers=None, timeout=5.0, data=None, method="GET"):
        calls.append(url)
        return _success(json.loads(data))

    monkeypatch.setattr(geoip, "_fetch_json", fake_fetch)
    monkeypatch.setattr(geoip, "_resolve_host", lambda host: "1.2.3.4")
    configs = [{"id": "demo", "source_url": "https://demo.test"}]

    first = geoip.resolve_site_geo(configs)
    second = geoip.resolve_site_geo(configs)

    assert len(calls) == 1
    assert first == second and first["demo"]["ip"] == "1.2.3.4"


def test_batch_transport_failure_short_backoff(monkeypatch):
    """批量请求传输层失败：整批记 2 分钟短失败缓存，期间不再发请求，过期后恢复重试。"""
    _reset_caches()
    calls: list = []

    def failing_fetch(url, headers=None, timeout=5.0, data=None, method="GET"):
        calls.append(url)
        raise OSError("simulated rate limit")

    monkeypatch.setattr(geoip, "_fetch_json", failing_fetch)
    monkeypatch.setattr(geoip, "_resolve_host", lambda host: "1.2.3.4")
    configs = [{"id": "demo", "source_url": "https://demo.test"}]

    assert geoip.resolve_site_geo(configs) == {}
    assert geoip.resolve_site_geo(configs) == {}  # 退避期内不再发请求
    assert len(calls) == 1

    geoip._geo_cache["1.2.3.4"] = (time.monotonic() - 1, None)  # 快进：失败缓存过期
    monkeypatch.setattr(geoip, "_fetch_json", lambda *a, **k: _success(["1.2.3.4"]))
    retried = geoip.resolve_site_geo(configs)
    assert retried["demo"]["country"] == "测试国"


def test_locate_ips_chunks_over_batch_size(monkeypatch):
    """超过单请求上限（100 个 IP）时分块多次请求。"""
    _reset_caches()
    sizes: list[int] = []

    def fake_fetch(url, headers=None, timeout=5.0, data=None, method="GET"):
        ips = json.loads(data)
        sizes.append(len(ips))
        return _success(ips)

    monkeypatch.setattr(geoip, "_fetch_json", fake_fetch)
    geoip._locate_ips([f"10.0.0.{i}" for i in range(250)])

    assert sizes == [100, 100, 50]


def test_api_fail_answer_is_negative_cached(monkeypatch):
    """接口明确返回失败（如保留段 IP）：该 IP 不出现在结果里，且按失败 TTL 记住不再重查。"""
    _reset_caches()
    calls: list = []

    def fake_fetch(url, headers=None, timeout=5.0, data=None, method="GET"):
        calls.append(url)
        return [{"status": "fail", "message": "reserved range"}]

    monkeypatch.setattr(geoip, "_fetch_json", fake_fetch)
    monkeypatch.setattr(geoip, "_resolve_host", lambda host: "192.168.1.1")
    configs = [{"id": "demo", "source_url": "https://demo.test"}]

    assert geoip.resolve_site_geo(configs) == {}
    assert geoip.resolve_site_geo(configs) == {}
    assert len(calls) == 1  # 失败结论已缓存，不反复打接口
