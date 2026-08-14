"""Task 10: 独立QAクリティックのテスト。"""

from __future__ import annotations

from orchestrator.config import OrchestratorConfig
from orchestrator.executor import PiResult, STEP_CONTINUE, STEP_FORK, STEP_SINGLE
from orchestrator.qa import (
    QAOptions,
    build_qa_prompt,
    parse_qa_report,
    run_qa,
)
from orchestrator.workspace import TaskStatus, Workspace


def done_task(ws: Workspace, title: str, description: str = ""):
    task = ws.add_task(title, description=description)
    ws.transition(task.id, TaskStatus.RUNNING)
    ws.transition(task.id, TaskStatus.DONE)
    return task


def ok(report: str, session: str = "qa-session") -> PiResult:
    return PiResult(returncode=0, report=report, session_id=session)


def test_parse_pass_and_structured_issues():
    passed, issues = parse_qa_report("検証完了\n判定: PASS\n問題: なし", 1)
    assert passed is True
    assert issues == []

    passed, issues = parse_qa_report(
        "判定: FAIL\n問題:\n- TASK_ID: 1 | SEVERITY: high | TITLE: テスト不足 | DETAIL: 回帰テストがありません。",
        1,
    )
    assert passed is False
    assert issues[0].task_id == 1
    assert issues[0].severity == "high"
    assert "回帰テスト" in issues[0].detail


def test_missing_verdict_is_conservatively_failed():
    passed, issues = parse_qa_report("テストは実行しましたが判定を書き忘れました。", 4)
    assert passed is False
    assert issues[0].task_id == 4
    assert "不明" in issues[0].title


def test_qa_prompt_is_independent_and_contains_criteria(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    task = ws.add_task("機能実装", description="仕様を実装する")
    prompt = build_qa_prompt(task, QAOptions())
    assert "独立したQAクリティック" in prompt
    assert "コードの修正は行わず" in prompt
    assert "テストを実行" in prompt
    assert "Task 1" in prompt


def test_pass_moves_done_task_to_accepted(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    task = done_task(ws, "合格する実装")
    calls: list[tuple[str, str | None]] = []

    def verify(config, prompt, **kwargs):
        calls.append((STEP_SINGLE, kwargs.get("model")))
        assert "独立したQAクリティック" in prompt
        return ok("検証結果\nテスト: 成功\n判定: PASS\n問題: なし")

    report = run_qa(ws, OrchestratorConfig(), QAOptions(repo_dir=tmp_path / "work"), verify=verify)

    assert report.passed is True
    assert report.accepted_ids == [task.id]
    assert report.failed_ids == []
    assert ws.get_task(task.id).status is TaskStatus.ACCEPTED
    assert calls == [(STEP_SINGLE, OrchestratorConfig().qa_model)]
    assert "QA" in ws.get_task(task.id).report
    assert "ACCEPTED" in ws.read_log(task.id)


def test_fail_reworks_through_task9_then_rechecks(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    task = done_task(ws, "QAで修正する実装")
    qa_reports = iter([
        "判定: FAIL\n問題:\n- TASK_ID: 1 | SEVERITY: high | TITLE: 例外処理不足 | DETAIL: 入力エラーを処理してください。",
        "判定: PASS\n問題: なし",
    ])
    implementation_steps: list[str] = []

    def verify(config, prompt, **kwargs):
        assert "検証基準" in prompt
        return ok(next(qa_reports), session="independent-qa")

    def single(config, task_text, **kwargs):
        implementation_steps.append(STEP_SINGLE)
        return ok("修正しました", session="implementation-session")

    def contn(config, task_text, **kwargs):
        implementation_steps.append(STEP_CONTINUE)
        assert "QA修正要求" in task_text
        return ok("フォローアップ完了", session="implementation-session")

    report = run_qa(
        ws,
        OrchestratorConfig(),
        QAOptions(repo_dir=tmp_path / "work", max_rounds=2, rework_attempts=1, commit=False),
        verify=verify,
        single=single,
        contn=contn,
    )

    assert report.passed is True
    assert report.rounds == 2
    assert report.accepted_ids == [task.id]
    assert report.failed_ids == []
    assert report.rework_results
    assert implementation_steps == [STEP_SINGLE, STEP_CONTINUE]
    assert ws.get_task(task.id).status is TaskStatus.ACCEPTED
    assert "QA不合格" in ws.read_log(task.id)
    assert "Task 9実装ループ" in ws.read_log(task.id)


def test_fail_after_max_rounds_remains_failed(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    task = done_task(ws, "解消不能な問題")

    def verify(config, prompt, **kwargs):
        return ok("判定: FAIL\n問題:\n- TASK_ID: 1 | SEVERITY: medium | TITLE: 問題 | DETAIL: 未解消")

    report = run_qa(
        ws,
        OrchestratorConfig(),
        QAOptions(repo_dir=tmp_path / "work", max_rounds=1, commit=False),
        verify=verify,
    )

    assert report.passed is False
    assert report.failed_ids == [task.id]
    assert ws.get_task(task.id).status is TaskStatus.FAILED
    assert not report.rework_results


def test_nonzero_qa_process_is_failed(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    task = done_task(ws, "QAプロセス障害")

    def verify(config, prompt, **kwargs):
        return PiResult(returncode=12, report="pytest crashed", session_id=None)

    report = run_qa(
        ws,
        OrchestratorConfig(),
        QAOptions(repo_dir=tmp_path / "work", max_rounds=1, commit=False),
        verify=verify,
    )

    assert report.passed is False
    assert report.inspections[0].returncode == 12
    assert "QAプロセス失敗" in report.inspections[0].issues[0].title
    assert ws.get_task(task.id).status is TaskStatus.FAILED


def test_qa_options_validate_positive_rounds():
    for kwargs in ({"max_rounds": 0}, {"rework_attempts": 0}):
        try:
            QAOptions(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid QA option should fail")
