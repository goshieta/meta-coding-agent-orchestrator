"""Task 9: 実装ループ（executor.py）のテスト。"""

from __future__ import annotations

from orchestrator.config import OrchestratorConfig
from orchestrator.executor import (
    LoopOptions,
    PiResult,
    STEP_COMPACT,
    STEP_CONTINUE,
    STEP_FORK,
    STEP_SINGLE,
    pi_continue,
    pi_single,
    run_loop,
)
from orchestrator.workspace import TaskStatus, Workspace


class FakeGit:
    """executor の git 注入用 fake。"""

    def __init__(self) -> None:
        self.is_repo = False
        self.calls: list[list[str]] = []
        self.commits: list[str] = []
        self.counter = 0

    def __call__(self, args: list[str], cwd: str):
        self.calls.append(list(args))
        if args[0] == "rev-parse":
            return {"returncode": 0 if self.is_repo else 1, "stdout": "true\n" if self.is_repo else "", "stderr": ""}
        if args[0] == "init":
            self.is_repo = True
            return {"returncode": 0, "stdout": "initialized", "stderr": ""}
        if args[0] == "add":
            return {"returncode": 0, "stdout": "", "stderr": ""}
        if args[0] == "commit":
            self.counter += 1
            message = args[args.index("-m") + 1]
            self.commits.append(message)
            return {"returncode": 0, "stdout": f"[main {self.counter:07x}] {message}\n", "stderr": ""}
        return {"returncode": 0, "stdout": "", "stderr": ""}


def ok_result(label: str, session: str | None = None) -> PiResult:
    return PiResult(returncode=0, report=f"成功: {label}", session_id=session)


def fail_result(label: str, session: str | None = None) -> PiResult:
    return PiResult(returncode=1, report=f"失敗: {label}", session_id=session)


def test_pi_wrappers_build_commands():
    calls = []

    def runner(cmd, cwd=None, timeout=3600):
        calls.append((cmd, cwd, timeout))
        return {"returncode": 0, "stdout": "PI_OK", "stderr": ""}

    config = OrchestratorConfig()
    single = pi_single(config, "実装する", spec_file="spec.md", cwd="/repo", runner=runner)
    continued = pi_continue(config, "続ける", session="session-1", cwd="/repo", runner=runner)

    assert single.ok and "PI_OK" in single.report
    assert continued.ok
    assert "-p" in calls[0][0] and "@spec.md" in calls[0][0]
    assert "--session" in calls[1][0] and "session-1" in calls[1][0]
    assert calls[0][1] == "/repo"


