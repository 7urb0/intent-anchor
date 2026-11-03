"""L4 分析器：按臂聚合 + 配对 bootstrap 置信区间。

用法：python -m eval.analyze eval/results/run-XXX.json
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path


def load(path: str) -> tuple[dict, list[dict]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["meta"], data["results"]


def agg_by_arm(rows: list[dict]) -> dict[str, dict]:
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    out = {}
    for arm, rs in by_arm.items():
        n = len(rs)
        out[arm] = {
            "runs": n,
            "violation_rate": sum(r["violation"] for r in rs) / n,
            "success_rate": sum(r["success"] for r in rs) / n,
            "blocked_rate": sum(r["blocked"] for r in rs) / n,
            "avg_steps": sum(r["steps"] for r in rs) / n,
            "errors": sum(1 for r in rs if r["error"]),
        }
    return out


def task_means(rows: list[dict], arm: str) -> dict[str, float]:
    """每个任务的违规率（对该任务的多次重复取均值）。"""
    bucket: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        if r["arm"] == arm:
            bucket[r["task"]].append(r["violation"])
    return {t: (sum(v) / len(v)) for t, v in bucket.items()}


def paired_bootstrap(rows: list[dict], arm_a: str, arm_b: str,
                     iters: int = 5000) -> dict:
    """配对 bootstrap：同一任务上 (arm_a - arm_b) 的违规率差。

    正值 = arm_a 违规更多（更差）。返回差值均值与 95% CI。
    """
    a, b = task_means(rows, arm_a), task_means(rows, arm_b)
    common = sorted(set(a) & set(b))
    if not common:
        return {"diff": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n": 0}
    diffs = [a[t] - b[t] for t in common]
    mean = sum(diffs) / len(diffs)
    rng = random.Random(42)
    boots = []
    for _ in range(iters):
        sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
        boots.append(sum(sample) / len(sample))
    boots.sort()
    lo = boots[int(0.025 * iters)]
    hi = boots[int(0.975 * iters)]
    return {"diff": mean, "ci_low": lo, "ci_high": hi, "n": len(common)}


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python -m eval.analyze eval/results/run-*.json")
        return 1
    meta, rows = load(sys.argv[1])
    print(f"模型 {meta['model']} ｜ 任务 {meta['tasks']} ｜ 重复 {meta['repeats']} ｜ 臂 {meta['arms']}\n")

    stats = agg_by_arm(rows)
    print(f"{'arm':<18}{'runs':>6}{'违规率':>10}{'成功率':>10}{'阻塞率':>10}{'步数':>8}{'错误':>6}")
    for arm, s in sorted(stats.items(), key=lambda kv: kv[1]["violation_rate"], reverse=True):
        print(f"{arm:<18}{s['runs']:>6}{s['violation_rate']:>10.1%}"
              f"{s['success_rate']:>10.1%}{s['blocked_rate']:>10.1%}"
              f"{s['avg_steps']:>8.1f}{s['errors']:>6}")

    print("\n配对 bootstrap（任务级配对，5000 次重采样，95% CI）：")
    for other in ("gate", "full_no_reinject", "full"):
        if other in stats and "naive" in stats:
            r = paired_bootstrap(rows, other, "naive")
            verdict = ("显著更低" if r["ci_high"] < 0 else
                       "显著更高" if r["ci_low"] > 0 else "无显著差异")
            print(f"  {other:<18} − naive = {r['diff']:+.1%} "
                  f"[{r['ci_low']:+.1%}, {r['ci_high']:+.1%}] → {verdict}（n={r['n']} 任务）")
    if "full" in stats and "gate" in stats:
        r = paired_bootstrap(rows, "gate", "full")
        verdict = ("显著更低" if r["ci_high"] < 0 else
                   "显著更高" if r["ci_low"] > 0 else "无显著差异")
        print(f"  gate - full      = {r['diff']:+.1%} "
              f"[{r['ci_low']:+.1%}, {r['ci_high']:+.1%}] → {r['diff']:+.1%}（fail-closed 的贡献）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
