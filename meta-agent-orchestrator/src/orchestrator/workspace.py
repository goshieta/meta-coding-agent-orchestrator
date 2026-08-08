"""ワークスペース・共有ボード・状態管理 (PLAN F-04 / F-05 / Task 5)。

実装・計画ループで共有する共通基盤を提供する:

- 作業ディレクトリ（``workdir``）の生成・管理
- 共有ボード ``plan.md`` の読み書き（タスク一覧・状態・依存・優先度）
- タスク別ログの保持（``logs/<task>.log``）
- pi セッション（``--session``）・``/export`` による JSONL 永続化の管理
  （再起動時（冪等性）に復元できる）

再起動時の冪等性を保つため、機械可読な状態は ``state.json`` に保存し、
``plan.md`` はそこから生成・更新される人間向けの共有ボードとして扱う。
``Workspace`` は open 時に既存状態があれば再読み込みして復元する。

状態遷移（WORKFLOW）:
    planned → running → done / failed → qa → accepted / failed
    failed  → running（再実行）/ planned（再計画）
    accepted（最終・QA 通過済み）: 終端
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# ファイル・ディレクトリ名の定義
# ---------------------------------------------------------------------------
BOARD_FILENAME = "plan.md"      # 共有ボード（人間向け Markdown）
STATE_FILENAME = "state.json"   # 機械可読な状態（冪等性の要）
LOGS_DIR = "logs"               # タスク別ログ
SESSIONS_DIR = "sessions"       # 永続化した pi セッション JSONL


def _now() -> str:
    """UTC の ISO8601 文字列。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 状態遷移
# ---------------------------------------------------------------------------
class TaskStatus(str, Enum):
    """タスクの状態。

    .. note::
        ``done`` は実装が完了した状態、``accepted`` は QA を通過した最終状態を
        表す。最終達成判定は ``accepted`` を基準にする。
    """

    PLANNED = "planned"   # 計画済み・未着手
    RUNNING = "running"   # 実行中
    DONE = "done"         # 実装完了（QA 待ち）
    FAILED = "failed"     # 失敗（再実行 / 再計画の対象）
    QA = "qa"             # QA 検証中
    ACCEPTED = "accepted" # QA 通過・最終確定（終端）


# バリデーション付き遷移表（例: planned → running → done/failed → qa → done）
WORKFLOW: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PLANNED: {TaskStatus.RUNNING, TaskStatus.FAILED},
    TaskStatus.RUNNING: {TaskStatus.DONE, TaskStatus.FAILED},
    TaskStatus.DONE: {TaskStatus.QA, TaskStatus.FAILED},
    TaskStatus.FAILED: {TaskStatus.RUNNING, TaskStatus.PLANNED},
    TaskStatus.QA: {TaskStatus.ACCEPTED, TaskStatus.DONE, TaskStatus.FAILED},
    TaskStatus.ACCEPTED: set(),
}

# タスク完了（依存完了）判定に使う「完了」状態
_TERMINAL_STATES = {TaskStatus.ACCEPTED}
_COMPLETED_STATES = {TaskStatus.DONE, TaskStatus.QA, TaskStatus.ACCEPTED}


# ---------------------------------------------------------------------------
# データモデル
# ---------------------------------------------------------------------------
@dataclass
class Task:
    """共有ボード上の単一タスク。"""

    id: int
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PLANNED
    dependencies: list[int] = field(default_factory=list)
    priority: int = 1
    session_id: str | None = None
    attempts: int = 0
    report: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    # -- シリアライズ ------------------------------------------------------
    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        data = dict(data)
        data["status"] = TaskStatus(data["status"])
        return cls(**data)


