"""GitHub への最終納品（PLAN F-07 / F-11、Task 11）。

QA を通過した成果物だけを GitHub へ納品する。GitHub リポジトリの作成、remote
設定、push の状態はワークスペースへ永続化し、再実行時の二重作成・二重 push を
防止する。認証トークンはプロセス引数・ログ・状態ファイルへ保存しない。

納品フロー::

    all accepted -> final decision -> human approval -> create -> push -> delivered

Task 12 の human_gate はまだ任意のため、承認 callable を注入できる。承認が無い
場合は ``awaiting_approval`` で安全に停止し、後から同じ状態を再開できる。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from orchestrator.config import OrchestratorConfig
from orchestrator.workspace import TaskStatus, Workspace

DELIVERY_FILENAME = "delivery.json"
DEFAULT_GITHUB_API = "https://api.github.com"
DEFAULT_REMOTE = "origin"
DEFAULT_BRANCH = "main"

STATUS_BLOCKED = "blocked"
STATUS_AWAITING_APPROVAL = "awaiting_approval"
STATUS_CREATING = "creating"
STATUS_CREATED = "created"
STATUS_PUSHING = "pushing"
STATUS_FAILED = "failed"
STATUS_DELIVERED = "delivered"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DeliveryOptions:
    """最終納品の制御設定。"""

    repo_name: str | None = None
    owner: str | None = None
    private: bool = False
    api_url: str = DEFAULT_GITHUB_API
    remote: str = DEFAULT_REMOTE
    branch: str = DEFAULT_BRANCH
    require_approval: bool = True


@dataclass
class DeliveryResult:
    """納品処理の結果。``error`` には秘密情報を含めない。"""

    status: str
    repository_url: str | None = None
    pushed: bool = False
    idempotent: bool = False
    awaiting_approval: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_DELIVERED

    def summary(self) -> str:
        if self.ok:
            prefix = "GitHub納品済み（再実行は冪等）" if self.idempotent else "GitHub納品完了"
            return f"{prefix}: {self.repository_url or '(URLなし)'}"
        if self.awaiting_approval:
            return "GitHub納品待機中: 人間の最終承認が必要です。"
        return f"GitHub納品失敗/未完了: {self.error or self.status}"


class DeliveryError(RuntimeError):
    """GitHub作成・認証・Git pushに関する安全なエラー。"""


class GitHubClient:
    """GitHub REST API の最小クライアント。"""

    def __init__(self, token: str, api_url: str = DEFAULT_GITHUB_API, opener: Callable | None = None) -> None:
        if not token:
            raise ValueError("GitHub token が指定されていません")
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.opener = opener or urllib.request.urlopen

    def _request(self, method: str, path: str, payload: Mapping[str, object] | None = None) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.api_url}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            # GitHubの本文をそのまま返さず、tokenやヘッダが漏れない一般的な説明にする。
            if exc.code in (401, 403):
                raise DeliveryError(f"GitHub認証または権限エラー（HTTP {exc.code}）") from exc
            raise DeliveryError(f"GitHub APIエラー（HTTP {exc.code}）") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DeliveryError("GitHub APIへ接続できませんでした") from exc
        except (json.JSONDecodeError, OSError) as exc:
            raise DeliveryError("GitHub APIの応答を解釈できませんでした") from exc

    def create_repository(self, name: str, *, owner: str | None = None, private: bool = False) -> dict:
        """ユーザーまたはOrganizationにリポジトリを作成する。"""
        path = f"/orgs/{urllib.parse.quote(owner, safe='')}/repos" if owner else "/user/repos"
        return self._request("POST", path, {"name": name, "private": private, "auto_init": False})


# urllib.parse は遅延 import ではなく明示的に公開実装へ追加する。
import urllib.parse  # noqa: E402  (定数・型宣言の後でも可読性を優先)


def _default_repo_name(workdir: Path) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", workdir.name).strip("-.")
    return name or "meta-agent-delivery"


def _safe_error(exc: BaseException, secrets: tuple[str, ...] = ()) -> str:
    """外部コマンド/API例外からsecretらしい値を排除したエラーを作る。"""
    text = str(exc).strip()
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s]+", r"\1<redacted>", text)
    # tokenをremote URLへ誤って埋め込んだ場合も、エラーメッセージへ出さない。
    text = re.sub(r"(?i)(https?://)([^/@\s]+):([^/@\s]+)@", r"\1<credentials>@", text)
    return text or exc.__class__.__name__


def _git_run(
    args: list[str],
    cwd: str,
    *,
    token: str | None = None,
    runner: Callable | None = None,
) -> dict:
    """Gitを実行する。tokenは一時的な環境変数設定としてだけ渡す。"""
    env = None
    if token:
        env = dict(os.environ)
        # remote URLへtokenを埋め込まず、Gitの一時設定として認証ヘッダを渡す。
        env.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraheader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Bearer {token}",
        })
    if runner is not None:
        try:
            return runner(args, cwd, env)
        except TypeError:
            # 既存の2引数runnerも利用可能（テスト・拡張向け）。
            return runner(args, cwd)
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
        return {"returncode": proc.returncode, "stdout": proc.stdout or "", "stderr": proc.stderr or ""}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": -1, "stdout": "", "stderr": _safe_error(exc)}


def _git_ok(result: dict) -> None:
    if result.get("returncode") != 0:
        detail = (result.get("stderr") or result.get("stdout") or "git command failed").strip()
        raise DeliveryError(f"Git操作に失敗しました: {_safe_error(RuntimeError(detail))}")


@contextmanager
def _delivery_lock(workspace: Workspace):
    """同一ワークスペースの納品を直列化する。"""
    lock_path = workspace.workdir / "delivery.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except (ImportError, AttributeError):  # pragma: no cover - 非POSIX環境
            pass
        try:
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except (ImportError, AttributeError):  # pragma: no cover
                pass


def _all_tasks_accepted(workspace: Workspace) -> bool:
    return bool(workspace.tasks) and all(task.status is TaskStatus.ACCEPTED for task in workspace.tasks)


def _state(workspace: Workspace) -> dict:
    """Workspace拡張前後で利用できる納品状態アクセサ。"""
    return workspace.delivery_state


def _save_state(workspace: Workspace, **updates: object) -> dict:
    state = dict(_state(workspace))
    state.update(updates)
    workspace.set_delivery_state(**state)
    return state


def _create_or_get_repository(
    client: GitHubClient,
    options: DeliveryOptions,
    name: str,
) -> dict:
    return client.create_repository(name, owner=options.owner, private=options.private)


def deliver(
    workspace: Workspace,
    config: OrchestratorConfig,
    options: DeliveryOptions | None = None,
    *,
    approval: Callable[[], bool] | None = None,
    final_decision: Callable[[Workspace], bool] | None = None,
    github_client: GitHubClient | None = None,
    git_runner: Callable | None = None,
) -> DeliveryResult:
    """成果物を一度だけGitHubへ作成・pushする。

    Args:
        approval: Task12と接続する最終承認 callable。未指定かFalseならpushしない。
        final_decision: オーケストレータの最終判定。未指定時は全タスク ``accepted``。
        github_client: APIテスト用の注入クライアント。
        git_runner: Gitテスト用runner（``(args, cwd, env)`` または2引数）。

    失敗時は状態を ``failed`` に保存する。リポジトリ作成済みでpushだけ失敗した場合、
    repository_urlを保持して次回は作成を繰り返さずpushを再試行する。
    """
    opts = options or DeliveryOptions()
    with _delivery_lock(workspace):
        state = _state(workspace)
        if state.get("status") == STATUS_DELIVERED:
            return DeliveryResult(
                status=STATUS_DELIVERED,
                repository_url=state.get("repository_url"),
                pushed=True,
                idempotent=True,
            )

        if not _all_tasks_accepted(workspace):
            error = "全タスクがQA通過（accepted）ではありません"
            _save_state(workspace, status=STATUS_BLOCKED, last_error=error, updated_at=_now())
            return DeliveryResult(status=STATUS_BLOCKED, error=error)

        decision = final_decision(workspace) if final_decision else True
        if not decision:
            error = "オーケストレータの最終判定が不合格です"
            _save_state(workspace, status=STATUS_BLOCKED, last_error=error, updated_at=_now())
            return DeliveryResult(status=STATUS_BLOCKED, error=error)

        if opts.require_approval:
            if approval is None or not approval():
                _save_state(workspace, status=STATUS_AWAITING_APPROVAL, last_error=None, updated_at=_now())
                return DeliveryResult(status=STATUS_AWAITING_APPROVAL, awaiting_approval=True)

        token = config.github_token
        if not token:
            error = "GITHUB_TOKEN が設定されていません"
            _save_state(workspace, status=STATUS_FAILED, last_error=error, updated_at=_now())
            return DeliveryResult(status=STATUS_FAILED, error=error)

        repo_name = state.get("repo_name") or opts.repo_name or _default_repo_name(workspace.workdir)
        repository_url = state.get("repository_url")
        try:
            if not repository_url:
                _save_state(workspace, status=STATUS_CREATING, repo_name=repo_name, updated_at=_now())
                client = github_client or GitHubClient(token, opts.api_url)
                created = _create_or_get_repository(client, opts, repo_name)
                repository_url = str(created.get("clone_url") or created.get("ssh_url") or "")
                if not repository_url:
                    raise DeliveryError("GitHub APIがclone URLを返しませんでした")
                html_url = created.get("html_url")
                _save_state(
                    workspace,
                    status=STATUS_CREATED,
                    repo_name=repo_name,
                    repository_url=repository_url,
                    html_url=html_url,
                    updated_at=_now(),
                    last_error=None,
                )

            _save_state(workspace, status=STATUS_PUSHING, updated_at=_now(), last_error=None)
            remote_result = _git_run(["remote", "get-url", opts.remote], str(workspace.workdir), runner=git_runner)
            if remote_result.get("returncode") != 0:
                _git_ok(_git_run(["remote", "add", opts.remote, repository_url], str(workspace.workdir), runner=git_runner))
            elif (remote_result.get("stdout") or "").strip() != repository_url:
                _git_ok(_git_run(["remote", "set-url", opts.remote, repository_url], str(workspace.workdir), runner=git_runner))

            _git_ok(_git_run(
                ["push", "--set-upstream", opts.remote, f"HEAD:{opts.branch}"],
                str(workspace.workdir), token=token, runner=git_runner,
            ))
            _save_state(
                workspace,
                status=STATUS_DELIVERED,
                repo_name=repo_name,
                repository_url=repository_url,
                pushed_at=_now(),
                updated_at=_now(),
                last_error=None,
            )
            return DeliveryResult(status=STATUS_DELIVERED, repository_url=repository_url, pushed=True)
        except (DeliveryError, ValueError, OSError) as exc:
            error = _safe_error(exc, (token,))
            _save_state(
                workspace,
                status=STATUS_FAILED,
                repo_name=repo_name,
                repository_url=repository_url,
                last_error=error,
                updated_at=_now(),
            )
            return DeliveryResult(status=STATUS_FAILED, repository_url=repository_url, error=error)


# ``run_delivery`` はpipeline側が読みやすい別名。
run_delivery = deliver

__all__ = [
    "DEFAULT_GITHUB_API",
    "DEFAULT_BRANCH",
    "DEFAULT_REMOTE",
    "DeliveryError",
    "DeliveryOptions",
    "DeliveryResult",
    "GitHubClient",
    "STATUS_AWAITING_APPROVAL",
    "STATUS_BLOCKED",
    "STATUS_DELIVERED",
    "STATUS_FAILED",
    "deliver",
    "run_delivery",
]
