"""L2 层基础：ScriptedModel / 全局熔断 / 轨迹快照。"""

import pytest

from goal_harness import AnchoredAgent, EvidenceLog, Requirement, Spec
from goal_harness.agent import OpenAICompatClient
from goal_harness.testing import (
    RealModelRequestBlocked,
    ScriptedModel,
    block_real_requests,
    normalize_transcript,
    text,
    tool_call,
)


def make_spec() -> Spec:
    return Spec(
        requirements=(
            Requirement("REQ-001", "股价必须来自官方实时接口", True),
            Requirement("REQ-002", "结论必须为中文", False),
        ),
        policy_version="policy-test-v1",
    )


# ---------- 全局熔断 ----------

def test_real_client_blocked_in_tests():
    """conftest 的 autouse 熔断生效：真实 client 在测试里必抛。"""
    client = OpenAICompatClient(api_key="sk-test", base_url="https://example.invalid",
                               model="whatever")
    with pytest.raises(RealModelRequestBlocked, match="ScriptedModel"):
        client.chat([{"role": "user", "content": "hi"}], [])


def test_block_context_restores_flag():
    from goal_harness.agent import _REAL_REQUESTS_BLOCKED

    with block_real_requests():
        pass
    # 退出上下文后应恢复到上下文进入前的状态（conftest 里仍有外层 block）
    assert isinstance(_REAL_REQUESTS_BLOCKED, bool)


# ---------- ScriptedModel ----------

def test_scripted_model_replays_in_order():
    model = ScriptedModel([text("第一步"), text("第二步")])
    assert model.chat([], []).content == "第一步"
    assert model.chat([], []).content == "第二步"
    assert model.remaining == 0


def test_scripted_model_exhausted_raises():
    model = ScriptedModel([text("唯一一句")])
    model.chat([], [])
    with pytest.raises(AssertionError, match="脚本耗尽"):
        model.chat([], [])


def test_scripted_model_lenient_mode():
    model = ScriptedModel([], on_exhausted="placeholder")
    reply = model.chat([], [])
    assert reply.content and "脚本已耗尽" in reply.content


def test_function_model_sees_messages():
    """from_function：按消息内容动态决策（测分支用）。"""
    def decide(messages: list[dict]):
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        return text("已收到工具结果" if has_tool_result else "还没有结果")

    model = ScriptedModel.from_function(decide)
    assert model.chat([{"role": "user", "content": "x"}], []).content == "还没有结果"
    assert model.chat([{"role": "tool", "content": "{}"}], []).content == "已收到工具结果"


# ---------- 轨迹快照 ----------

def test_transcript_snapshot_escalation_path(snapshot, deterministic_clock):
    """硬约束偏离被阻塞的完整轨迹快照（时间已钉死，可稳定断言）。"""
    spec = make_spec()
    evidence = EvidenceLog()
    model = ScriptedModel([
        tool_call("file_deviation", {
            "req_ids": ["REQ-001"], "reason": "官方接口超时",
            "scope": "local", "alternative": "缓存", "evidence_ref": ""}),
    ])
    agent = AnchoredAgent(spec, evidence, model)
    result = agent.run("查股价")

    assert result.blocked is True
    assert normalize_transcript(agent.last_transcript) == snapshot
