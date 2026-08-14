"""Task 12: 質問・回答・強制停止・再開のテスト。"""

from __future__ import annotations

import json

import pytest

from orchestrator.human_gate import (
    HumanGate,
    HumanGateError,
    STATUS_ANSWERED,
    STATUS_AWAITING_ANSWER,
    STATUS_RESUMED,
    STATUS_STOPPED,
)
from orchestrator.workspace import TaskStatus, Workspace


def _running_workspace(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    task = ws.add_task("実装タスク")
    ws.transition(task.id, TaskStatus.RUNNING)
    ws.set_session(task.id, "pi-session-123")
    return ws, task


def test_question_answer_is_persisted_and_can_continue(tmp_path):
    ws, task = _running_workspace(tmp_path)
    gate = HumanGate(ws)

    question = gate.submit_question(
        "認証方式はOAuthでよいですか？",
        context="仕様書に認証方式の記載がありません",
        task_id=task.id,
    )
    assert gate.waiting
    assert question.question_id == gate.active_question_id
    assert ws.human_gate_state["status"] == STATUS_AWAITING_ANSWER

    result = gate.answer_question(question.question_id, "はい、OAuthを採用してください")
    assert result.status == STATUS_ANSWERED
    assert result.answer.startswith("はい")
    assert gate.active_question_id is None
    assert ws.human_gate_state["history"][0]["answer"]["text"].startswith("はい")
    assert "回答を受領" in ws.read_log(task.id)

    reopened = Workspace.open(tmp_path / "work")
    assert reopened.human_gate_state["last_question_id"] == question.question_id
    assert reopened.get_task(task.id).session_id == "pi-session-123"


def test_ask_waits_for_answer_and_accepts_injected_input(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    gate = HumanGate(ws)
    result = gate.ask("続行してよいですか？", input_fn=lambda prompt: "yes")
    assert result.status == STATUS_ANSWERED
    assert result.answer == "yes"


def test_stop_command_saves_state_before_returning_stop(tmp_path):
    ws, task = _running_workspace(tmp_path)
    gate = HumanGate(ws)
    question = gate.submit_question("不足情報を教えてください", task_id=task.id)

    result = gate.wait_for_answer(question.question_id, input_fn=lambda prompt: "STOP")
    assert result.status == STATUS_STOPPED
    assert result.stopped is True
    assert gate.stopped is True
    assert ws.human_gate_state["stop_reason"]
    # 安全停止は実装途中のセッションとタスク状態を破棄しない。
    assert ws.get_task(task.id).status is TaskStatus.RUNNING
    assert ws.get_task(task.id).session_id == "pi-session-123"
    state = json.loads(ws.state_path.read_text(encoding="utf-8"))
    assert state["human_gate"]["status"] == STATUS_STOPPED
    assert "stopped" in ws.board_path.read_text(encoding="utf-8")


def test_resume_preserves_pending_question_and_accepts_later_answer(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    gate = HumanGate(ws)
    question = gate.submit_question("再開条件は何ですか？")
    stopped = gate.force_stop(reason="メンテナンス")
    assert stopped.stopped

    resumed = gate.resume()
    assert resumed.status == STATUS_AWAITING_ANSWER
    assert resumed.resumed is True
    assert gate.active_question_id == question.question_id

    answer = gate.wait_for_answer(input_fn=lambda prompt: "再開してください")
    assert answer.status == STATUS_ANSWERED
    assert gate.state["history"][0]["question"]["question_id"] == question.question_id


def test_resume_is_idempotent_when_not_stopped(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    gate = HumanGate(ws)
    result = gate.resume()
    assert result.resumed is False
    assert result.status == STATUS_RESUMED or result.status == "idle"


def test_invalid_question_and_answer_are_rejected(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    gate = HumanGate(ws)
    with pytest.raises(ValueError):
        gate.submit_question("  ")
    question = gate.submit_question("質問")
    with pytest.raises(ValueError):
        gate.answer_question(question.question_id, "  ")
    with pytest.raises(HumanGateError):
        gate.answer_question("q-not-current", "回答")


def test_stop_and_resume_shortcuts_work(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    gate = HumanGate(ws)
    stopped = gate.handle_command("/stop")
    assert stopped is not None and stopped.stopped
    resumed = gate.handle_command("resume")
    assert resumed is not None and resumed.resumed


def test_delivery_approval_can_use_human_gate_callback(tmp_path):
    """Task11のapproval callableへHumanGateを接続できる。"""
    ws = Workspace.open(tmp_path / "work")
    gate = HumanGate(ws)
    answers = iter(["承認"])
    assert gate.request_approval(input_fn=lambda prompt: next(answers)) is True
    assert ws.human_gate_state["approval"] is True