def test_loop_executes_dependency_order_and_commits_each_done(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    first = ws.add_task("最初の実装", priority=1)
    second = ws.add_task("依存タスク", dependencies=[first.id], priority=1)
    independent = ws.add_task("独立タスク", priority=2)
    calls: list[tuple[str, int]] = []
    fake_git = FakeGit()

    def single(config, task, **kwargs):
        task_id = int(task.split("タスク ", 1)[1].split(":", 1)[0])
        calls.append((STEP_SINGLE, task_id))
        return ok_result(f"task-{task_id}", session=f"session-{task_id}")

    result = run_loop(ws, OrchestratorConfig(), LoopOptions(repo_dir=tmp_path / "work"), single=single, git_runner=fake_git)

    assert result.done_ids == [first.id, second.id, independent.id]
    assert result.failed_ids == []
    assert calls == [(STEP_SINGLE, first.id), (STEP_SINGLE, second.id), (STEP_SINGLE, independent.id)]
    assert len(result.commits) == 3
    assert fake_git.commits == [
        f"Task {first.id}: 最初の実装",
        f"Task {second.id}: 依存タスク",
        f"Task {independent.id}: 独立タスク",
    ]
    assert ws.get_task(first.id).status is TaskStatus.DONE
    assert "成功" in ws.get_task(first.id).report
    assert "executor" in ws.read_log(first.id)
    assert "成功 3" in result.summary()


def test_failed_task_uses_fork_and_updates_board(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    task = ws.add_task("再実行が必要な実装")
    fake_git = FakeGit()
    steps: list[str] = []

    def single(config, task_text, **kwargs):
        steps.append(STEP_SINGLE)
        return fail_result("first attempt", session="failed-session")

    def fork(config, session, task_text, **kwargs):
        steps.append(STEP_FORK)
        assert session == "failed-session"
        return ok_result("fork retry", session="forked-session")

    result = run_loop(ws, OrchestratorConfig(), LoopOptions(repo_dir=tmp_path / "work", max_attempts=2), single=single, fork=fork, git_runner=fake_git)

    assert steps == [STEP_SINGLE, STEP_FORK]
    assert result.done_ids == [task.id]
    assert ws.get_task(task.id).status is TaskStatus.DONE
    assert ws.get_task(task.id).session_id == "forked-session"
    assert fake_git.commits == [f"Task {task.id}: 再実行が必要な実装"]
    assert "run_pi_fork" in ws.read_log(task.id)


def test_exhausted_failure_becomes_failed_without_commit(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    task = ws.add_task("常に失敗する実装")
    fake_git = FakeGit()
    steps: list[str] = []

    def single(config, task_text, **kwargs):
        steps.append(STEP_SINGLE)
        session = "retry-session" if steps.count(STEP_SINGLE) == 2 else None
        return fail_result("no session", session=session)

    def fork(config, session, task_text, **kwargs):
        steps.append(STEP_FORK)
        return fail_result("fork failed", session="retry-session")

    result = run_loop(ws, OrchestratorConfig(), LoopOptions(repo_dir=tmp_path / "work", max_attempts=3), single=single, fork=fork, git_runner=fake_git)

    assert result.done_ids == []
    assert result.failed_ids == [task.id]
    assert ws.get_task(task.id).status is TaskStatus.FAILED
    assert not fake_git.commits
    assert steps == [STEP_SINGLE, STEP_SINGLE, STEP_FORK]
    assert "失敗" in result.summary()


def test_steering_and_compact_are_injected(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    task = ws.add_task("長いタスク")
    fake_git = FakeGit()
    steps: list[str] = []

    def single(config, task_text, **kwargs):
        steps.append(STEP_SINGLE)
        return PiResult(returncode=0, report="x" * 100, session_id="s1")

    def contn(config, task_text, **kwargs):
        steps.append(STEP_CONTINUE)
        assert "追加指示" in task_text
        return ok_result("follow-up", session="s1")

    def compact(config, session, instructions="", **kwargs):
        steps.append(STEP_COMPACT)
        assert session == "s1"
        return ok_result("compacted", session="s1")

    run_loop(
        ws,
        OrchestratorConfig(),
        LoopOptions(repo_dir=tmp_path / "work", compact_threshold=50, steering={task.id: ["テストを追加してください"]}),
        single=single,
        contn=contn,
        compact=compact,
        git_runner=fake_git,
    )

    assert steps == [STEP_SINGLE, STEP_CONTINUE, STEP_COMPACT]
    report = ws.get_task(task.id).report
    assert "追加指示への応答" in report
    assert "compact_context" in report


def test_running_task_resumes_with_continue(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    task = ws.add_task("再開するタスク")
    ws.transition(task.id, TaskStatus.RUNNING)
    ws.set_session(task.id, "persisted-session")
    fake_git = FakeGit()
    steps: list[str] = []

    def contn(config, task_text, session=None, **kwargs):
        steps.append(STEP_CONTINUE)
        assert session == "persisted-session"
        return ok_result("resumed", session="persisted-session")

    result = run_loop(ws, OrchestratorConfig(), LoopOptions(repo_dir=tmp_path / "work"), contn=contn, git_runner=fake_git)

    assert result.done_ids == [task.id]
    assert steps == [STEP_CONTINUE]
    assert ws.get_task(task.id).status is TaskStatus.DONE


def test_persisted_failed_task_is_retried_with_fork(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    task = ws.add_task("前回失敗タスク")
    ws.transition(task.id, TaskStatus.RUNNING)
    ws.set_session(task.id, "old-session")
    ws.transition(task.id, TaskStatus.FAILED)
    fake_git = FakeGit()
    steps: list[str] = []

    def fork(config, session, task_text, **kwargs):
        steps.append(STEP_FORK)
        assert session == "old-session"
        return ok_result("recovered", session="new-session")

    result = run_loop(ws, OrchestratorConfig(), LoopOptions(repo_dir=tmp_path / "work", max_attempts=1), fork=fork, git_runner=fake_git)

    assert result.done_ids == [task.id]
    assert steps == [STEP_FORK]
    assert ws.get_task(task.id).status is TaskStatus.DONE


def test_commit_failure_marks_task_failed(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    task = ws.add_task("commit失敗タスク")

    def bad_git(args, cwd):
        if args[0] == "rev-parse":
            return {"returncode": 0, "stdout": "true\n", "stderr": ""}
        if args[0] == "add":
            return {"returncode": 1, "stdout": "", "stderr": "index locked"}
        return {"returncode": 1, "stdout": "", "stderr": "commit failed"}

    def single(config, task_text, **kwargs):
        return ok_result("implemented", session="s1")

    result = run_loop(ws, OrchestratorConfig(), LoopOptions(repo_dir=tmp_path / "work"), single=single, git_runner=bad_git)

    assert result.failed_ids == [task.id]
    assert ws.get_task(task.id).status is TaskStatus.FAILED
    assert "commit失敗" in ws.get_task(task.id).report


def test_options_validation():
    try:
        LoopOptions(max_attempts=0)
    except ValueError as exc:
        assert "max_attempts" in str(exc)
    else:
        raise AssertionError("max_attempts=0 should fail")
