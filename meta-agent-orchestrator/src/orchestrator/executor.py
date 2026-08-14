"""実装ループ（PLAN F-05 / TASK.md Task 9）。

共有ボード（Task 5）のタスクを**依存・優先度順**に実行し、pi（Task 4 のコマンド構築 +
実行）で実装し、**自然言語レポートを回収してオーケストレータへ報告**するループを実装する。

- 実行選択（実行インターフェーサーの pi Tool に対応）:
  - 初回 / 新規: ``run_pi_single``（``pi -p @spec.md "タスク"``）
  - 継続 / follow-up / steering: ``run_pi_continue``（既存セッションへの指示注入）
  - 再実行（失敗時）: ``run_pi_fork``（失敗したセッションを fork して新セッションで再試行）
- コンテクスト肥大防止: レポートが ``compact_threshold`` を超えたら ``run_pi_compact``
  （/compact 相当）で解放
- commit: **タスク完了（DONE）ごとに git commit**（:mod:`orchestrator.git`）
- 状態反映: 共有ボードの transition / タスク別ログ（Task 5）

Manager-only 原則:
    executor 自体は pi への指示と git commit ・ボード/ログ更新のみを行い、
    コード本体の読み書き・検証はすべて pi に委譲する。

冪等性:
    中断された ``running`` タスクは再開時に ``run_pi_continue`` で復元し、
    失敗（``failed``）タスクは fork で再実行する。

テスト容易性のため、pi 実行と git 実行は注入可能な callable に委譲する
（既定は本モジュールの :func:`pi_single` 等 / :mod:`orchestrator.git`）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping

from orchestrator import git as g
from orchestrator.config import OrchestratorConfig
from orchestrator.tools import pi_tools as pt
from orchestrator.workspace import Task, TaskStatus, Workspace

logger = logging.getLogger(__name__)

# 実行ステップの識別子（レポート・ログ用）
STEP_SINGLE = "run_pi_single"
STEP_CONTINUE = "run_pi_continue"
STEP_FORK = "run_pi_fork"
STEP_COMPACT = "compact_context"


# ---------------------------------------------------------------------------
# pi 実行結果
# ---------------------------------------------------------------------------
@dataclass
class PiResult:
    """単一の pi 実行結果。オーケストレータが消費する自然言語レポートを持つ。"""

    returncode: int
    report: str
    session_id: str | None = None
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def __str__(self) -> str:
        return self.report


def _extract_session(result: dict, cmd: list[str]) -> str | None:
    """pi の出力/コマンドから (best-effort) セッション ID を抽出する。"""
    text = " ".join(str(c) for c in cmd) + "\n" + (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
    m = re.search(r"(?:--session|session)[=\s:]+([A-Za-z0-9_.:-]+)", text)
    return m.group(1) if m else None


def _to_result(result: dict, cmd: list[str]) -> PiResult:
    report = pt._format_report(cmd, result)
    return PiResult(
        returncode=result.get("returncode") or 0,
        report=report,
        stdout=result.get("stdout") or "",
        stderr=result.get("stderr") or "",
        session_id=_extract_session(result, cmd),
    )


# ---------------------------------------------------------------------------
# 各実行ステップ（pi コマンド構築 + 実行）。テスト注入可能な薄いラッパー。
# ---------------------------------------------------------------------------
def pi_single(
    config: OrchestratorConfig,
    task: str,
    spec_file: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    runner: Callable | None = None,
) -> PiResult:
    """``pi -p [@spec] "task"`` のワンショット実行（新規タスク）。"""
    cmd = pt.build_single_cmd(config, task, spec_file=spec_file, model=model)
    result = (runner or pt._run_pi)(cmd, cwd=cwd)
    return _to_result(result, cmd)


def pi_continue(
    config: OrchestratorConfig,
    task: str,
    session: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    runner: Callable | None = None,
) -> PiResult:
    """``pi -c "task"`` / ``pi --session <id> "task"`` の継続・指示注入。"""
    cmd = pt.build_continue_cmd(config, task, session=session, model=model)
    result = (runner or pt._run_pi)(cmd, cwd=cwd)
    return _to_result(result, cmd)


def pi_fork(
    config: OrchestratorConfig,
    session: str,
    task: str,
    cwd: str | None = None,
    model: str | None = None,
    runner: Callable | None = None,
) -> PiResult:
    """``pi --fork <id> "task"`` の新セッションでの再実行。"""
    cmd = pt.build_fork_cmd(config, session, task, model=model)
    result = (runner or pt._run_pi)(cmd, cwd=cwd)
    return _to_result(result, cmd)


def pi_compact(
    config: OrchestratorConfig,
    session: str,
    instructions: str = "",
    cwd: str | None = None,
    model: str | None = None,
    runner: Callable | None = None,
) -> PiResult:
    """セッションへコンテクスト圧縮（/compact 相当）の指示を送る。"""
    cmd = pt.build_compact_cmd(config, session, instructions=instructions, model=model)
    result = (runner or pt._run_pi)(cmd, cwd=cwd)
    return _to_result(result, cmd)


# ---------------------------------------------------------------------------
# ループ設定・結果
# ---------------------------------------------------------------------------
@dataclass
class LoopOptions:
    """実装ループの制御オプション。"""

    max_attempts: int = 3               # 成功までに試行する最大回数（1 = 再試行なし）
    commit: bool = True                 # タスク完了ごとに git commit するか
    compact_threshold: int | None = 20000  # レポート文字数が超えたら compact を検討（None=しない）
    repo_dir: str | Path | None = None  # pi 実行 cwd 兼 git 作業ディレクトリ（既定: ワークスペース）
    spec_file: str | Path | None = None  # `@spec.md` として pi へ渡す仕様書コピー
    model: str | None = None            # 実行モデル上書き（既定: config.exec_model）
    steering: Mapping[int, list[str]] | None = None  # task_id -> 進行中 follow-up 指示
    retry_failed: bool = True             # 再起動時に保存済み failed タスクも再試行するか
    task_ids: Iterable[int] | None = None # 指定時は対象タスクだけを処理

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts は1以上で指定してください")
        if self.compact_threshold is not None and self.compact_threshold < 0:
            raise ValueError("compact_threshold は0以上またはNoneで指定してください")


@dataclass
class TaskOutcome:
    """単一タスクの実行結果。"""

    task_id: int
    status: TaskStatus
    attempts: int
    report: str = ""
    session_id: str | None = None
    commits: list[g.GitResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status is TaskStatus.DONE


@dataclass
class LoopResult:
    """実装ループ全体の結果。オーケストレータ（manager）が自然言語で消費する。"""

    outcomes: dict[int, TaskOutcome] = field(default_factory=dict)
    commits: list[g.GitResult] = field(default_factory=list)

    @property
    def done_ids(self) -> list[int]:
        return sorted(i for i, o in self.outcomes.items() if o.ok)

    @property
    def failed_ids(self) -> list[int]:
        return sorted(i for i, o in self.outcomes.items() if not o.ok)

    def summary(self) -> str:
        """オーケストレータ向けの自然言語要約を返す。"""
        total = len(self.outcomes)
        done = len(self.done_ids)
        failed = len(self.failed_ids)
        committed = sum(1 for c in self.commits if c.committed)
        lines = [
            f"実装ループ完了: 処理 {total} タスク, 成功 {done}, 失敗 {failed}, コミット {committed} 件。",
        ]
        for tid in sorted(self.outcomes):
            o = self.outcomes[tid]
            mark = "✅ done" if o.ok else "❌ failed"
            lines.append(
                f"- Task {tid}: {mark}（試行 {o.attempts} 回） "
                f"{'セッション ' + (o.session_id or '?') if o.session_id else ''}".rstrip()
            )
            if o.report:
                first = o.report.splitlines()[0] if o.report.splitlines() else ""
                lines.append(f"    レポート: {first}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# タスクごとの実行
# ---------------------------------------------------------------------------
def _task_instruction(task: Task) -> str:
    """タスクの実行指示（タイトル + 説明）を pi へ渡す形に整形する。"""
    parts: list[str] = [f"タスク {task.id}: {task.title}"]
    if task.description:
        parts.append(task.description.strip())
    return "\n".join(parts).strip()


def _process_task(
    workspace: Workspace,
    config: OrchestratorConfig,
    opts: LoopOptions,
    task: Task,
    repo_dir: str,
    *,
    resume: bool,
    single: Callable,
    contn: Callable,
    fork: Callable,
    compact: Callable,
    git_runner: Callable | None,
    force_fork: bool = False,
) -> TaskOutcome:
    """単一タスクを実行し、状態・ログ・コミットを反映して :class:`TaskOutcome` を返す。"""
    cwd = repo_dir
    model = opts.model
    instruction = _task_instruction(task)

    if resume:
        # 中断されたタスク: 前回セッションを継続
        workspace.log(task.id, f"[executor] 中断タスクを再開します（セッション: {task.session_id}）")
    else:
        workspace.transition(task.id, TaskStatus.RUNNING)
        workspace.log(task.id, "[executor] タスク実行開始")

    session = task.session_id or None
    local_attempts = 0
    final: PiResult | None = None
    outcome_commits: list[g.GitResult] = []

    while local_attempts < opts.max_attempts:
        local_attempts += 1
        workspace.log(task.id, f"[executor] 試行 {local_attempts}/{opts.max_attempts} 開始")

        if local_attempts == 1 and force_fork and session:
            step = STEP_FORK
            result = fork(config, session, instruction, cwd=cwd, model=model)
        elif local_attempts == 1 and resume and session:
            step = STEP_CONTINUE
            result = contn(config, instruction, session=session, cwd=cwd, model=model)
        elif local_attempts == 1:
            step = STEP_SINGLE
            result = single(config, instruction, spec_file=str(opts.spec_file) if opts.spec_file else None, cwd=cwd, model=model)
        else:
            # 再実行: 失敗セッションを fork して新セッションで再試行（セッション無しは単発で再挑戦）
            if session:
                step = STEP_FORK
                result = fork(config, session, instruction, cwd=cwd, model=model)
            else:
                step = STEP_SINGLE
                result = single(config, instruction, spec_file=str(opts.spec_file) if opts.spec_file else None, cwd=cwd, model=model)

        if result.session_id:
            session = result.session_id
            workspace.set_session(task.id, session)
        workspace.log(task.id, f"[executor] {step} 終了: exit={result.returncode}")

        # 進行中指示注入（steering / follow-up）を pi へ送る
        steering_msgs = (opts.steering or {}).get(task.id, [])
        parts = [result.report]
        steering_rc = result.returncode
        for msg in steering_msgs:
            workspace.log(task.id, f"[executor] steering: {msg}")
            sres = contn(config, f"追加指示: {msg}", session=session, cwd=cwd, model=model)
            if sres.session_id:
                session = sres.session_id
                workspace.set_session(task.id, session)
            parts.append(f"\n[追加指示への応答]\n{sres.report}")
            if sres.returncode != 0:
                steering_rc = sres.returncode
                parts.append(f"(追加指示の実行が失敗: exit={sres.returncode})")
        final = result
        if steering_msgs:
            report = "\n".join(parts)
            final = PiResult(returncode=steering_rc, report=report, session_id=session)

        # コンテクスト肥大防止
        if opts.compact_threshold and final and len(final.report) > opts.compact_threshold and session:
            cresult = compact(config, session, instructions="直近のタスクの要点を維持して圧縮", cwd=cwd, model=model)
            workspace.log(task.id, f"[executor] compact 実行（report {len(final.report)} chars）→ exit={cresult.returncode}")
            final.report += f"\n[compact_context] {cresult.report.splitlines()[-1] if cresult.report.splitlines() else cresult.report}"

        if final.ok:
            workspace.log(task.id, "[executor] 成功 → DONE")
            break

        # 失敗: 次の試行へ（fork）
        workspace.log(task.id, f"[executor] 失敗（exit={final.returncode}）→ 再試行/失敗処理")
        if local_attempts >= opts.max_attempts:
            break

    # ---- 結果反映 ----
    report = final.report if final else "(レポートなし)"
    task = workspace.get_task(task.id)  # 最新状態（transition 後）

    if final is not None and final.ok:
        commit_ok = True
        if opts.commit:
            message = f"Task {task.id}: {task.title}"
            commit = g.commit_all(repo_dir, message, runner=git_runner)
            outcome_commits.append(commit)
            commit_ok = commit.ok
            workspace.log(task.id, f"[executor] commit: {message} → ok={commit.ok} committed={commit.committed} hash={commit.hash}")
            if not commit.ok:
                report += f"\n[commit失敗]\n{commit.message}"
                workspace.log(task.id, "[executor] commit に失敗したため DONE に遷移しません")

        if commit_ok:
            workspace.transition(task.id, TaskStatus.DONE)
            workspace.update_task(task.id, report=report, session_id=session)
            workspace.log(task.id, "[executor] タスクを DONE に確定しレポートを保存")
        else:
            workspace.transition(task.id, TaskStatus.FAILED)
            workspace.update_task(task.id, report=report, session_id=session)
            workspace.log(task.id, f"[executor] タスクを FAILED に確定（commit失敗、試行 {local_attempts} 回）")
    else:
        # final は {returncode, report} を持つ。回数上限に達した場合 = 失敗
        workspace.transition(task.id, TaskStatus.FAILED)
        workspace.update_task(task.id, report=report, session_id=session)
        workspace.log(task.id, f"[executor] タスクを FAILED に確定（試行 {local_attempts} 回）")

    task = workspace.get_task(task.id)
    return TaskOutcome(
        task_id=task.id,
        status=task.status,
        attempts=task.attempts,
        report=task.report or "",
        session_id=task.session_id,
        commits=outcome_commits,
    )


# ---------------------------------------------------------------------------
# ループ本体
# ---------------------------------------------------------------------------
def run_loop(
    workspace: Workspace,
    config: OrchestratorConfig,
    options: LoopOptions | None = None,
    *,
    single: Callable | None = None,
    contn: Callable | None = None,
    fork: Callable | None = None,
    compact: Callable | None = None,
    git_runner: Callable | None = None,
) -> LoopResult:
    """共有ボードのタスクを依存・優先度順に実行する実装ループ。

    Args:
        workspace: 共有ボード・状態・ログのワークスペース（Task 5）。
        config: 実行設定（モデル割当等）。
        options: ループ制御オプション。省略時は既定。
        single / contn / fork / compact: pi 実行ステップ（テスト注入用）。
        git_runner: git 実行（テスト注入用）。

    Returns:
        :class:`LoopResult`（各タスクの outcome とコミット、要約）。
    """
    opts = options or LoopOptions()
    single = single or pi_single
    contn = contn or pi_continue
    fork = fork or pi_fork
    compact = compact or pi_compact

    repo_dir = str(opts.repo_dir) if opts.repo_dir else str(workspace.workdir)
    Path(repo_dir).mkdir(parents=True, exist_ok=True)
    if opts.commit:
        g.ensure_git_repo(repo_dir, runner=git_runner)

    outcomes: dict[int, TaskOutcome] = {}
    commits: list[g.GitResult] = []

    selected_ids = set(opts.task_ids) if opts.task_ids is not None else None

    # ---- 中断（running）タスクの再開（冪等性） ----
    interrupted = [
        t for t in workspace.tasks
        if t.status is TaskStatus.RUNNING and (selected_ids is None or t.id in selected_ids)
    ]
    for task in sorted(interrupted, key=lambda t: (t.priority, t.id)):
        outcome = _process_task(
            workspace, config, opts, task, repo_dir,
            resume=True, single=single, contn=contn, fork=fork, compact=compact, git_runner=git_runner,
        )
        outcomes[task.id] = outcome
        commits.extend(outcome.commits)

    # ---- 前回実行で failed のまま保存されたタスクを fork で再試行 ----
    if opts.retry_failed:
        persisted_failed = [
            t for t in workspace.tasks
            if t.status is TaskStatus.FAILED and (selected_ids is None or t.id in selected_ids)
        ]
        for task in sorted(persisted_failed, key=lambda t: (t.priority, t.id)):
            workspace.transition(task.id, TaskStatus.RUNNING)
            outcome = _process_task(
                workspace, config, opts, task, repo_dir,
                resume=True, single=single, contn=contn, fork=fork, compact=compact,
                git_runner=git_runner, force_fork=True,
            )
            outcomes[task.id] = outcome
            commits.extend(outcome.commits)

    # ---- 依存・優先度順の実行 ----
    # 1件完了するたびに ready_tasks() を再計算する。これにより、依存解決で
    # 新しく ready になった高優先度タスクを、低優先度の独立タスクより先に処理できる。
    while True:
        ready = workspace.ready_tasks()
        if selected_ids is not None:
            ready = [t for t in ready if t.id in selected_ids]
        if not ready:
            break
        task = sorted(ready, key=lambda t: (t.priority, t.id))[0]
        if task.id in outcomes:
            # ready_tasks() は通常この分岐に入らない。到達時は状態不整合として停止する。
            raise RuntimeError(f"タスク {task.id} が実装ループで重複処理されました")
        outcome = _process_task(
            workspace, config, opts, task, repo_dir,
            resume=False, single=single, contn=contn, fork=fork, compact=compact, git_runner=git_runner,
        )
        outcomes[task.id] = outcome
        commits.extend(outcome.commits)

    return LoopResult(outcomes=outcomes, commits=commits)
