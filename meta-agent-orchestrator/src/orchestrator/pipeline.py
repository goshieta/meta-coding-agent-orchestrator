"""Task 13 の統合パイプライン。

CLI/Docker の入口から、コンテクスト構築、計画、実装、独立 QA、納品までを
一つの再開可能な流れとして接続する。各段階のコード接触は pi に委譲し、状態・
ログ・セッションは ``Workspace`` に保存する。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from orchestrator.agents import build_crew
from orchestrator.config import OrchestratorConfig
from orchestrator.context import ContextResult, build_context
from orchestrator.deliver import DeliveryOptions, DeliveryResult, deliver
from orchestrator.executor import LoopOptions, LoopResult, run_loop
from orchestrator.human_gate import HumanGate
from orchestrator.planner import PlanResult, create_plan
from orchestrator.qa import QAOptions, QAReport, run_qa
from orchestrator.workspace import Workspace


@dataclass
class PipelineResult:
    """統合パイプラインの各段階の結果。"""

    context: ContextResult
    plan: PlanResult
    implementation: LoopResult
    qa: QAReport
    delivery: DeliveryResult | None = None

    @property
    def ok(self) -> bool:
        return bool(self.qa.passed and self.delivery and self.delivery.ok)

    def summary(self) -> str:
        lines = [self.context.describe(), self.plan.summary(), self.implementation.summary(), self.qa.summary()]
        if self.delivery:
            lines.append(self.delivery.summary())
        return "\n".join(lines)


def run_pipeline(
    spec_path: str | Path,
    workspace: Workspace,
    config: OrchestratorConfig,
    *,
    repo_path: str | Path | None = None,
    log_dir: str | Path | None = None,
    max_attempts: int = 3,
    qa_rounds: int = 2,
    approval: Callable[[], bool] | None = None,
    delivery_options: DeliveryOptions | None = None,
    # 外部サービスを起動しない統合テスト用の注入点
    context_runner: Callable | None = None,
    pi_single: Callable | None = None,
    pi_continue: Callable | None = None,
    pi_fork: Callable | None = None,
    pi_compact: Callable | None = None,
    git_runner: Callable | None = None,
    qa_verify: Callable | None = None,
) -> PipelineResult:
    """全フェーズを接続して実行する。

    ``approval`` を省略した場合は ``HumanGate`` の対話承認を使う。Docker の
    detached 実行で stdin が閉じている場合、HumanGate は状態を保存して安全停止する。
    """
    workdir = workspace.workdir
    context = build_context(spec_path, workspace, config, repo_path=repo_path, run_pi=context_runner)

    # CrewAI の manager/delegatee 構成を生成して設定の注入と Manager-only 制約を
    # 起動時に検証する。実際のコード接触は下の executor/QA の pi Tool に委譲する。
    build_crew(config, repo_path=str(repo_path) if repo_path else None, include_survey=repo_path is not None)
    plan = create_plan(workspace, context)

    target = Path(repo_path) if repo_path else workdir
    spec_for_pi = context.spec_copy_path
    implementation = run_loop(
        workspace,
        config,
        LoopOptions(
            max_attempts=max_attempts,
            repo_dir=target,
            spec_file=spec_for_pi,
            commit=True,
        ),
        single=pi_single,
        contn=pi_continue,
        fork=pi_fork,
        compact=pi_compact,
        git_runner=git_runner,
    )

    qa = run_qa(
        workspace,
        config,
        QAOptions(
            repo_dir=target,
            spec_file=spec_for_pi,
            max_rounds=qa_rounds,
            commit=True,
        ),
        verify=qa_verify,
        single=pi_single,
        contn=pi_continue,
        fork=pi_fork,
        compact=pi_compact,
        git_runner=git_runner,
    )

    delivery: DeliveryResult | None = None
    if qa.passed:
        gate = HumanGate(workspace)
        options = delivery_options or DeliveryOptions(repo_dir=target)
        # 明示指定がない場合、既存案件の実装対象を push する。新規案件では
        # Workspace がそのまま成果物リポジトリになる。
        if options.repo_dir is None:
            options.repo_dir = target
        delivery = deliver(
            workspace,
            config,
            options,
            approval=approval or (lambda: gate.request_approval()),
        )
    else:
        workspace.log(0, "[pipeline] QA不合格のため納品を保留しました") if False else None

    return PipelineResult(context, plan, implementation, qa, delivery)


__all__ = ["PipelineResult", "run_pipeline"]
