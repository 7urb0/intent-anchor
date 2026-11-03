"""属性测试（hypothesis）：断言不变量，而非个别样例。

对齐「不变量优于样例」的思路——手工样例只能覆盖想得到的路径，
属性测试用随机输入轰炸边界（空序列、超长序列、任意篡改位置）。
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from goal_harness import EvidenceLog


@given(st.lists(st.text(min_size=1), min_size=0, max_size=40))
@settings(max_examples=40, deadline=None)
def test_any_record_sequence_verifies(kinds: list[str]) -> None:
    """不变量 1：任意长度的记录序列，哈希链恒可验证。"""
    ev = EvidenceLog()
    for kind in kinds:
        ev.record(kind, {"i": len(ev)})
    assert ev.verify() is True
    assert len(ev) == len(kinds)


@given(
    st.lists(st.text(min_size=1), min_size=1, max_size=20),
    st.integers(min_value=0),
    st.sampled_from(["kind", "payload", "prev", "seq"]),
)
@settings(max_examples=40, deadline=None)
def test_tampering_any_field_always_detected(kinds: list[str],
                                             index: int,
                                             field: str) -> None:
    """不变量 2：任意记录、任意字段被改，验证必失败。"""
    ev = EvidenceLog()
    for kind in kinds:
        ev.record(kind, {"i": len(ev)})
    target = ev._records[index % len(ev._records)]
    if field == "payload":
        target["payload"] = {"tampered": True}
    elif field == "kind":
        target["kind"] = "tampered"
    elif field == "prev":
        target["prev"] = "0" * 64
    else:
        target["seq"] = 999
    assert ev.verify() is False


@given(st.lists(st.booleans(), min_size=1, max_size=15),
       st.lists(st.booleans(), min_size=1, max_size=15))
@settings(max_examples=25, deadline=None)
def test_pending_never_contains_reverted(hard_flags: list[bool],
                                         scopes_local: list[bool]) -> None:
    """不变量 3：无论偏离如何组合，pending()（交给下游的清单）永不含已被回退的条目。"""
    from goal_harness import (
        Arbiter,
        DeviationApplication,
        DeviationLedger,
        Requirement,
        Spec,
        Verdict,
    )

    reqs = tuple(Requirement(f"R{i}", f"需求 {i}", h)
                 for i, h in enumerate(hard_flags))
    spec = Spec(reqs, "prop-v1")
    evidence = EvidenceLog()
    ledger = DeviationLedger()
    arbiter = Arbiter(spec)

    for i, is_local in enumerate(scopes_local):
        evidence.record("verification", {"vid": f"V{i}", "check": "ok"})
        dev = DeviationApplication(
            dev_id=f"DEV-{i}", req_ids=(f"R{i % len(reqs)}",),
            reason="prop", scope="local" if is_local else "global",
            alternative="alt", evidence_ref=f"V{i}")
        verdict, trace = arbiter.rule(dev, evidence)
        ledger.file(dev, verdict, trace)

    pending_ids = {item["dev"]["dev_id"] for item in ledger.pending()}
    reverted = {item["dev"]["dev_id"] for item in ledger.all_items().values()
                if item["verdict"] == Verdict.REVERT.value}
    assert pending_ids.isdisjoint(reverted)
