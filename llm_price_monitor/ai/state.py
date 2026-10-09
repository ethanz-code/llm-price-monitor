"""AI 请求日志钩子与模型 max_tokens 上限学习（跨会话进程内状态）。

钩子由应用启动时注入（llm_price_monitor.ai.ai_log_hook = …），测试也会整体替换
这些模块属性，因此运行时一律经包命名空间（_ai.xxx）读写，不绑定导入时的对象。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import llm_price_monitor.ai as _ai

# 进程内缓存各模型学到的 max_tokens 上限：ai_request 构造请求时直接按上限降额，不再白发一次 400
_MODEL_MAX_TOKENS_LIMIT: dict[str, int] = {}

# AI 请求日志钩子：由应用启动时注入 store.add_ai_log，ai.py 不反向依赖存储层
AiLogHook = Any
ai_log_hook: AiLogHook | None = None

# 模型 max_tokens 上限的持久化钩子：上限只能从 400 报错里学出来（无查询接口），学习结果
# 落库后重启不丢，每个模型一生最多白发一次降额请求。loader 返回 {model: limit}，saver 增量写一条。
model_limits_loader: Callable[[], dict[str, int]] | None = None
model_limits_saver: Callable[[str, int], None] | None = None

# 单批价格抽取 JSON 的最低输出预算（同 config.ai_from_raw 的最低校验值）：已学上限低于它的
# 模型连一批都装不下，发起必然截断成坏 JSON，按"规格过小"从候选序里剔除。
MIN_USABLE_MAX_TOKENS = 4000


def log_ai_request(**fields: Any) -> None:
    if _ai.ai_log_hook is None:
        return
    try:
        _ai.ai_log_hook(**fields)
    except Exception:
        pass  # 日志失败绝不影响主流程


def learn_model_limit(model: str, limit: int) -> None:
    """记录从 400 报错里学到的模型 max_tokens 上限：进内存缓存，落库钩子存在时同步持久化。"""
    if _ai._MODEL_MAX_TOKENS_LIMIT.get(model) == limit:
        return
    _ai._MODEL_MAX_TOKENS_LIMIT[model] = limit
    saver = _ai.model_limits_saver
    if saver is None:
        return
    try:
        saver(model, limit)
    except Exception:
        pass  # 落库失败只损失重启后的预载，不影响本轮


def load_model_limits() -> None:
    """启动预载：把库里学过的上限并入内存缓存，重启后不再白发降额 400。"""
    loader = _ai.model_limits_loader
    if loader is None:
        return
    try:
        learned = loader()
    except Exception:
        return
    if isinstance(learned, dict):
        _ai._MODEL_MAX_TOKENS_LIMIT.update({str(k): int(v) for k, v in learned.items() if isinstance(v, int) and v > 0})
