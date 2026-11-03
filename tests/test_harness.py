"""intent-anchor 核心行为测试。

每个测试对应 demo 中一条路径，把「手跑验证」升级为可断言的行为契约：
  - 哈希链完整性与防篡改（EvidenceLog）
  - 仲裁三分类 + fail-closed（Arbiter）
  - 交接不允许默认继承（Handover）
  - 网关覆盖架构测试（verify_gateway_coverage）
"""

from dataclasses import FrozenInstanceError

import pytest

from goal_harness import (
    Arbiter,
    DeviationApplication,
    DeviationLedger,
    EvidenceLog,
    Handover,
    Requirement,
    Spec,
    ToolGateway,
    Verdict,
    verify_gateway_coverage,
)

# ---------- 公共夹具 ----------

def make_spec(policy_mode: str = "stepwise") -> Spec:
    return Spec(
        requirements=(
            Requirement("REQ-001", "必须使用真实数据源 A 的原始数据", hard=True),
            Requirement("REQ-002", "输出必须为 JSON 格式", hard=False),
        ),
        policy_version="policy-test-v1",
        policy_mode=policy_mode,
    )


def make_dev(**overrides) -> DeviationApplication:
    defaults = {
        "dev_id": "DEV-T1",
        "req_ids": ("REQ-002",),
        "reason": "测试偏离",
        "scope": "local",
        "alternative": "替代方案",
        "evidence_ref": "VERIFY-T1",
    }
    defaults.update(overrides)
    return DeviationApplication(**defaults)


def evidence_with(vid: str | None) -> EvidenceLog:
    ev = EvidenceLog()
    if vid:
        ev.record("verification", {"vid": vid, "check": "回归测试通过"})
    return ev


# ---------- EvidenceLog：哈希链 ----------

class TestEvidenceLog:
    def test_chain_integrity_untampered(self):
        ev = evidence_with("V1")
        ev.record("tool_call", {"tool": "fetch"})
        assert ev.verify() is True

    def test_tampering_breaks_chain(self):
        ev = evidence_with("V1")
        ev.record("tool_call", {"tool": "fetch"})
        ev._records[0]["payload"]["check"] = "被悄悄改掉"
        assert ev.verify() is False

    def test_find_verification_hit_and_miss(self):
        ev = evidence_with("V1")
        assert ev.find_verification("V1") is not None
        assert ev.find_verification("不存在") is None


# ---------- Arbiter：三分类 + fail-closed + 策略分级 ----------

class TestArbiter:
    def test_approve_with_real_evidence(self):
        verdict, trace = Arbiter(make_spec()).rule(
            make_dev(), evidence_with("VERIFY-T1"))
        assert verdict is Verdict.APPROVE
        assert "VERIFY-T1" in trace

    def test_escalate_on_hard_constraint(self):
        # 触及硬约束：即使有真实验证证据也必须升级
        verdict, _ = Arbiter(make_spec()).rule(
            make_dev(req_ids=("REQ-001",)), evidence_with("VERIFY-T1"))
        assert verdict is Verdict.ESCALATE

    def test_failclosed_on_missing_evidence(self):
        # v2 核心：自评置信度已删除；查无验证记录 → 一律升级，绝不自动放行
        verdict, trace = Arbiter(make_spec()).rule(
            make_dev(), evidence_with(None))          # 证据层为空
        assert verdict is Verdict.ESCALATE
        assert "fail-closed" in trace

    def test_failclosed_on_dangling_ref(self):
        # 引用了一个存在其他 ID 的证据层：同样查无此记录
        verdict, _ = Arbiter(make_spec()).rule(
            make_dev(evidence_ref="别人的验证"), evidence_with("VERIFY-T1"))
        assert verdict is Verdict.ESCALATE

    def test_revert_on_global_scope(self):
        verdict, _trace = Arbiter(make_spec()).rule(
            make_dev(scope="global"), evidence_with("VERIFY-T1"))
        assert verdict is Verdict.REVERT

    def test_short_policy_approves_without_evidence(self):
        # 短任务策略：软约束偏离免证据批量入账（框架开销随任务长度伸缩）
        verdict, trace = Arbiter(make_spec(policy_mode="short")).rule(
            make_dev(evidence_ref=""), evidence_with(None))
        assert verdict is Verdict.APPROVE
        assert "short" in trace

    def test_short_policy_still_escalates_hard(self):
        # 策略分级不豁免硬约束
        verdict, _ = Arbiter(make_spec(policy_mode="short")).rule(
            make_dev(req_ids=("REQ-001",), evidence_ref=""),
            evidence_with(None))
        assert verdict is Verdict.ESCALATE


