# Changelog

## [0.2.0] - 2026-11-03

### 新增
- `EvidenceLog.save/load`：JSONL 快照落盘，篡改快照 load 后 `verify()` 必为 False
- `Spec` 构造即校验：需求非空、ID 唯一、policy_mode 合法；`DeviationLedger.get()` 审计接口
- `examples/bench.py`：网关开销基准（净开销约 18 µs/次，本地参考值）
- 质量工程：pytest-cov（覆盖率 99%）+ ruff（零告警）

### 修复
- `Handover.receive` 质疑分支补全 fail-closed 语义：质疑时证据已失效 → `challenged_and_escalated`（继续阻塞下游），不再悬在半空

### 测试
- 15 → 27 个行为契约测试：新增 Spec 校验、快照往返/防篡改、混合约束升级、质疑回退/升级、网关注入未决偏离等场景

## [0.1.0] - 2026-11-03

首个公开版本（概念验证原型）。

### 组件
- `Spec`：不可变规格层（硬约束带 ID、只读、版本化、摘要）
- `ToolGateway`：工具调用强制网关（目标状态注入为结构副作用 + 周期性硬约束重注入）
- `Arbiter`：机械仲裁器（成员检查 + 证据查账 + 策略分派，fail-closed，零 LLM 判断）
- `DeviationLedger` / `Handover`：偏离账本与显式交接协议（拒绝默认继承）
- `EvidenceLog`：哈希链证据层（防篡改）+ `verify_gateway_coverage` 架构测试

### 设计沿革（v1 原型 → v0.1.0 的三处确定性化改造）
1. **删除自评置信度**：`confidence: float` 由提交方自评，是残余漂移面；改为 `evidence_ref` 查账，查无此记录一律 ESCALATE（fail-closed）
2. **策略分级机械生效**：`policy_mode=short` 下软约束偏离免证据批量入账，框架开销随任务长度伸缩
3. **架构断言可执行化**：新增 `verify_gateway_coverage()`，「无目标状态查询的工具调用」从行为不可区分变成测试直接红

[0.1.0]: https://github.com/7urb0/intent-anchor/releases/tag/v0.1.0
