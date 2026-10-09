"""Artificial Analysis 模型榜单抓取：解析 /leaderboards/models 页面的内联表格。

AA 榜单页是服务端渲染的 Next.js 页面，269 行模型数据全部内联在唯一的 <table>
里（无 JSON 数据块、不依赖 JS），httpx 直抓即可解析。每行 9 列：模型名（带
/models/<slug> 链接）、上下文窗口、厂商、Intelligence Index、Cost per Task、
Median Tokens/s、Latency First Chunk、Total Response、Further Analysis。

行序即排名（按指数降序）；同一基础模型的推理档位变体（-xhigh / -high / -low
后缀）各占一行、slug 唯一。Cost per Task 是 AA 的复合任务成本口径，不是每百
万 token 价，榜单仅作展示、不与目录价格混用。解析对结构严格：缺列/多余行一
律报错，不兜底。
"""
from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from typing import Any

import httpx

from .normalize import model_key

AA_LEADERBOARD_URL = "https://artificialanalysis.ai/leaderboards/models"

# 数据行必须恰好 9 列（含两列不可见的操作列文案），列数变化说明页面结构变了
_EXPECTED_CELLS = 9

_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _number(text: str) -> float | None:
    """'58' → 58；'20 *'（脚注标记）→ 20；'$5.98' → 5.98；'1,565' → 1565；'--' → None。"""
    match = _NUM_RE.search(text)
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def _model_key(slug: str) -> str:
    """AA slug 与目录模型 id 的共同匹配键：model_key 再去掉点号。

    AA slug 用连字符表达版本点号（gpt-6-sol / gpt-6.5），目录 id 常带点号
    （gpt-6.5-sol），去掉点号后两者归一（gpt65sol）。
    """
    return model_key(slug).replace(".", "")


class _LeaderboardParser(HTMLParser):
    """收集榜单表格行：<tr> 内逐 <td> 缓存纯文本，记录首个 /models/ 链接的 slug。"""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str | None, list[str]]] = []
        self._in_row = False
        self._in_cell = False
        self._slug: str | None = None
        self._cells: list[str] = []
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._in_row = True
            self._slug = None
            self._cells = []
        elif not self._in_row:
            return
        elif tag == "td":
            self._in_cell = True
            self._buffer = []
        elif tag == "a" and self._slug is None:
            href = dict(attrs).get("href") or ""
            if href.startswith("/models/"):
                self._slug = href.removeprefix("/models/").strip("/")

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_cell:
            self._cells.append(re.sub(r"\s+", " ", "".join(self._buffer)).strip())
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if self._slug is not None:
                self.rows.append((self._slug, self._cells))
            self._in_row = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._buffer.append(data)


def _entry(rank: int, slug: str, cells: list[str]) -> dict[str, Any]:
    name, context, creator = cells[0], cells[1], cells[2]
    index, cost_per_task, speed, latency, total = (_number(c) for c in cells[3:8])
    return {
        "rank": rank,
        "slug": slug,
        "key": _model_key(slug),
        "name": name,
        "creator": creator or None,
        "context_window": context or None,
        "intelligence_index": index,
        "cost_per_task_usd": cost_per_task,
        "median_output_tokens_per_second": speed,
        "latency_first_chunk_seconds": latency,
        "total_response_seconds": total,
        "url": f"{AA_LEADERBOARD_URL.split('/leaderboards')[0]}/models/{slug}",
    }


def parse_leaderboard(html: str) -> dict[str, Any]:
    """榜单页 HTML → 排名文档；没有数据行或行结构异常都直接报错。"""
    parser = _LeaderboardParser()
    parser.feed(html)
    if not parser.rows:
        raise ValueError("Artificial Analysis 榜单页未解析到任何模型行（页面结构可能已变化）")
    models: list[dict[str, Any]] = []
    for rank, (slug, cells) in enumerate(parser.rows, start=1):
        if len(cells) != _EXPECTED_CELLS:
            raise ValueError(
                f"榜单行结构异常（{slug}：{len(cells)} 列，应为 {_EXPECTED_CELLS} 列），页面结构可能已变化"
            )
        models.append(_entry(rank, slug, cells))
    now = time.time()
    return {
        "generated_at": now,
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "artificialanalysis.ai",
        "source_url": AA_LEADERBOARD_URL,
        "models": models,
    }


def fetch_rankings(*, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """抓取 AA 榜单页并解析为排名文档；不落盘，持久化由调用方决定。"""
    with httpx.Client(follow_redirects=True, timeout=30, transport=transport) as client:
        response = client.get(AA_LEADERBOARD_URL)
        response.raise_for_status()
        return parse_leaderboard(response.text)


def rank_for_key(rankings: dict[str, Any] | None, model: str) -> dict[str, Any] | None:
    """目录模型 id（如 gpt-6.5-sol）→ 榜单条目；对不上返回 None，不做模糊硬凑。

    slug 与展示名都参与匹配：slug 覆盖变体行（-xhigh 等），展示名覆盖 AA 页
    slug 与常见 API id 写法不一致的条目；同键多条时取排名靠前的。
    """
    if not rankings or not model:
        return None
    key = _model_key(model)
    if not key:
        return None
    best: dict[str, Any] | None = None
    for entry in rankings.get("models") or []:
        if entry.get("key") == key or _model_key(str(entry.get("name") or "")) == key:
            if best is None or int(entry.get("rank") or 0) < int(best.get("rank") or 0):
                best = entry
    return best