# ---------------------------------------------------------------------------
# ワークスペース
# ---------------------------------------------------------------------------
class Workspace:
    """作業ディレクトリ・共有ボード・ログ・セッションを統合管理する。

    開いた時点で既存の ``state.json`` があれば再読み込みし、前回の進行状態・
    セッションを復元する（冪等性）。
    """

    def __init__(self, workdir: str | Path, create: bool = True) -> None:
        self.workdir = Path(workdir)
        self.board_path = self.workdir / BOARD_FILENAME
        self.state_path = self.workdir / STATE_FILENAME
        self.logs_dir = self.workdir / LOGS_DIR
        self.sessions_dir = self.workdir / SESSIONS_DIR

        self._tasks: list[Task] = []
        self._next_id = 1

        if create:
            self._ensure_dirs()
        self._load_or_init()

    # -- 生成 / 復元 -------------------------------------------------------
    @classmethod
    def open(cls, workdir: str | Path) -> "Workspace":
        """既存（または新規）ワークスペースを開く。存在すれば状態を復元する。"""
        return cls(workdir)

    def _ensure_dirs(self) -> None:
        for d in (self.workdir, self.logs_dir, self.sessions_dir):
            d.mkdir(parents=True, exist_ok=True)

    def _load_or_init(self) -> None:
        """state.json があれば復元、なければ空の状態で初期化する。"""
        if self.state_path.is_file():
            self._load_state()
        else:
            self._tasks = []
            self._next_id = 1
            self.save()

    # -- タスク管理 --------------------------------------------------------
    @property
    def tasks(self) -> list[Task]:
        """状態でソートされたタスク一覧（コピー）。"""
        return sorted(self._tasks, key=lambda t: (t.priority, t.id))

    def add_task(
        self,
        title: str,
        description: str = "",
        dependencies: list[int] | tuple[int, ...] = (),
        priority: int = 1,
        status: TaskStatus = TaskStatus.PLANNED,
    ) -> Task:
        """タスクを追加し、ボード・状態を更新して返す。"""
        task = Task(
            id=self._next_id,
            title=title,
            description=description,
            dependencies=list(dependencies),
            priority=priority,
            status=status,
        )
        self._next_id += 1
        self._tasks.append(task)
        self.save()
        return task

    def get_task(self, task_id: int) -> Task:
        for t in self._tasks:
            if t.id == task_id:
                return t
        raise KeyError(f"タスク {task_id} が存在しません")

    def update_task(self, task_id: int, **fields) -> Task:
        """タスクの任意フィールドを更新し、updated_at を更新して保存する。"""
        task = self.get_task(task_id)
        if "status" in fields:
            fields["status"] = (
                fields["status"] if isinstance(fields["status"], TaskStatus)
                else TaskStatus(fields["status"])
            )
        for key, value in fields.items():
            if not hasattr(task, key):
                raise AttributeError(f"Task にフィールド {key} はありません")
            setattr(task, key, value)
        task.updated_at = _now()
        self.save()
        return task

    def transition(self, task_id: int, new_status: TaskStatus | str) -> Task:
        """状態遷移をバリデーション付きで実行する。

        許可されていない遷移（WORKFLOW 表にない組み合わせ）は ``ValueError``。
        """
        new = new_status if isinstance(new_status, TaskStatus) else TaskStatus(new_status)
        task = self.get_task(task_id)
        if new not in WORKFLOW[task.status]:
            allowed = ", ".join(s.value for s in sorted(WORKFLOW[task.status], key=lambda s: s.value))
            raise ValueError(
                f"不正な状態遷移: {task.status.value} → {new.value} "
                f"（許可: {allowed or 'なし（終端）'}）"
            )
        task.status = new
        if new in (TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.QA):
            task.attempts += 1
        task.updated_at = _now()
        self.save()
        return task

    def ready_tasks(self) -> list[Task]:
        """依存がすべて完了（done / qa / accepted）した PLANNED タスクを返す。"""
        completed_ids = {t.id for t in self._tasks if t.status in _COMPLETED_STATES}
        return [
            t for t in self.tasks
            if t.status is TaskStatus.PLANNED and set(t.dependencies) <= completed_ids
        ]

    # -- ログ --------------------------------------------------------------
    def log_path(self, task_id: int) -> Path:
        """タスク別ログのパス（``logs/<task_id>.log``）。"""
        return self.logs_dir / f"{task_id}.log"

    def log(self, task_id: int, message: str) -> None:
        """タスク別ログへタイムスタンプ付きで追記する。"""
        path = self.log_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{_now()} [{task_id}] {message}\n")

    def read_log(self, task_id: int) -> str:
        """タスク別ログの内容を返す（なければ空文字列）。"""
        path = self.log_path(task_id)
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    # -- pi セッション管理（冪等性） --------------------------------------
    def set_session(self, task_id: int, session_id: str) -> Task:
        """タスクに pi セッションIDを紐付ける。"""
        return self.update_task(task_id, session_id=session_id)

    def restore_session(self, task_id: int) -> str | None:
        """タスクの前回 pi セッションIDを返す（なければ None）。"""
        return self.get_task(task_id).session_id

    def persist_session(self, task_id: int, jsonl_path: str | Path) -> Path:
        """pi の ``/export`` で出力した JSONL を作業領域へ永続化する。

        ``sessions/<task_id>.jsonl`` へコピーし、タスクへセッションIDを記録する。

        Returns:
            コピー先のパス。
        """
        src = Path(jsonl_path)
        if not src.is_file():
            raise FileNotFoundError(f"セッション JSONL が存在しません: {src}")
        dest = self.sessions_dir / f"{task_id}.jsonl"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        self.set_session(task_id, src.stem)
        return dest

    def list_sessions(self) -> dict[int, str]:
        """各タスクの永続化セッションID（task_id -> session_id）を返す。"""
        return {t.id: sid for t in self._tasks if (sid := t.session_id)}

    def restore_all(self) -> dict[int, str]:
        """全タスクの前回セッションを復元（再起動時の冪等性の要）。"""
        return self.list_sessions()

    # -- 永続化 ------------------------------------------------------------
    def save(self) -> None:
        """状態を state.json に保存し、plan.md 共有ボードを再生成する。</think>
        冪等に呼べる（何度呼んでも同じ結果）。"""
        self._ensure_dirs()
        self.state_path.write_text(
            json.dumps(
                {"next_id": self._next_id, "tasks": [t.to_dict() for t in self._tasks]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.board_path.write_text(self._render_plan(), encoding="utf-8")

    def _load_state(self) -> None:
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._next_id = int(data.get("next_id", 1))
        self._tasks = [Task.from_dict(d) for d in data.get("tasks", [])]

    # -- 共有ボード（plan.md）生成 ----------------------------------------
    def _render_plan(self) -> str:
        lines: list[str] = [
            "# 共有ボード (plan.md)",
            "",
            "> このファイルは state.json から自動生成される人間向けの共有ボードです。直接編集しても再生成で上書きされます。",
            f"> 更新: {_now()}",
            "",
            "## タスク一覧",
            "",
            "| ID | 状態 | 優先度 | 依存 | タイトル | セッション | 試行回数 |",
            "|----|------|--------|------|----------|-----------|----------|",
        ]
        for t in self.tasks:
            deps = ",".join(str(d) for d in t.dependencies) or "-"
            ses = t.session_id or "-"
            lines.append(
                f"| {t.id} | {t.status.value} | {t.priority} | {deps} | {t.title} | {ses} | {t.attempts} |"
            )

        lines += [
            "",
            "## タスク詳細",
            "",
        ]
        for t in self.tasks:
            lines += [
                f"### Task {t.id}: {t.title}",
                f"- 状態: `{t.status.value}` ／ 優先度: {t.priority} ／ 試行回数: {t.attempts}",
                f"- 依存: {', '.join(str(d) for d in t.dependencies) or 'なし'}",
                f"- pi セッション: {t.session_id or '未設定'}",
                f"- ログ: `{LOGS_DIR}/{t.id}.log`",
            ]
            if t.description:
                lines.append("")
                lines.append(t.description.strip())
            if t.report:
                lines.append("")
                lines.append(f"**レポート**: {t.report}")
            lines.append("")

        lines += [
            "## 状態遷移",
            "",
            "`planned → running → done / failed → qa → accepted / failed`",
            "",
        ]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"Workspace(workdir={self.workdir!s}, tasks={len(self._tasks)})"
