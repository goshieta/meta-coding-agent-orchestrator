"""独立 QA クリティック（PLAN F-06 / TASK.md Task 10）。

実装ループとは別の pi 実行（別セッション・別プロンプト）で成果物を検証し、
判定を共有ボードへ反映する。QA エージェント自身はコードへ直接アクセスせず、
検証とテスト実行は pi へ委譲する。

ワークフロー::

    done -> qa -> accepted
                 -> failed -> Task 9 implementation loop -> done -> qa

QA は既存の実装セッションを継続しない。毎回、独立した ``run_pi_single`` として
起動するため、実装エージェントの自己申告を鵜呑みにせず批判的に検証できる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping

from orchestrator.config import OrchestratorConfig
from orchestrator.executor import LoopOptions, LoopResult, PiResult, pi_single, run_loop
from orchestrator.workspace import Task, TaskStatus, Workspace

# QA エージェントへ必ず渡す検証基準。呼び出し元から追加基準も指定できる。
DEFAULT_QA_CRITERIA = """次の観点を独立に確認してください。
1. 仕様書の要求を満たしているか（機能・入出力・エラー処理）。
2. テストを実行し、成功結果だけでなく不足するテストも確認したか。
3. 実装の可読性、責務分離、保守性に重大な問題がないか。
4. セキュリティ、秘密情報の混入、既存機能の回帰がないか。

