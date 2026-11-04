"""v0.7.0 新机制测试：工具级 allow/deny（policy-as-code）+ 进度感知仲裁。

两个机制都不含 LLM 判断，全部从规格元数据与证据层派生——测试可以直接断言。
"""

from __future__ import annotations

import pytest

from goal_harness import (
    AnchoredAgent,
    Arbiter,
    DeviationApplication,
    DeviationLedger,
    EvidenceLog,
    ProgressArbiter,
    Requirement,
    Spec,
    ToolGateway,
    Verdict,
)
from goal_harness.testing import ScriptedModel, text, tool_call


def make_spec(bound: bool = True, deny: bool = True) -> Spec:
    return Spec((Requirement("REQ-001", "必须使用 official_price", True,
                             ("official_price",) if bound else (),
                             ("cached_price",) if deny else ()),),
                "t-policy")


def _dev(req_ids=("REQ-001",), evidence_ref: str = "") -> DeviationApplication:
    return DeviationApplication(dev_id="DEV-001", req_ids=tuple(req_ids),
                                reason="官方接口超时", scope="local",
                                alternative="改用缓存", evidence_ref=evidence_ref)


# ---------------- 工具级 allow/deny ----------------

def test_policy_denies_forbidden_tool():
    spec = make_spec()
    ev = EvidenceLog()
    gw = ToolGateway(spec, DeviationLedger(), ev)
    executed = {"n": 0}

    def shortcut(ctx, **kw):
        executed["n"] += 1
        return {"price": 999.99}

    out = gw.wrap("cached_price", shortcut)({})
    assert out["error"] == "policy_denied"
    assert executed["n"] == 0                       # 工具本体没有执行
    assert ev.filter("policy_denied")[0]["payload"]["tool"] == "cached_price"
    assert ev.filter("policy_denied")[0]["payload"]["denied"] == ["cached_price"]
    assert ev.filter("tool_call") == []             # 不产生 tool_call 事件


def test_policy_allows_non_denied_tool():
    spec = make_spec()
    ev = EvidenceLog()
    gw = ToolGateway(spec, DeviationLedger(), ev)
    out = gw.wrap("official_price", lambda ctx, **kw: {"price": 88.8})({}, symbol="A")
    assert out == {"price": 88.8}
    assert len(ev.filter("tool_call")) == 1


def test_no_binding_means_no_enforcement():
    """向后兼容：未声明绑定/禁用 → 旧行为（任何工具都能调）。"""
    spec = make_spec(bound=False, deny=False)
    gw = ToolGateway(spec, DeviationLedger(), EvidenceLog())
    assert gw.wrap("cached_price", lambda ctx, **kw: {"ok": 1})({}) == {"ok": 1}


def test_enforce_policy_can_be_disabled():
    spec = make_spec()
    gw = ToolGateway(spec, DeviationLedger(), EvidenceLog(), enforce_policy=False)
    assert gw.wrap("cached_price", lambda ctx, **kw: {"ok": 1})({}) == {"ok": 1}


# ---------------- 进度感知仲裁 ----------------

def test_progress_reverts_when_tool_untried():
    spec = make_spec()
    arb = ProgressArbiter(spec, min_attempts=2)
    verdict, trace = arb.rule(_dev(), EvidenceLog())
    assert verdict is Verdict.REVERT
    assert "未尝试" in trace and "official_price" in trace


def test_progress_reverts_under_quota_then_escalates():
    spec = make_spec()
    ev = EvidenceLog()
    arb = ProgressArbiter(spec, min_attempts=2)
    ev.record("tool_call", {"tool": "official_price", "args": []})
    assert arb.rule(_dev(), ev)[0] is Verdict.REVERT        # 1/2 次：先重试
    ev.record("tool_call", {"tool": "official_price", "args": []})
    verdict, trace = arb.rule(_dev(), ev)                    # 2/2 次：死路
    assert verdict is Verdict.ESCALATE and "双人控制队列" in trace


def test_progress_falls_back_to_fail_closed_without_binding():
    """没有授权路径的规格 → 不假设有路，保持 v0.6 的 fail-closed。"""
    spec = make_spec(bound=False)
    arb = ProgressArbiter(spec)
    assert arb.rule(_dev(), EvidenceLog())[0] is Verdict.ESCALATE


