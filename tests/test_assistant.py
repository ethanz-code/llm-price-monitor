"""智能分析助手接口：入口可见性、未配置拒答与带数据上下文的问答。"""
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from llm_price_monitor.webapi.routes import assistant
from llm_price_monitor.webapi.app import create_app
from tests.test_webapi import _config, workspace  # noqa: F401  workspace 为共享 fixture


def _enable_ai(client: TestClient) -> None:
    # 种子模式 skip_existing 不会覆盖已有的 ai 配置，这里直接写库
    client.app.state.store.set_document(
        "ai", {"enabled": True, "base_url": "https://ai.test/v1", "model": "test-model", "api_key": "sk-test"}
    )


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "演示站点 demo-model 输入 5.0 USD/1M tokens。"}}]}


def test_status_hidden_until_ai_configured(workspace: Path):
    unconfigured = TestClient(create_app(_config(workspace)))
    assert unconfigured.get("/api/assistant/status").json()["available"] is False
    configured = TestClient(create_app(_config(workspace)))
    _enable_ai(configured)
    body = configured.get("/api/assistant/status").json()
    assert body["available"] is True and body["model"] == "test-model"


def test_ask_requires_ai_configured(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    assert client.post("/api/assistant/ask", json={"question": "有哪些站点？"}).status_code == 400


def test_ask_injects_platform_data_and_returns_answer(workspace: Path, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> _FakeResponse:
        captured["url"] = url
        captured["body"] = json
        return _FakeResponse()

    monkeypatch.setattr(assistant.httpx, "post", fake_post)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    res = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res.status_code == 200
    assert "demo-model" in res.json()["answer"]
    # 公开写接口：带配置的 POST 也不要求管理员会话（已由 200 证明）
    prompt = captured["body"]["messages"][-1]["content"]
    assert "demo 站现在什么价？" in prompt and "demo-model" in prompt


def test_ask_rejects_blank_question(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    assert client.post("/api/assistant/ask", json={"question": "   "}).status_code == 400


def test_ask_context_strips_credentials_and_admin_paths(workspace: Path, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> _FakeResponse:
        captured["body"] = json
        return _FakeResponse()

    monkeypatch.setattr(assistant.httpx, "post", fake_post)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    # 站点配置带凭据、访问记录带管理页路径：都不应进入 AI 上下文
    client.app.state.store.upsert_site("demo", {
        "id": "demo", "adapter": "standard", "models": ["demo-model"],
        "network": {"url": "https://demo.test/pricing"},
        "cookie": "session=should-not-leak",
        "request_headers": {"Authorization": "Bearer ${SECRET_TOKEN}"},
        "token_refresh": {"url": "https://demo.test/refresh", "refresh_token": "should-not-leak"},
    })
    client.app.state.store.add_visit(path="/admin", ip="1.2.3.4", user_agent="test", browser="test", os="test", device="desktop")
    client.app.state.store.add_visit(path="/", ip="1.2.3.4", user_agent="test", browser="test", os="test", device="desktop")
    res = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res.status_code == 200
    prompt = captured["body"]["messages"][-1]["content"]
    assert "demo" in prompt and "https://demo.test/pricing" in prompt
    for secret in ("should-not-leak", "SECRET_TOKEN", "refresh_token", "/admin"):
        assert secret not in prompt


def test_ask_stream_requires_ai_configured(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    assert client.post("/api/assistant/ask/stream", json={"question": "有哪些站点？"}).status_code == 400


def test_ask_stream_emits_delta_frames(workspace: Path, monkeypatch):
    def fake_stream(*_args: object, **_kwargs: object):
        yield "demo"
        yield "-model 现价 5.0。"

    monkeypatch.setattr(assistant, "ai_stream", fake_stream)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    res = client.post("/api/assistant/ask/stream", json={"question": "demo 什么价？"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    frames = [line[6:] for line in res.text.splitlines() if line.startswith("data: ")]
    import json as _json

    deltas = "".join(_json.loads(frame).get("delta", "") for frame in frames)
    assert deltas == "demo-model 现价 5.0。"
    assert _json.loads(frames[-1])["done"] is True


class _GateResponse:
    """按请求里的系统提示词区分分类调用与正式回答调用。"""

    def __init__(self, content: str) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self.content}}]}


def _gated_client(workspace: Path, monkeypatch, actions: list[str], captured: dict):
    """actions 为每次分类调用的判定结果，按次出队；正式回答调用固定回演示答案。"""
    gate_actions = list(actions)

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> _GateResponse:
        captured.setdefault("calls", []).append(json["messages"][0]["content"])
        is_gate = "问题分类器" in json["messages"][0]["content"]
        action = gate_actions.pop(0) if gate_actions else "data"
        return _GateResponse(f'{{"action": "{action}"}}' if is_gate else "demo-model 输入 5.0 USD/1M tokens。")

    monkeypatch.setattr(assistant.httpx, "post", fake_post)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    client.app.state.store.set_document("settings", {"assistant_daily_limit": 1})
    return client


def test_ask_gate_refuses_without_consuming_quota(workspace: Path, monkeypatch):
    captured: dict = {}
    client = _gated_client(workspace, monkeypatch, ["refuse", "data"], captured)
    res = client.post("/api/assistant/ask", json={"question": "帮我写个快排"})
    assert res.status_code == 200
    assert "只能回答" in res.json()["answer"]
    assert len(captured["calls"]) == 1  # 只有分类调用，没有正式回答
    # 拒答不占每日次数：再问数据问题仍可正常回答
    res2 = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res2.status_code == 200 and "demo-model" in res2.json()["answer"]


def test_ask_gate_general_skips_data_json(workspace: Path, monkeypatch):
    captured: dict = {}
    client = _gated_client(workspace, monkeypatch, ["general"], captured)
    res = client.post("/api/assistant/ask", json={"question": "上下文长度是什么意思？"})
    assert res.status_code == 200
    answer_call = captured["calls"][-1]
    assert "平台当前数据 JSON" not in answer_call  # general 路径不带数据
    # general 计入每日次数：已用 1 次，再问即超限
    limited = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert limited.status_code == 429


def test_ask_forwards_recent_history(workspace: Path, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> _FakeResponse:
        captured["body"] = json
        return _FakeResponse()

    monkeypatch.setattr(assistant.httpx, "post", fake_post)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    res = client.post("/api/assistant/ask", json={
        "question": "我刚才问了什么？",
        "history": [
            {"role": "user", "content": "demo 站现在什么价？"},
            {"role": "assistant", "content": "demo-model 输入 5.0。"},
            {"role": "system", "content": "应被忽略"},
            {"role": "user", "content": "   "},
        ],
    })
    assert res.status_code == 200
    prompt = captured["body"]["messages"][-1]["content"]
    assert "demo 站现在什么价？" in prompt and "最近对话" in prompt
    assert "应被忽略" not in prompt


def test_ask_stream_failure_does_not_consume_quota(workspace: Path, monkeypatch):
    def failing_stream(*args: Any, **kwargs: Any):
        raise assistant.httpx.ConnectError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(assistant, "ai_stream", failing_stream)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    client.app.state.store.set_document("settings", {"assistant_daily_limit": 1})
    res = client.post("/api/assistant/ask/stream", json={"question": "demo 什么价？"})
    assert res.status_code == 200
    assert '"error"' in res.text
    # 失败不扣次数：下一次提问仍可通过配额校验并正常回答
    monkeypatch.setattr(assistant, "ai_stream", lambda *args, **kwargs: iter(["demo-model 现价 5.0。"]))
    res2 = client.post("/api/assistant/ask/stream", json={"question": "demo 什么价？"})
    frames = [line[6:] for line in res2.text.splitlines() if line.startswith("data: ")]
    import json as _json
    assert any(_json.loads(frame).get("done") for frame in frames)
