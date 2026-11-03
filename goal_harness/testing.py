"""goal_harness.testing —— 测试工具（公开 API，业务代码不应 import）。

对齐业界惯例：
  - `ScriptedModel`：脚本回放（OpenAI Agents SDK `agents.testing.ScriptedModel`）
  - `ScriptedModel.from_function`：按消息动态决策（pydantic-ai `FunctionModel`）
  - `block_real_requests()`：全局熔断真实模型请求（pydantic-ai
    `ALLOW_MODEL_REQUESTS = False`）
  - `normalize_transcript()`：轨迹规整，供快照断言（syrupy / inline-snapshot）

用法：
    from goal_harness.testing import ScriptedModel, text, tool_call
    model = ScriptedModel([tool_call("get_price", {"symbol": "A"}), text("完成")])
    agent = AnchoredAgent(spec, evidence, model)
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from .agent import (
    ModelClient,
    ModelReply,
    RealModelRequestBlocked,
    ToolCall,
    set_real_requests_blocked,
)

__all__ = [
    "RealModelRequestBlocked",
    "ScriptedModel",
    "block_real_requests",
    "normalize_transcript",
    "text",
    "tool_call",
]


def text(content: str) -> ModelReply:
    """构造一条纯文本回复。"""
    return ModelReply(content=content)


def tool_call(name: str, arguments: dict[str, Any],
              call_id: str | None = None) -> ModelReply:
    """构造一条单工具调用回复（call_id 省略时自动编号，便于断言）。"""
    return ModelReply(content=None,
                      tool_calls=[ToolCall(call_id or f"call_{name}", name, arguments)])


class ScriptedModel(ModelClient):
    """按脚本顺序回放的模型（CI 用，零网络、零 key）。"""

    def __init__(self, script: list[ModelReply],
                 on_exhausted: str = "raise") -> None:
        self._script = list(script)
        self._on_exhausted = on_exhausted
        self.calls: list[list[dict]] = []          # 每次调用传入的消息快照

    def chat(self, messages: list[dict], tools_schema: list[dict]) -> ModelReply:
        self.calls.append([dict(m) for m in messages])
        if not self._script:
            if self._on_exhausted == "raise":
                raise AssertionError("脚本耗尽：模型被调用的次数超出预期")
            return ModelReply(content="（脚本已耗尽，按约定返回空回复）")
        return self._script.pop(0)

    @classmethod
    def from_function(cls, fn: Callable[[list[dict]], ModelReply]) -> _FunctionModel:
        """按当前消息动态决定回复（pydantic-ai FunctionModel 式），用于测分支。"""
        return _FunctionModel(fn)

    @property
    def remaining(self) -> int:
        return len(self._script)


class _FunctionModel(ModelClient):
    def __init__(self, fn: Callable[[list[dict]], ModelReply]) -> None:
        self._fn = fn
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict], tools_schema: list[dict]) -> ModelReply:
        self.calls.append([dict(m) for m in messages])
        return self._fn(messages)


@contextmanager
def block_real_requests() -> Iterator[None]:
    """熔断真实模型请求：误用 OpenAICompatClient 会抛 RealModelRequestBlocked。"""
    set_real_requests_blocked(True)
    try:
        yield
    finally:
        set_real_requests_blocked(False)


_VOLATILE_KEYS = ("ts", "tool_call_id", "id")


def normalize_transcript(messages: list[dict]) -> list[dict]:
    """把轨迹里随时间变化的部分替换为占位符，供快照断言。"""
    out: list[dict] = []
    for m in messages:
        clean = {k: v for k, v in m.items() if k not in _VOLATILE_KEYS}
        if clean.get("tool_calls"):
            clean["tool_calls"] = [
                {**tc, "id": "<id>"} for tc in clean["tool_calls"]]
        if clean.get("role") == "system":
            clean["content"] = "<system prompt: spec 已注入>"   # 内容随 policy 变，快照只关心存在
        out.append(clean)
    return out