def test_progress_soft_requirement_uses_parent_rules():
    spec = Spec((Requirement("REQ-S", "软约束", False, ("tool_x",)),), "t-soft")
    arb = ProgressArbiter(spec)
    verdict, _ = arb.rule(_dev(req_ids=("REQ-S",)), EvidenceLog())
    assert verdict is Verdict.ESCALATE       # 软约束 + stepwise + 无证据 → 父类 fail-closed


def test_progress_snapshot():
    spec = make_spec()
    ev = EvidenceLog()
    ev.record("tool_call", {"tool": "official_price", "args": []})
    snap = ProgressArbiter(spec, min_attempts=2).progress(ev)["REQ-001"]
    assert snap["attempts"] == {"official_price": 1}
    assert snap["untried"] == [] and snap["exhausted"] is False
    assert snap["can_continue"] is True


def test_min_attempts_validated():
    with pytest.raises(ValueError):
        ProgressArbiter(make_spec(), min_attempts=0)


# ---------------- 端到端：agent + 脚本模型 ----------------

def test_agent_revert_then_retry_then_escalate():
    """模型一上来就申报偏离 → 被驳回并要求先走授权路径；配额耗尽后才升级。"""
    spec = make_spec()
    ev = EvidenceLog()

    def official(ctx, **kw):
        raise TimeoutError("临时超时")

    dev = tool_call("file_deviation", {"req_ids": ["REQ-001"], "reason": "接口不可用",
                                       "scope": "local", "alternative": "用缓存"})
    script = [
        dev,                                        # 1) 未尝试授权路径 → REVERT
        tool_call("official_price", {"symbol": "A"}),   # 2) 第 1 次尝试失败
        dev,                                        # 3) 1/2 次 → REVERT（先重试）
        tool_call("official_price", {"symbol": "A"}),   # 4) 第 2 次尝试失败
        dev,                                        # 5) 2/2 次 → ESCALATE
    ]
    agent = AnchoredAgent(spec, ev, ScriptedModel(script), max_steps=8,
                          arbiter=ProgressArbiter(spec, min_attempts=2))
    agent.register("official_price", official, "官方行情", {"type": "object",
                                                           "properties": {}})
    agent.register("cached_price", lambda ctx, **kw: {"price": 999.99}, "缓存",
                   {"type": "object", "properties": {}})
    result = agent.run("查股价")

    assert result.blocked is True                       # 配额耗尽后才阻塞
    assert result.escalated == ["DEV-003"]
    ledger_items = agent.ledger.all_items()
    assert ledger_items["DEV-001"]["verdict"] == "revert"     # 驳回，不阻塞
    assert ledger_items["DEV-002"]["verdict"] == "revert"     # 先重试
    assert ledger_items["DEV-003"]["verdict"] == "escalate"
    # 授权路径确实被尝试过两次（进度查账的依据）
    assert len(ev.filter("tool_call")) == 2


def test_agent_policy_blocks_silent_bypass():
    """沉默绕过的洞：策略拒绝让捷径工具连调用都不成立。"""
    spec = make_spec()
    ev = EvidenceLog()
    script = [tool_call("cached_price", {"symbol": "A"}), text("股价 999.99")]
    agent = AnchoredAgent(spec, ev, ScriptedModel(script), max_steps=4)
    agent.register("official_price", lambda ctx, **kw: {"price": 88.8}, "官方",
                   {"type": "object", "properties": {}})
    hits = {"n": 0}

    def shortcut(ctx, **kw):
        hits["n"] += 1
        return {"price": 999.99}

    agent.register("cached_price", shortcut, "缓存", {"type": "object", "properties": {}})
    agent.run("查股价")

    assert hits["n"] == 0                                # 工具本体没跑
    assert ev.filter("policy_denied")[0]["payload"]["tool"] == "cached_price"
    assert ev.filter("policy_denied")[0]["payload"]["denied"] == ["cached_price"]
    # 模型收到 policy_denied 后再作答（此处脚本直接给文本），答案里没有捷径数据来源
    assert ev.filter("tool_call") == []


def test_arbiter_signature_unchanged():
    """父类未回归：未绑定规格 + 默认仲裁器仍 fail-closed。"""
    spec = make_spec(bound=False)
    assert Arbiter(spec).rule(_dev(), EvidenceLog())[0] is Verdict.ESCALATE
