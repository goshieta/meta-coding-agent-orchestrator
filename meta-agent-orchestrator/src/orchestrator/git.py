"""git 操作ヘルパー（PLAN F-05 / TASK.md Task 9）。

実装ループ（executor）が成果物の作業ディレクトリで**タスク完了ごとに commit** し、
過程を追跡するための薄いラッパー。冪等性・再実行の観点から、git 実行は
テスト注入可能な callable（``runner``）に委譲できる。

- ``is_git_repo`` : 該当ディレクトリが git リポジトリか
- ``ensure_git_repo`` : リポジトリでなければ ``git init -b main`` する（冪等）
- ``commit_all`` : 未コミット変更を `git add -A` + `git commit -m` で全コミット
- ``log`` : コミット履歴（hash / subject）を返す

注: executor は git commit という「管理・追跡」操作のみを行い、コード本体の
読み書きは pi に委譲する（Manager-only 原則）。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

GIT_BIN = "git"
DEFAULT_GIT_USER_NAME = "Meta-Agent Orchestrator"
DEFAULT_GIT_USER_EMAIL = "orchestrator@pi.local"

# コミットで対象に含める／除外するファイル（除外は .gitignore 側でも制御）
# ここでは「タスク完了ごとに commit」＝変更全体を追跡する方針で add -A を使う。


@dataclass
class GitResult:
    """git 操作の結果。``ok=False`` は操作失敗、``committed=False`` は変更なし。"""

    ok: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""
    committed: bool | None = None  # None=該当なし / True=commit 実行 / False=変更なし
    hash: str | None = None        # 生成されたコミットのハッシュ（あれば）

    @property
    def message(self) -> str:
        parts = [x for x in (self.stdout, self.stderr) if x]
        return "\n".join(p.strip() for p in parts)


def _git_run(
    args: list[str],
    cwd: str,
    runner: Callable[[list[str], str], dict] | None = None,
) -> dict:
    """git を subprocess で実行し {returncode, stdout, stderr} を返す。"""
    if runner is not None:
        return runner(args, cwd)
    try:
        proc = subprocess.run(
            [GIT_BIN, *[str(a) for a in args]],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=120,
        )
        return {"returncode": proc.returncode, "stdout": proc.stdout or "", "stderr": proc.stderr or ""}
    except FileNotFoundError:
        return {"returncode": -127, "stdout": "", "stderr": f"git 実行ファイルが見つかりません: {GIT_BIN}"}
    except subprocess.TimeoutExpired:  # pragma: no cover - 大規模リポジトリ等
        return {"returncode": -124, "stdout": "", "stderr": "git 実行がタイムアウトしました（120s）。"}


def _ok(result: dict, *, committed: bool | None = None, hash: str | None = None) -> GitResult:
    return GitResult(
        ok=result["returncode"] == 0,
        returncode=result["returncode"],
        stdout=result.get("stdout") or "",
        stderr=result.get("stderr") or "",
        committed=committed,
        hash=hash,
    )


def _extract_hash(commit_stdout: str) -> str | None:
    """`[main abc1234] subject` からコミットハッシュを抽出する。"""
    import re

    m = re.search(r"\[[^\]]*?([0-9a-f]{7,40})\]", commit_stdout)
    return m.group(1) if m else None


def is_git_repo(path: str | Path, runner: Callable | None = None) -> bool:
    """``path`` が git リポジトリの作業ツリー内かどうかを返す。"""
    r = _git_run(["rev-parse", "--is-inside-work-tree"], cwd=str(path), runner=runner)
    return r["returncode"] == 0 and (r.get("stdout") or "").strip() == "true"


def ensure_git_repo(path: str | Path, runner: Callable | None = None) -> GitResult:
    """git リポジトリでなければ初期化して成功可否を返す（冪等）。"""
    if is_git_repo(path, runner=runner):
        return GitResult(ok=True, returncode=0, stdout="already a git repository")
    r = _git_run(["init", "-b", "main"], cwd=str(path), runner=runner)
    return _ok(r)


def ensure_git_identity(path: str | Path, runner: Callable | None = None) -> GitResult:
    """ローカルgit作者情報を確認し、未設定時だけ安全な既定値を設定する。

    グローバルgit設定は変更しない。既存の作者情報は尊重するため、既存リポジトリで
    人間が設定した名前・メールアドレスを上書きしない。
    """
    path = str(path)
    checks = (
        ("user.name", DEFAULT_GIT_USER_NAME),
        ("user.email", DEFAULT_GIT_USER_EMAIL),
    )
    for key, default in checks:
        current = _git_run(["config", "--local", "--get", key], cwd=path, runner=runner)
        if current["returncode"] == 0 and (current.get("stdout") or "").strip():
            continue
        configured = _git_run(["config", "--local", key, default], cwd=path, runner=runner)
        if configured["returncode"] != 0:
            return _ok(configured)
    return GitResult(ok=True, returncode=0, stdout="git identity is ready")


def commit_all(
    path: str | Path,
    message: str,
    runner: Callable | None = None,
) -> GitResult:
    """作業ディレクトリの変更を全て commit する。

    - リポジトリでなければ ``init`` してから commit（結果の追跡を保証）。
    - ``git add -A`` で全変更をステージし、``git commit -m <message>``。
    - 「nothing to commit / nothing added」はエラーではなく ``committed=False`` を返す。

    Returns:
        :class:`GitResult`。``ok`` は操作全体の成否、``committed`` は実際に
        commit が生成されたか（変更なしの場合は False）。
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if not is_git_repo(path, runner=runner):
        initialized = ensure_git_repo(path, runner=runner)
        if not initialized.ok:
            return initialized

    identity = ensure_git_identity(path, runner=runner)
    if not identity.ok:
        return identity

    add = _git_run(["add", "-A"], cwd=str(path), runner=runner)
    if add["returncode"] != 0:
        return _ok(add)

    commit = _git_run(["commit", "-m", message], cwd=str(path), runner=runner)
    err = commit.get("stderr") or ""
    out = commit.get("stdout") or ""
    if commit["returncode"] != 0:
        if "nothing to commit" in err or "nothing added to commit" in err:
            # 変更なし（冪等）: エラーではない
            return GitResult(ok=True, returncode=0, stdout=out, stderr=err, committed=False, hash=None)
        return _ok(commit)
    return GitResult(
        ok=True,
        returncode=0,
        stdout=out,
        stderr=err,
        committed=True,
        hash=_extract_hash(out),
    )


def log(path: str | Path, n: int = 10, runner: Callable | None = None) -> list[str]:
    """最近のコミット履歴を ``"abc1234 subject"`` 形式で返す。"""
    r = _git_run(["log", f"-{n}", "--format=%h %s"], cwd=str(path), runner=runner)
    if r["returncode"] != 0:
        return []
    return [line for line in (r.get("stdout") or "").strip().splitlines() if line]


def short_hash(path: str | Path, runner: Callable | None = None) -> str | None:
    """最新コミットの短いハッシュを返す（無ければ None）。"""
    lines = log(path, n=1, runner=runner)
    if not lines:
        return None
    return lines[0].split()[0] if lines[0].split() else None
