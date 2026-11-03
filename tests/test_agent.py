"""AnchoredAgent 行为契约测试：FakeClient 脚本回放，CI 零依赖 key。"""

import json

from goal_harness import (
    AnchoredAgent,
    EvidenceLog,
    ModelReply,
    Requirement,
    Spec,
    ToolCall,
    verify_gateway_coverage,
)


def make_spec() -> Spec:
    return Spec(
        requirements=(
            Requirement("REQ-001", "股价必须来自官方实时接口", hard=True),
            Requirement("REQ-002", "摘要必须为中文", hard=False),
        ),
        policy_version="policy-live-v1",
        policy_mode="stepwise",
    )


def make_agent(evidence: EvidenceLog | None = None) -> AnchoredAgent:
    agent = AnchoredAgent(make_spec(), evidence or EvidenceLog(),
                          client=None)  # client 由测试注入
    agent.register(
        "get_stock_price",
        lambda ctx, source: {"price": 102.5, "source": source},
        description="查询股价",
        parameters={"type": "object",
                    "properties": {"source": {"type": "string"}},
                    "required": ["source"]},
    )
    return agent


class FakeClient:
    """按脚本顺序回放模型响应。"""

    def __init__(self, script: list[ModelReply]) -> None:
        self._script = list(script)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools_schema) -> ModelReply:
        self.calls.append([m for m in messages])
        assert self._script, "脚本耗尽：模型被调用的次数超出预期"
        return self._script.pop(0)


def text_reply(text: str) -> ModelReply:
    return ModelReply(content=text)


def tool_reply(name: str, arguments: dict, call_id: str = "c1") -> ModelReply:
    return ModelReply(content=None, tool_calls=[ToolCall(call_id, name, arguments)])


def tool_message(agent_messages: list[dict], call_id: str = "c1") -> dict:
    return next(m for m in agent_messages if m.get("tool_call_id") == call_id)


# ---------- 基本回路 ----------

class TestBasicLoop:
    def test_plain_completion(self):
        client = FakeClient([text_reply("直接回答")])
        agent = make_agent()
        agent.client = client
        result = agent.run("1+1 等于几")
        assert result.final_text == "直接回答"
        assert result.steps == 1 and not result.blocked

    def test_business_tool_goes_through_gateway(self):
        client = FakeClient([
            tool_reply("get_stock_price", {"source": "官方接口"}),
            text_reply("股价 102.5"),
        ])
        agent = make_agent()
        agent.client = client
        result = agent.run("查股价")
        assert result.final_text == "股价 102.5"
        # 网关结构副作用：目标状态查询被强制记录
        assert len(agent.evidence.filter("goal_query")) == 1
        assert verify_gateway_coverage(agent.evidence) == []


# ---------- 偏离申报通道 ----------

class TestDeviationChannel:
    def test_approved_deviation_fed_back(self):
        evidence = EvidenceLog()
        evidence.record("verification", {"vid": "VERIFY-1", "check": "回归通过"})
        client = FakeClient([
            tool_reply("file_deviation", {
                "req_ids": ["REQ-002"], "reason": "英文数据源",
                "scope": "local", "alternative": "机器翻译摘要",
                "evidence_ref": "VERIFY-1"}, call_id="d1"),
            text_reply("按批准的替代方案完成"),
        ])
        agent = make_agent(evidence)
        agent.client = client
        result = agent.run("写摘要")
        assert not result.blocked
        tool_msg = json.loads(tool_message(client.calls[1], "d1")["content"])
        assert tool_msg["verdict"] == "approve"
        assert agent.ledger.get("DEV-001")["verdict"] == "approve"

    def test_escalate_on_hard_constraint_blocks(self):
        client = FakeClient([
            tool_reply("file_deviation", {
                "req_ids": ["REQ-001"], "reason": "官方接口超时，想用缓存",
                "scope": "local", "alternative": "备用缓存",
                "evidence_ref": ""}),
        ])
        agent = make_agent()
        agent.client = client
        result = agent.run("查股价")
        assert result.blocked and result.escalated == ["DEV-001"]
        assert result.final_text is None                    # 阻塞后不再产出结论
        assert agent.client._script == []                   # 阻塞后不再调用模型

    def test_failclosed_when_no_evidence(self):
        # 软约束偏离但没引用验证证据 → fail-closed 同样阻塞（不自动放行）
        client = FakeClient([
            tool_reply("file_deviation", {
                "req_ids": ["REQ-002"], "reason": "懒得验证",
                "scope": "local", "alternative": "未验证方案",
                "evidence_ref": ""}),
        ])
        agent = make_agent()
        agent.client = client
        result = agent.run("写摘要")
        assert result.blocked and result.escalated == ["DEV-001"]
        # ESCALATE 后循环即断，模型不会被二次调用；回填内容从账本断言
        item = agent.ledger.get("DEV-001")
        assert item["verdict"] == "escalate" and "fail-closed" in item["trace"]

    def test_unknown_tool_reported_not_crash(self):
        client = FakeClient([
            tool_reply("不存在的工具", {}),
            text_reply("好的，改用现有工具"),
        ])
        agent = make_agent()
        agent.client = client
        result = agent.run("测试")
        assert result.final_text == "好的，改用现有工具"
        tool_msg = json.loads(tool_message(client.calls[1], "c1")["content"])
        assert "未知工具" in tool_msg["error"]


# ---------- 工具异常回填 ----------

class TestToolErrors:
    def test_exception_fed_back_to_model(self):
        agent = make_agent()

        def broken(ctx):
            raise TimeoutError("官方接口超时 30s")

        agent.register("broken_tool", broken, description="会超时的工具",
                       parameters={"type": "object", "properties": {}})
        client = FakeClient([
            tool_reply("broken_tool", {}, call_id="x1"),
            text_reply("已收到超时错误"),
        ])
        agent.client = client
        result = agent.run("触发异常")
        assert result.final_text == "已收到超时错误"
        tool_msg = json.loads(tool_message(client.calls[1], "x1")["content"])
        assert "TimeoutError" in tool_msg["error"]


# 导入放末尾避免遮蔽（file_deviation 常量来自模块）
