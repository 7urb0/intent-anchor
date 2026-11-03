"""L4 评测执行器：四臂消融 × 任务集 × k 次重复。

臂：
  naive        裸工具、无 spec 注入、无治理工具（基线：模型自然会怎么做）
  gate         完整 harness，但仲裁器放行一切（有门无锁）
  full         完整 harness（不可变规格 + 网关 + fail-closed 仲裁 + 重注入）
  full_no_reinject  完整 harness 但关闭周期性硬约束重注入

用法：
  uv run python -m eval.runner --repeats 3 --workers 4
  uv run python -m eval.runner --repeats 1 --arms naive,full   # 冒烟
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from eval.tasks import TASKS, Task
from goal_harness import (
    AgentResult,
    AnchoredAgent,
    Arbiter,
    DeviationApplication,
    EvidenceLog,
    OpenAICompatClient,
    Requirement,
    Spec,
    Verdict,
)
from goal_harness.agent import ModelClient, ModelReply

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def load_env() -> None:
    if not ENV_PATH.exists():
        return
    with open(ENV_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


def make_client() -> OpenAICompatClient:
    return OpenAICompatClient(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com"),
        model=os.environ.get("OPENAI_MODEL", "deepseek-chat"),
    )


def make_spec(task: Task) -> Spec:
    return Spec(tuple(Requirement(rid, text, hard) for rid, text, hard in task.requirements),
                f"eval-{task.family}")


class PermissiveArbiter(Arbiter):
    """弱仲裁臂：一律放行（模拟「有申报通道但没有 fail-closed」）。"""

    def rule(self, dev: DeviationApplication, evidence: EvidenceLog) -> tuple[Verdict, str]:
        return Verdict.APPROVE, "permissive arm: 无条件放行"


class NaiveAgent(ModelClient):
    """基线臂：裸工具、无治理，直接走模型 loop。"""

    def __init__(self, client: ModelClient, task: Task, max_steps: int = 6) -> None:
        self._client = client
        self._task = task
        self._max_steps = max_steps
        self.blocked = False

    def chat(self, messages, tools_schema):  # pragma: no cover - 直接透传
        return self._client.chat(messages, tools_schema)

    def run(self) -> AgentResult:
        schema = [{"type": "function",
                   "function": {"name": n, "description": d, "parameters": p}}
                  for n, _, d, p in self._task.tools]
        tools = {n: fn for n, fn, _, _ in self._task.tools}
        messages: list[dict] = [
            {"role": "system", "content": "你是一个执行任务的助手。"},
            {"role": "user", "content": self._task.prompt},
        ]
        final_text: str | None = None
        steps = 0
        steps = 0
        for step in range(1, self._max_steps + 1):
            steps = step
            reply: ModelReply = self._client.chat(messages, schema)
            if not reply.tool_calls:
                final_text = reply.content
                break
            messages.append({"role": "assistant", "content": reply.content or "",
                             "tool_calls": [{"id": tc.call_id, "type": "function",
                                             "function": {"name": tc.name,
                                                          "arguments": json.dumps(tc.arguments,
                                                                                  ensure_ascii=False)}}
                                            for tc in reply.tool_calls]})
            for tc in reply.tool_calls:
                fn = tools.get(tc.name)
                if fn is None:
                    result = {"error": f"未知工具 {tc.name}"}
                else:
                    try:
                        result = fn({}, **tc.arguments)
                    except Exception as exc:
                        result = {"error": f"{type(exc).__name__}: {exc}"}
                messages.append({"role": "tool", "tool_call_id": tc.call_id,
                                 "content": json.dumps(result, ensure_ascii=False)})
        return AgentResult(final_text=final_text, steps=steps,
                           blocked=False, escalated=[])


def run_once(task: Task, arm: str, client: OpenAICompatClient) -> dict:
    started = time.time()
    try:
        if arm == "naive":
            result = NaiveAgent(client, task).run()
        else:
            arbiter = PermissiveArbiter(make_spec(task)) if arm == "gate" else None
            agent = AnchoredAgent(
                make_spec(task), EvidenceLog(), client, max_steps=6,
                arbiter=arbiter,
                reinject_every=10**9 if arm == "full_no_reinject" else 3,
            )
            for name, fn, desc, params in task.tools:
                agent.register(name, fn, desc, params)
            result = agent.run(task.prompt)
        grade = task.grade(result.final_text, result.blocked)
        return {
            "task": task.task_id, "family": task.family, "arm": arm,
            "violation": grade["violation"], "success": grade["success"],
            "blocked": result.blocked, "steps": result.steps,
            "escalated": len(result.escalated),
            "answer_head": (result.final_text or "")[:80],
            "seconds": round(time.time() - started, 1), "error": None,
        }
    except Exception as exc:
        return {"task": task.task_id, "family": task.family, "arm": arm,
                "violation": False, "success": False, "blocked": False,
                "steps": 0, "escalated": 0, "answer_head": "",
                "seconds": round(time.time() - started, 1),
                "error": f"{type(exc).__name__}: {exc}"}


ARMS = ("naive", "gate", "full", "full_no_reinject")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--out", default="eval/results")
    args = ap.parse_args()

    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        print("缺少 OPENAI_API_KEY")
        return 1
    client = make_client()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    jobs = [(t, arm, rep) for t in TASKS for arm in arms for rep in range(args.repeats)]
    print(f"任务 {len(TASKS)} × 臂 {len(arms)} × 重复 {args.repeats} = {len(jobs)} 次运行")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_once, t, arm, client): (t.task_id, arm, rep)
                   for t, arm, rep in jobs}
        for done, fut in enumerate(as_completed(futures), start=1):
            tid, arm, rep = futures[fut]
            row = fut.result()
            row["repeat"] = rep
            results.append(row)
            print(f"[{done}/{len(jobs)}] {tid:3} {arm:16} rep{rep} "
                  f"viol={row['violation']} succ={row['success']} "
                  f"blocked={row['blocked']} err={row['error']}")

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = outdir / f"run-{stamp}.json"
    payload = {"meta": {"repeats": args.repeats, "arms": arms,
                        "model": os.environ.get("OPENAI_MODEL", "deepseek-chat"),
                        "tasks": len(TASKS)},
               "results": results}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n原始数据: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
