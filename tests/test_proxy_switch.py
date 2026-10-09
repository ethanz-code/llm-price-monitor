"""代理节点自动切换：选可达最快节点、过滤信息位与当前节点、冷却与未启用；Fallback 联动重试一次。"""
import urllib.parse

import httpx
import pytest

from llm_price_monitor import egress, proxy_switch
from llm_price_monitor.http_retry import FallbackTransport, RetryingTransport

SECRET = "test-secret"


class FakeController:
    """按 mihomo 控制接口形状假造路由：组信息、逐节点 delay 实测、PUT 切换。"""

    def __init__(self, members: list[str], delays: dict[str, int | None], now: str) -> None:
        self.members = members
        self.delays = delays
        self.now = now
        self.selected: list[str] = []
        self.probed: list[str] = []

    def request(self, base: str, secret: str, path: str, *, method: str = "GET", body: dict | None = None, timeout: float = 8.0):
        group_path = f"/proxies/{urllib.parse.quote('节点组', safe='')}"
        if method == "GET" and path == group_path:
            return {"all": list(self.members), "now": self.now}
        if method == "PUT" and path == group_path and body:
            self.selected.append(body["name"])
            self.now = body["name"]  # 与真实 mihomo 一致：切换后组内当前节点随之变化
            return None
        if method == "GET" and "/delay" in path:
            # mihomo 的实测端点不带组前缀：/proxies/{节点}/delay?timeout=&url=
            member = urllib.parse.unquote(path.split("/proxies/", 1)[1].rsplit("/delay", 1)[0])
            self.probed.append(member)
            delay = self.delays.get(member)
            if delay is None:
                raise RuntimeError("delay probe timeout")
            return {"delay": delay}
        raise AssertionError(f"意外请求 {method} {path}")


@pytest.fixture(autouse=True)
def _clean_state():
    proxy_switch._switched_at.clear()
    proxy_switch.configure_settings(lambda: {})
    yield
    proxy_switch._switched_at.clear()
    proxy_switch.configure_settings(lambda: {})


@pytest.fixture
def monkeypatch_proxy(monkeypatch):
    """把 _request 换成假控制器，并在用例结束后还原。"""
    def install(controller: FakeController) -> FakeController:
        monkeypatch.setattr(proxy_switch, "_request", controller.request)
        return controller

    yield install


def test_switch_picks_fastest_reachable_and_skips_info_nodes(monkeypatch_proxy):
    controller = monkeypatch_proxy(FakeController(
        members=["剩余流量：100GB", "官网通知", "节点慢", "节点快", "节点中"],
        delays={"节点慢": 400, "节点快": 90, "节点中": 250},
        now="节点慢",
    ))
    proxy_switch.configure_settings(lambda: {
        "proxy_controller_url": "http://127.0.0.1:9097",
        "proxy_controller_secret": SECRET,
        "proxy_switch_group": "节点组",
    })
    node = proxy_switch.auto_switch_for("demo.test", "https://demo.test/api/pricing")
    assert node == "节点快"
    assert controller.selected == ["节点快"]
    # 信息位与当前节点不做候选
    assert "剩余流量：100GB" not in controller.probed and "官网通知" not in controller.probed


def test_switch_returns_none_when_nothing_reachable(monkeypatch_proxy):
    controller = monkeypatch_proxy(FakeController(
        members=["节点A", "节点B"],
        delays={"节点A": None, "节点B": None},
        now="节点A",
    ))
    proxy_switch.configure_settings(lambda: {
        "proxy_controller_url": "http://127.0.0.1:9097",
        "proxy_controller_secret": SECRET,
        "proxy_switch_group": "节点组",
    })
    assert proxy_switch.auto_switch_for("demo.test", "https://demo.test/api/pricing") is None
    assert controller.selected == []


def test_switch_is_rate_limited_per_host(monkeypatch_proxy):
    controller = monkeypatch_proxy(FakeController(
        members=["节点A", "节点B"],
        delays={"节点A": 100, "节点B": 200},
        now="节点A",
    ))
    proxy_switch.configure_settings(lambda: {
        "proxy_controller_url": "http://127.0.0.1:9097",
        "proxy_controller_secret": SECRET,
        "proxy_switch_group": "节点组",
    })
    assert proxy_switch.auto_switch_for("demo.test", "https://demo.test/api/pricing") == "节点B"
    calls_after_first = len(controller.selected)
    # 同一域名冷却期内不再切：返回 None，控制接口零请求
    assert proxy_switch.auto_switch_for("demo.test", "https://demo.test/api/pricing") is None
    assert len(controller.selected) == calls_after_first == 1
    # 其他域名不受影响
    assert proxy_switch.auto_switch_for("other.test", "https://other.test/api/pricing") == "节点A"


def test_switch_disabled_without_controller_config(monkeypatch_proxy):
    controller = monkeypatch_proxy(FakeController(members=["节点A"], delays={"节点A": 100}, now="节点A"))
    proxy_switch.configure_settings(lambda: {"proxy_controller_url": "", "proxy_switch_group": ""})
    assert proxy_switch.auto_switch_for("demo.test", "https://demo.test/api/pricing") is None
    assert controller.selected == [] and controller.probed == []


# ---------- FallbackTransport 联动：代理腿也被拒 → 换节点 → 重试一次 ----------


@pytest.fixture(autouse=True)
def _clean_egress():
    egress._failures.clear()
    egress.configure_provider(None)
    yield
    egress._failures.clear()
    egress.configure_provider(None)


def test_fallback_retries_proxy_once_after_auto_switch(monkeypatch):
    monkeypatch.setattr("llm_price_monitor.proxy_switch.auto_switch_for", lambda host, url: "新节点")
    calls = {"direct": [], "proxy": []}

    def direct(request):
        calls["direct"].append(1)
        raise httpx.ConnectError("reset", request=request)

    def proxied(request):
        calls["proxy"].append(1)
        if len(calls["proxy"]) <= 1 + 3:  # 首轮含传输层退避重试，全被拒
            raise httpx.ConnectError("tls reset via proxy", request=request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr("llm_price_monitor.http_retry.time.sleep", lambda _s: None)
    client = httpx.Client(
        timeout=5,
        transport=FallbackTransport(
            direct=RetryingTransport(httpx.MockTransport(direct)),
            proxied=RetryingTransport(httpx.MockTransport(proxied)),
        ),
    )
    assert client.get("https://demo.test/api/pricing").status_code == 200
    assert len(calls["direct"]) == 4  # 直连腿完整退避
    assert len(calls["proxy"]) == 5  # 首轮 4 次 + 切换后重试 1 次


def test_fallback_raises_when_auto_switch_unavailable(monkeypatch):
    monkeypatch.setattr("llm_price_monitor.proxy_switch.auto_switch_for", lambda host, url: None)
    calls = {"proxy": []}

    def direct(request):
        raise httpx.ConnectError("reset", request=request)

    def proxied(request):
        calls["proxy"].append(1)
        raise httpx.ConnectError("tls reset via proxy", request=request)

    monkeypatch.setattr("llm_price_monitor.http_retry.time.sleep", lambda _s: None)
    client = httpx.Client(
        timeout=5,
        transport=FallbackTransport(
            direct=RetryingTransport(httpx.MockTransport(direct)),
            proxied=RetryingTransport(httpx.MockTransport(proxied)),
        ),
    )
    with pytest.raises(httpx.ConnectError):
        client.get("https://demo.test/api/pricing")
    assert len(calls["proxy"]) == 4  # 未切换：首轮退避耗尽即失败
