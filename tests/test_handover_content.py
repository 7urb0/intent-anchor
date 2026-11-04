"""v0.8.0 内容层强制执行测试：交接前隐去禁用来源的数值。"""

from __future__ import annotations

from goal_harness import (
    AnchoredAgent,
    DeviationLedger,
    EvidenceLog,
    Requirement,
    Spec,
    ToolGateway,
    redact_forbidden,
)
from goal_harness.handover_content import forbidden_values
from goal_harness.testing import ScriptedModel, text, tool_call


def make_downstream_spec() -> Spec:
    return Spec((Requirement("REQ-001", "只能使用官方来源数值", True,
                             (), ("cached_price",)),), "t-redact")


def make_upstream_evidence() -> EvidenceLog:
    """上游证据层：cached_price 产出 999.99（含禁用来源的数值）。"""
    ev = EvidenceLog()
    spec = Spec((Requirement("REQ-001", "x", True),), "up")
    gw = ToolGateway(spec, DeviationLedger(), ev)
    gw.wrap("cached_price", lambda ctx, **kw: {"price": 999.99, "note": "缓存 2024 年"})({})
    return ev


def test_forbidden_values_extracted():
    vals = forbidden_values(make_upstream_evidence(), make_downstream_spec())
    assert "999.99" in vals and "2024" in vals


def test_no_denied_tools_means_no_redaction():
    spec = Spec((Requirement("REQ-001", "x", True),), "up")
    assert forbidden_values(make_upstream_evidence(), spec) == []
    text, n = redact_forbidden("股价 999.99", make_upstream_evidence(), spec)
    assert (text, n) == ("股价 999.99", 0)


def test_redact_removes_sentinel():
    ev = make_upstream_evidence()
    spec = make_downstream_spec()
    text, n = redact_forbidden("ACME 股价为 999.99（缓存 2024 年数据）。", ev, spec)
    assert "999.99" not in text and "2024" not in text
    assert n >= 1
    assert "【已隐去：未批准数值】" in text
    # 替换动作入证据，可审计
    red = ev.filter("content_redacted")
    assert len(red) == 1 and red[0]["payload"]["replacements"] >= 1


def test_redact_idempotent():
    ev = make_upstream_evidence()
    spec = make_downstream_spec()
    once, _ = redact_forbidden("股价 999.99", ev, spec)
    twice, n2 = redact_forbidden(once, ev, spec)
    assert twice == once and n2 == 0


def test_agent_end_to_end_downstream_never_sees_value():
    """端到端：禁用工具的产出经网关后入证据，隐去后下游 prompt 不含哨兵。"""
    up_spec = Spec((Requirement("REQ-001", "x", True),), "up")
    ev = EvidenceLog()
    agent = AnchoredAgent(up_spec, ev,
                          ScriptedModel([tool_call("cached_price", {}),
                                         text("上游结论：股价 999.99。")]),
                          max_steps=2)
    agent.register("cached_price", lambda ctx, **kw: {"price": 999.99}, "缓存",
                   {"type": "object", "properties": {}})
    agent.run("q")
    down_spec = make_downstream_spec()
    out_text, n = redact_forbidden("上游结论：股价 999.99。", ev, down_spec)
    assert n >= 1 and "999.99" not in out_text
