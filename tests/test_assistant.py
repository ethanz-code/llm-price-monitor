"""智能分析助手接口：入口可见性、未配置拒答、前置门控与工具循环问答。"""
import json
import sqlite3
import time
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


def _finish(text: str = "") -> dict[str, Any]:
    return {"type": "finish", "finish_reason": "stop", "tool_calls": [], "usage": None}


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


def test_ask_runs_tool_loop_and_returns_answer(workspace: Path, monkeypatch):
    """数据问答走工具循环：首轮模型要数据→真实执行工具→末轮不带工具强制作答。"""
    captured: dict[str, Any] = {}
    rounds: list[list[dict[str, Any]] | None] = []

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        # 分类门控走真实 AI 请求（打桩 httpx.Client 接住）：这里放行判定为 data
        return _GateResponse('{"action": "data"}')

    def fake_events(config, messages, tools, *, scene, **kwargs):
        rounds.append(tools)
        captured["last_messages"] = messages
        if len(rounds) == 1:
            yield {"type": "finish", "finish_reason": "tool_calls", "usage": None, "tool_calls": [
                {"id": "c1", "type": "function", "name": "get_prices", "arguments": '{"site": "demo"}'}
            ]}
            return
        yield {"type": "delta", "text": "demo"}
        yield {"type": "delta", "text": "-model 输入 5.0 USD/1M tokens。"}
        yield _finish()

    monkeypatch.setattr(assistant.httpx, "Client", lambda *args, **kwargs: _GateHttpClient(fake_post))
    monkeypatch.setattr(assistant, "ai_stream_messages_fallback", fake_events)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    res = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res.status_code == 200
    assert res.json()["answer"] == "demo-model 输入 5.0 USD/1M tokens。"
    # 前几轮都带工具清单（最多 3 轮取数）
    assert rounds[0] is assistant.TOOL_SPECS and rounds[1] is assistant.TOOL_SPECS
    # 工具结果作为 tool 消息回填：demo-model 的价格由执行器查库得出，不来自模型
    tool_messages = [m for m in captured["last_messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 1 and "demo-model" in tool_messages[0]["content"] and "5.0" in tool_messages[0]["content"]


def test_ask_forces_answer_after_max_tool_rounds(workspace: Path, monkeypatch):
    """模型连续 3 轮都在要数据：第 4 次请求不再带工具，模型必须用手头数据作答。"""
    rounds: list[list[dict[str, Any]] | None] = []

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        return _GateResponse('{"action": "data"}')

    def fake_events(config, messages, tools, *, scene, **kwargs):
        rounds.append(tools)
        if tools is not None:
            yield {"type": "finish", "finish_reason": "tool_calls", "usage": None, "tool_calls": [
                {"id": f"c{len(rounds)}", "type": "function", "name": "get_prices", "arguments": "{}"}
            ]}
            return
        yield {"type": "delta", "text": "按查到的数据回答。"}
        yield _finish()

    monkeypatch.setattr(assistant.httpx, "Client", lambda *args, **kwargs: _GateHttpClient(fake_post))
    monkeypatch.setattr(assistant, "ai_stream_messages_fallback", fake_events)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    res = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res.status_code == 200
    assert res.json()["answer"] == "按查到的数据回答。"
    assert len(rounds) == assistant._MAX_TOOL_ROUNDS + 1  # 3 轮取数 + 1 轮强制回答
    assert rounds[:assistant._MAX_TOOL_ROUNDS] == [assistant.TOOL_SPECS] * assistant._MAX_TOOL_ROUNDS
    assert rounds[-1] is None


def test_ask_rejects_blank_question(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    assert client.post("/api/assistant/ask", json={"question": "   "}).status_code == 400


def test_ask_never_leaks_credentials(workspace: Path, monkeypatch):
    """站点凭据、token_refresh、管理页路径都不出现在提示词与工具结果里。"""
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        return _GateResponse('{"action": "data"}')

    def fake_events(config, messages, tools, *, scene, **kwargs):
        captured["prompt"] = json.dumps(messages, ensure_ascii=False)
        yield {"type": "delta", "text": "好的。"}
        yield _finish()

    monkeypatch.setattr(assistant.httpx, "Client", lambda *args, **kwargs: _GateHttpClient(fake_post))
    monkeypatch.setattr(assistant, "ai_stream_messages_fallback", fake_events)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    # 站点配置带凭据；访问记录带管理页路径（助手没有任何工具能查到访问明细）
    client.app.state.store.upsert_site("demo", {
        "id": "demo", "adapter": "standard", "models": ["demo-model"],
        "network": {"url": "https://demo.test/pricing"},
        "cookie": "session=should-not-leak",
        "request_headers": {"Authorization": "Bearer ${SECRET_TOKEN}"},
        "token_refresh": {"url": "https://demo.test/refresh", "refresh_token": "should-not-leak"},
    })
    client.app.state.store.add_visit(path="/admin", ip="1.2.3.4", user_agent="test", browser="test", os="test", device="desktop")
    res = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res.status_code == 200
    assert "https://demo.test/pricing" not in captured["prompt"]  # 提示词只带问题与历史，站点数据一律走工具
    for secret in ("should-not-leak", "SECRET_TOKEN", "/admin"):
        assert secret not in captured["prompt"]
    # 工具结果允许公开的站点地址，但不含任何凭据
    store = client.app.state.store
    tool_results = assistant._execute_tool(store, "list_sites", "{}") + assistant._execute_tool(store, "get_prices", "{}")
    assert "https://demo.test/pricing" in tool_results
    for secret in ("should-not-leak", "SECRET_TOKEN"):
        assert secret not in tool_results


def test_ask_stream_requires_ai_configured(workspace: Path):
    client = TestClient(create_app(_config(workspace)))
    assert client.post("/api/assistant/ask/stream", json={"question": "有哪些站点？"}).status_code == 400


def test_ask_stream_emits_delta_frames(workspace: Path, monkeypatch):
    def fake_events(*_args: object, **_kwargs: object):
        yield {"type": "delta", "text": "demo"}
        yield {"type": "delta", "text": "-model 现价 5.0。"}
        yield _finish()

    monkeypatch.setattr(assistant, "ai_stream_messages_fallback", fake_events)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    res = client.post("/api/assistant/ask/stream", json={"question": "demo 什么价？"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    frames = [line[6:] for line in res.text.splitlines() if line.startswith("data: ")]
    deltas = "".join(json.loads(frame).get("delta", "") for frame in frames)
    assert deltas == "demo-model 现价 5.0。"
    assert json.loads(frames[-1])["done"] is True


def test_ask_stream_emits_status_frame_during_tool_round(workspace: Path, monkeypatch):
    """取数期间下发 status 帧，前端才知道是在查库不是卡死。"""
    calls = []

    def fake_events(*_args: object, **_kwargs: object):
        calls.append(1)
        if len(calls) == 1:
            yield {"type": "finish", "finish_reason": "tool_calls", "usage": None, "tool_calls": [
                {"id": "c1", "type": "function", "name": "get_site_status", "arguments": "{}"}
            ]}
            return
        yield {"type": "delta", "text": "一切正常。"}
        yield _finish()

    monkeypatch.setattr(assistant, "ai_stream_messages_fallback", fake_events)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    res = client.post("/api/assistant/ask/stream", json={"question": "demo 站状态怎么样？"})
    frames = [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]
    statuses = [frame["status"] for frame in frames if "status" in frame]
    assert statuses == [assistant.TOOL_STATUS_LABELS["get_site_status"]]
    assert any(frame.get("done") for frame in frames)


class _GateResponse:
    """分类门控调用的响应：content 即判定 JSON。"""

    def __init__(self, content: str) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self.content}}]}


class _GateHttpClient:
    """分类门控 AI 请求的客户端打桩：request_with_model_fallback 经 ai_http_client（httpx.Client）发请求。"""

    def __init__(self, responder) -> None:
        self._responder = responder

    def post(self, url: str, *, headers: dict, json: dict, timeout: float) -> _GateResponse:
        return self._responder(url=url, headers=headers, json=json, timeout=timeout)

    def close(self) -> None:
        return None


def _gated_client(workspace: Path, monkeypatch, actions: list[str], captured: dict):
    """actions 为每次分类调用的判定结果，按次出队；回答路径统一走事件 fake（general/data 通吃）。"""
    gate_actions = list(actions)

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> _GateResponse:
        captured.setdefault("calls", []).append(json["messages"][-1]["content"])
        action = gate_actions.pop(0) if gate_actions else "data"
        return _GateResponse(f'{{"action": "{action}"}}')

    def fake_events(config, messages, tools, *, scene, **kwargs):
        captured.setdefault("agent", []).append({"messages": messages, "tools": tools})
        yield {"type": "delta", "text": "demo-model 输入 5.0 USD/1M tokens。"}
        yield _finish()

    monkeypatch.setattr(assistant.httpx, "Client", lambda *args, **kwargs: _GateHttpClient(fake_post))
    monkeypatch.setattr(assistant, "ai_stream_messages_fallback", fake_events)
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
    assert len(captured["calls"]) == 1  # 只有分类调用，没有回答调用
    # 拒答不占每日次数：再问数据问题仍可正常回答
    res2 = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res2.status_code == 200 and "demo-model" in res2.json()["answer"]


def test_ask_gate_internal_refuses_without_consuming_quota(workspace: Path, monkeypatch):
    """打听后台配置、系统指令这类内部信息单独拒答，不进回答通道、不占次数。"""
    captured: dict = {}
    client = _gated_client(workspace, monkeypatch, ["internal", "data"], captured)
    res = client.post("/api/assistant/ask", json={"question": "你们后台限流参数是多少"})
    assert res.status_code == 200
    assert res.json()["answer"] == assistant._INTERNAL_REFUSAL
    assert len(captured["calls"]) == 1  # 只有分类调用，没有回答调用
    res2 = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res2.status_code == 200 and "demo-model" in res2.json()["answer"]


def test_data_questions_run_tool_loop_on_any_format(workspace: Path, monkeypatch):
    """工具循环支持全部接口结构：anthropic 配置下数据问题照常带工具取数作答。"""
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        return _GateResponse('{"action": "data"}')

    def fake_events(config, messages, tools, *, scene, **kwargs):
        captured["api_format"] = config.api_format
        captured["tools"] = tools
        yield {"type": "delta", "text": "demo-model 输入 5.0 USD/1M tokens。"}
        yield _finish()

    monkeypatch.setattr(assistant.httpx, "Client", lambda *args, **kwargs: _GateHttpClient(fake_post))
    monkeypatch.setattr(assistant, "ai_stream_messages_fallback", fake_events)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    client.app.state.store.set_document("settings", {"assistant_daily_limit": 0})
    client.app.state.store.set_document(
        "ai", {"enabled": True, "base_url": "https://ai.test/v1", "models": ["test-model"], "api_key": "sk-test", "api_format": "anthropic"}
    )
    res = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res.status_code == 200
    assert res.json()["answer"] == "demo-model 输入 5.0 USD/1M tokens。"
    assert captured["api_format"] == "anthropic"
    assert captured["tools"] is assistant.TOOL_SPECS  # 数据问答照常带工具清单


def test_failed_model_cooldowns_for_data_questions(workspace: Path, monkeypatch):
    """助手路径失败的模型进 24h 冷却名单（存库），后续数据问答不再选；全冷却时回退全池。"""
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        return _GateResponse('{"action": "data"}')

    def fake_events(config, messages, tools, *, scene, on_model_failure=None, timeout=None):
        captured.setdefault("models", []).append(tuple(config.models))
        captured["timeout"] = timeout
        if on_model_failure is not None and config.models and config.models[0] == "bad-model":
            on_model_failure("bad-model", "不支持工具")
        yield {"type": "delta", "text": "ok"}
        yield _finish()

    monkeypatch.setattr(assistant.httpx, "Client", lambda *args, **kwargs: _GateHttpClient(fake_post))
    monkeypatch.setattr(assistant, "ai_stream_messages_fallback", fake_events)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    client.app.state.store.set_document("settings", {"assistant_daily_limit": 0})
    store = client.app.state.store
    store.set_document(
        "ai", {"enabled": True, "base_url": "https://ai.test/v1", "models": ["bad-model", "good-model"], "api_key": "sk-test"}
    )
    res = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res.status_code == 200
    assert captured["models"][-1] == ("bad-model", "good-model")  # 首题全池
    assert captured["timeout"] == assistant._ASSISTANT_TIMEOUT  # 助手死线生效
    doc = store.get_document(assistant._COOLDOWN_DOC)
    assert doc["bad-model"] > time.time()  # 失败模型带过期时间入库
    res2 = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res2.status_code == 200
    assert captured["models"][-1] == ("good-model",)  # 冷却模型已剔除
    doc["good-model"] = doc["bad-model"]
    store.set_document(assistant._COOLDOWN_DOC, doc)
    res3 = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res3.status_code == 200
    assert captured["models"][-1] == ("bad-model", "good-model")  # 全冷却回退全池，不拒服


def test_ask_gate_general_skips_tools_and_data(workspace: Path, monkeypatch):
    captured: dict = {}
    client = _gated_client(workspace, monkeypatch, ["general"], captured)
    res = client.post("/api/assistant/ask", json={"question": "上下文长度是什么意思？"})
    assert res.status_code == 200
    agent_call = captured["agent"][-1]
    assert agent_call["tools"] is None  # general 走轻量通道，不带工具清单
    assert "不需要平台数据" in agent_call["messages"][0]["content"]
    assert "当前时间：" in agent_call["messages"][-1]["content"]  # 带当前时间，才能回答“现在几点”
    # general 计入每日次数：已用 1 次，再问即超限
    limited = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert limited.status_code == 429


def test_ask_forwards_recent_history(workspace: Path, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> _GateResponse:
        return _GateResponse('{"action": "data"}')

    def fake_events(config, messages, tools, *, scene, **kwargs):
        captured["user_message"] = messages[-1]["content"]
        yield {"type": "delta", "text": "你刚才问过价格。"}
        yield _finish()

    monkeypatch.setattr(assistant.httpx, "Client", lambda *args, **kwargs: _GateHttpClient(fake_post))
    monkeypatch.setattr(assistant, "ai_stream_messages_fallback", fake_events)
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
    prompt = captured["user_message"]
    assert "demo 站现在什么价？" in prompt and "最近对话" in prompt
    assert "应被忽略" not in prompt


def test_ask_stream_failure_does_not_consume_quota(workspace: Path, monkeypatch):
    def failing_events(*args: Any, **kwargs: Any):
        raise assistant.httpx.ConnectError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(assistant, "ai_stream_messages_fallback", failing_events)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    client.app.state.store.set_document("settings", {"assistant_daily_limit": 1})
    res = client.post("/api/assistant/ask/stream", json={"question": "demo 什么价？"})
    assert res.status_code == 200
    assert '"error"' in res.text
    # 失败不扣次数：下一次提问仍可通过配额校验并正常回答
    monkeypatch.setattr(
        assistant, "ai_stream_messages_fallback",
        lambda *args, **kwargs: iter([{"type": "delta", "text": "demo-model 现价 5.0。"}, _finish()]),
    )
    res2 = client.post("/api/assistant/ask/stream", json={"question": "demo 什么价？"})
    frames = [line[6:] for line in res2.text.splitlines() if line.startswith("data: ")]
    assert any(json.loads(frame).get("done") for frame in frames)


def test_ask_stream_unexpected_exception_becomes_error_frame(workspace: Path, monkeypatch):
    """SSE 边界：任何异常都要转成错误帧送达前端，抛出去会直接掐断连接。"""
    def broken_events(*args: Any, **kwargs: Any):
        raise RuntimeError("模拟未预期异常")
        yield  # pragma: no cover

    monkeypatch.setattr(assistant, "ai_stream_messages_fallback", broken_events)
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


def test_ask_throttles_eleventh_request_within_an_hour(workspace: Path, monkeypatch):
    """每 IP 每小时最多 10 问：第 11 次直接 429，也不再发起 LLM 调用。"""
    captured: dict = {}
    client = _gated_client(workspace, monkeypatch, ["general"], captured)
    client.app.state.store.set_document("settings", {"assistant_daily_limit": 100})  # 解开日限额，只测每小时限速
    for _ in range(10):
        assert client.post("/api/assistant/ask", json={"question": "上下文长度是什么？"}).status_code == 200
    calls_after_ten = len(captured["calls"])
    limited = client.post("/api/assistant/ask", json={"question": "再来一个"})
    assert limited.status_code == 429
    assert "用完" in limited.json()["detail"]
    assert len(captured["calls"]) == calls_after_ten


def test_ask_hourly_limit_is_configurable(workspace: Path, monkeypatch):
    """每小时限速读 settings.assistant_hourly_limit：自定义值生效，0 表示关闭。"""
    captured: dict = {}
    client = _gated_client(workspace, monkeypatch, ["general"], captured)
    client.app.state.store.set_document("settings", {"assistant_daily_limit": 100, "assistant_hourly_limit": 2})
    assert client.post("/api/assistant/ask", json={"question": "问题一"}).status_code == 200
    assert client.post("/api/assistant/ask", json={"question": "问题二"}).status_code == 200
    limited = client.post("/api/assistant/ask", json={"question": "问题三"})
    assert limited.status_code == 429
    # 设为 0 = 关闭每小时限速，立即可继续提问（日限额仍生效）
    client.app.state.store.set_document("settings", {"assistant_daily_limit": 100, "assistant_hourly_limit": 0})
    assert client.post("/api/assistant/ask", json={"question": "问题四"}).status_code == 200


def test_tool_executors_filter_sort_and_bound(workspace: Path):
    """工具执行器：过滤/排序/行数上限在纯库查询层就正确。"""
    store = TestClient(create_app(_config(workspace))).app.state.store
    rows = dict(store.latest_all())
    for index, price in enumerate((2.0, 5.0, 8.0)):
        key = f"demo:model-{index}:default"
        rows[key] = _latest_price_row("demo", f"model-{index}", price)
    store.replace_latest(rows)
    result = json.loads(assistant._execute_tool(store, "get_prices", '{"order": "input_asc", "limit": 2}'))
    assert result["total"] == 4  # demo-model 基础价 + 3 个新模型
    assert [row["input_price"] for row in result["prices"]] == [2.0, 5.0]  # 升序 + 截到 2 条
    filtered = json.loads(assistant._execute_tool(store, "get_prices", '{"model": "model-1"}'))
    assert [row["model"] for row in filtered["prices"]] == ["model-1"]
    unknown = json.loads(assistant._execute_tool(store, "nope", "{}"))
    assert "未知工具" in unknown["error"]
    bad_args = json.loads(assistant._execute_tool(store, "get_prices", "不是json"))
    assert "参数不合法" in bad_args["error"]
    history = json.loads(assistant._execute_tool(store, "get_price_history", "{}"))
    assert "error" in history  # 缺 model 参数给指引而不是报错


def test_tool_get_price_events_reads_beyond_recent_200(workspace: Path):
    """事件读取窗口（2000 + since 下推）：按模型过滤的查询不被最近的 200 条其他模型事件挤掉。"""
    store = TestClient(create_app(_config(workspace))).app.state.store
    now = time.time()
    # rare-model 先写入（id 最小、最旧）：按"最新 200 条"读取会被完全挤掉，放大的窗口才能命中
    events = [{"site_id": "demo", "model": "rare-model", "kind": "changed", "detected_at": now - 300}]
    events += [
        {"site_id": "demo", "model": f"model-{index}", "kind": "changed", "detected_at": now - index}
        for index in range(250)
    ]
    store.append_events(events)
    result = json.loads(assistant._execute_tool(store, "get_price_events", '{"model": "rare-model", "days": 7}'))
    assert result["total"] == 1
    assert result["events"][0]["model"] == "rare-model"


def test_tool_get_official_prices_filters_and_hints(workspace: Path):
    """官方价工具：模型/厂商子串过滤、目录未生成报错、查不到给中性提示。"""
    store = TestClient(create_app(_config(workspace))).app.state.store
    # 开发环境 var/catalog.json 会被启动装载进 store：先显式清空再验"目录未生成"分支
    store.set_document("catalog", {})
    missing = json.loads(assistant._execute_tool(store, "get_official_prices", "{}"))
    assert "官方价目录还没有生成" in missing["error"]
    store.set_document("catalog", {
        "generated_at": 1000.0,
        "models": {
            "glm-5": {
                "model": "glm-5", "name": "GLM-5", "vendor": "Zhipu AI", "region": "cn",
                "list": {"input": 0.8, "output": 2.0}, "list_cny": {"input": 6.0, "output": 15.0},
                "list_tiers": [{"input": 1.6, "output": 4.0, "cache_read": 0.2, "tier": ">128K"}],
                "list_tiers_cny": [{"input": 12.0, "output": 30.0, "cache_read": 1.5, "tier": ">128K"}],
            },
            "gpt-5": {
                "model": "gpt-5", "vendor": "OpenAI", "region": "global",
                "list": {"input": 1.25, "output": 10.0}, "list_cny": {"input": 9.0, "output": 72.0},
                "cache": {"read": 0.125, "write": None},
            },
        },
    })
    result = json.loads(assistant._execute_tool(store, "get_official_prices", '{"model": "glm"}'))
    assert result["total"] == 1
    row = result["official_prices"][0]
    assert row["vendor"] == "Zhipu AI" and row["region"] == "cn"
    assert row["list_cny"]["input"] == 6.0  # 人民币精确标价
    assert row["list_tiers"][0]["tier"] == ">128K"  # 分档价随条目返回
    by_vendor = json.loads(assistant._execute_tool(store, "get_official_prices", '{"vendor": "openai"}'))
    assert [row["model"] for row in by_vendor["official_prices"]] == ["gpt-5"]
    assert "cache" in by_vendor["official_prices"][0]
    miss = json.loads(assistant._execute_tool(store, "get_official_prices", '{"model": "qwen"}'))
    assert miss["total"] == 0 and miss["official_prices"] == []
    assert "管理端未配置" in miss["hint"] or "管理端" in miss["hint"]


def _latest_price_row(site_id: str, model: str, input_price: float) -> dict[str, Any]:
    return {
        "site_id": site_id,
        "model": model,
        "input_price": input_price,
        "output_price": input_price * 5,
        "unit": "USD/1M tokens",
        "price_status": "confirmed",
        "requires_auth": False,
        "source_url": "https://demo.test/pricing",
        "captured_at": 1000.0,
        "fingerprint": "b" * 64,
        "metadata": {"group": "default"},
    }


def test_ask_falls_back_to_next_model_on_stream_error(workspace: Path, monkeypatch):
    """回答链路模型报 400 自动换下一个模型；整体失败时 ask 转 502。"""
    import httpx

    import llm_price_monitor.ai as ai_module

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "bad-model":
            return httpx.Response(400, json={"error": {"message": "模型不存在"}})
        return httpx.Response(
            200,
            text='data: {"choices": [{"delta": {"content": "demo-model 输入 5.0。"}}]}\n\ndata: [DONE]\n\n',
        )

    real_client = httpx.Client
    # 门控（非流式）与回答（流式）都经 ai_http_client → httpx.Client，统一被 MockTransport 接住
    monkeypatch.setattr(ai_module.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
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
    # 门控（bad-model 400 → good-model）+ 回答链路（bad-model 400 → good-model）
    assert calls == ["bad-model", "good-model", "bad-model", "good-model"]


def test_ask_retries_same_model_when_thinking_restricted(workspace: Path, monkeypatch):
    """思考不可关的模型（如 glm-5.3）拒收 enable_thinking=false：分类与回答各自翻参重试成功。"""
    import httpx

    import llm_price_monitor.ai as ai_module

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append({"model": body["model"], "enable_thinking": body.get("enable_thinking"), "stream": body.get("stream")})
        if body.get("enable_thinking") is False:
            return httpx.Response(400, json={"error": {"message": "The value of the enable_thinking parameter is restricted to True."}})
        if body.get("stream"):
            return httpx.Response(200, text='data: {"choices": [{"delta": {"content": "demo-model 输入 5.0。"}}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"action": "data"}'}}]})

    real_client = httpx.Client
    monkeypatch.setattr(ai_module.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(ai_module, "_MODEL_THINKING_REQUIRED", set())  # 学习态隔离：本测试自证翻参与学习
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
    # 分类门控第一次 false 被 400 拒、翻 true 重发成功，并学会该模型思考不可关；
    # 回答腿经 ai_request 直接按 true 构造，不再白发那次 400
    assert [call["model"] for call in calls] == ["glm-5.3"] * 3
    assert [call["enable_thinking"] for call in calls] == [False, True, True]
    # 翻参重试在日志里记为独立的 param_retry 状态，不与换模型重试（fallback）混淆
    assert store.read_ai_logs(status="param_retry")[1] == 1
    assert store.read_ai_logs(status="fallback")[1] == 0
    assert "glm-5.3" in ai_module._MODEL_THINKING_REQUIRED


def test_ask_general_empty_answer_fails_without_consuming_quota(workspace: Path, monkeypatch):
    """general 通道模型 200 但零文本（内容过滤/思考烧尽）：按 AI 失败返回 502，
    不产出空气泡、不扣当日配额——与 data 路径及「AI 失败不扣次数」口径一致。"""
    gate_actions = ["general", "data"]
    stream_calls: list[list[Any] | None] = []

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float) -> _GateResponse:
        return _GateResponse(f'{{"action": "{gate_actions.pop(0)}"}}')

    def fake_events(config, messages, tools, *, scene, **kwargs):
        stream_calls.append(tools)
        if len(stream_calls) == 1:  # general 通道首轮：模型 200 但一个 delta 都没有
            yield _finish()
            return
        yield {"type": "delta", "text": "demo-model 输入 5.0 USD/1M tokens。"}
        yield _finish()

    monkeypatch.setattr(assistant.httpx, "Client", lambda *args, **kwargs: _GateHttpClient(fake_post))
    monkeypatch.setattr(assistant, "ai_stream_messages_fallback", fake_events)
    client = TestClient(create_app(_config(workspace)))
    _enable_ai(client)
    client.app.state.store.set_document("settings", {"assistant_daily_limit": 1})

    res = client.post("/api/assistant/ask", json={"question": "你好呀"})
    assert res.status_code == 502
    assert "空回答" in res.json()["detail"]
    # 空回答不扣次数：当日额度还剩 1 次，正常数据提问仍能成功
    res2 = client.post("/api/assistant/ask", json={"question": "demo 站现在什么价？"})
    assert res2.status_code == 200 and "demo-model" in res2.json()["answer"]