# ---------- Handover：不允许默认继承 ----------

class TestHandover:
    def _prepare(self):
        spec = make_spec()
        evidence = evidence_with("VERIFY-T1")
        ledger = DeviationLedger()
        arbiter = Arbiter(spec)
        dev = make_dev()
        verdict, trace = arbiter.rule(dev, evidence)
        ledger.file(dev, verdict, trace)
        return Handover(ledger, arbiter, evidence), ledger

    def test_reject_on_missing_decision(self):
        handover, _ = self._prepare()
        with pytest.raises(ValueError, match="不允许默认继承"):
            handover.receive("downstream", {})           # 缺裁决 → 交接失败

    def test_explicit_accept_completes(self):
        handover, ledger = self._prepare()
        handover.receive("downstream", {"DEV-T1": "accept"})
        assert ledger.pending()[0]["status"] == "confirmed_by_downstream"


# ---------- 网关：强制注入 + 架构测试 ----------

class TestGateway:
    def test_goal_query_is_structural_side_effect(self):
        spec = make_spec()
        evidence = EvidenceLog()
        gateway = ToolGateway(spec, DeviationLedger(), evidence)
        tool = gateway.wrap("t", lambda ctx: ctx["goal_digest"])
        ctx = {}
        tool(ctx)
        # 目标状态查询是每次调用的结构副作用，不是自愿 API
        assert len(evidence.filter("goal_query")) == 1
        assert ctx["goal_digest"] == spec.digest()

    def test_periodic_reinjection(self):
        spec = make_spec()
        gateway = ToolGateway(spec, DeviationLedger(), EvidenceLog(),
                              reinject_every=2)
        tool = gateway.wrap("t", lambda ctx: ctx.get("hard_constraints", ""))
        ctx = {}
        tool(ctx)                                        # 第 1 次：不重注入
        first = ctx["hard_constraints"]
        tool(ctx)                                        # 第 2 次：触发重注入
        assert first == "" and "REQ-001" in ctx["hard_constraints"]

    def test_gateway_coverage_architecture_test(self):
        evidence = EvidenceLog()
        gateway = ToolGateway(make_spec(), DeviationLedger(), evidence)
        tool = gateway.wrap("t", lambda ctx: None)
        ctx = {}
        tool(ctx)
        assert verify_gateway_coverage(evidence) == []   # 走网关 → 无违例
        # 模拟绕过网关的直接调用记录
        evidence.record("tool_call", {"tool": "bypassed", "args": []})
        violations = verify_gateway_coverage(evidence)
        assert len(violations) == 1 and "绕过" in violations[0]


# ---------- Spec：构造即校验（v0.2.0） ----------

class TestSpecValidation:
    def test_rejects_empty_requirements(self):
        with pytest.raises(ValueError, match="至少需要一条需求"):
            Spec((), "v1")

    def test_rejects_duplicate_req_ids(self):
        with pytest.raises(ValueError, match="需求 ID 重复"):
            Spec((Requirement("R1", "a", False), Requirement("R1", "b", True)), "v1")

    def test_rejects_unknown_policy_mode(self):
        with pytest.raises(ValueError, match="policy_mode"):
            Spec((Requirement("R1", "a", False),), "v1", policy_mode="yolo")

    def test_frozen_immutability(self):
        spec = make_spec()
        with pytest.raises(FrozenInstanceError):
            spec.policy_mode = "short"  # type: ignore[misc]

    def test_digest_stable_and_sensitive(self):
        spec_a, spec_b = make_spec(), make_spec()
        assert spec_a.digest() == spec_b.digest()          # 同内容同摘要
        altered = Spec(
            (Requirement("REQ-001", "必须使用真实数据源 A 的原始数据", hard=True),
             Requirement("REQ-002", "输出必须为 XML 格式", hard=False)),   # 改了软约束文本
            "policy-test-v1")
        assert altered.digest() != spec_a.digest()         # 任何改动都改变摘要


