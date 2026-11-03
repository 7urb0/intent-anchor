"""bench.py —— ToolGateway 单次调用开销基准（本地参考值，非正式基准报告）。

方法：同一直函数分别直调与经网关调 N 次，取均值差。
网关每次调用多做：2 次 dict 赋值 + 2 次 evidence.record（各一次 SHA256）。
"""

import time

from goal_harness import DeviationLedger, EvidenceLog, Requirement, Spec, ToolGateway


def run(n: int = 20_000) -> None:
    spec = Spec((Requirement("R1", "示例约束", False),), "bench-v1")
    evidence = EvidenceLog()
    gateway = ToolGateway(spec, DeviationLedger(), evidence, reinject_every=10**9)
    ctx: dict = {}

    def noop(ctx: dict) -> None:
        return None

    wrapped = gateway.wrap("noop", noop)

    t0 = time.perf_counter()
    for _ in range(n):
        noop(ctx)
    direct = (time.perf_counter() - t0) / n

    t0 = time.perf_counter()
    for _ in range(n):
        wrapped(ctx)
    via_gateway = (time.perf_counter() - t0) / n

    overhead_us = (via_gateway - direct) * 1e6
    print(f"直调均值:     {direct * 1e6:8.2f} µs/次")
    print(f"经网关均值:   {via_gateway * 1e6:8.2f} µs/次")
    print(f"网关净开销:   {overhead_us:8.2f} µs/次  （主要来自每调用 2 次 SHA256）")
    print(f"事件规模:     {len(evidence)} 条证据记录")


if __name__ == "__main__":
    run()
