"""Task 9: git 操作ヘルパー（git.py）のテスト。"""

from __future__ import annotations

from pathlib import Path

from orchestrator import git


class _FakeGit:
    """記録可能な git runner。実際のコミット履歴を模した dict で応答する。"""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.repo: list[tuple[str, str]] = []  # (hash, subject) の簡易履歴
        self.counter = 0
        self.is_repo = False

    def __call__(self, args: list[str], cwd: str):
        self.calls.append((list(args), cwd))
        name = args[0]
        if name == "rev-parse":
            return {"returncode": 0 if self.is_repo else 1,
                    "stdout": "true\n" if self.is_repo else "", "stderr": ""}
        if name == "init":
            self.is_repo = True
            return {"returncode": 0, "stdout": "Initialized repository", "stderr": ""}
        if name == "add":
            return {"returncode": 0, "stdout": "", "stderr": ""}
        if name == "commit":
            if not getattr(self, "has_changes", True):  # 変更なしのケース
                return {"returncode": 1, "stdout": "", "stderr": "nothing to commit, working tree clean"}
            self.counter += 1
            h = f"{self.counter:07x}"
            subject = args[args.index("-m") + 1] if "-m" in args else ""
            self.repo.append((h, subject))
            return {"returncode": 0, "stdout": f"[main {h}] {subject}\n", "stderr": ""}
        if name == "log":
            lines = [f"{h} {s}" for h, s in reversed(self.repo)]
            return {"returncode": 0, "stdout": "\n".join(lines) + ("\n" if lines else ""), "stderr": ""}
        return {"returncode": 0, "stdout": "", "stderr": ""}


def test_is_git_repo_detects():
    fg = _FakeGit()
    assert git.is_git_repo("/tmp/x", runner=fg) is False
    fg.is_repo = True
    assert git.is_git_repo("/tmp/x", runner=fg) is True


def test_ensure_git_repo_inits_once():
    fg = _FakeGit()
    r1 = git.ensure_git_repo("/tmp/x", runner=fg)
    assert r1.ok and "init" in [c[0][0] for c in fg.calls]
    # 2回目は既にリポジトリのため init しない
    before = len(fg.calls)
    r2 = git.ensure_git_repo("/tmp/x", runner=fg)
    assert r2.ok
    assert [c[0][0] for c in fg.calls[before:]] == ["rev-parse"]


def test_commit_all_creates_commit_with_message():
    fg = _FakeGit()
    git.ensure_git_repo("/tmp/x", runner=fg)
    r = git.commit_all("/tmp/x", "Task 1: 機能A", runner=fg)
    assert r.ok and r.committed is True
    assert r.hash is not None
    assert fg.repo[-1][1] == "Task 1: 機能A"


def test_log_returns_history():
    fg = _FakeGit()
    for m in ("first", "second"):
        git.commit_all("/tmp/x", m, runner=fg)
    entries = git.log("/tmp/x", runner=fg)
    assert entries[0].endswith("second")


def test_commit_all_no_changes_is_ok_not_committed():
    fg = _FakeGit()
    fg.has_changes = False
    git.ensure_git_repo("/tmp/x", runner=fg)
    r = git.commit_all("/tmp/x", "noop", runner=fg)
    assert r.ok is True
    assert r.committed is False


def test_commit_all_missing_git_reports_error():
    def runner(args, cwd):
        return {"returncode": -127, "stdout": "", "stderr": "git 実行ファイルが見つかりません"}

    r = git.commit_all("/tmp/x", "m", runner=runner)
    assert r.ok is False and r.returncode == -127
