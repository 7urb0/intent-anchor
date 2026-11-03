"""
goal_harness.py —— 「目标状态作为一等公民」的 Harness 原型（v2）
====================================================================
对应文章《当完整不再足够：长程Agent的目标重建问题与Harness转向》的五个组件：

  (1) Spec            不可变规格层：硬约束带 ID、只读、带版本与摘要
  (2) ToolGateway     强制接口：工具调用的中间件，查询目标状态是结构副作用
  (3) Arbiter         机械仲裁器：只做成员检查与证据核验，零 LLM 判断
  (4) Handover        交接协议：偏离清单从账本派生，下游必须显式接受/质疑
  (5) EvidenceLog     证据层：append-only 哈希链，不做人工摘要

设计元原则（文章核心论点）：
  任何依赖漂移方自律的地方，都是漏洞；换成确定性机制，它才成立。
  —— 所以本文件里没有一个组件需要模型"自觉"配合。

────────────────────────────────────────────────────────────────
v2 优化（针对 v1 的三处残余漂移面 / 过度设计，全部换成确定性机制）：

  opt-1 仲裁去自评：v1 的 `confidence` 由提交方自评，等于把漂移面
        上移一层。v2 改为 fail-closed：偏离申请必须引用证据层中
        【真实存在】的验证事件（evidence_ref）；引用不到 → 一律
        ESCALATE 交人工，绝不自动放行。「判断」降维成「查账」。
  opt-2 策略分级落地：文章的元决策（短任务批量审查/中长任务逐级
        仲裁）在 v1 只是文档。v2 由 Spec.policy_mode 机械生效——
        short 模式下软约束偏离免证据直接批准（事后批量审查），
        硬约束照常升级；框架开销随任务长度伸缩，短任务不再过度设计。
  opt-3 架构测试：文章断言「没有状态查询记录的工具调用不存在」。
        v1 只有被动记录。v2 提供 verify_gateway_coverage()：逐条
        比对事件流，发现无 goal_query 前置的 tool_call 即报违例，
        让「有网关但被绕过」从行为上不可区分变成测试直接红。

运行环境：纯标准库，Python >= 3.10
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


def _sha(obj: Any) -> str:
    """对任意对象做规范化 JSON 后取 SHA256，用于摘要与哈希链。"""
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


# ============================================================
# (1) 不可变规格层
#     硬约束在这里是「带 ID 的成员检查对象」，不是 prompt 里的一段文字。
#     frozen=True + tuple：构造后任何主体都改写不了。
# ============================================================

@dataclass(frozen=True)
class Requirement:
    req_id: str
    text: str
    hard: bool          # True = 不可协商的硬约束


@dataclass(frozen=True)
class Spec:
    """任务开始时冻结，写入后只读。执行中任何人只能读、不能改。

    构造即校验（机械规则，非约定）：需求非空、ID 唯一、策略模式合法。
    坏的规格应该在任务开始时就失败，而不是漂移到执行中段才暴露。
    """
    requirements: tuple[Requirement, ...]
    policy_version: str                      # 偏离处理策略的版本号（组件 5）
    policy_mode: str = "stepwise"            # short=批量事后审查 / stepwise=逐级仲裁

    def __post_init__(self) -> None:
        if not self.requirements:
            raise ValueError("Spec 至少需要一条需求")
        ids = [r.req_id for r in self.requirements]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"需求 ID 重复: {dupes}")
        if self.policy_mode not in ("short", "stepwise"):
            raise ValueError(f"未知 policy_mode: {self.policy_mode}（可选 short / stepwise）")

    def digest(self) -> str:
        return _sha({"reqs": [r.__dict__ for r in self.requirements],
                     "policy": [self.policy_version, self.policy_mode]})

    def is_hard(self, req_id: str) -> bool:
        return any(r.req_id == req_id and r.hard for r in self.requirements)

    def hard_summary(self) -> str:
        """供周期性重注入用：硬约束的紧凑清单。"""
        return "\n".join(f"[{r.req_id}] {r.text}"
                         for r in self.requirements if r.hard)


# ============================================================
# (5) 证据层：append-only 哈希链
#     从不可变事件流派生，不做人工摘要；篡改任意一条都会断链。
# ============================================================

class EvidenceLog:
    """证据层：线程安全 + 可选流式落盘（v0.4.0）。

    - record() 全程持锁：并发调用时 seq 连续、哈希链不断；
    - 传入 path 即进入流式模式：每条记录原子追加落盘并 flush，
      格式与 save()/load() 的 JSONL 完全一致；
    - load() 重建后可继续追加（_prev 从最后一行恢复）。
    """

    def __init__(self, path: str | None = None) -> None:
        import threading
        self._records: list[dict] = []
        self._prev: str = "GENESIS"
        self._lock = threading.Lock()
        self._path = path
        if path and not os.path.exists(path):
            open(path, "w", encoding="utf-8").close()

    def record(self, kind: str, payload: dict) -> dict:
        with self._lock:
            rec = {"seq": len(self._records), "ts": round(time.time(), 3),
                   "kind": kind, "payload": payload, "prev": self._prev}
            rec["hash"] = _sha(rec)
            self._records.append(rec)
            self._prev = rec["hash"]
            if self._path:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
        return rec

    def verify(self) -> bool:
        """架构测试入口：哈希链完整性校验。"""
        prev = "GENESIS"
        for rec in self._records:
            expected = rec["hash"]
            body = {k: v for k, v in rec.items() if k != "hash"}
            if _sha(body) != expected or rec["prev"] != prev:
                return False
            prev = expected
        return True

    def find_verification(self, vid: str) -> dict | None:
        """查账接口：按 ID 找验证事件。找不到就是找不到，没有灰色地带。"""
        for r in self._records:
            if r["kind"] == "verification" and r["payload"].get("vid") == vid:
                return r
        return None

    def filter(self, kind: str) -> list[dict]:
        return [r for r in self._records if r["kind"] == kind]

    def save(self, path: str) -> None:
        """快照导出为 JSONL（每行一条事件，按 seq 有序）。

        append-only 语义不靠文件系统、靠哈希链保证：导出的快照被改任何
        一个字符，load 回来 verify() 都会 False。并发流式追加未实现（见 README 局限）。
        """
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n" for rec in self._records)

    @classmethod
    def load(cls, path: str) -> EvidenceLog:
        """从 JSONL 快照重建证据层；重建后 verify()/find_verification() 立即可用。"""
        inst = cls()
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                inst._records.append(rec)
                inst._prev = rec["hash"]
        return inst

    def __len__(self) -> int:
        return len(self._records)


# ============================================================
# 偏离申请（结构化，不是散文）
# ============================================================

class Verdict(Enum):
    APPROVE = "approve"      # 有验证证据 / 短任务策略，未触及硬约束
    REVERT = "revert"        # 可自动判定的违规
    ESCALATE = "escalate"    # 触及硬约束 / 无验证证据（fail-closed）→ 人工双人控制


@dataclass(frozen=True)
class DeviationApplication:
    dev_id: str
    req_ids: tuple[str, ...]   # 触及的需求 ID（成员检查的对象）
    reason: str
    scope: str                 # "local" | "global"
    alternative: str
    evidence_ref: str          # 必须指向证据层一条真实存在的验证事件（opt-1）
    #
    # v1→v2 变更说明：删除了 self-assessed `confidence: float`。
    # 自评置信度 = 让可能漂移的一方给自己打分，正是元原则禁止的设计。
    # 「这个替代方案验证过吗」降维成「证据层里有没有那条验证记录」。


# ============================================================
# (3) 机械仲裁器
#     不是新 LLM，不是 Agent：只做三类确定性操作——
#     ① 成员检查（触及了哪些硬约束 ID？）
#     ② 模式分派（policy_mode 决定软约束偏离走哪条路）
#     ③ 查账（evidence_ref 在证据层里存在吗？）
#     输出永远三分类，每条决策附规则轨迹，可审计。fail-closed：
#     任何「查不到 / 说不清」的情形，默认 ESCALATE 而非放行。
# ============================================================

class Arbiter:
    def __init__(self, spec: Spec) -> None:
        self.spec = spec

    def rule(self, dev: DeviationApplication,
             evidence: EvidenceLog) -> tuple[Verdict, str]:
        # ① 成员检查：硬约束永远升级，不看任何其他理由
        touched_hard = [r for r in dev.req_ids if self.spec.is_hard(r)]
        if touched_hard:
            return Verdict.ESCALATE, f"触及硬约束 {touched_hard} → 双人控制队列，阻塞下游"

        # ② 模式分派：短任务策略下，软约束偏离免证据批准（事后批量审查）
        #    ——框架开销随任务长度伸缩（opt-2）
        if self.spec.policy_mode == "short":
            return Verdict.APPROVE, (
                f"策略 {self.spec.policy_version}(short)：软约束偏离 {dev.dev_id} "
                f"入账后延至批量审查，不中断执行")

        # ③ 查账：验证事件必须在证据层真实存在（opt-1）
        if evidence.find_verification(dev.evidence_ref) is None:
            return Verdict.ESCALATE, (
                f"evidence_ref={dev.evidence_ref} 在证据层查无此验证记录 → "
                f"fail-closed 拒绝自动放行，交人工")
        if dev.scope != "local":
            return Verdict.REVERT, "影响范围非局部，超出子任务授权"
        return Verdict.APPROVE, (
            f"验证证据 {dev.evidence_ref} 核验通过 + 影响局部({dev.scope})")


# ============================================================
# 偏离账本：所有偏离的唯一事实来源
#     交接清单从这里「派生」，不手写摘要（对抗继承性漂移）。
# ============================================================

class DeviationLedger:
    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def file(self, dev: DeviationApplication, verdict: Verdict, trace: str) -> dict:
        self._items[dev.dev_id] = {"dev": dev.__dict__, "verdict": verdict.value,
                                   "trace": trace, "status": verdict.value}
        return self._items[dev.dev_id]

    def pending(self) -> list[dict]:
        """进入下游的未决/已批准偏离（REVERT 的不外传——回退即退出交接清单）。"""
        return [v for v in self._items.values() if v["verdict"] in ("approve", "escalate")]

    def get(self, dev_id: str) -> dict | None:
        """按 ID 查任意偏离（含已回退的），供审计与测试断言。"""
        return self._items.get(dev_id)

    def confirm_inherited(self, dev_id: str, by: str) -> None:
        self._items[dev_id]["status"] = f"confirmed_by_{by}"

    def inherit(self, item: dict, origin: str) -> str:
        """把上游账本条目继承进本账本：保留裁决与轨迹，状态标记来源。"""
        dev_id = item["dev"]["dev_id"]
        self._items[dev_id] = {**item, "origin": origin,
                               "status": f"inherited_from_{origin}"}
        return dev_id

    def all_items(self) -> dict[str, dict]:
        """全量条目（含已回退的），供链级审计。"""
        return dict(self._items)

    def discard(self, dev_id: str) -> None:
        """移除条目（仅用于交接被拒时的继承回滚）。"""
        self._items.pop(dev_id, None)


# ============================================================
# (2) 强制接口：工具调用网关
#     关键设计：子 Agent 拿到的不是工具列表，而是被网关包裹后的工具。
#     「查询目标状态」不是自愿调用的 API，而是每次工具调用的结构副作用——
#     不经过网关的工具调用在架构上无法发生。
#     另含周期性约束重注入（Periodic Constraint Injection）。
# ============================================================

class ToolGateway:
    def __init__(self, spec: Spec, ledger: DeviationLedger,
                 evidence: EvidenceLog, reinject_every: int = 3) -> None:
        self.spec = spec
        self.ledger = ledger
        self.evidence = evidence
        self.reinject_every = reinject_every
        self._calls = 0

    def wrap(self, name: str, fn: Callable) -> Callable:
        def wrapped(ctx: dict, *args: Any, **kwargs: Any) -> Any:
            self._calls += 1
            # ---- 结构副作用①：强制查询并注入目标状态 ----
            ctx["goal_digest"] = self.spec.digest()
            ctx["pending_deviations"] = [d["dev"]["dev_id"]
                                         for d in self.ledger.pending()]
            # ---- 结构副作用②：周期性硬约束重注入 ----
            ctx["hard_constraints"] = (self.spec.hard_summary()
                                       if self._calls % self.reinject_every == 0
                                       else ctx.get("hard_constraints", ""))
            self.evidence.record("goal_query", {"tool": name,
                                                "digest": ctx["goal_digest"][:12]})
            # ---- 工具本体执行 ----
            result = fn(ctx, *args, **kwargs)
            self.evidence.record("tool_call", {"tool": name,
                                               "args": [str(a)[:50] for a in args]})
            return result
        return wrapped


# ============================================================
# 架构测试（opt-3）
#     文章断言：「没有状态查询记录的工具调用不存在」。
#     本函数把断言变成可执行检查：事件流中每个 tool_call 的前一条
#     必须是 goal_query；违例非空 = 网关被绕过 = 测试直接红。
# ============================================================

def verify_gateway_coverage(evidence: EvidenceLog) -> list[str]:
    violations: list[str] = []
    prev_kind = None
    for rec in evidence._records:
        if rec["kind"] == "tool_call" and prev_kind != "goal_query":
            violations.append(f"seq={rec['seq']}: tool_call 未由 goal_query 前置（网关被绕过）")
        prev_kind = rec["kind"]
    return violations


# ============================================================
# (4) 交接协议
#     上游产物进入下游时附带偏离清单（从账本派生）；
#     下游不能默认继承——必须逐条显式接受或质疑。
#     质疑触发仲裁，而不是下游自行判断「能不能接受」。
# ============================================================

class Handover:
    def __init__(self, ledger: DeviationLedger, arbiter: Arbiter,
                 evidence: EvidenceLog) -> None:
        self.ledger = ledger
        self.arbiter = arbiter
        self.evidence = evidence

    def manifest(self) -> dict:
        return {"deviations": self.ledger.pending()}

    def receive(self, downstream: str, decisions: dict[str, str]) -> None:
        """decisions: {dev_id: "accept" | "challenge"}，缺项视为未决，交接不完成。"""
        pend = self.ledger.pending()
        missing = [d["dev"]["dev_id"] for d in pend if d["dev"]["dev_id"] not in decisions]
        if missing:
            raise ValueError(f"交接被拒绝：未显式裁决的偏离 {missing}（不允许默认继承）")
        for d in pend:
            did = d["dev"]["dev_id"]
            if decisions[did] == "accept":
                self.ledger.confirm_inherited(did, downstream)
                self.evidence.record("handover_accept", {"dev": did, "by": downstream})
            elif decisions[did] == "challenge":
                # 质疑 → 重新仲裁（本原型中复用原裁决；真实实现应升级队列）
                dev = DeviationApplication(**d["dev"])
                verdict, _trace = self.arbiter.rule(dev, self.evidence)
                self.evidence.record("handover_challenge",
                                     {"dev": did, "by": downstream, "verdict": verdict.value})
                if verdict is Verdict.REVERT:
                    d["verdict"] = Verdict.REVERT.value
                    d["status"] = f"challenged_and_reverted_by_{downstream}"
                elif verdict is Verdict.ESCALATE:
                    # fail-closed：质疑时证据已失效/不复存在 → 升级人工，继续阻塞下游
                    d["verdict"] = Verdict.ESCALATE.value
                    d["status"] = f"challenged_and_escalated_by_{downstream}"
