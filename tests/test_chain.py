"""多 Agent 链式交接 + 并发证据层 测试（v0.4.0）。"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from goal_harness import (
    AgentNode,
    DeviationApplication,
    EvidenceLog,
    HandoverChain,
    Requirement,
    Spec,
    Verdict,
)


def make_spec() -> Spec:
    return Spec(
        requirements=(
            Requirement("REQ-001", "股价必须来自官方实时接口", hard=True),
            Requirement("REQ-002", "结论必须为中文", hard=False),
        ),
        policy_version="policy-chain-v1",
        policy_mode="stepwise",
    )


def make_node(name: str) -> AgentNode:
    return AgentNode(name, make_spec())


def file_approved(node: AgentNode, dev_id: str = "DEV-001",
                  req_ids: tuple[str, ...] = ("REQ-002",),
                  scope: str = "local") -> DeviationApplication:
    """在节点上入账一条已批准的偏离（证据齐备）。"""
    node.evidence.record("verification", {"vid": f"V-{dev_id}", "check": "回归通过"})
    dev = DeviationApplication(dev_id=dev_id, req_ids=req_ids, reason="测试偏离",
                               scope=scope, alternative="替代方案",
                               evidence_ref=f"V-{dev_id}")
    verdict, trace = node.arbiter.rule(dev, node.evidence)
    node.ledger.file(dev, verdict, trace)
    return dev


# ---------- 链式交接 ----------

class TestHandoverChain:
    def _three_nodes(self) -> list[AgentNode]:
        return [make_node("agentA"), make_node("agentB"), make_node("agentC")]

    def test_chain_requires_two_nodes(self):
        with pytest.raises(ValueError, match="至少需要两个节点"):
            HandoverChain([make_node("solo")])

    def test_accept_across_three_hops(self):
        a, b, c = self._three_nodes()
        file_approved(a, "DEV-A1")
        chain = HandoverChain([a, b, c])

        chain.transfer(0, {"DEV-A1": "accept"})
        assert b.ledger.get("DEV-A1")["status"] == "confirmed_by_agentB"

        chain.transfer(1, {"DEV-A1": "accept"})
        assert c.ledger.get("DEV-A1")["status"] == "confirmed_by_agentC"
        assert c.ledger.get("DEV-A1")["origin"] == "agentB"   # 保留来源标记
        assert chain.audit() == []                            # 链级审计干净

    def test_missing_decision_rejects_transfer(self):
        a, b, _ = self._three_nodes()
        file_approved(a)
        chain = HandoverChain([a, b, make_node("agentC")])
        with pytest.raises(ValueError, match="未显式裁决"):
            chain.transfer(0, {})
        assert b.ledger.pending() == []                       # 交接失败：下游不持有

    def test_challenge_escalates_when_evidence_missing_downstream(self):
        # 上游误判：无验证证据却入账 approve（evidence_ref 悬空）
        a, b, c = self._three_nodes()
        dev = DeviationApplication(dev_id="DEV-A1", req_ids=("REQ-002",),
                                   reason="上游误判", scope="local",
                                   alternative="替代方案", evidence_ref="V-不存在")
        a.ledger.file(dev, Verdict.APPROVE, "上游仲裁失误")
        chain = HandoverChain([a, b, c])
        chain.transfer(0, {"DEV-A1": "challenge"})
        # B 质疑 → 查账发现证据不存在 → fail-closed 升级，继续阻塞 C
        assert b.ledger.get("DEV-A1")["status"] == "challenged_and_escalated_by_agentB"
        assert b.ledger.pending()[0]["dev"]["dev_id"] == "DEV-A1"

    def test_challenge_revert_on_global_scope(self):
        # 模拟上游漏判：入账时按 local 批准，但实际影响是 global
        a, b, _ = self._three_nodes()
        file_approved(a, "DEV-001")                       # 正常批准（local + 有证据）
        stored = a.ledger.get("DEV-001")
        stored["dev"] = {**stored["dev"], "scope": "global"}   # 基线被改（上游失误）
        chain = HandoverChain([a, b, make_node("agentC")])
        chain.transfer(0, {"DEV-001": "challenge"})
        # B 质疑 → 证据随交接有效（副本已入 B 证据层）→ 影响非局部 → 回退
        assert b.ledger.get("DEV-001")["verdict"] == Verdict.REVERT.value
        assert b.ledger.pending() == []                   # 回退项退出向 C 的交接清单

    def test_audit_detects_silent_drop(self):
        a, b, _ = self._three_nodes()
        file_approved(a)
        chain = HandoverChain([a, b, make_node("agentC")])
        # 不做 transfer：偏离在 B 账本中完全不存在
        violations = chain.audit()
        assert len(violations) == 1 and "静默丢失" in violations[0]

    def test_invalid_choice_rejected(self):
        a, b, _ = self._three_nodes()
        file_approved(a)
        chain = HandoverChain([a, b, make_node("agentC")])
        with pytest.raises(ValueError, match="非法裁决"):
            chain.transfer(0, {"DEV-001": "随便看看"})


# ---------- EvidenceLog：并发 + 流式落盘 ----------

class TestConcurrentEvidence:
    def test_parallel_records_keep_chain_intact(self, tmp_path):
        ev = EvidenceLog()
        def write_batch(tid: int) -> None:
            for j in range(25):
                ev.record("tool_call", {"worker": tid, "j": j})
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write_batch, range(8)))
        assert len(ev) == 200
        seqs = sorted(r["seq"] for r in ev._records)
        assert seqs == list(range(200))                       # seq 连续无重无漏
        assert ev.verify() is True                            # 哈希链完整

    def test_streaming_sink_matches_memory(self, tmp_path):
        path = str(tmp_path / "stream.jsonl")
        ev = EvidenceLog(path=path)                           # 流式模式
        ev.record("goal_query", {"tool": "t"})
        ev.record("tool_call", {"tool": "t"})
        assert ev.verify() is True
        reloaded = EvidenceLog.load(path)                     # sink 文件与 save() 同格式
        assert len(reloaded) == 2 and reloaded.verify() is True

    def test_reloaded_log_continues_chain(self, tmp_path):
        path = str(tmp_path / "stream.jsonl")
        ev = EvidenceLog(path=path)
        ev.record("verification", {"vid": "V1", "check": "ok"})
        reloaded = EvidenceLog.load(path)
        reloaded.record("tool_call", {"tool": "t"})           # 续写不重置哈希链
        assert reloaded.verify() is True
