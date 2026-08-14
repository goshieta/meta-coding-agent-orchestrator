"""Task 11: GitHub最終納品のテスト。"""

from __future__ import annotations

import json

from orchestrator.config import OrchestratorConfig
from orchestrator.deliver import (
    DeliveryOptions,
    GitHubClient,
    STATUS_AWAITING_APPROVAL,
    STATUS_BLOCKED,
    STATUS_DELIVERED,
    STATUS_FAILED,
    deliver,
)
from orchestrator.workspace import TaskStatus, Workspace


def accepted_task(ws: Workspace, title: str = "完成タスク"):
    task = ws.add_task(title)
    ws.transition(task.id, TaskStatus.RUNNING)
    ws.transition(task.id, TaskStatus.DONE)
    ws.transition(task.id, TaskStatus.QA)
    ws.transition(task.id, TaskStatus.ACCEPTED)
    return task


class FakeGitHub:
    def __init__(self, url="https://github.com/acme/project.git"):
        self.url = url
        self.calls = []

    def create_repository(self, name, *, owner=None, private=False):
        self.calls.append((name, owner, private))
        return {"clone_url": self.url, "html_url": self.url.removesuffix(".git")}


class FakeGit:
    def __init__(self, fail_push=False):
        self.calls = []
        self.remote = None
        self.fail_push = fail_push

    def __call__(self, args, cwd, env=None):
        self.calls.append((list(args), cwd, env))
        if args[:2] == ["remote", "get-url"]:
            if self.remote:
                return {"returncode": 0, "stdout": self.remote, "stderr": ""}
            return {"returncode": 2, "stdout": "", "stderr": "no remote"}
        if args[:2] == ["remote", "add"]:
            self.remote = args[3]
            return {"returncode": 0, "stdout": "", "stderr": ""}
        if args[:2] == ["remote", "set-url"]:
            self.remote = args[3]
            return {"returncode": 0, "stdout": "", "stderr": ""}
        if args and args[0] == "push" and self.fail_push:
            return {"returncode": 1, "stdout": "", "stderr": "authentication failed"}
        return {"returncode": 0, "stdout": "", "stderr": ""}


def test_delivery_requires_all_tasks_accepted(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    ws.add_task("未完了")
    result = deliver(ws, OrchestratorConfig(github_token="secret"), approval=lambda: True)
    assert result.status == STATUS_BLOCKED
    assert "accepted" in result.error
    assert ws.delivery_state["status"] == STATUS_BLOCKED


def test_delivery_waits_for_human_approval_without_side_effects(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    accepted_task(ws)
    github = FakeGitHub()
    result = deliver(
        ws,
        OrchestratorConfig(github_token="secret"),
        github_client=github,
        approval=lambda: False,
    )
    assert result.status == STATUS_AWAITING_APPROVAL
    assert result.awaiting_approval is True
    assert github.calls == []
    assert ws.delivery_state["status"] == STATUS_AWAITING_APPROVAL


def test_create_and_push_happens_once_and_is_idempotent(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    accepted_task(ws)
    github = FakeGitHub()
    git = FakeGit()
    config = OrchestratorConfig(github_token="secret-token")
    options = DeliveryOptions(owner="acme", repo_name="project")

    result = deliver(ws, config, options, approval=lambda: True, github_client=github, git_runner=git)
    assert result.status == STATUS_DELIVERED
    assert result.pushed is True
    assert github.calls == [("project", "acme", False)]
    push_calls = [c for c in git.calls if c[0] and c[0][0] == "push"]
    assert len(push_calls) == 1
    assert push_calls[0][2]["GIT_CONFIG_VALUE_0"] == "Authorization: Bearer secret-token"
    assert "secret-token" not in json.dumps(ws.delivery_state)

    second = deliver(ws, config, options, approval=lambda: True, github_client=github, git_runner=git)
    assert second.status == STATUS_DELIVERED
    assert second.idempotent is True
    assert github.calls == [("project", "acme", False)]
    assert len([c for c in git.calls if c[0] and c[0][0] == "push"]) == 1


def test_push_failure_preserves_created_repository_and_can_retry(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    accepted_task(ws)
    github = FakeGitHub()
    failed_git = FakeGit(fail_push=True)
    config = OrchestratorConfig(github_token="secret")
    first = deliver(ws, config, approval=lambda: True, github_client=github, git_runner=failed_git)
    assert first.status == STATUS_FAILED
    assert first.repository_url == github.url
    assert ws.delivery_state["repository_url"] == github.url
    assert "authentication" in first.error

    successful_git = FakeGit()
    second = deliver(ws, config, approval=lambda: True, github_client=github, git_runner=successful_git)
    assert second.status == STATUS_DELIVERED
    assert github.calls == [("work", None, False)]


def test_missing_token_is_explicit_and_not_logged(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    accepted_task(ws)
    result = deliver(ws, OrchestratorConfig(github_token=None), approval=lambda: True)
    assert result.status == STATUS_FAILED
    assert "GITHUB_TOKEN" in result.error
    assert "token" not in ws.board_path.read_text(encoding="utf-8").lower()


def test_github_client_redacts_auth_failures():
    class ErrorOpener:
        def __call__(self, request, timeout):
            from urllib.error import HTTPError
            raise HTTPError(request.full_url, 401, "unauthorized", {}, None)

    client = GitHubClient("top-secret", opener=ErrorOpener())
    try:
        client.create_repository("project")
    except Exception as exc:
        assert "401" in str(exc)
        assert "top-secret" not in str(exc)
    else:
        raise AssertionError("authentication failure expected")


def test_delivery_state_survives_workspace_reopen(tmp_path):
    ws = Workspace.open(tmp_path / "work")
    accepted_task(ws)
    ws.set_delivery_state(status=STATUS_DELIVERED, repository_url="https://github.com/a/b.git")
    reopened = Workspace.open(tmp_path / "work")
    assert reopened.delivery_state["status"] == STATUS_DELIVERED
    assert reopened.delivery_state["repository_url"].endswith("b.git")
