# -*- coding: utf-8 -*-
"""intent-anchor —— 目标状态作为一等公民的长程 Agent Harness（概念验证原型）。"""

from .goal_harness import (
    Arbiter,
    DeviationApplication,
    DeviationLedger,
    EvidenceLog,
    Handover,
    Requirement,
    Spec,
    ToolGateway,
    Verdict,
    verify_gateway_coverage,
)

__version__ = "0.1.0"

__all__ = [
    "Arbiter",
    "DeviationApplication",
    "DeviationLedger",
    "EvidenceLog",
    "Handover",
    "Requirement",
    "Spec",
    "ToolGateway",
    "Verdict",
    "verify_gateway_coverage",
    "__version__",
]
