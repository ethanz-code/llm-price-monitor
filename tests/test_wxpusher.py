"""WxPusher 推送：单条文本消息的请求体与业务码校验。"""
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


def test_send_wxpusher_builds_payload(monkeypatch):
    calls = _capture(monkeypatch)
    wxpusher.send_wxpusher(app_token="tk", content="正文", summary="摘要", uid="u1")
    assert len(calls) == 1
    url, payload = calls[0]
    assert url == wxpusher.SEND_URL
    assert payload["appToken"] == "tk"
    assert payload["content"] == "正文"
    assert payload["summary"] == "摘要"
    assert payload["contentType"] == 1
    assert payload["uids"] == ["u1"]


def test_send_wxpusher_without_uid_omits_uids(monkeypatch):
    calls = _capture(monkeypatch)
    wxpusher.send_wxpusher(app_token="tk", content="正文", summary="摘要", uid=None)
    assert "uids" not in calls[0][1]


def test_send_wxpusher_raises_on_business_error(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"code": 1001, "msg": "appToken 无效"}, request=request)

    monkeypatch.setattr(wxpusher.httpx, "post", fake_post)
    try:
        wxpusher.send_wxpusher(app_token="tk", content="正文", summary="摘要", uid=None)
    except RuntimeError as exc:
        assert "1001" in str(exc)
    else:
        raise AssertionError("业务码非 1000 时应抛错")
