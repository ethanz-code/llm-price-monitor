"""JSON 文件读写：统一失败语义（读到坏文件按空对象处理，不抛异常）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_object(path: Path) -> dict[str, Any]:
    """读取 JSON 对象；文件不存在、损坏或顶层不是对象时返回空 dict。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: Any, *, indent: int = 2, sort_keys: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=sort_keys) + "\n", encoding="utf-8")
