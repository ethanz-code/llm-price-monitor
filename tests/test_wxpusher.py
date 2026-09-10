"""WxPusher 变化事件汇总推送：消息构建、无变化不发、超长截断。"""
import httpx

from llm_price_monitor import wxpusher


def _capture(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"code": 1000}, request=request)

    monkeypatch.setattr(wxpusher.httpx, "post", fake_post)
    return calls


def test_send_change_digest_builds_message(monkeypatch):
    calls = _capture(monkeypatch)
    wxpusher.send_change_digest(
        app_token="tk",
        uid="u1",
        price_events=[{
            "site_id": "demo", "model": "m1", "kind": "changed",
            "previous": {"input_price": 1, "output_price": 2, "unit": "USD/1M tokens", "metadata": {"group": "vip"}},
            "current": {"input_price": 3, "output_price": 4, "unit": "USD/1M tokens", "metadata": {"group": "vip"}},
        }],
        status_events=[{"site_id": "demo", "kind": "status_changed", "changes": [{"op": "add"}]}],
        notice_events=[{"site_id": "demo", "kind": "notice_init", "content": "公告正文"}],
    )
    assert len(calls) == 1
    _, payload = calls[0]
    assert payload["uids"] == ["u1"]
    assert "demo · m1（vip）价格变化" in payload["content"]
    assert "输入 3 / 输出 4" in payload["content"] and "原 输入 1 / 输出 2" in payload["content"]
    assert "渠道状态有变化（1 处）" in payload["content"]
    assert "公告发布" in payload["content"]


def test_send_change_digest_no_events_sends_nothing(monkeypatch):
    calls = _capture(monkeypatch)
    wxpusher.send_change_digest(app_token="tk", uid=None, price_events=[], status_events=[], notice_events=[])
    assert calls == []


def test_send_change_digest_truncates_long_lists(monkeypatch):
    calls = _capture(monkeypatch)
    events = [{"site_id": "s", "model": "m", "kind": "new", "previous": None, "current": {"metadata": {}}} for _ in range(40)]
    wxpusher.send_change_digest(app_token="tk", uid=None, price_events=events, status_events=[], notice_events=[])
    content = calls[0][1]["content"]
    assert "其余 10 条变化略" in content
