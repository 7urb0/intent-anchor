"""
handover_content.py —— 交接内容层强制执行（v0.8.0）
====================================================
动机（v0.7.1 实测）：三级链上，交接协议 + 机械质疑 + 明确禁令**挡不住**下游把
上游的未批准数值写进交付物（5/5 条链 100% 继承漂移），下游甚至伪造「已获批准」
标签。根因：把「下游读到禁令并遵守」当防线 = 又一处自律 = 漏洞。

本模块把裁决落到**内容层**：未批准来源（下游规格的 denied_tools）产出的数值，
在上游文本交给下游之前被确定性隐去。下游看到的不是警告，而是物理事实——
那个数字不在文本里，想抄也没得抄。

元原则的直接推论：连「下游遵守禁令」也是自律，必须换成确定性机制。
"""

from __future__ import annotations

import contextlib
import re

from .goal_harness import EvidenceLog, Spec

_PLACEHOLDER = "【已隐去：未批准数值】"


def forbidden_values(evidence: EvidenceLog, spec: Spec) -> list[str]:
    """从证据层提取「禁用工具产出」中的数值字面量（含千分位书写形态）。"""
    denied = spec.forbidden_tools()
    if not denied:
        return []
    raws: set[str] = set()
    for rec in evidence.filter("tool_call"):
        if rec["payload"].get("tool") in denied:
            result = rec["payload"].get("result") or ""
            for m in re.finditer(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", result):
                raws.add(m.group())
    out: list[str] = []
    for v in sorted(raws, key=len, reverse=True):     # 长字面量优先，防部分替换
        out.append(v)
        if "," not in v:
            with contextlib.suppress(ValueError):
                out.append(f"{int(v):,}")
    return out


def redact_forbidden(text: str, evidence: EvidenceLog, spec: Spec,
                     placeholder: str = _PLACEHOLDER) -> tuple[str, int]:
    """把文本中「禁用来源的数值」替换为占位符，返回 (新文本, 替换次数)。

    替换动作本身入证据层（content_redacted），可审计：链上任何人都能查到
    「哪次交接隐去了什么」。没有禁用清单 = 原样返回（向后兼容）。
    """
    values = forbidden_values(evidence, spec)
    if not values:
        return text, 0
    out = text
    n = 0
    for v in values:
        if v in out:
            out = out.replace(v, placeholder)
            n += 1
    if n:
        evidence.record("content_redacted",
                        {"values": values[:20], "replacements": n})
    return out, n
