"""Task 13: 外部 API / pi を起動しない統合パイプライン検証。"""

from __future__ import annotations

from orchestrator.config import OrchestratorConfig
from orchestrator.executor import PiResult
from orchestrator.pipeline import run_pipeline
from orchestrator.workspace import TaskStatus, Workspace


def _pi_ok(label: str, session: str = "session-1") -> PiResult:
    return PiResult(returncode=0, report=f"{label}: 実装・テスト完了", session_id=session)


def test_pipeline_connects_context_plan_execute_qa_and_approval(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text("# 統合テスト仕様\n\n機能を実装する。\n", encoding="utf-8")
    workspace = Workspace.open(tmp_path / "work")
    calls: list[str] = []

    def single(config, prompt, **kwargs):
        calls.append("implement")
        return _pi_ok("実装")

    def verify(config, prompt, **kwargs):
        calls.append("qa")
        return _pi_ok("判定: PASS\n問題: なし", "qa-session")

    result = run_pipeline(
        spec,
        workspace,
        OrchestratorConfig(github_token=None),
        pi_single=single,
        qa_verify=verify,
        approval=lambda: False,
    )

    assert result.context.is_existing is False
    assert result.plan.tasks
    assert calls == ["implement", "qa"]
    assert result.implementation.done_ids == [1]
    assert result.qa.passed is True
    assert result.delivery is not None
    assert result.delivery.awaiting_approval is True
    assert workspace.get_task(1).status is TaskStatus.ACCEPTED
    assert (workspace.workdir / "context" / "initial_prompt.md").is_file()
    assert "awaiting_approval" in workspace.board_path.read_text(encoding="utf-8")


def test_pipeline_reuses_persisted_plan_on_restart(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text("# 仕様\n", encoding="utf-8")
    ws = Workspace.open(tmp_path / "work")
    first = run_pipeline(
        spec, ws, OrchestratorConfig(),
        pi_single=lambda *args, **kwargs: _pi_ok("実装"),
        qa_verify=lambda *args, **kwargs: _pi_ok("判定: PASS\n問題: なし"),
        approval=lambda: False,
    )
    reopened = Workspace.open(tmp_path / "work")
    assert reopened.tasks
    assert run_pipeline(
        spec, reopened, OrchestratorConfig(),
        pi_single=lambda *args, **kwargs: _pi_ok("実装"),
        qa_verify=lambda *args, **kwargs: _pi_ok("判定: PASS\n問題: なし"),
        approval=lambda: False,
    ).plan.reused is True
