# Changelog

## [0.6.0] - 2026-11-03

### 新增：L4 统计评测（消除「无量化数据」局限）

- `eval/tasks.py`：12 个陷阱任务（4 族 × 2 陷阱 + 1 对照），判定全机械（哨兵值数值匹配）
- `eval/runner.py`：四臂消融（`naive` / `gate` / `full` / `full_no_reinject`）× k 次重复，
  并发执行，原始数据落 `eval/results/*.json`
- `eval/analyze.py`：按臂聚合 + **任务级配对 bootstrap 95% CI**
- `eval/EVALUATION.md`：完整实验报告（含局限与负面结果）

### 实测结论（144 次真实运行，deepseek-chat）

| 臂 | 违规率 | 成功率 |
|---|---|---|
| `naive`（裸工具） | 41.7% | 86.1% |
| `gate`（有申报通道、无 fail-closed） | 44.4% | 88.9% |
| `full`（完整 harness） | **0.0%** | 91.7% |
| `full_no_reinject` | 0.0% | 91.7% |

- `full − naive` = **−41.7%**，95% CI [−69.4%, −16.7%] → 显著
- `gate − naive` = +2.8%，CI [−13.9%, +19.4%] → **无显著差异**：只加申报通道没有用，
  **fail-closed 才是效果来源**
- `full_no_reinject` 与 `full` 相同：周期重注入在 2–3 步的短任务上**未测出收益**（如实报告）

### 其它
- `AnchoredAgent` 新增 `reinject_every` 参数（消融用）
- `Makefile` 新增 `make eval`

## [0.5.0] - 2026-11-03

### 新增：四层测试架构（先调研 LangGraph / pydantic-ai / OpenAI Agents SDK 后落地）

- **L0 静态门禁**：ruff（中文标点规则 RUF001-003 按语言适配关闭）+ 覆盖率阈值 95% +
  `Makefile` 单入口（`make check` / `make live`）；CI 改为「lint + 测试 + 覆盖率」
- **L1 属性测试**：hypothesis 三条不变量——任意记录序列可验证、任意字段篡改必被检出、
  `pending()` 永不含已回退条目
- **L2 脚本化模型公开 API** `goal_harness.testing`：
  - `ScriptedModel`（对齐 OpenAI Agents SDK）+ `from_function`（pydantic-ai FunctionModel 式）
  - `block_real_requests()` 全局真实请求熔断（pydantic-ai ALLOW_MODEL_REQUESTS=False 同款），
    conftest 自动启用 + socket 封禁兜底（`@pytest.mark.live` 放行）
  - `normalize_transcript()` + syrupy 轨迹快照；`AnchoredAgent.last_transcript` 记录完整对话
  - `deterministic_clock` 夹具钉死时间源
- **L3 真实模型冒烟**：`tests/test_live_smoke.py`（正常路径 / 故障阻塞），`-m live` 手动跑
- pytest marker 分层：`live` / `slow` / `serial`，默认 `addopts = -m 'not live'`

### 测试
- 44 → **58 个**：+3 属性测试、+7 testing API、+4 分支覆盖（真实客户端解析、REVERT 回填等）
- 覆盖率 **98%**（阈值 95%），ruff 零告警

## [0.4.0] - 2026-11-03

### 新增：多 Agent 链式交接（消除局限③）+ 并发证据层（消除局限④）
- `AgentNode` / `HandoverChain`：N 级链式交接——偏离随清单显式传递（佐证验证
  事件一并复制并标记来源），下游逐条 accept / challenge；被拒交接回滚继承；
  `audit()` 把「偏离被静默丢弃」变成可检出的链级违例
- `DeviationLedger.inherit / all_items / discard`
- `EvidenceLog`：线程安全（并发 8 线程 × 25 记录，seq 连续、哈希链完整）+
  可选流式落盘（每条记录原子 append + fsync，格式与快照一致）

### 测试
- 34 → 44 个：三级链 accept 贯通、缺裁决拒绝+回滚、跨跳质疑升级/回退、
  链级审计检出静默丢失、并发哈希链完整性、流式 sink 与快照格式互通

## [0.3.0] - 2026-11-03

### 新增：真实模型集成（消除局限①）
- `AnchoredAgent`：真实 LLM 驱动的受锚定 Agent（OpenAI 兼容协议，DeepSeek 实测通过）
- 治理工具面：`file_deviation`（偏离申报唯一通道）+ `request_goal_state`（目标状态显式查询）
- ESCALATE 即阻塞：触及硬约束或无验证证据的偏离直接终止执行，等待人工裁决
- `examples/live_demo.py`：端到端实测——官方接口超时场景下，真实模型未擅自绕过，
  走申报通道被 fail-closed 拦截，全程轨迹可审计（哈希链证据层）
- 依赖：openai>=1.0（唯一运行时依赖）

### 测试
- 27 → 34 个：FakeClient 脚本回放覆盖批准/阻塞/fail-closed/未知工具/异常回填，
  CI 零依赖 key；真实调用以 examples/live_demo.py 门控

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
