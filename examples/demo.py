# -*- coding: utf-8 -*-
"""
demo.py —— 演示 goal_harness v2 拦截漂移的对照组实验
======================================================
场景复刻文章开头的例子：
  子 Agent 执行中遇到工具异常，想用「近似替代方案」绕过约束。

跑五条路径：
  A. naive          ：没有 harness → 妥协悄悄发生，下游无感知继承（漂移成功）
  B. 有验证证据      ：软约束偏离 + 引用真实验证记录 → 批准入账，显式交接
  C. 触及硬约束      ：无论理由多充分 → 升级人工队列，阻塞下游
  D. 自称有把握但无证据（v2 新增）：fail-closed → 不自动放行
                     ——对应 v1 的残余漂移面：自评 confidence 已被删除
  E. 架构测试        ：网关覆盖检查 + 哈希链防篡改演示
"""

from goal_harness import (Arbiter, DeviationApplication, DeviationLedger,
                          EvidenceLog, Handover, Requirement, Spec,
                          ToolGateway, Verdict, verify_gateway_coverage)

# ---------- 组装不可变规格层 ----------
SPEC = Spec(
    requirements=(
        Requirement("REQ-001", "必须使用真实数据源 A 的原始数据", hard=True),
        Requirement("REQ-002", "输出必须为 JSON 格式", hard=False),
        Requirement("REQ-003", "图表需支持缩放交互", hard=False),
    ),
    policy_version="policy-v2",
    policy_mode="stepwise",          # 中长任务：逐级仲裁；换成 "short" 看路径 F
)

print("=" * 62)
print("路径 A：naive —— 没有 harness，漂移如何悄悄发生")
print("=" * 62)

# 子 Agent 遇到工具异常，自作主张换了数据源，没有任何申报动作。
upstream_output_A = {"data_source": "缓存近似数据 B", "format": "JSON", "note": "原始数据源超时，改用近似"}
downstream_output_A = {**upstream_output_A, "chart": "已基于近似数据绘制"}
print(f"上游产物: {upstream_output_A}")
print(f"下游继承: {downstream_output_A}")
print(">>> 没有任何报错、没有任何记录——协调漂移+继承性漂移无声完成\n")


def run_harness(dev: DeviationApplication, label: str, note: str = "",
                has_evidence: bool = True) -> None:
    print("=" * 62)
    print(f"路径 {label}：harness 接入" + (f"（{note}）" if note else ""))
    print("=" * 62)
    evidence = EvidenceLog()
    ledger = DeviationLedger()
    arbiter = Arbiter(SPEC)
    gateway = ToolGateway(SPEC, ledger, evidence, reinject_every=2)

    def fetch_data(ctx, source):
        return {"data": f"from {source}", "ctx_digest": ctx["goal_digest"][:8]}

    def render_chart(ctx, data):
        has_reinject = bool(ctx.get("hard_constraints"))
        print(f"  [render] 本次是否触发硬约束重注入: {has_reinject}")
        return {"chart": f"chart of {data['data']}"}

    tools = {"fetch_data": gateway.wrap("fetch_data", fetch_data),
             "render_chart": gateway.wrap("render_chart", render_chart)}

    ctx = {}
    data = tools["fetch_data"](ctx, "数据源A")
    tools["render_chart"](ctx, data)

    # 验证事件：由验证器（测试/校验脚本）写入证据层，带 ID 可被仲裁器查账。
    # 注意：验证事件只有验证器能写——偏离提交方无法伪造一条不存在的记录。
    if has_evidence:
        evidence.record("verification", {"vid": dev.evidence_ref,
                                         "check": "替代方案回归测试通过"})
        print(f"  验证事件已入证据层: {dev.evidence_ref}")
    else:
        print(f"  ⚠️ 提交方引用了证据 {dev.evidence_ref}，但证据层中查无此记录")

    print(f"  提交偏离申请: {dev.dev_id} 触及 {dev.req_ids} 理由: {dev.reason}")
    verdict, trace = arbiter.rule(dev, evidence)
    ledger.file(dev, verdict, trace)
    print(f"  仲裁结果: {verdict.value} —— {trace}")
    if verdict is Verdict.ESCALATE:
        print("  >>> 已进入人工双人控制队列，下游被阻塞，漂移止步于此")

    handover = Handover(ledger, arbiter, evidence)
    manifest = handover.manifest()
    print(f"  交接清单(派生自账本): {[d['dev']['dev_id'] for d in manifest['deviations']]}")
    try:
        decisions = {d["dev"]["dev_id"]: ("accept" if verdict is Verdict.APPROVE else "challenge")
                     for d in manifest["deviations"]}
        handover.receive("downstream_agent", decisions)
        print("  交接完成：下游显式裁决，无默认继承")
    except ValueError as e:
        print(f"  交接被拒: {e}")

    print(f"  证据链完整性: {evidence.verify()}  |  事件总数: {len(evidence)}")
    print(f"  网关覆盖检查: {'通过' if not verify_gateway_coverage(evidence) else verify_gateway_coverage(evidence)}")
    print()


