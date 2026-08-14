"""コンテクスト構築（新規・既存）（PLAN F-03 / F-10 / TASK.md Task 6）。

仕様書を起点に pi へ投入する**初期コンテクスト**を構築する。既存案件では
調査エージェントが pi（読み取り専用ツール ``--tools read,grep,find,ls``）経由で
コードベースを解析し、**現状レポート**を生成する。

- **新規**（``repo_path`` なし）: 仕様書を pi 初期プロンプトとして投入するための
  コンテクスト（``context/initial_prompt.md``）を構築・保存する。
  ``pi @spec.md "..."`` の形で pi に投入できる。
- **既存**（``repo_path`` あり）: 調査エージェント（pi 読み取り専用）でコードベースを
  解析し、自然言語の現状レポート（``context/survey_report.md``）を生成する。

用途は「タスク分解（plan）に必要なコンテクストを揃える」こと。生成物は
ワークスペースの作業ディレクトリへ保存し、後続タスク（Task 8 / 9）が参照する。
子のコンテクストは常に pi（/ pi 連携 Tool）経由で扱うため、マネジメント層は
コードへ直接アクセスしない（Manager-only 原則）。
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from orchestrator.config import OrchestratorConfig
from orchestrator.workspace import Workspace
from orchestrator.tools import pi_tools as pt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# コンテクスト保存先（ワークスペース配下）
# ---------------------------------------------------------------------------
CONTEXT_DIR = "context"                 # コンテクストを置くディレクトリ名
SPEC_COPY_FILENAME = "spec.md"           # 仕様書のコピー
INITIAL_PROMPT_FILENAME = "initial_prompt.md"   # 新規経路の初期コンテクスト
SURVEY_REPORT_FILENAME = "survey_report.md"    # 既存経路の現状レポート
META_FILENAME = "context.json"           # コンテクストの機械可読なメタ情報

# 調査で pi を実行するときの既定タイムアウト（秒）
SURVEY_TIMEOUT = 3600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _context_dir(workspace: Workspace) -> Path:
    """コンテクスト保存ディレクトリ（``<workdir>/context``）を生成して返す。"""
    d = workspace.workdir / CONTEXT_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 新規経路: 初期コンテクストの構築
# ---------------------------------------------------------------------------
def build_initial_prompt(spec_path: str | Path, config: OrchestratorConfig) -> str:
    """新規経路の初期コンテクスト（pi 初期プロンプト）文字列を構築する。

    仕様書の内容を埋め込み、`pi @spec.md "..."` で pi へ投入できる自己完結型の
    プロンプトを返す。AGENTS.md / システムプロンプトは pi 側の自動ロードに任せ、
    ここでは仕様書を起点とした実行指示を組み立てる。
    """
    spec = Path(spec_path)
    spec_text = spec.read_text(encoding="utf-8") if spec.is_file() else "(仕様書を読み込めません)"

    lines = [
        "# 初期コンテクスト（新規案件）",
        "",
        f"> 生成時刻: {_now()}",
        f"> 仕様書: {spec}",
        f"> 実行モデル: {config.exec_model or '(既定)'}",
        "",
        "あなたは pi（AI コーディングエージェント）として、以下の仕様書に従って実装を進めます。",
        "与えられた仕様書を唯一の要求として、それを満たす成果物を段階ごとに作成してください。",
        "",
        "## 作業方針",
        "",
        "- 仕様書を読み、過不足なく満たすことを最優先にする。",
        "- 曖昧な点は仮定を明示して進める。仕様の重大な過不足は人間へ質問する。",
        "- ファイル・関数・責務が split され、可読性を保つ。",
        "- 実装後は動作テストを行い、README を最新に保つ。",
        "",
        "## 仕様書",
        "",
        "```markdown",
        spec_text.rstrip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def save_initial_context(workspace: Workspace, initial_prompt: str) -> Path:
    """新規経路の初期コンテクストを ``context/initial_prompt.md`` へ保存する。"""
    return _write(_context_dir(workspace) / INITIAL_PROMPT_FILENAME, initial_prompt)


# ---------------------------------------------------------------------------
# 既存経路: 調査プロンプトと現状レポート
# ---------------------------------------------------------------------------
def build_survey_prompt() -> str:
    """調査エージェント（pi 読み取り専用）へ渡すプロンプトを返す。"""
    return (
        "あなたは既存リポジトリの調査エージェントです。"
        "読み取り専用ツール（read / grep / find / ls）のみを使って、"
        "指定されたコードベースの現状を調査し、以下の項目を含む「現状レポート」を日本語で出力してください。\n"
        "\n"
        "1. 概要（プロジェクトの目的・種別）\n"
        "2. 技術スタック（言語・フレームワーク・依存）\n"
        "3. ディレクトリ構成（主要な場所）\n"
        "4. 主要モジュールとその責務\n"
        "5. エントリポイント・テストの有無（test の実行方法）\n"
        "6. README などから読み取れる開発方針・注意点\n"
        "7. 変更・拡張の候補と想定されるリスク\n"
        "\n"
        "コードは一切変更しないこと。あなたは読み取り専用ツールのみを持っており、"
        "書き込みや実行はできません。読み取りで確認できる範囲で最善を尽くしてください。"
        "レポートはオーケストレータがタスク分解に使えるよう、構造化して簡潔にまとめてください。"
    )


def save_survey_report(workspace: Workspace, report: str) -> Path:
    """既存経路の現状レポートを ``context/survey_report.md`` へ保存する。"""
    return _write(_context_dir(workspace) / SURVEY_REPORT_FILENAME, report)


def run_survey(
    repo_path: str | Path,
    config: OrchestratorConfig,
    prompt: str | None = None,
    run_pi: Callable[..., dict[str, Any]] | None = None,
) -> str:
    """pi（読み取り専用）で既存リポジトリを調査し、現状レポート文字列を返す。

    Args:
        repo_path: 調査対象の既存リポジトリ（pi の cwd になる）。
        config: 実行設定（モデル割当・provider・trust 等）。
        prompt: 調査プロンプト（省略時は :func:`build_survey_prompt`）。
        run_pi: pi 実行関数（テスト注入用）。省略時は :func:`pi_tools._run_pi`。

    Returns:
        pi が出力した調査結果（自然言語レポート）。失敗時はエラー内容を含む。
    """
    repo = Path(repo_path)
    prompt = prompt if prompt is not None else build_survey_prompt()
    # 調査役割のモデルを明示し、実行役のモデルへ誤フォールバックしない。
    cmd = pt.build_survey_cmd(config, prompt, model=config.survey_model)
    runner = run_pi or pt._run_pi
    result = runner(cmd, cwd=str(repo), timeout=SURVEY_TIMEOUT)

    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    rc = result.get("returncode")

    if rc == 0 and stdout:
        # pi の自然言語出力そのものを現状レポートとして扱う
        return stdout

    # 失敗時: 原因をレポートとして返す（オーケストレータが再試行を判断できる）
    details = "\n".join(x for x in (stdout, stderr) if x)
    return (
        f"# 現状レポート（調査失敗）\n\n"
        f"調査エージェントの実行に失敗しました（exit code: {rc}）。\n\n"
        f"{details}\n"
    )


# ---------------------------------------------------------------------------
# 全体オーケストレーション
# ---------------------------------------------------------------------------
@dataclass
class ContextResult:
    """コンテクスト構築の結果。後続（planner / executor）が参照する。"""

    is_existing: bool                        # True=既存経路 / False=新規経路
    spec_path: Path                          # 検証済みの仕様書（絶対パス）
    spec_copy_path: Path                     # ワークスペースへコピーした仕様書
    initial_prompt: str | None = None        # 新規経路の初期コンテクスト本文
    initial_prompt_path: Path | None = None  # 新規経路の保存先
    survey_report: str | None = None         # 既存経路の現状レポート本文
    survey_report_path: Path | None = None   # 既存経路の保存先
    repo_path: Path | None = None            # 既存リポジトリ（既存経路のみ）

    def describe(self) -> str:
        """共有ボード / ログ / オーケストレータ向けの自然言語要約を返す。"""
        if self.is_existing:
            return (
                f"既存案件: リポジトリ {self.repo_path} を調査し、現状レポートを "
                f"{self.survey_report_path} に保存しました。"
            )
        return (
            f"新規案件: 仕様書 {self.spec_path} から初期コンテクストを "
            f"{self.initial_prompt_path} に保存しました。"
        )


def build_context(
    spec_path: str | Path,
    workspace: Workspace,
    config: OrchestratorConfig,
    repo_path: str | Path | None = None,
    run_pi: Callable[..., dict[str, Any]] | None = None,
) -> ContextResult:
    """新規・既存どちらの経路でもタスク分解に必要なコンテクストを揃えて保存する。

    Args:
        spec_path: 検証済みの仕様書パス。
        workspace: 保存先のワークスペース（作業ディレクトリ）。
        config: 実行設定。
        repo_path: 既存リポジトリ（任意）。指定時は既存経路として現状レポートを生成。
        run_pi: pi 実行関数（テスト注入用。既定は pi_tools の実行関数）。

    Returns:
        :class:`ContextResult`。生成物はワークスペース配下に保存済み。
    """
    spec = Path(spec_path).resolve()
    ctx_dir = _context_dir(workspace)

    # 仕様書をワークスペースへコピー（@spec.md として pi に渡せる形で保存）
    spec_copy = ctx_dir / SPEC_COPY_FILENAME
    shutil.copyfile(spec, spec_copy)
    _write(ctx_dir / META_FILENAME, _render_meta(spec, repo_path))

    if repo_path is None:
        prompt = build_initial_prompt(spec, config)
        prompt_path = save_initial_context(workspace, prompt)
        logger.info("新規経路の初期コンテクストを構築: %s", prompt_path)
        return ContextResult(
            is_existing=False,
            spec_path=spec,
            spec_copy_path=spec_copy,
            initial_prompt=prompt,
            initial_prompt_path=prompt_path,
        )

    # 既存経路: pi 読み取り専用で現状把握
    report = run_survey(repo_path, config, run_pi=run_pi)
    report_path = save_survey_report(workspace, report)
    logger.info("既存経路の現状レポートを構築: %s", report_path)
    return ContextResult(
        is_existing=True,
        spec_path=spec,
        spec_copy_path=spec_copy,
        survey_report=report,
        survey_report_path=report_path,
        repo_path=Path(repo_path).resolve(),
    )


def _render_meta(spec_path: Path, repo_path: str | Path | None) -> str:
    """コンテクストのメタ情報（JSON）を生成する。"""
    import json

    return json.dumps(
        {
            "spec_path": str(spec_path),
            "repo_path": str(Path(repo_path).resolve()) if repo_path else None,
            "is_existing": repo_path is not None,
            "generated_at": _now(),
        },
        ensure_ascii=False,
        indent=2,
    )