# ---------- EvidenceLog：快照落盘（v0.2.0） ----------

class TestEvidencePersistence:
    def test_save_load_roundtrip(self, tmp_path):
        ev = evidence_with("V1")
        ev.record("goal_query", {"tool": "t"})
        ev.record("tool_call", {"tool": "t"})
        path = str(tmp_path / "evidence.jsonl")
        ev.save(path)
        restored = EvidenceLog.load(path)
        assert len(restored) == len(ev) == 3
        assert restored.verify() is True
        assert restored.find_verification("V1") is not None

    def test_tampered_snapshot_fails_verification(self, tmp_path):
        ev = evidence_with("V1")
        path = str(tmp_path / "evidence.jsonl")
        ev.save(path)
        # 模拟有人直接改快照文件：哈希链必须抓住
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        import json
        rec = json.loads(lines[0])
        rec["payload"]["check"] = "事后篡改"
        lines[0] = json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        assert EvidenceLog.load(path).verify() is False

    def test_load_continues_appending(self, tmp_path):
        """load 回来的证据层可以继续追加，且新纪录接在原链之后。"""
        ev = evidence_with("V1")
        path = str(tmp_path / "evidence.jsonl")
        ev.save(path)
        restored = EvidenceLog.load(path)
        restored.record("handover_accept", {"dev": "DEV-T1", "by": "downstream"})
        assert restored.verify() is True
        assert len(restored) == 2


# ---------- 组合场景（v0.2.0） ----------

class TestComposedScenarios:
    def test_mixed_req_ids_escalate(self):
        # 偏离同时触及软约束和硬约束：只要沾到硬约束就升级
        spec = make_spec()
        verdict, trace = Arbiter(spec).rule(
            make_dev(req_ids=("REQ-002", "REQ-001")), evidence_with("VERIFY-T1"))
        assert verdict is Verdict.ESCALATE
        assert "REQ-001" in trace

    def test_gateway_injects_pending_deviations(self):
        spec = make_spec()
        evidence = evidence_with("VERIFY-T1")
        ledger = DeviationLedger()
        dev = make_dev()
        verdict, trace = Arbiter(spec).rule(dev, evidence)
        ledger.file(dev, verdict, trace)
        gateway = ToolGateway(spec, ledger, evidence)
        ctx = {}
        gateway.wrap("t", lambda c: None)(ctx)
        assert "DEV-T1" in ctx["pending_deviations"]       # 下游工具调用强制看到未决偏离

    def test_handover_challenge_reverts(self):
        """质疑 → 重新仲裁 → REVERT 的路径（模拟证据过期后被下游质疑）。"""
        spec = make_spec()
        ledger = DeviationLedger()
        # 账本里先入账一条 approve（当时有证据），但交接时用的证据层已不含该记录
        stale_evidence = EvidenceLog()
        dev = make_dev()
        ledger.file(dev, Verdict.APPROVE, "当时核验通过")
        handover = Handover(ledger, Arbiter(spec), stale_evidence)
        handover.receive("downstream", {"DEV-T1": "challenge"})
        assert ledger.pending()[0]["status"] == "challenged_and_escalated_by_downstream"

    def test_handover_challenge_reverts_on_global_scope(self):
        """质疑 → 重新仲裁 → REVERT 的路径（影响非局部时自动回退）。"""
        spec = make_spec()
        evidence = evidence_with("VERIFY-T1")
        ledger = DeviationLedger()
        ledger.file(make_dev(scope="global"), Verdict.APPROVE, "误入账：影响其实是全局")
        handover = Handover(ledger, Arbiter(spec), evidence)
        handover.receive("downstream", {"DEV-T1": "challenge"})
        # 回退的偏离退出交接清单（下游不再看到），但账本仍可审计到全过程
        assert ledger.pending() == []
        assert ledger.get("DEV-T1")["status"] == "challenged_and_reverted_by_downstream"
