"""出口失败记忆：直连失败标记、TTL 过期重探、成功洗白、备用代理地址实时读取。"""
import pytest

from llm_price_monitor import egress


@pytest.fixture(autouse=True)
def _clean_egress():
    """每个用例从干净的失败记忆与未接线的 provider 出发，避免用例间串味。"""
    egress._failures.clear()
    egress.configure_provider(None)
    yield
    egress._failures.clear()
    egress.configure_provider(None)


def test_mark_failed_switches_to_proxy_until_ttl(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(egress, "_now", lambda: now[0])

    egress.mark_direct_failed("Example.COM")  # 归一化：大小写不影响命中
    assert egress.plan("example.com").use_proxy
    assert not egress.plan("example.com").reprobe

    now[0] += egress.REPROBE_SECONDS - 1
    assert egress.plan("example.com").use_proxy

    now[0] += 1  # 过 TTL：放行一次直连重探
    plan = egress.plan("example.com")
    assert not plan.use_proxy and plan.reprobe


def test_reprobe_failure_renews_ttl(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(egress, "_now", lambda: now[0])

    egress.mark_direct_failed("demo.test")
    now[0] += egress.REPROBE_SECONDS + 1
    assert egress.plan("demo.test").reprobe

    egress.mark_direct_failed("demo.test")  # 重探仍失败 → 记忆续期，回到走代理
    assert egress.plan("demo.test").use_proxy


def test_mark_direct_ok_clears_memory(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(egress, "_now", lambda: now[0])

    egress.mark_direct_failed("demo.test")
    now[0] += egress.REPROBE_SECONDS + 1
    egress.mark_direct_ok("demo.test")  # 重探成功 → 洗白，后续直连
    plan = egress.plan("demo.test")
    assert not plan.use_proxy and not plan.reprobe


def test_fallback_proxy_reads_provider_with_trim(monkeypatch):
    # 未接线：永远 None
    assert egress.fallback_proxy() is None
    egress.configure_provider(lambda: " http://172.17.0.1:7890 ")
    assert egress.fallback_proxy() == "http://172.17.0.1:7890"
    # provider 抛错（如库被关）只当未配置，采集不因此挂掉
    def boom():
        raise RuntimeError("db closed")

    egress.configure_provider(boom)
    assert egress.fallback_proxy() is None
