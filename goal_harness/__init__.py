"""intent-anchor —— 目标状态作为一等公民的长程 Agent Harness（概念验证原型）。"""

from .agent import (
                    AgentResult,
                    AnchoredAgent,
                    ModelClient,
                    ModelReply,
                    OpenAICompatClient,
                    ToolCall,
)
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

__version__ = "0.3.0"

__all__ = [
    "AgentResult",
    "AnchoredAgent",
    "Arbiter",
    "DeviationApplication",
    "DeviationLedger",
    "EvidenceLog",
    "Handover",
    "ModelClient",
    "ModelReply",
    "OpenAICompatClient",
    "Requirement",
    "Spec",
    "ToolCall",
    "ToolGateway",
    "Verdict",
    "__version__",
    "verify_gateway_coverage",
]
