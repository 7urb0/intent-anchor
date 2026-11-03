"""
chain.py —— 多 Agent 链式交接（v0.4.0，消除局限③）
====================================================
核心不变量：**偏离在链上必须显式传递，无处藏身。**

  - 每个节点（AgentNode）持有自己的账本与证据层；
  - transfer()：上游 pending 偏离全部继承到下游账本（打上来源标记），
    下游必须逐条显式 accept / challenge——缺一条即交接失败；
  - challenge 交给下游仲裁器机械裁决（fail-closed 语义与单跳一致）；
  - audit()：链级审计——上游存在、下游账本却查无此偏离 = 静默丢失，
    从「不可见」变成「可检出的违例」。
"""

from __future__ import annotations

from .goal_harness import (
    Arbiter,
    DeviationApplication,
    DeviationLedger,
    EvidenceLog,
    Spec,
    Verdict,
)


class AgentNode:
    """链上的一个节点：自己的名字、账本、仲裁器与证据层。"""

    def __init__(self, name: str, spec: Spec, evidence: EvidenceLog | None = None) -> None:
        if not name:
            raise ValueError("节点必须有名字（审计报告用）")
        self.name = name
        self.evidence = evidence or EvidenceLog()
        self.ledger = DeviationLedger()
        self.arbiter = Arbiter(spec)


class HandoverChain:
    def __init__(self, nodes: list[AgentNode]) -> None:
        if len(nodes) < 2:
            raise ValueError("链至少需要两个节点")
        self.nodes = nodes

    def transfer(self, i: int, decisions: dict[str, str]) -> dict:
        """第 i → i+1 个节点之间的交接。decisions: {dev_id: accept|challenge}。"""
        up, down = self.nodes[i], self.nodes[i + 1]
        manifest = up.ledger.pending()

        # ① 先全部继承（打来源标记）+ 复制佐证验证事件，再要求显式裁决
        inherited: list[str] = []
        for item in manifest:
            vid = item["dev"].get("evidence_ref")
            if vid:
                src = up.evidence.find_verification(vid)
                # 证据随交接走（记录来源）：否则下游 challenge 时一律 fail-closed
                if src and down.evidence.find_verification(vid) is None:
                    down.evidence.record("verification", {
                        "vid": vid, "check": src["payload"].get("check", ""),
                        "inherited_from": up.name})
            dev_id = down.ledger.inherit(item, origin=up.name)
            down.evidence.record("inherited_deviation",
                                 {"dev": dev_id, "from": up.name, "by": down.name})
            inherited.append(dev_id)

        # ② 缺裁决 = 交接失败：回滚继承，下游不持有任何偏离
        undecided = [d for d in inherited if d not in decisions]
        if undecided:
            for d in inherited:
                down.ledger.discard(d)
            raise ValueError(
                f"交接被拒绝：{down.name} 未显式裁决 {undecided}（不允许默认继承）")

        # ③ 逐条执行裁决：accept 确认 / challenge 交下游仲裁器
        for item in manifest:
            did = item["dev"]["dev_id"]
            choice = decisions[did]
            if choice == "accept":
                down.ledger.confirm_inherited(did, down.name)
                down.evidence.record("handover_accept",
                                     {"dev": did, "by": down.name, "from": up.name})
            elif choice == "challenge":
                dev = DeviationApplication(**item["dev"])
                verdict, _trace = down.arbiter.rule(dev, down.evidence)
                down.evidence.record("handover_challenge",
                                     {"dev": did, "by": down.name,
                                      "from": up.name, "verdict": verdict.value})
                stored = down.ledger.get(did)
                assert stored is not None
                if verdict is Verdict.REVERT:
                    stored["verdict"] = Verdict.REVERT.value
                    stored["status"] = f"challenged_and_reverted_by_{down.name}"
                elif verdict is Verdict.ESCALATE:
                    stored["verdict"] = Verdict.ESCALATE.value
                    stored["status"] = f"challenged_and_escalated_by_{down.name}"
            else:
                raise ValueError(f"非法裁决 {choice!r}（只接受 accept / challenge）")
        return {"transferred": inherited, "from": up.name, "to": down.name}

    def audit(self) -> list[str]:
        """链级审计：上游存在的偏离，下游账本必须能查到（任何状态）。

        「下游没记录」意味着偏离被静默丢弃——审计违例。
        """
        violations: list[str] = []
        for up, down in zip(self.nodes, self.nodes[1:]):
            for dev_id, item in up.ledger.all_items().items():
                if down.ledger.get(dev_id) is None:
                    violations.append(
                        f"{up.name} → {down.name}: 偏离 {dev_id}"
                        f"（{item['verdict']}）在下游账本中静默丢失")
        return violations
