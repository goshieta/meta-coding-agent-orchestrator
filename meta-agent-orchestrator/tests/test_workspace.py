"""Task 5: ワークスペース・共有ボード・状態管理 (workspace.py) のテスト。

達成基準:
- plan.md を生成し、状態を更新・再読み込みできる。
- 再起動時に前回のセッション・状態を復元できる（冪等性）。
"""

from __future__ import annotations

import json

import pytest

from orchestrator.workspace import (
    BOARD_FILENAME,
    LOGS_DIR,
    SESSIONS_DIR,
    STATE_FILENAME,
    TaskStatus,
    Workspace,
    WORKFLOW,
)


def _advance(ws: Workspace, task_id: int, *statuses: TaskStatus | str) -> None:
    """正規フロー（planned→running→…）に沿って状態を前進させるヘルパー。"""
    for s in statuses:
        ws.transition(task_id, s)


# ---------------------------------------------------------------------------
# 初期化・ディレクトリ生成
# ---------------------------------------------------------------------------
def test_workspace_creates_dirs(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    assert ws.workdir.is_dir()
    assert ws.logs_dir.is_dir()
    assert ws.sessions_dir.is_dir()
    assert ws.board_path.is_file()


def test_plan_md_and_state_generated(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.add_task("タスクA")
    assert (ws.workdir / BOARD_FILENAME).is_file()
    assert (ws.workdir / STATE_FILENAME).is_file()


# ---------------------------------------------------------------------------
# タスク管理・状態遷移
# ---------------------------------------------------------------------------
def test_add_and_get_task(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    t = ws.add_task("タスクA", description="説明", dependencies=[], priority=2)
    assert t.id == 1
    assert t.title == "タスクA"
    assert ws.get_task(1).status is TaskStatus.PLANNED
    assert ws.tasks == [t]


def test_task_ids_increment(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    a = ws.add_task("A")
    b = ws.add_task("B")
    assert a.id == 1 and b.id == 2


def test_status_flow_planned_to_accepted(tmp_path) -> None:
    """planned → running → done → qa → accepted の正規フローが通る。"""
    ws = Workspace(tmp_path / "ws")
    t = ws.add_task("タスク")
    ws.transition(t.id, TaskStatus.RUNNING)
    assert ws.get_task(t.id).status is TaskStatus.RUNNING
    ws.transition(t.id, "done")
    assert ws.get_task(t.id).status is TaskStatus.DONE
    ws.transition(t.id, TaskStatus.QA)
    assert ws.get_task(t.id).status is TaskStatus.QA
    ws.transition(t.id, TaskStatus.ACCEPTED)
    assert ws.get_task(t.id).status is TaskStatus.ACCEPTED


def test_illegal_transition_raises(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    t = ws.add_task("タスク")
    # planned → accepted は不正
    with pytest.raises(ValueError):
        ws.transition(t.id, TaskStatus.ACCEPTED)


def test_attempts_increment_on_failure(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    t = ws.add_task("タスク")
    ws.transition(t.id, TaskStatus.RUNNING)
    ws.transition(t.id, TaskStatus.FAILED)
    ws.transition(t.id, TaskStatus.RUNNING)  # 再実行
    assert ws.get_task(t.id).attempts >= 1


def test_ready_tasks_respects_dependencies(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    base = ws.add_task("ベース", dependencies=[])
    dep = ws.add_task("依存あり", dependencies=[base.id])
    assert ws.ready_tasks() == [base]  # dep は未完了の base に依存
    _advance(ws, base.id, TaskStatus.RUNNING, TaskStatus.DONE, TaskStatus.QA, TaskStatus.ACCEPTED)
    assert ws.ready_tasks() == [dep]


# ---------------------------------------------------------------------------
# タスク別ログ
# ---------------------------------------------------------------------------
def test_log_and_read(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    t = ws.add_task("タスク")
    ws.log(t.id, "こんにちは")
    ws.log(t.id, "2 行目")
    content = ws.read_log(t.id)
    assert "こんにちは" in content and "2 行目" in content
    assert (ws.logs_dir / f"{t.id}.log").is_file()


def test_read_log_missing_returns_empty(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    assert ws.read_log(99) == ""


# ---------------------------------------------------------------------------
# pi セッション管理（冪等性）
# ---------------------------------------------------------------------------
def test_set_and_restore_session(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    t = ws.add_task("タスク")
    ws.set_session(t.id, "sess-abc")
    assert ws.restore_session(t.id) == "sess-abc"
    assert ws.list_sessions() == {t.id: "sess-abc"}


def test_persist_session_copies_jsonl(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    t = ws.add_task("タスク")
    src = tmp_path / "export" / "sess1.jsonl"
    src.parent.mkdir()
    src.write_text('{"type":"user"}\n', encoding="utf-8")
    dest = ws.persist_session(t.id, src)
    assert dest == ws.sessions_dir / f"{t.id}.jsonl"
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == '{"type":"user"}\n'
    # タスクにセッションが記録される
    assert ws.get_task(t.id).session_id == "sess1"


def test_restore_all_returns_sessions(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    a = ws.add_task("A")
    b = ws.add_task("B")
    ws.set_session(a.id, "s1")
    ws.set_session(b.id, "s2")
    assert ws.restore_all() == {a.id: "s1", b.id: "s2"}


# ---------------------------------------------------------------------------
# 冪等性: 再起動（再 open）で状態・セッションが復元される
# ---------------------------------------------------------------------------
def test_reopen_restores_state(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    t = ws.add_task("タスクA", description="説明", priority=3)
    _advance(ws, t.id, TaskStatus.RUNNING, TaskStatus.DONE)
    ws.set_session(t.id, "sess-x")
    ws.log(t.id, "ログ")

    # 再起動（新規 Workspace で同じ workdir を開く）
    ws2 = Workspace.open(tmp_path / "ws")
    assert len(ws2.tasks) == 1
    t2 = ws2.get_task(1)
    assert t2.title == "タスクA"
    assert t2.description == "説明"
    assert t2.priority == 3
    assert t2.status is TaskStatus.DONE
    assert t2.session_id == "sess-x"
    assert "ログ" in ws2.read_log(1)


def test_reopen_does_not_duplicate(tmp_path) -> None:
    """同じ workdir を何度開いてもタスクが重複しない（冪等性）。"""
    ws = Workspace(tmp_path / "ws")
    ws.add_task("A")
    ws2 = Workspace.open(tmp_path / "ws")
    ws2.add_task("B")
    ws3 = Workspace.open(tmp_path / "ws")
    assert [t.title for t in ws3.tasks] == ["A", "B"]


def test_next_id_preserved_across_reopen(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.add_task("A")
    ws.add_task("B")
    ws2 = Workspace.open(tmp_path / "ws")
    c = ws2.add_task("C")
    assert c.id == 3


def test_save_is_idempotent(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.add_task("A")
    ws.save()
    ws.save()
    data = json.loads(ws.state_path.read_text(encoding="utf-8"))
    assert len(data["tasks"]) == 1
    assert data["next_id"] == 2


# ---------------------------------------------------------------------------
# 共有ボード（plan.md）
# ---------------------------------------------------------------------------
def test_plan_md_contains_task_info(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.add_task("テストタスク", description="詳細説明")
    _advance(ws, 1, TaskStatus.RUNNING, TaskStatus.DONE)
    board = ws.board_path.read_text(encoding="utf-8")
    assert "テストタスク" in board
    assert "詳細説明" in board
    assert "done" in board
    assert "Task 1" in board


def test_workflow_definition_has_expected_keys() -> None:
    assert TaskStatus.PLANNED in WORKFLOW
    assert TaskStatus.ACCEPTED in WORKFLOW
    # accepted は終端
    assert WORKFLOW[TaskStatus.ACCEPTED] == set()
