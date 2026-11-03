"""补充分支覆盖：真实客户端解析、request_goal_state、REVERT 回填分支。"""

from types import SimpleNamespace

from goal_harness import AnchoredAgent, EvidenceLog, Requirement, Spec
from goal_harness.agent import (
    OpenAICompatClient,
    set_real_requests_blocked,
)
from goal_harness.testing import ScriptedModel, text, tool_call


def make_agent(model) -> AnchoredAgent:
    spec = Spec((Requirement("REQ-001", "硬约束", True),
                 Requirement("REQ-002", "软约束", False)), "policy-cov-v1")
    return AnchoredAgent(spec, EvidenceLog(), model)


# ---------- 真实客户端：用桩替换底层 SDK，验证解析逻辑（不联网） ----------

def _stub(client: OpenAICompatClient, tool_calls) -> None:
    class FakeCompletions:
        def create(self, **_kw):
            message = SimpleNamespace(content="hi", tool_calls=tool_calls)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))


def test_openai_client_parses_tool_calls():
    client = OpenAICompatClient(api_key="x", base_url="https://example.invalid", model="m")
    _stub(client, [SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="get_price", arguments='{"symbol": "ACME"}'))])
    set_real_requests_blocked(False)               # 临时放行以走解析分支（仍是桩，不联网）
    try:
        reply = client.chat([{"role": "user", "content": "x"}], [])
    finally:
        set_real_requests_blocked(True)
    assert reply.content == "hi"
    assert reply.tool_calls[0].name == "get_price"
    assert reply.tool_calls[0].arguments == {"symbol": "ACME"}


def test_openai_client_survives_malformed_arguments():
    client = OpenAICompatClient(api_key="x", base_url="https://example.invalid", model="m")
    _stub(client, [SimpleNamespace(
        id="call_2", function=SimpleNamespace(name="t", arguments="{not json"))])
    set_real_requests_blocked(False)
    try:
        reply = client.chat([], [])
    finally:
        set_real_requests_blocked(True)
    assert reply.tool_calls[0].arguments == {"_raw": "{not json"}


# ---------- request_goal_state 工具 ----------

def test_request_goal_state_returns_policy_and_constraints():
    agent = make_agent(ScriptedModel([
        tool_call("request_goal_state", {}),
        text("完成"),
    ]))
    result = agent.run("随便")
    assert result.final_text == "完成"
    tool_msgs = [m for m in agent.last_transcript if m.get("role") == "tool"]
    assert tool_msgs and "REQ-001" in tool_msgs[0]["content"]


# ---------- REVERT 回填分支（软约束 + 影响全局 + 有证据 → 驳回） ----------

def test_revert_branch_fed_back_to_model():
    evidence = EvidenceLog()
    evidence.record("verification", {"vid": "V1", "check": "ok"})
    spec = Spec((Requirement("REQ-002", "软约束", False),), "policy-cov-v1")
    agent = AnchoredAgent(spec, evidence, ScriptedModel([
        tool_call("file_deviation", {
            "req_ids": ["REQ-002"], "reason": "影响全局",
            "scope": "global", "alternative": "改整体格式", "evidence_ref": "V1"}),
        text("按驳回意见回到原方案"),
    ]))
    result = agent.run("写报告")
    assert result.final_text == "按驳回意见回到原方案"
    assert agent.ledger.get("DEV-001")["verdict"] == "revert"
    tool_msgs = [m for m in agent.last_transcript if m.get("role") == "tool"]
    assert "驳回" in tool_msgs[0]["content"]
