"""
progress.py —— 进度感知仲裁器（v0.7.0 第六个组件）
=================================================
动机（来自 v0.6.1 实测）：fail-closed 把违规率压到 0%，但代价是把陷阱任务的
**完成率压到 0%**——被阻塞是当前唯一的失效模式。文章的 StateQGP 类结构针对的
正是这个平衡点：区分「真的走不通」与「正常流程摩擦」，把后者转成可执行的重试，
而不是立即升级人工。

本组件不做「进度自评」（那等于把漂移面上移一层，违反元原则），而是把进度降维成
**证据查账**：每条硬约束的授权工具在证据层里被调用过几次？

判定（三分类，零 LLM 判断）：
  1. 还有未尝试的授权工具        → REVERT（先走那条路，不许申报替代方案）
  2. 授权工具已试但次数不足配额    → REVERT（先重试，摩擦不是死路）
  3. 配额耗尽 / 未声明工具绑定     → 退回 fail-closed：ESCALATE 交人工

未声明 allowed_tools 的需求保持 v0.6 语义（触及硬约束一律 ESCALATE）——没有
「授权路径」的证据时，不假设存在路径。
"""

from __future__ import annotations

from .goal_harness import (
    Arbiter,
    DeviationApplication,
    EvidenceLog,
    Spec,
    Verdict,
)


class ProgressArbiter(Arbiter):
    """进度感知仲裁：把「是否还有可走的授权路径」变成对证据层的机械查账。"""

    def __init__(self, spec: Spec, min_attempts: int = 2) -> None:
        super().__init__(spec)
        if min_attempts < 1:
            raise ValueError("min_attempts 必须 >= 1")
        self.min_attempts = min_attempts

    # ---- 证据查账接口 ----

    def attempts(self, evidence: EvidenceLog) -> dict[str, int]:
        """每条需求授权工具在证据层里的调用次数（policy_denied 也算一次尝试）。"""
        counts: dict[str, int] = {}
        for kind in ("tool_call", "policy_denied"):
            for rec in evidence.filter(kind):
                tool = rec["payload"].get("tool")
                if tool:
                    counts[tool] = counts.get(tool, 0) + 1
        return counts

    def progress(self, evidence: EvidenceLog) -> dict[str, dict]:
        """每条需求的进度快照：授权工具尝试次数、剩余路径、是否可继续。"""
        counts = self.attempts(evidence)
        out: dict[str, dict] = {}
        for req in self.spec.requirements:
            tried = {t: counts.get(t, 0) for t in req.allowed_tools}
            untried = [t for t, n in tried.items() if n == 0]
            exhausted = bool(req.allowed_tools) and all(
                n >= self.min_attempts for n in tried.values())
            out[req.req_id] = {
                "hard": req.hard,
                "attempts": tried,
                "untried": untried,
                "exhausted": exhausted,
                "can_continue": bool(untried) or not exhausted,
            }
        return out

    # ---- 仲裁 ----

    def rule(self, dev: DeviationApplication,
             evidence: EvidenceLog) -> tuple[Verdict, str]:
        hard = [r for r in dev.req_ids if self.spec.is_hard(r)]
        if not hard:
            return super().rule(dev, evidence)          # 软约束走父类策略分派

        counts = self.attempts(evidence)
        bound = {rid: self.spec.tools_for(rid) for rid in hard}
        if not any(bound.values()):
            # 没声明授权路径 → 无法证明「还有路可走」→ 保持 fail-closed
            return super().rule(dev, evidence)

        untried: dict[str, list[str]] = {}
        under_quota: dict[str, list[str]] = {}
        for rid, tools in bound.items():
            if not tools:
                continue
            never = [t for t in tools if counts.get(t, 0) == 0]
            if never:
                untried[rid] = never
            elif any(counts.get(t, 0) < self.min_attempts for t in tools):
                under_quota[rid] = [f"{t} {counts.get(t, 0)}/{self.min_attempts}"
                                    for t in tools]

        if untried:
            detail = "；".join(f"{rid} → 未尝试 {ts}" for rid, ts in untried.items())
            return Verdict.REVERT, (
                f"硬约束仍有未尝试的授权路径（{detail}）→ 驳回偏离："
                f"先按原约束执行该路径，不得申报替代方案")
        if under_quota:
            detail = "；".join(f"{rid} → {ts}" for rid, ts in under_quota.items())
            return Verdict.REVERT, (
                f"授权路径已尝试但未达重试配额（{detail}）→ 驳回偏离："
                f"摩擦不等于死路，先重试")
        return Verdict.ESCALATE, (
            f"硬约束 {hard} 的授权路径已尝试 {self.min_attempts} 次以上且未成功 → "
            f"双人控制队列，阻塞下游")
