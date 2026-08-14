"""共有ボードへ実装計画を作成する軽量プランナー (F-04)。

オーケストレーション層はコードベースを直接解析しない。ここで扱うのは仕様書と
pi が返した現状レポートだけであり、コードへの接触は後続の pi 実行へ委譲する。
計画は ``Workspace`` に永続化されるため、再起動時に同じタスクを重複作成しない。

高度なタスク分解を行う場合は、``task_factory`` に CrewAI manager / pi のレポートを
渡して差し替えられる。デフォルトはパイプラインを停止させない決定的なフォールバック
計画である。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from orchestrator.context import ContextResult
from orchestrator.workspace import Task, Workspace


@dataclass(frozen=True)
class PlanResult:
    """計画作成の結果。"""

    tasks: tuple[Task, ...]
    reused: bool = False
    report: str = ""

    def summary(self) -> str:
        action = "既存計画を再利用" if self.reused else "計画を作成"
        return f"{action}: {len(self.tasks)} タスク。" + (f" {self.report}" if self.report else "")


def _default_description(context: ContextResult) -> str:
    """pi に渡す実装コンテクストの場所だけを記述する。"""
    lines = [
        "仕様書とコンテクストを読み、実装・テスト・README更新まで完了してください。",
        f"仕様書: {context.spec_copy_path}",
    ]
    if context.initial_prompt_path:
        lines.append(f"初期コンテクスト: {context.initial_prompt_path}")
    if context.survey_report_path:
        lines.append(f"既存案件の現状レポート: {context.survey_report_path}")
    lines += [
        "コードの読み書き・検証は pi の実行セッション内で行い、完了時に自然言語レポートを返してください。",
    ]
    return "\n".join(lines)


def create_plan(
    workspace: Workspace,
    context: ContextResult,
    *,
    task_factory: Callable[[Workspace, ContextResult], Iterable[tuple[str, str, int, list[int]]]] | None = None,
) -> PlanResult:
    """共有ボードへ計画を作成する。

    既にタスクが存在する場合はそれをそのまま返す。これが再起動時の冪等性を担保
    する。外部プランナーを注入する場合の tuple は ``(title, description, priority,
    dependencies)`` である。
    """
    if workspace.tasks:
        return PlanResult(tuple(workspace.tasks), reused=True, report="state.json の計画を復元しました")

    factory = task_factory
    definitions = list(factory(workspace, context)) if factory else [
        ("仕様書に基づく実装", _default_description(context), 1, []),
    ]
    if not definitions:
        raise ValueError("実行可能なタスクが計画されていません")

    created: list[Task] = []
    for title, description, priority, dependencies in definitions:
        created.append(
            workspace.add_task(
                title,
                description=description,
                priority=priority,
                dependencies=dependencies,
            )
        )
    return PlanResult(tuple(created), report="仕様書起点の初期計画を共有ボードへ保存しました")


__all__ = ["PlanResult", "create_plan"]