if __name__ == "__main__":
    # B：可协商偏离 + 有验证证据 → 批准
    ev = EvidenceLog()
    run_harness(DeviationApplication(
        dev_id="DEV-001", req_ids=("REQ-002",),
        reason="渲染库对严格 JSON 有兼容问题，改用 JSON5 超集",
        scope="local", alternative="JSON5 序列化",
        evidence_ref="VERIFY-001"), "B")

    # C：硬约束偏离 → 无条件升级
    run_harness(DeviationApplication(
        dev_id="DEV-002", req_ids=("REQ-001",),
        reason="数据源 A 超时 30s，想改用近似缓存 B",
        scope="local", alternative="缓存近似数据 B",
        evidence_ref="VERIFY-002"), "C")

    # D（v2 新增）：自称有把握、但证据层查无验证记录 → fail-closed 拒绝自动放行
    run_harness(DeviationApplication(
        dev_id="DEV-003", req_ids=("REQ-002",),
        reason="我很确定这个替代方案没问题（v1 里 confidence=0.99 就能过）",
        scope="local", alternative="未经验证的替代实现",
        evidence_ref="VERIFY-不存在的编号"), "D", note="fail-closed 演示",
        has_evidence=False)

    # F：同一偏离，把策略切成 short → 免证据批量入账，不中断执行
    print("=" * 62)
    print("路径 F：策略切换演示 —— Spec.policy_mode='short'")
    print("=" * 62)
    short_spec = Spec(SPEC.requirements, policy_version="policy-v2", policy_mode="short")
    ev_short = EvidenceLog()
    ledger_short = DeviationLedger()
    arbiter_short = Arbiter(short_spec)
    dev_f = DeviationApplication(
        dev_id="DEV-004", req_ids=("REQ-002",),
        reason="短任务，不值得为软约束偏离中断",
        scope="local", alternative="JSON5 序列化",
        evidence_ref="")   # 没有证据
    verdict_f, trace_f = arbiter_short.rule(dev_f, ev_short)
    ledger_short.file(dev_f, verdict_f, trace_f)
    print(f"  仲裁结果: {verdict_f.value} —— {trace_f}")
    print("  >>> 同一份代码，策略分级让框架开销随任务长度伸缩（opt-2）")

    # E：哈希链防篡改演示
    print()
    print("=" * 62)
    print("路径 E：证据层防篡改")
    print("=" * 62)
    ev_tamper = EvidenceLog()
    ev_tamper.record("verification", {"vid": "V1", "check": "ok"})
    ev_tamper.record("verification", {"vid": "V2", "check": "ok"})
    print(f"  篡改前完整性: {ev_tamper.verify()}")
    ev_tamper._records[0]["payload"]["check"] = "被悄悄改掉"   # 模拟内部篡改
    print(f"  篡改后完整性: {ev_tamper.verify()}  ← 任何静默修改都会断链")
