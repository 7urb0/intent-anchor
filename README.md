# intent-anchor

> 把「目标状态」从 prompt 里的一段文字，变成执行中强制维持的一等公民结构。

**English summary**: `intent-anchor` is a proof-of-concept harness for long-horizon LLM agents. Instead of trusting the model to "stay on task", it makes goal adherence **mechanical**: hard constraints live in an immutable spec (not a system prompt), every tool call is forced through a gateway that re-injects goal state, deviations require evidence-backed approval by a deterministic arbiter (fail-closed), and downstream agents must explicitly accept or challenge upstream deviations — silent inheritance is impossible. Zero dependencies, ~350 lines, 27 tests, 99% coverage. Status: **POC, not production-ready** — see [当前状态与局限](#当前状态与局限).

---

## 这是什么

一个**概念验证原型**（POC，非生产框架），验证一个来自工程实践的论点：

> 长程 Agent 任务中，即使提示词完整、信息物理可达，执行轨迹仍会系统性偏离原始意图。
> 原因不是信息丢了（传递保真），而是约束在每一步决策中不再被激活（重建保真）。
> **任何依赖漂移方自律的地方，都是漏洞；换成确定性机制，它才成立。**

因此本库不做「更聪明的 Agent」，而是给 Agent 套上**不依赖自觉的目标维持结构**。

## 四种漂移 → 五个组件

| 漂移形态 | 表现 | 对应组件 |
|---|---|---|
| 约束漂移 | 硬约束效力随轨迹展开衰减 | `Spec` 不可变规格层：硬约束是带 ID 的成员检查对象，只读、版本化 |
| 语义漂移 | 每步都有理由，交付才发现偏了 | `DeviationLedger`：任何方向偏移必须显式申报入账 |
| 协调漂移 | 各子 Agent 局部合理、全局失配 | `ToolGateway`：目标状态查询是每次工具调用的结构副作用，非自愿 API |
| 继承性漂移 | 下游默认继承上游已放宽的基线 | `Handover`：交接清单从账本派生，下游必须逐条显式接受或质疑 |

辅助机制：`Arbiter` 机械仲裁器（成员检查 + 证据查账 + 策略分派，**零 LLM 判断**，fail-closed）、`EvidenceLog` 哈希链证据层（防篡改）、`verify_gateway_coverage` 架构测试（「没查询过目标状态的工具调用」直接测试红）。

## 快速开始

```bash
git clone https://github.com/7urb0/intent-anchor.git
cd intent-anchor
uv sync                # 零运行时依赖；uv 自动建环境
uv run pytest          # 27 个行为契约测试（覆盖率 99%）
uv run python examples/demo.py
```

`demo.py` 复刻一个典型事故现场：子 Agent 工具异常后想用近似替代方案绕过约束——

- **naive 路径**：妥协无声发生，下游无感知继承（漂移完成）
- **harness 路径**：偏离显式申报 → 仲裁（批准 / 升级 / fail-closed 拒绝）→ 交接显式裁决

## 设计细节

**fail-closed 仲裁**：偏离申请必须引用证据层中真实存在的验证事件（`evidence_ref`）。查无此记录 → 一律 `ESCALATE` 交人工，绝不自动放行。不存在「自评置信度 0.99 就能过」——让可能漂移的一方给自己打分，正是要消除的漂移面。

**策略分级**：`Spec.policy_mode` 机械生效。`stepwise`（中长任务）软约束偏离逐级查账；`short`（短任务）免证据批量入账、不中断执行。框架开销随任务长度伸缩，硬约束在任何策略下都无条件升级。

**周期性重注入**：网关每 N 次工具调用强制把完整硬约束清单注回上下文（Periodic Constraint Injection）。

## 当前状态与局限

**这是概念验证，不是生产框架。** 以下边界如实声明：

1. **未与真实 LLM Agent 集成**——`examples/demo.py` 中工具为模拟函数。与 LangGraph 等框架的真实集成在路线图中
2. **无量化数据**——「能拦住漂移」目前是逻辑推演 + 行为测试，没有真实任务上的约束违反率对照
3. **单/双 Agent 假设**——`Handover` 是两方交接的最小实现，多 Agent 拓扑未验证
4. **EvidenceLog 支持快照落盘**（save/load，篡改快照过不了哈希链校验），但尚未做并发安全的流式追加
5. **API 不稳定**——0.x 版本，接口随时会变
6. **设计动机来自对公开文章观点的工程化转译**，本文档不引用任何未核实的实验数字

## Roadmap

- [ ] **v0.2**：与 LangGraph 真实集成（`ToolGateway` 包住 tool node），至少一个真实长任务案例
- [ ] **v0.3**：消融实验——「有/无偏离账本与强制重注入」对约束违反率的影响，数据回填本文档
- [x] **v0.2**：EvidenceLog 快照落盘（save/load）+ Spec 构造校验 + 覆盖率 99%
- [ ] **v0.x+**：并发安全的流式证据追加、多 Agent 拓扑验证

## 测试与质量

- 27 个行为契约测试，核心模块覆盖率 **99%**（`uv run pytest --cov=goal_harness`）
- `ruff check` 零告警
- 网关开销基准（`examples/bench.py`，本机参考值）：经网关调用净开销约 **18 µs/次**，主要来自每调用 2 次 SHA256 哈希——相对一次 LLM 工具调用（百毫秒级）可忽略

## 致谢

问题定义与架构思路受文章《当完整不再足够：长程Agent的目标重建问题与Harness转向》（2026-11）启发，以及对 Anthropic《Building Effective Agents》中 workflow/agent 边界论述的回应。本项目为其工程化转译与独立实现。

## License

[MIT](LICENSE)
