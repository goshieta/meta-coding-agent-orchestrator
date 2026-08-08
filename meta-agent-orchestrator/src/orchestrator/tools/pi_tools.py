"""pi 連携 CrewAI Tool 群（PLAN F-05 / TASK.md Task 4）。

マネジメント層（CrewAI）が pi プロセスを **subprocess** で呼び出して操作するための
Tool 群。実行の実体は常に pi であり、マネジメント層エージェントはコードへ
直接アクセスせず、本 Tool 経由でのみ pi を駆動する（Manager-only 原則）。

提供 Tool:
    - ``run_pi_single``   : ``pi -p @spec.md "タスク"`` のワンショット実行（新規タスク）
    - ``run_pi_continue`` : ``pi -c`` / ``--session <id>`` によるセッション継続
    - ``run_pi_fork``     : ``pi --fork <id>`` で新セッション生成（失敗・再実行時）
    - ``compact_context`` : セッションへコンテクスト圧縮（/compact 相当）の指示を送る
    - ``export_session``  : セッション（JSONL）を永続化する
    - ``run_pi_survey``   : 読み取り専用ツール（--tools read,grep,find,ls）で既存コードを解析（F-10）

各 Tool は :class:`orchestrator.config.OrchestratorConfig` から、provider / model /
thinking / trust 制御（--approve 等）を pi のコマンドへ付与する。

ノート（headless での /compact ・ /export）:
    pi の ``/compact`` と ``/export`` は対話型 TUI のスラッシュコマンドであり、
    非対話（``-p``）モードでは直接実行できない。本 Tool 群では非対話オーケストレーションに
    合わせて以下で対応する。
    - ``compact_context`` : セッションへ「圧縮して続行」という指示プロンプトを pi に送る
      （自動運用でのコンテクスト解放を担う）。
    - ``export_session``  : pi のセッションファイル（JSONL）を永続ディレクトリへ複製
      （/export の JSONL 化・永続化に相当）。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from crewai.tools import BaseTool
from pydantic import PrivateAttr

from orchestrator.config import OrchestratorConfig

logger = logging.getLogger(__name__)

# pi 実行コマンド名（PATH から解決。無ければそのまま subprocess に渡す）
PI_COMMAND = "pi"

# --thinking のデフォルトレベル（config.pi_thinking が True のとき）
_THINKING_LEVEL_ON = "high"
_THINKING_LEVEL_OFF = "off"

# 調査（既存コード把握 F-10）で pi に許可する読み取り専用ツールの allowlist
SURVEY_TOOLS: tuple[str, ...] = ("read", "grep", "find", "ls")

# 非0終了コードとして返す擬似コード（pi が起動できなかった場合など）
RC_NOT_FOUND = -127
RC_TIMEOUT = -124
RC_SESSION_NOT_FOUND = -2


# ---------------------------------------------------------------------------
# pi 利用可否・コマンド構築
# ---------------------------------------------------------------------------
def pi_available() -> bool:
    """pi 実行ファイルが PATH 上に存在するかを返す（テスト・事前確認用）。"""
    return shutil.which(PI_COMMAND) is not None


def _pi_bin() -> str:
    """pi の実行パス（解決できれば絶対パス、できなければ 'pi'）。"""
    return shutil.which(PI_COMMAND) or PI_COMMAND


def _pi_tools_flag(tools: list[str] | tuple[str, ...] | None) -> list[str]:
    """`--tools` の allowlist フラグを構築する（指定時のみ）。

    読み取り専用ツール（SURVEY_TOOLS 等）で pi を制限実行するために使う。
    """
    if not tools:
        return []
    return ["--tools", ",".join(tools)]


def _pi_common_flags(config: OrchestratorConfig, tools: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """provider / session-dir / trust /（任意）tools allowlist の共通フラグを構築する。"""
    flags: list[str] = []
    if config.pi_provider:
        flags += ["--provider", config.pi_provider]
    flags += _pi_session_dir_flags(config)
    flags += _pi_trust_flags(config)
    flags += _pi_tools_flag(tools)
    return flags


def _pi_model_flags(
    config: OrchestratorConfig,
    model: str | None = None,
    thinking: bool | None = None,
) -> list[str]:
    """--model と --thinking フラグを構築する。

    ``model`` 未指定時は既定の実行モデル（``config.exec_model``）を使う。
    ``thinking`` は config.pi_thinking（bool）をレベル文字列へ変換して付与する。
    """
    flags: list[str] = []
    chosen = model or config.exec_model
    if chosen:
        flags += ["--model", chosen]
    level = _THINKING_LEVEL_ON if (thinking if thinking is not None else config.pi_thinking) else _THINKING_LEVEL_OFF
    flags += ["--thinking", level]
    return flags


def _pi_session_dir_flags(config: OrchestratorConfig) -> list[str]:
    if config.pi_session_dir:
        return ["--session-dir", config.pi_session_dir]
    return []


def _pi_trust_flags(config: OrchestratorConfig) -> list[str]:
    """trust 制御を --approve / --no-approve へ変換する。

    ``PI_TRUST`` の値:
        - approve 系（approve / a / yes）-> ``--approve``
        - 拒否系（no-approve / na / none / no）-> ``--no-approve``
        - その他（defaultProjectTrust 等）-> フラグなし（pi 既定動作）
    """
    trust = (config.pi_trust or "").strip().lower()
    if trust in {"approve", "a", "yes"}:
        return ["--approve"]
    if trust in {"no-approve", "na", "none", "no"}:
        return ["--no-approve"]
    return []


def _print_arg() -> list[str]:
    return ["-p"]


# ---- 各モードのコマンド構築（テスト可能な純粋関数） -------------------------
def build_single_cmd(
    config: OrchestratorConfig,
    task: str,
    spec_file: str | None = None,
    model: str | None = None,
    thinking: bool | None = None,
) -> list[str]:
    """``pi -p [@spec] "タスク"`` のワンショット実行コマンドを構築する。"""
    cmd = [_pi_bin()]
    cmd += _pi_model_flags(config, model, thinking)
    cmd += _pi_common_flags(config)
    cmd += _print_arg()
    if spec_file:
        cmd += [f"@{spec_file}"]
    cmd += [task]
    return cmd


def build_continue_cmd(
    config: OrchestratorConfig,
    task: str,
    session: str | None = None,
    model: str | None = None,
    thinking: bool | None = None,
) -> list[str]:
    """``pi -c "タスク"`` / ``pi --session <id> "タスク"`` の継続コマンドを構築する。"""
    cmd = [_pi_bin()]
    cmd += _pi_model_flags(config, model, thinking)
    cmd += _pi_common_flags(config)
    cmd += _print_arg()
    if session:
        cmd += ["--session", session]
    else:
        cmd += ["-c"]
    cmd += [task]
    return cmd


def build_fork_cmd(
    config: OrchestratorConfig,
    session: str,
    task: str,
    model: str | None = None,
    thinking: bool | None = None,
) -> list[str]:
    """``pi --fork <id> "タスク"`` の新セッション生成コマンドを構築する。"""
    cmd = [_pi_bin()]
    cmd += _pi_model_flags(config, model, thinking)
    cmd += _pi_common_flags(config)
    cmd += _print_arg()
    cmd += ["--fork", session, task]
    return cmd


def build_survey_cmd(
    config: OrchestratorConfig,
    prompt: str,
    model: str | None = None,
    thinking: bool | None = None,
) -> list[str]:
    """読み取り専用ツールで既存コードを調査する ``pi -p --tools read,grep,find,ls`` コマンドを構築する。

    実行時は ``cwd`` を調査対象リポジトリへ指定すること（Manager-only 原則：
    pi が read / grep / find / ls 経由でのみコードへアクセスする）。
    """
    cmd = [_pi_bin()]
    cmd += _pi_model_flags(config, model, thinking)
    cmd += _pi_common_flags(config, tools=SURVEY_TOOLS)
    cmd += _print_arg()
    cmd += [prompt]
    return cmd


def build_compact_cmd(
    config: OrchestratorConfig,
    session: str,
    instructions: str = "",
    model: str | None = None,
    thinking: bool | None = None,
) -> list[str]:
    """セッションへのコンテクスト圧縮指示コマンドを構築する。"""
    cmd = [_pi_bin()]
    cmd += _pi_model_flags(config, model, thinking)
    cmd += _pi_common_flags(config)
    cmd += _print_arg()
    cmd += ["--session", session]
    message = "会話を圧縮（/compact 相当）して、最重要の要約だけを維持した上で続行してください。"
    if instructions:
        message += f" 焦点: {instructions}"
    cmd += [message]
    return cmd


# ---------------------------------------------------------------------------
# サブプロセス実行とレポート整形
# ---------------------------------------------------------------------------
def _run_pi(argv: list[str], cwd: str | None = None, timeout: int = 3600) -> dict[str, Any]:
    """pi を subprocess で実行し、{returncode, stdout, stderr} を返す。"""
    try:
        proc = subprocess.run(
            [str(a) for a in argv],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
        }
    except FileNotFoundError:
        return {
            "returncode": RC_NOT_FOUND,
            "stdout": "",
            "stderr": f"pi 実行ファイルが見つかりません: {argv[0]}",
        }
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - 長時間運用時のみ
        return {
            "returncode": RC_TIMEOUT,
            "stdout": exc.stdout or "" if isinstance(exc.stdout, str) else "",
            "stderr": f"pi 実行がタイムアウトしました（{timeout}s）。",
        }


def _format_report(command: list[str], result: dict[str, Any]) -> str:
    """pi の実行結果を自然言語レポートとして整形する（オーケストレータが消費）。"""
    rc = result.get("returncode")
    lines = [
        f"実行コマンド: pi {' '.join(command)}",
        f"exit code: {rc}",
    ]
    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    if stdout:
        lines.append("--- stdout ---")
        lines.append(stdout)
    if stderr:
        lines.append("--- stderr ---")
        lines.append(stderr)
    if rc == 0:
        lines.append("ステータス: 成功")
    else:
        lines.append("ステータス: 失敗（→ run_pi_fork で再実行を検討）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# セッション（JSONL）の永続化（export_session 用）
# ---------------------------------------------------------------------------
def _session_dir(config: OrchestratorConfig) -> Path:
    """セッション保存ディレクトリを返す（設定 or pi 既定）。"""
    if config.pi_session_dir:
        return Path(config.pi_session_dir).expanduser()
    return Path.home() / ".pi" / "agent" / "sessions"


def _find_session_file(session: str, session_dir: Path) -> Path | None:
    """セッション ID/パスから JSONL セッションファイルを解決する。"""
    candidate = Path(session)
    if candidate.is_file() and candidate.suffix == ".jsonl":
        return candidate
    if not session_dir.is_dir():
        return None
    try:
        for path in session_dir.rglob("*.jsonl"):
            # ファイル名（stem）またはパス文字列にセッション ID が含まれるか
            if session in path.stem or session in str(path):
                return path
    except OSError:
        return None
    return None


def _export_session_jsonl(
    config: OrchestratorConfig,
    session: str,
    dest: str | None = None,
) -> str:
    """セッション JSONL を永続化し、レポートを返す。"""
    session_dir = _session_dir(config)
    src = _find_session_file(session, session_dir)
    if src is None:
        result = {
            "returncode": RC_SESSION_NOT_FOUND,
            "stdout": "",
            "stderr": (
                f"セッションファイルが見つかりません: {session!r} "
                f"（検索先: {session_dir}）。pi 内で --session <id> により保存してください。"
            ),
        }
        return _format_report(["pi", "--export", session, dest or "(検索)"], result)

    dest_path = Path(dest).expanduser() if dest else None
    if dest_path is not None and dest_path.is_dir():
        dest_path = dest_path / src.name
    elif dest_path is None:
        snapshots = session_dir / "exports"
        snapshots.mkdir(parents=True, exist_ok=True)
        dest_path = snapshots / f"{src.stem}.export.jsonl"

    import shutil as _sh

    _sh.copy2(src, dest_path)
    result = {
        "returncode": 0,
        "stdout": f"セッション '{src}' を '{dest_path}' へ JSONL で永続化しました。",
        "stderr": "",
    }
    return _format_report(["pi", "--session", session, "--export", str(dest_path)], result)


# ---------------------------------------------------------------------------
# CrewAI Tool 定義
# ---------------------------------------------------------------------------
class RunPiSingleTool(BaseTool):
    """pi をワンショット（非対話 -p）で実行し、タスクを遂行させる Tool。"""

    name: str = "run_pi_single"
    description: str = (
        "Run a one-shot pi session (`pi -p @spec.md \"task\"`) to execute a single task "
        "(implement / investigate / verify). Use for the FIRST execution of a fresh task. "
        "Returns a natural-language report with the exit code. Manager agents must use this "
        "tool instead of touching code directly."
    )

    _config: OrchestratorConfig = PrivateAttr(default_factory=OrchestratorConfig)

    def _run(self, task: str, spec_file: str | None = None, model: str | None = None) -> str:
        cmd = build_single_cmd(self._config, task, spec_file=spec_file, model=model)
        result = _run_pi(cmd)
        return _format_report(cmd, result)


class RunPiContinueTool(BaseTool):
    """既存セッションを継続（-c / --session）してタスクを進める Tool。"""

    name: str = "run_pi_continue"
    description: str = (
        "Continue an existing pi session (`pi -c` or `pi --session <id>`) with a follow-up "
        "task/steering instruction. Use to resume work in the SAME session. Returns a "
        "natural-language report with the exit code."
    )

    _config: OrchestratorConfig = PrivateAttr(default_factory=OrchestratorConfig)

    def _run(
        self,
        task: str,
        session: str | None = None,
        model: str | None = None,
    ) -> str:
        cmd = build_continue_cmd(self._config, task, session=session, model=model)
        result = _run_pi(cmd)
        return _format_report(cmd, result)


class RunPiForkTool(BaseTool):
    """失敗・再実行時にセッションを fork（--fork）して新セッションで再実行する Tool。"""

    name: str = "run_pi_fork"
    description: str = (
        "Fork an existing pi session into a NEW session (`pi --fork <id> \"task\"`) and run "
        "a task there. Use to RETRY a failed task with fresh context. Returns a "
        "natural-language report with the exit code."
    )

    _config: OrchestratorConfig = PrivateAttr(default_factory=OrchestratorConfig)

    def _run(self, session: str, task: str, model: str | None = None) -> str:
        cmd = build_fork_cmd(self._config, session, task, model=model)
        result = _run_pi(cmd)
        return _format_report(cmd, result)


class CompactContextTool(BaseTool):
    """セッションへコンテクスト圧縮（/compact 相当）の指示を送る Tool。"""

    name: str = "compact_context"
    description: str = (
        "Send a context-compaction directive to a pi session (equivalent of the `/compact` "
        "TUI command) to free up context when the conversation grows large. Use periodically "
        "during long implementation loops. Returns a natural-language report with the exit code."
    )

    _config: OrchestratorConfig = PrivateAttr(default_factory=OrchestratorConfig)

    def _run(
        self,
        session: str,
        instructions: str = "",
        model: str | None = None,
    ) -> str:
        cmd = build_compact_cmd(self._config, session, instructions=instructions, model=model)
        result = _run_pi(cmd)
        return _format_report(cmd, result)


class RunPiSurveyTool(BaseTool):
    """読み取り専用ツールで既存コードベースを調査・現状把握する Tool（F-10）。"""

    name: str = "run_pi_survey"
    description: str = (
        "Survey / analyze an EXISTING codebase in READ-ONLY mode by running pi with a "
        "restricted tool allowlist (`--tools read,grep,find,ls`). Use to understand the current "
        "state of an existing repository and produce a status report (F-10). No write/bash "
        "tools are permitted, so it cannot modify anything. Returns a natural-language report "
        "with the exit code."
    )

    _config: OrchestratorConfig = PrivateAttr(default_factory=OrchestratorConfig)
    _repo_dir: str | None = PrivateAttr(default=None)

    def _run(self, prompt: str, model: str | None = None) -> str:
        cmd = build_survey_cmd(self._config, prompt, model=model)
        result = _run_pi(cmd, cwd=self._repo_dir)
        return _format_report(cmd, result)


class ExportSessionTool(BaseTool):
    """セッション（JSONL）を永続化する Tool（/export の JSONL 化に相当）。"""

    name: str = "export_session"
    description: str = (
        "Persist a pi session by copying its JSONL file to a durable location "
        "(equivalent of the `/export` / JSONL persistence). Optionally provide a destination "
        "file or directory. Returns a natural-language report with the exit code / saved path."
    )

    _config: OrchestratorConfig = PrivateAttr(default_factory=OrchestratorConfig)

    def _run(self, session: str, dest: str | None = None) -> str:
        return _export_session_jsonl(self._config, session, dest)


# ---------------------------------------------------------------------------
# ファクトリ
# ---------------------------------------------------------------------------
def build_pi_tools(config: OrchestratorConfig | None = None) -> list[BaseTool]:
    """設定に紐づいた pi Tool 一式を生成する。

    Args:
        config: モデル割当や trust 等の設定。省略時は既定値。

    Returns:
        pi 連携 Tool のリスト（CrewAI Agent にツールとして渡す）。
    """
    config = config or OrchestratorConfig()
    tools: list[BaseTool] = [
        RunPiSingleTool(),
        RunPiContinueTool(),
        RunPiForkTool(),
        CompactContextTool(),
        ExportSessionTool(),
        RunPiSurveyTool(),
    ]
    for t in tools:
        t._config = config
        if isinstance(t, RunPiSurveyTool):
            t._repo_dir = None
    return tools


def make_survey_tool(config: OrchestratorConfig | None = None, repo_dir: str | None = None) -> BaseTool:
    """調査専用 Tool（run_pi_survey）を返す。既存リポジトリを ``repo_dir`` で指定できる。"""
    tool = RunPiSurveyTool()
    tool._config = config or OrchestratorConfig()
    tool._repo_dir = repo_dir
    return tool


def make_pi_single_tool(config: OrchestratorConfig | None = None) -> BaseTool:
    """単体取得用ヘルパー（run_pi_single）。"""
    return build_pi_tools(config)[0]
