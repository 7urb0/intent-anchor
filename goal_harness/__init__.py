"""intent-anchor —— 目标状态作为一等公民的长程 Agent Harness（概念验证原型）。"""

from .agent import (
    AgentResult,
    AnchoredAgent,
    ModelClient,
    ModelReply,
    OpenAICompatClient,
    ToolCall,
)
from .chain import AgentNode, HandoverChain
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
from .progress import ProgressArbiter

__version__ = "0.7.0"

__all__ = [
    "AgentNode",
    "AgentResult",
    "AnchoredAgent",
    "Arbiter",
    "DeviationApplication",
    "DeviationLedger",
    "EvidenceLog",
    "Handover",
    "HandoverChain",
    "ModelClient",
    "ModelReply",
    "OpenAICompatClient",
    "ProgressArbiter",
    "Requirement",
    "Spec",
    "ToolCall",
    "ToolGateway",
    "Verdict",
    "__version__",
    "verify_gateway_coverage",
]
