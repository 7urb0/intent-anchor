"""
agent.py —— AnchoredAgent：真实模型驱动的受锚定 Agent（v0.3.0）
=================================================================
设计要点（对应 docs/plan.md v0.3）：

  1. 模型面：OpenAI 兼容协议（DeepSeek / DashScope / OpenAI，base_url 可配）
  2. 业务工具全部经 ToolGateway 包裹——目标状态注入是结构副作用
  3. 治理工具是模型「唯一的偏离通道」：
     - file_deviation(...)：申报偏离 → Arbiter 机械裁决 → 结果回填对话
     - request_goal_state()：显式查询当前目标状态
  4. ESCALATE → 立即终止执行（阻塞下游），绝不静默继续
  5. 模型无法伪造证据：验证事件只能由环境/验证器写入 EvidenceLog

本模块不含任何 prompt 层的「请遵守约束」式自觉依赖——约束靠结构。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from .goal_harness import (
    Arbiter,
    DeviationApplication,
    DeviationLedger,
    EvidenceLog,
    Spec,
    ToolGateway,
    Verdict,
)

FILE_DEVIATION = "file_deviation"
REQUEST_GOAL_STATE = "request_goal_state"


# ------------------------------------------------------------------
# 模型客户端抽象：真实（OpenAI 兼容）与测试（脚本回放）共用同一接口
# ------------------------------------------------------------------

@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict


@dataclass
class ModelReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class ModelClient:
    """模型客户端协议：chat(messages, tools_schema) -> ModelReply"""

    def chat(self, messages: list[dict], tools_schema: list[dict]) -> ModelReply:
        raise NotImplementedError


# 全局真实请求熔断（对齐 pydantic-ai 的 ALLOW_MODEL_REQUESTS = False）
_REAL_REQUESTS_BLOCKED = False


def set_real_requests_blocked(blocked: bool) -> None:
    """测试用：熔断/恢复所有真实模型请求。"""
    global _REAL_REQUESTS_BLOCKED
    _REAL_REQUESTS_BLOCKED = blocked


class RealModelRequestBlocked(RuntimeError):
    """测试环境误发真实模型请求时抛出。"""


class OpenAICompatClient(ModelClient):
    """OpenAI 兼容实现（DeepSeek / DashScope / OpenAI）。"""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        from openai import OpenAI  # 延迟导入：纯测试环境无需安装
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def chat(self, messages: list[dict], tools_schema: list[dict]) -> ModelReply:
        if _REAL_REQUESTS_BLOCKED:
            raise RealModelRequestBlocked(
                "测试环境禁止真实模型请求：请使用 goal_harness.testing.ScriptedModel")
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,             # type: ignore[arg-type]
            tools=tools_schema or None,
        )
        msg = resp.choices[0].message
        calls = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append(ToolCall(call_id=tc.id, name=tc.function.name, arguments=args))
        return ModelReply(content=msg.content, tool_calls=calls)


# ------------------------------------------------------------------
# AnchoredAgent
# ------------------------------------------------------------------

@dataclass
class AgentResult:
    final_text: str | None
    steps: int
    blocked: bool                       # True = 有偏离被 ESCALATE，执行被阻塞
    escalated: list[str]                # 被升级人工的偏离 ID


class AnchoredAgent:
    def __init__(self, spec: Spec, evidence: EvidenceLog, client: ModelClient,
                 max_steps: int = 8, arbiter: Arbiter | None = None) -> None:
        self.spec = spec
        self.evidence = evidence
        self.client = client
        self.max_steps = max_steps
        self.ledger = DeviationLedger()
        # 可注入仲裁器：评测消融需要对照「弱仲裁」等变体
        self.arbiter = arbiter or Arbiter(spec)
        self.gateway = ToolGateway(spec, self.ledger, evidence)
        self._tools: dict[str, tuple[Callable, dict, str]] = {}
        self.last_transcript: list[dict] = []

    # ---- 注册业务工具（自动过网关）+ 治理工具 ----

    def register(self, name: str, fn: Callable, description: str,
                 parameters: dict) -> None:
        """注册业务工具；fn 签名 fn(ctx, **kwargs)，ctx 由网关注入。"""
        self._tools[name] = (self.gateway.wrap(name, fn), parameters, description)

    def _governance_tools(self) -> dict[str, tuple[Callable, dict, str]]:
        return {
            FILE_DEVIATION: (self._file_deviation, {
                "type": "object",
                "properties": {
                    "req_ids": {"type": "array", "items": {"type": "string"},
                                "description": "该偏离触及的需求 ID 列表"},
                    "reason": {"type": "string"},
                    "scope": {"type": "string", "enum": ["local", "global"]},
                    "alternative": {"type": "string",
                                    "description": "替代方案说明"},
                    "evidence_ref": {"type": "string",
                                     "description": "验证事件 ID；若替代方案未经任何验证，留空字符串"},
                },
                "required": ["req_ids", "reason", "scope", "alternative"],
            }, "当无法满足某条需求时，申报偏离而不是擅自绕过。未申报的绕过是违规。"),
            REQUEST_GOAL_STATE: (self._request_goal_state, {"type": "object",
                                                            "properties": {}},
                                 "查询当前目标摘要、硬约束清单与未决偏离。"),
        }

    # ---- 治理工具实现（不经过网关；自身就是治理动作） ----

    def _file_deviation(self, ctx: dict, req_ids: list[str], reason: str,
                        scope: str, alternative: str,
                        evidence_ref: str = "") -> dict:
        dev = DeviationApplication(
            dev_id=f"DEV-{len(self.ledger._items) + 1:03d}",
            req_ids=tuple(req_ids), reason=reason, scope=scope,
            alternative=alternative, evidence_ref=evidence_ref)
        verdict, trace = self.arbiter.rule(dev, self.evidence)
        self.ledger.file(dev, verdict, trace)
        self.evidence.record("deviation_filed", {"dev": dev.dev_id,
                                                 "verdict": verdict.value})
        out = {"dev_id": dev.dev_id, "verdict": verdict.value, "detail": trace}
        if verdict is Verdict.APPROVE:
            out["instruction"] = "允许按替代方案继续，但必须在最终输出中声明该偏离。"
        elif verdict is Verdict.ESCALATE:
            out["instruction"] = "执行已被阻塞：该偏离触及硬约束或缺乏验证证据，等待人工裁决，不得继续。"
        else:
            out["instruction"] = "该偏离被驳回，请回到原约束方案。"
        return out

    def _request_goal_state(self, ctx: dict) -> dict:
        return {"goal_digest": self.spec.digest()[:16],
                "hard_constraints": self.spec.hard_summary(),
                "policy": f"{self.spec.policy_version}({self.spec.policy_mode})",
                "pending_deviations": [d["dev"]["dev_id"]
                                       for d in self.ledger.pending()]}

    # ---- 主循环 ----

    def run(self, task: str) -> AgentResult:
        system = (
            "你是一个在严格约束下工作的 Agent。\n"
            f"当前任务的硬约束（不可协商，ID 唯一）：\n{self.spec.hard_summary()}\n"
            f"偏离处理策略：{self.spec.policy_version}({self.spec.policy_mode})。\n"
            "当无法满足某条需求时，你必须调用 file_deviation 申报偏离，"
            "由仲裁器机械裁决；未申报就绕过约束是违规行为。"
        )
        messages: list[dict] = [{"role": "system", "content": system},
                                {"role": "user", "content": task}]
        all_tools = {**self._tools, **self._governance_tools()}
        schema = [{"type": "function",
                   "function": {"name": n, "description": desc, "parameters": p}}
                  for n, (_, p, desc) in all_tools.items()]

        escalated: list[str] = []
        blocked = False
        final_text: str | None = None

        for _ in range(self.max_steps):
            reply = self.client.chat(messages, schema)
            if not reply.tool_calls:
                final_text = reply.content
                break
            messages.append({"role": "assistant",
                             "content": reply.content or "",
                             "tool_calls": [
                                 {"id": tc.call_id, "type": "function",
                                  "function": {"name": tc.name,
                                               "arguments": json.dumps(
                                                   tc.arguments, ensure_ascii=False)}}
                                 for tc in reply.tool_calls]})
            for tc in reply.tool_calls:
                if tc.name not in all_tools:
                    result = {"error": f"未知工具 {tc.name}"}
                else:
                    fn = all_tools[tc.name][0]
                    try:
                        result = fn(ctx={}, **tc.arguments)
                    except Exception as exc:
                        result = {"error": f"{type(exc).__name__}: {exc}"}
                messages.append({"role": "tool", "tool_call_id": tc.call_id,
                                 "content": json.dumps(result, ensure_ascii=False)})
                if tc.name == FILE_DEVIATION and result.get("verdict") == "escalate":
                    escalated.append(result["dev_id"])
                    blocked = True
            if blocked:
                break

        self.last_transcript = [dict(m) for m in messages]
        return AgentResult(final_text=final_text, steps=min(_ + 1, self.max_steps),
                           blocked=blocked, escalated=escalated)
