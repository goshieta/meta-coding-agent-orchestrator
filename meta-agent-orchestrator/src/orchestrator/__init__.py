"""Meta-Agent Orchestrator.

仕様書をワンライナーで渡すだけで、CrewAI 製マネジメント層が pi の
セッション・コンテクスト・品質を自律管理し、最終成果物を GitHub へ
納品する開発オーケストレーション中間層。
"""

from orchestrator.workspace import Task, TaskStatus, Workspace
from orchestrator.executor import LoopOptions, LoopResult, TaskOutcome, run_loop
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
