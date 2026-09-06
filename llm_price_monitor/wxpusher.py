"""WxPusher 推送：只覆盖「appToken + UID 发文本消息」这一个最小场景。

API 文档 https://wxpusher.zjiecode.com/docs/：POST /api/send/message，
请求体 appToken/content/summary(≤100 字)/contentType(1=文本)/uids，业务码 1000 表示成功，限流约 2 QPS。
"""
from __future__ import annotations

import httpx

SEND_URL = "https://wxpusher.zjiecode.com/api/send/message"


def send_wxpusher(*, app_token: str, content: str, summary: str, uid: str | None, timeout: float = 10.0) -> None:
    payload: dict[str, object] = {
        "appToken": app_token,
        "content": content,
        "summary": summary[:100],
        "contentType": 1,
    }
    if uid:
        payload["uids"] = [uid]
    response = httpx.post(SEND_URL, json=payload, timeout=timeout)
    response.raise_for_status()
    result = response.json()
    if result.get("code") != 1000:
        raise RuntimeError(f"WxPusher 返回 code={result.get('code')}: {result.get('msg')}")
