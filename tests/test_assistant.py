"""智能分析助手接口：入口可见性、未配置拒答与带数据上下文的问答。"""
import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from llm_price_monitor.webapi.routes import assistant
from llm_price_monitor.webapi.app import create_app
from tests.test_webapi import _config, workspace  # noqa: F401  workspace 为共享 fixture


def _enable_ai(client: TestClient) -> None:
    # 种子模式 skip_existing 不会覆盖已有的 ai 配置，这里直接写库
    client.app.state.store.set_document(
        "ai", {"enabled": True, "base_url": "https://ai.test/v1", "models": ["test-model"], "api_key": "sk-test"}
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

    monkeypatch.setattr(assistant, "ai_stream_fallback", fake_stream)
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
        # 记录最后一条消息（用户消息）：门控判定看系统提示词，数据 JSON 与当前时间都在用户消息里
        captured.setdefault("calls", []).append(json["messages"][-1]["content"])
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
    assert res.json()["answer"] == assistant._REFUSAL
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
    assert "当前时间：" in captured["calls"][-1]  # 带当前时间，才能回答“现在几点”
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

    monkeypatch.setattr(assistant, "ai_stream_fallback", failing_stream)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    client.app.state.store.set_document("settings", {"assistant_daily_limit": 1})
    res = client.post("/api/assistant/ask/stream", json={"question": "demo 什么价？"})
    assert res.status_code == 200
    assert '"error"' in res.text
    # 失败不扣次数：下一次提问仍可通过配额校验并正常回答
    monkeypatch.setattr(assistant, "ai_stream_fallback", lambda *args, **kwargs: iter(["demo-model 现价 5.0。"]))
    res2 = client.post("/api/assistant/ask/stream", json={"question": "demo 什么价？"})
    frames = [line[6:] for line in res2.text.splitlines() if line.startswith("data: ")]
    import json as _json
    assert any(_json.loads(frame).get("done") for frame in frames)


def test_ask_stream_unexpected_exception_becomes_error_frame(workspace: Path, monkeypatch):
    """SSE 边界：任何异常都要转成错误帧送达前端，抛出去会直接掐断连接。"""
    def broken_stream(*args: Any, **kwargs: Any):
        raise RuntimeError("模拟未预期异常")
        yield  # pragma: no cover

    monkeypatch.setattr(assistant, "ai_stream_fallback", broken_stream)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    res = client.post("/api/assistant/ask/stream", json={"question": "demo 什么价？"})
    assert res.status_code == 200
    assert "模拟未预期异常" in res.text


def test_quota_counted_in_sqlite_and_enforced(workspace: Path, monkeypatch):
    """配额计数落 SQLite：提问成功后 assistant_usage 表 +1，重启/换文档都不影响限额判断。"""
    captured: dict = {}
    client = _gated_client(workspace, monkeypatch, ["general"], captured)
    assert client.post("/api/assistant/ask", json={"question": "上下文长度是什么？"}).status_code == 200
    with sqlite3.connect(workspace / "var" / "monitor.db") as conn:
        rows = conn.execute("SELECT ip, day, count FROM assistant_usage").fetchall()
    assert len(rows) == 1 and rows[0][2] == 1
    assert client.app.state.store.get_document("assistant_usage") is None  # 不再写文档
    # 表里计数达到限额后继续提问即 429
    limited = client.post("/api/assistant/ask", json={"question": "再问一个"})
    assert limited.status_code == 429


def test_ask_falls_back_to_next_model_on_model_error(workspace: Path, monkeypatch):
    import httpx

    calls: list[str] = []

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        calls.append(json["model"])
        if json["model"] == "bad-model":
            request = httpx.Request("POST", url)
            return httpx.Response(400, json={"error": {"message": "模型不存在"}}, request=request)
        return _FakeResponse()

    monkeypatch.setattr(assistant.httpx, "post", fake_post)
    # 生产逻辑会随机打乱模型池起点；这里固定不洗牌，保证 bad-model 先被尝试、fallback 必然触发
    monkeypatch.setattr("llm_price_monitor.ai.random.shuffle", lambda _: None)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    store = client.app.state.store
    doc = dict(store.get_document("ai") or {})
    doc["models"] = ["bad-model", "good-model"]
    doc.pop("model", None)
    store.set_document("ai", doc)
    res = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res.status_code == 200
    assert "demo-model" in res.json()["answer"]
    # 坏模型报 400 后自动换下一个模型，两个都被尝试过
    assert set(calls) == {"bad-model", "good-model"}


def test_ask_retries_same_model_when_thinking_restricted(workspace: Path, monkeypatch):
    """思考不可关的模型（如 glm-5.3）拒收 enable_thinking=false：同模型翻参重试成功，不换模型。"""
    import httpx

    calls: list[dict] = []

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        calls.append({"model": json["model"], "enable_thinking": json.get("enable_thinking")})
        if json.get("enable_thinking") is False:
            request = httpx.Request("POST", url)
            return httpx.Response(400, json={"error": {"message": "The value of the enable_thinking parameter is restricted to True."}}, request=request)
        return _FakeResponse()

    monkeypatch.setattr(assistant.httpx, "post", fake_post)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    store = client.app.state.store
    doc = dict(store.get_document("ai") or {})
    doc["models"] = ["glm-5.3"]
    doc.pop("model", None)
    store.set_document("ai", doc)
    res = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res.status_code == 200
    assert "demo-model" in res.json()["answer"]
    # 分类门控与正式回答各触发一次"同模型翻参重试"：第一次 false 被 400 拒，翻 true 重发成功
    assert [call["model"] for call in calls] == ["glm-5.3"] * 4
    assert [call["enable_thinking"] for call in calls] == [False, True, False, True]
    # 翻参重试在日志里记为独立的 param_retry 状态，不与换模型重试（fallback）混淆
    assert store.read_ai_logs(status="param_retry")[1] == 2
    assert store.read_ai_logs(status="fallback")[1] == 0


def test_stream_fallback_retries_same_model_when_thinking_restricted(monkeypatch):
    """流式链路同样翻参重试：enable_thinking=false 被 400 拒后用 true 原模型重发并正常产出分片。"""
    import json

    import httpx

    import llm_price_monitor.ai as ai_module
    from llm_price_monitor.ai import ai_stream_fallback
    from llm_price_monitor.config import AIConfig

    calls: list[bool | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body.get("enable_thinking"))
        if body.get("enable_thinking") is False:
            return httpx.Response(400, json={"error": {"message": "The value of the enable_thinking parameter is restricted to True."}})
        return httpx.Response(200, text='data: {"choices": [{"delta": {"content": "回答"}}]}\n\ndata: [DONE]\n\n')

    real_client = httpx.Client
    monkeypatch.setattr(ai_module.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    config = AIConfig(base_url="https://ai.test/v1", models=("test-model",))
    assert list(ai_stream_fallback(config, "系统提示", "用户问题", scene="流式测试")) == ["回答"]
    assert calls == [False, True]
