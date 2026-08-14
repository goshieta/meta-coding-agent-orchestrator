"""人間との最小限の接点（PLAN F-08 / Task 12）。

マネジメント層が仕様の不明点を質問し、人間の回答を共有状態へ保存してから
処理を続行するためのゲートを提供する。stdinを利用する対話APIに加え、UI/CLIや
Task 11の納品処理から利用できる、注入可能なプログラマブルAPIも備える。

重要な設計:

* 質問・回答・停止状態は ``Workspace.state.json`` に保存する。
* 強制停止は処理を中断する前に状態を ``stopped`` として保存する。
* 再開時は保存済みの質問履歴・回答・piセッションを保持したまま続行できる。
* HumanGateはコードや成果物へ直接アクセスせず、質問と状態だけを管理する。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from orchestrator.workspace import Workspace

STATUS_IDLE = "idle"
STATUS_AWAITING_ANSWER = "awaiting_answer"
STATUS_ANSWERED = "answered"
STATUS_STOPPED = "stopped"
STATUS_RESUMED = "resumed"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

# 大文字小文字や先頭の空白にかかわらず、入力行を停止コマンドとして扱う。
STOP_COMMANDS = frozenset({
    "stop", "force-stop", "force_stop", "halt", "abort", "shutdown",
    "/stop", ":stop", "停止", "強制停止", "中断",
})
RESUME_COMMANDS = frozenset({"resume", "continue", "/resume", ":resume", "再開", "続行"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return f"q-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class HumanQuestion:
    """人間へ提出した質問。"""

    question_id: str
    text: str
    context: str = ""
    task_id: int | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "text": self.text,
            "context": self.context,
            "task_id": self.task_id,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class HumanAnswer:
    """質問に対する人間の回答。"""

    question_id: str
    text: str
    answered_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "text": self.text,
            "answered_at": self.answered_at,
        }


@dataclass(frozen=True)
class HumanGateResult:
    """HumanGateの操作結果。"""

    status: str
    question_id: str | None = None
    answer: str | None = None
    stopped: bool = False
    resumed: bool = False
    approved: bool | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {STATUS_ANSWERED, STATUS_RESUMED, STATUS_APPROVED}


class HumanGateError(RuntimeError):
    """質問・回答・停止状態の不整合。"""


class HumanGate:
    """質問提出、回答待ち、強制停止、再開を管理する状態付きゲート。"""

    def __init__(
        self,
        workspace: Workspace,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], object] = print,
        stop_commands: Iterable[str] = STOP_COMMANDS,
        resume_commands: Iterable[str] = RESUME_COMMANDS,
    ) -> None:
        self.workspace = workspace
        self.input_fn = input_fn
        self.output_fn = output_fn
        self.stop_commands = frozenset(self._normalize(c) for c in stop_commands)
        self.resume_commands = frozenset(self._normalize(c) for c in resume_commands)

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(str(value).strip().lower().split())

    @property
    def state(self) -> dict[str, object]:
        return self.workspace.human_gate_state

    @property
    def stopped(self) -> bool:
        return self.state.get("status") == STATUS_STOPPED

    @property
    def waiting(self) -> bool:
        return self.state.get("status") == STATUS_AWAITING_ANSWER

    @property
    def active_question_id(self) -> str | None:
        value = self.state.get("active_question_id")
        return str(value) if value else None

    def _persist(self, **fields: object) -> dict[str, object]:
        """履歴を含む状態をWorkspaceへ一度で保存する。"""
        return self.workspace.set_human_gate_state(**fields, updated_at=_now())

    def submit_question(
        self,
        text: str,
        *,
        context: str = "",
        task_id: int | None = None,
        question_id: str | None = None,
    ) -> HumanQuestion:
        """質問を永続化し、人間の回答待ち状態へ移行する。"""
        text = str(text).strip()
        if not text:
            raise ValueError("質問が空です")
        if self.stopped:
            raise HumanGateError("HumanGateは停止中です。resume()後に質問してください")
        if self.waiting:
            raise HumanGateError(f"回答待ちの質問があります: {self.active_question_id}")

        question = HumanQuestion(
            question_id=question_id or _new_id(),
            text=text,
            context=str(context).strip(),
            task_id=task_id,
            created_at=_now(),
        )
        history = list(self.state.get("history", []))
        history.append({"question": question.to_dict(), "answer": None})
        self._persist(
            status=STATUS_AWAITING_ANSWER,
            active_question_id=question.question_id,
            active_question=question.to_dict(),
            history=history,
            stop_reason=None,
        )
        if task_id is not None:
            self.workspace.log(task_id, f"[human_gate] 質問を提出しました: {question.question_id}")
        return question

    def _find_history(self, question_id: str) -> tuple[int, dict[str, object]]:
        history = list(self.state.get("history", []))
        for index, entry in enumerate(history):
            question = entry.get("question", {}) if isinstance(entry, dict) else {}
            if isinstance(question, dict) and question.get("question_id") == question_id:
                return index, entry
        raise KeyError(f"質問が存在しません: {question_id}")

    def answer_question(self, question_id: str, answer: str) -> HumanGateResult:
        """指定された質問へ回答し、続行可能な状態へ保存する。"""
        if self.stopped:
            raise HumanGateError("停止中のため回答できません。resume()後に回答してください")
        if self.active_question_id != question_id:
            raise HumanGateError(f"現在回答待ちではない質問です: {question_id}")
        answer = str(answer).strip()
        if not answer:
            raise ValueError("回答が空です")

        index, entry = self._find_history(question_id)
        answer_obj = HumanAnswer(question_id, answer, _now())
        history = list(self.state.get("history", []))
        updated = dict(entry)
        updated["answer"] = answer_obj.to_dict()
        history[index] = updated
        question = self.state.get("active_question", {})
        task_id = question.get("task_id") if isinstance(question, dict) else None
        self._persist(
            status=STATUS_ANSWERED,
            active_question_id=None,
            active_question=None,
            last_question_id=question_id,
            last_answer=answer_obj.to_dict(),
            history=history,
            stop_reason=None,
        )
        if isinstance(task_id, int):
            self.workspace.log(task_id, f"[human_gate] 回答を受領しました: {question_id}")
        return HumanGateResult(STATUS_ANSWERED, question_id, answer, message="回答を保存しました")

    def handle_command(self, command: str, *, question_id: str | None = None) -> HumanGateResult | None:
        """停止・再開コマンドを解釈する。通常の回答なら ``None`` を返す。"""
        normalized = self._normalize(command)
        if normalized in self.stop_commands:
            return self.force_stop(reason=f"人間の停止コマンド: {command.strip() or 'stop'}")
        if normalized in self.resume_commands:
            return self.resume()
        return None

    def force_stop(self, *, reason: str = "人間からの強制停止") -> HumanGateResult:
        """停止状態を先に永続化してから、呼び出し元へ停止結果を返す。

        piセッションやタスクの途中状態は削除しないため、外側のパイプラインは
        ``Workspace.open`` とTask9の再開処理を使って後から復旧できる。
        """
        reason = str(reason).strip() or "人間からの強制停止"
        self._persist(
            status=STATUS_STOPPED,
            stopped_at=_now(),
            stop_reason=reason,
            active_question_id=self.active_question_id,
        )
        return HumanGateResult(STATUS_STOPPED, self.active_question_id, stopped=True, message=reason)

    def resume(self) -> HumanGateResult:
        """停止状態を解除する。保留中の質問があれば回答待ちへ戻す。"""
        if not self.stopped:
            return HumanGateResult(self.state.get("status", STATUS_IDLE), resumed=False, message="停止状態ではありません")
        next_status = STATUS_AWAITING_ANSWER if self.active_question_id else STATUS_RESUMED
        self._persist(status=next_status, resumed_at=_now(), stop_reason=None)
        return HumanGateResult(next_status, self.active_question_id, resumed=True, message="処理を再開できます")

    def wait_for_answer(
        self,
        question_id: str | None = None,
        *,
        input_fn: Callable[[str], str] | None = None,
    ) -> HumanGateResult:
        """stdin等から回答を待つ。待機中の停止コマンドは安全停止として処理する。"""
        question_id = question_id or self.active_question_id
        if not question_id or not self.waiting:
            raise HumanGateError("回答待ちの質問がありません")
        question = self.state.get("active_question", {})
        prompt = f"[human_gate] {question.get('text', '回答を入力してください')}\n> "
        read = input_fn or self.input_fn
        while True:
            try:
                value = read(prompt)
            except (EOFError, KeyboardInterrupt):
                return self.force_stop(reason="入力ストリーム終了または割り込み")
            command_result = self.handle_command(value, question_id=question_id)
            if command_result is not None:
                if command_result.stopped:
                    return command_result
                if command_result.resumed:
                    continue
            if self.stopped:
                return HumanGateResult(STATUS_STOPPED, question_id, stopped=True)
            return self.answer_question(question_id, value)

    def ask(
        self,
        text: str,
        *,
        context: str = "",
        task_id: int | None = None,
        wait: bool = True,
        input_fn: Callable[[str], str] | None = None,
    ) -> HumanQuestion | HumanGateResult:
        """質問を提出する。``wait=True``なら回答または停止まで対話的に待つ。"""
        question = self.submit_question(text, context=context, task_id=task_id)
        if not wait:
            return question
        return self.wait_for_answer(question.question_id, input_fn=input_fn)

    def request_approval(
        self,
        prompt: str = "成果物をGitHubへ納品してよいですか？",
        *,
        input_fn: Callable[[str], str] | None = None,
    ) -> bool:
        """Task 11の ``approval`` callable として利用できる対話承認。

        ``yes/y/はい/承認/approve`` のみ承認とし、それ以外（停止を含む）は拒否する。
        停止コマンドなら、回答待ち状態を保存した上で安全停止する。
        """
        result = self.ask(prompt, context="最終納品承認", wait=True, input_fn=input_fn)
        if not isinstance(result, HumanGateResult):
            return False
        if result.stopped:
            return False
        approved = self._normalize(result.answer or "") in {"yes", "y", "はい", "承認", "approve", "approved"}
        self._persist(
            status=STATUS_APPROVED if approved else STATUS_REJECTED,
            approval=approved,
            approval_at=_now(),
        )
        return approved


def force_stop(workspace: Workspace, reason: str = "人間からの強制停止") -> HumanGateResult:
    """簡易API: 実行中のワークスペースを安全停止する。"""
    return HumanGate(workspace).force_stop(reason=reason)


def resume(workspace: Workspace) -> HumanGateResult:
    """簡易API: 停止済みワークスペースを再開可能にする。"""
    return HumanGate(workspace).resume()


__all__ = [
    "HumanAnswer",
    "HumanGate",
    "HumanGateError",
    "HumanGateResult",
    "HumanQuestion",
    "STATUS_ANSWERED",
    "STATUS_APPROVED",
    "STATUS_AWAITING_ANSWER",
    "STATUS_IDLE",
    "STATUS_REJECTED",
    "STATUS_RESUMED",
    "STATUS_STOPPED",
    "STOP_COMMANDS",
    "RESUME_COMMANDS",
    "force_stop",
    "resume",
]