コードの修正は行わず、検証とレポートだけを実施してください。
最後に必ず次の形式で判定してください。
判定: PASS または FAIL
問題:
- TASK_ID: <タスクID> | SEVERITY: high/medium/low | TITLE: <短い題名> | DETAIL: <具体的な根拠>
問題が無い場合は「問題: なし」と書いてください。
"""

_VERDICT_RE = re.compile(r"(?:判定|verdict)\s*[:：]\s*(PASS|FAIL|合格|不合格)", re.IGNORECASE)
_ISSUE_RE = re.compile(
    r"TASK_ID\s*[:：]\s*(\d+)\s*\|\s*"
    r"SEVERITY\s*[:：]\s*(high|medium|low)\s*\|\s*"
    r"TITLE\s*[:：]\s*([^|\n]+?)\s*\|\s*"
    r"DETAIL\s*[:：]\s*(.+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QAIssue:
    """QA が検出した再実装対象の問題。"""

    task_id: int
    severity: str
    title: str
    detail: str

    def as_text(self) -> str:
        return f"[{self.severity}] Task {self.task_id}: {self.title} — {self.detail}"


@dataclass
class QAInspection:
    """1タスクに対する pi 検証結果。"""

    task_id: int
    passed: bool
    report: str
    issues: list[QAIssue] = field(default_factory=list)
    returncode: int = 0
    session_id: str | None = None


@dataclass
class QAOptions:
    """QAゲートと再実装の制御設定。"""

    repo_dir: str | Path | None = None
    spec_file: str | Path | None = None
    model: str | None = None
    criteria: str = DEFAULT_QA_CRITERIA
    max_rounds: int = 2
    rework_attempts: int = 3
    commit: bool = True
    retry_failed: bool = False
    task_ids: Iterable[int] | None = None

    def __post_init__(self) -> None:
        if self.max_rounds < 1:
            raise ValueError("max_rounds は1以上で指定してください")
        if self.rework_attempts < 1:
            raise ValueError("rework_attempts は1以上で指定してください")


@dataclass
class QAReport:
    """QAゲート全体の判定と、各検証・再実装のレポート。"""

    inspections: list[QAInspection] = field(default_factory=list)
    rework_results: list[LoopResult] = field(default_factory=list)
    rounds: int = 0

    def _latest(self) -> dict[int, QAInspection]:
        """再検証を含む場合に、タスクごとの最新判定だけを返す。"""
        latest: dict[int, QAInspection] = {}
        for inspection in self.inspections:
            latest[inspection.task_id] = inspection
        return latest

    @property
    def passed(self) -> bool:
        """全対象タスクの最新判定が合格し、未解決問題がない場合だけ合格とする。"""
        latest = self._latest()
        return bool(latest) and all(i.passed for i in latest.values()) and not self.unresolved_issues

    @property
    def unresolved_issues(self) -> list[QAIssue]:
        return [issue for inspection in self._latest().values() if not inspection.passed for issue in inspection.issues]

    @property
    def accepted_ids(self) -> list[int]:
        return sorted(i.task_id for i in self._latest().values() if i.passed)

    @property
    def failed_ids(self) -> list[int]:
        return sorted({issue.task_id for issue in self.unresolved_issues})

    def summary(self) -> str:
        status = "合格" if self.passed else "不合格"
        lines = [
            f"QAゲート: {status}（ラウンド {self.rounds}, 検証 {len(self.inspections)} 件, 問題 {len(self.unresolved_issues)} 件）"
        ]
        for inspection in self.inspections:
            mark = "PASS" if inspection.passed else "FAIL"
            lines.append(f"- Task {inspection.task_id}: {mark}")
            for issue in inspection.issues:
                lines.append(f"  - {issue.as_text()}")
        if self.rework_results:
            lines.append(f"- 実装ループへの再送: {len(self.rework_results)} 回")
        return "\n".join(lines)


def build_qa_prompt(task: Task, options: QAOptions) -> str:
    """独立QA piへ渡す検証プロンプトを構築する。"""
    return (
        "あなたは独立したQAクリティックです。実装エージェントの報告を信頼せず、"
        "対象リポジトリを自分で検証してください。コードの読み書きとテスト実行はpiを介して行います。\n\n"
        f"対象タスク: Task {task.id}: {task.title}\n"
        f"タスク説明:\n{task.description or '(説明なし)'}\n\n"
        f"検証基準:\n{options.criteria}\n"
        "検証結果は自然言語で根拠を示し、指定した判定形式を最後に必ず出力してください。"
    )


def parse_qa_report(report: str, task_id: int) -> tuple[bool, list[QAIssue]]:
    """piのQAレポートを保守的に判定し、問題リストを抽出する。

    判定マーカーがない場合は、QAの安全側に倒して不合格とする。問題のタスクIDが
    不正な場合や省略された場合は、検証対象タスクへ紐付ける。
    """
    match = _VERDICT_RE.search(report)
    issues: list[QAIssue] = []
    for issue_match in _ISSUE_RE.finditer(report):
        issue_task_id = int(issue_match.group(1))
        issues.append(
            QAIssue(
                task_id=issue_task_id if issue_task_id > 0 else task_id,
                severity=issue_match.group(2).lower(),
                title=issue_match.group(3).strip(),
                detail=issue_match.group(4).strip(),
            )
        )

    if match is None:
        return False, [QAIssue(task_id, "high", "QA判定が不明", "PASS/FAIL形式の判定がありません。")]

    verdict = match.group(1).upper()
    passed = verdict in {"PASS", "合格"}
    if not passed and not issues:
        issues.append(QAIssue(task_id, "high", "QA検証不合格", report.strip() or "QAがFAILを返しました。"))
    if passed and issues:
        # PASSと問題リストが矛盾する場合は、問題を優先する。
        passed = False
    if passed and re.search(r"問題\s*[:：](?!\s*(?:なし|none|無))", report, re.IGNORECASE):
        issues.append(QAIssue(task_id, "high", "問題リストとPASS判定が矛盾", "問題欄が空欄または未解決のままPASSになっています。"))
        passed = False
    return passed, issues


def qa_single(
    config: OrchestratorConfig,
    prompt: str,
    *,
    spec_file: str | Path | None = None,
    cwd: str | Path | None = None,
    model: str | None = None,
) -> PiResult:
    """独立QAセッションを起動する薄いラッパー。セッション継続は行わない。"""
    return pi_single(
        config,
        prompt,
        spec_file=str(spec_file) if spec_file else None,
        cwd=str(cwd) if cwd else None,
        model=model or config.qa_model,
    )


def _eligible_tasks(workspace: Workspace, options: QAOptions) -> list[Task]:
    selected = set(options.task_ids) if options.task_ids is not None else None
    return [
        task for task in workspace.tasks
        if task.status is TaskStatus.DONE and (selected is None or task.id in selected)
    ]


def _issue_instructions(issues: list[QAIssue]) -> Mapping[int, list[str]]:
    steering: dict[int, list[str]] = {}
    for issue in issues:
        steering.setdefault(issue.task_id, []).append(
            f"QA修正要求 [{issue.severity}]: {issue.title}\n{issue.detail}"
        )
    return steering


def run_qa(
    workspace: Workspace,
    config: OrchestratorConfig,
    options: QAOptions | None = None,
    *,
    verify: Callable | None = None,
    single: Callable | None = None,
    contn: Callable | None = None,
    fork: Callable | None = None,
    compact: Callable | None = None,
    git_runner: Callable | None = None,
) -> QAReport:
    """独立QAを実行し、不合格タスクをTask9の実装ループへ再送する。

    ``verify`` はテスト用に注入可能で、通常は ``qa_single``。再実装は
    :func:`orchestrator.executor.run_loop` に委譲するため、Task9と同じ状態・ログ・
    commitポリシーを利用する。QA検証のデフォルトは ``config.qa_model`` を使い、
    実装セッションのIDを継続しない。
    """
    opts = options or QAOptions()
    verify = verify or qa_single
    report = QAReport()
    repo_dir = str(opts.repo_dir) if opts.repo_dir else str(workspace.workdir)

    for round_number in range(1, opts.max_rounds + 1):
        report.rounds = round_number
        tasks = _eligible_tasks(workspace, opts)
        if not tasks:
            break
        round_issues: list[QAIssue] = []

        for task in tasks:
            workspace.transition(task.id, TaskStatus.QA)
            workspace.log(task.id, f"[qa] 独立QAを開始（ラウンド {round_number}）")
            prompt = build_qa_prompt(task, opts)
            result = verify(
                config,
                prompt,
                spec_file=opts.spec_file,
                cwd=repo_dir,
                model=opts.model or config.qa_model,
            )
            passed, issues = parse_qa_report(result.report, task.id) if result.returncode == 0 else (
                False,
                [QAIssue(task.id, "high", "QAプロセス失敗", f"exit={result.returncode}: {result.report}")],
            )
            # QAレポートが存在しないタスクを指した場合は、検証対象へ安全に紐付ける。
            normalized_issues: list[QAIssue] = []
            for issue in issues:
                try:
                    workspace.get_task(issue.task_id)
                except KeyError:
                    issue = QAIssue(task.id, issue.severity, issue.title, issue.detail)
                normalized_issues.append(issue)
            issues = normalized_issues
            inspection = QAInspection(
                task_id=task.id,
                passed=passed and not issues,
                report=result.report,
                issues=issues,
                returncode=result.returncode,
                session_id=result.session_id,
            )
            passed = inspection.passed
            report.inspections.append(inspection)
            task_report = f"[QA ラウンド {round_number}] {'PASS' if passed else 'FAIL'}\n{result.report}"
            if issues:
                task_report += "\n問題:\n" + "\n".join(f"- {issue.as_text()}" for issue in issues)
            workspace.update_task(task.id, report=task_report)
            workspace.log(task.id, f"[qa] 判定={'PASS' if passed else 'FAIL'} 問題数={len(issues)}")

            if passed:
                workspace.transition(task.id, TaskStatus.ACCEPTED)
                workspace.log(task.id, "[qa] QA合格 → ACCEPTED")
            else:
                round_issues.extend(issues)
                workspace.transition(task.id, TaskStatus.FAILED)
                workspace.log(task.id, "[qa] QA不合格 → Task 9実装ループへ再送")

        if not round_issues or round_number >= opts.max_rounds:
            break

        issue_task_ids = sorted({issue.task_id for issue in round_issues})
        rework_options = LoopOptions(
            max_attempts=opts.rework_attempts,
            commit=opts.commit,
            repo_dir=opts.repo_dir,
            spec_file=opts.spec_file,
            model=None,
            steering=_issue_instructions(round_issues),
            retry_failed=True,
            task_ids=issue_task_ids,
        )
        rework = run_loop(
            workspace,
            config,
            rework_options,
            single=single,
            contn=contn,
            fork=fork,
            compact=compact,
            git_runner=git_runner,
        )
        report.rework_results.append(rework)
        for task_id in rework.failed_ids:
            workspace.log(task_id, "[qa] 再実装後も失敗したためQAゲートを停止")

    return report


# 読みやすい別名。呼び出し側は run_quality_gate としても利用できる。
run_quality_gate = run_qa

__all__ = [
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
