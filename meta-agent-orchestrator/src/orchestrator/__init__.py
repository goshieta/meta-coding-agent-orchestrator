"""Meta-Agent Orchestrator.

仕様書をワンライナーで渡すだけで、CrewAI 製マネジメント層が pi の
セッション・コンテクスト・品質を自律管理し、最終成果物を GitHub へ
納品する開発オーケストレーション中間層。
"""

from orchestrator.workspace import Task, TaskStatus, Workspace
from orchestrator.executor import LoopOptions, LoopResult, TaskOutcome, run_loop
from orchestrator.human_gate import (
    HumanAnswer,
    HumanGate,
    HumanGateError,
    HumanGateResult,
    HumanQuestion,
    force_stop,
    resume,
)
from orchestrator.deliver import (
    DeliveryError,
    DeliveryOptions,
    DeliveryResult,
    GitHubClient,
    deliver,
    run_delivery,
)
from orchestrator.planner import PlanResult, create_plan
from orchestrator.pipeline import PipelineResult, run_pipeline as run_integrated_pipeline
from orchestrator.qa import (
    DEFAULT_QA_CRITERIA,
    QAIssue,
    QAInspection,
    QAOptions,
    QAReport,
    build_qa_prompt,
    parse_qa_report,
    qa_single,
    run_qa,
    run_quality_gate,
)
__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Workspace",
    "Task",
    "TaskStatus",
    "LoopOptions",
    "LoopResult",
    "TaskOutcome",
    "run_loop",
    "PlanResult",
    "create_plan",
    "PipelineResult",
    "run_integrated_pipeline",
    "HumanAnswer",
    "HumanGate",
    "HumanGateError",
    "HumanGateResult",
    "HumanQuestion",
    "force_stop",
    "resume",
    "DeliveryError",
    "DeliveryOptions",
    "DeliveryResult",
    "GitHubClient",
    "deliver",
    "run_delivery",
    "DEFAULT_QA_CRITERIA",
    "QAIssue",
    "QAInspection",
    "QAOptions",
    "QAReport",
    "build_qa_prompt",
    "parse_qa_report",
    "qa_single",
    "run_qa",
    "run_quality_gate",
]
