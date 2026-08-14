"""CLI エントリポイント・仕様書検証 (PLAN F-01 / Task 3)。

`run-spec.sh <spec.md> [existing_repo/]` のワンライナー起動を実現し、入力を検証する。

- 引数: 仕様書パス（必須）/ 既存リポジトリパス（任意）/ 各モデル上書きフラグ
- 検証: 仕様書未指定・ファイル不存在・空ファイル -> エラーメッセージを出力して即終了（exit 非0）
- 成功時: ログパスを出力（Task 7 のコンテナ対応後はコンテナIDも出力）
- Task 2 の設定を読み込み、仕様書パス・リポジトリパス・モデル設定を後続へ引き渡す
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from orchestrator.config import (
    ENV_ORCHESTRATOR_MODEL,
    ENV_EXEC_MODEL,
    ENV_QA_MODEL,
    ENV_SURVEY_MODEL,
    OrchestratorConfig,
    build_config,
)

# 検証失敗時の終了コード（非0）
EXIT_VALIDATION_ERROR = 2
# 成功時の終了コード
EXIT_OK = 0


def validate_spec(spec_path: str | Path) -> Path:
    """仕様書を検証し、絶対パスを返す。

    Raises:
        ValueError: 未指定・不存在・空ファイルの場合。
    """
    if not spec_path:
        raise ValueError("仕様書（.md）が指定されていません。使い方: run-spec.sh <spec.md> [existing_repo/]")

    path = Path(spec_path)
    if not path.is_file():
        raise ValueError(f"仕様書ファイルが存在しません: {path}")
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError(f"仕様書ファイルが空です: {path}")
    return path.resolve()


def validate_existing_repo(repo_path: str | Path | None) -> Path | None:
    """既存リポジトリパス（任意）を検証する。指定なしは None を返す。

    Raises:
        ValueError: 指定されたが存在しない場合。
    """
    if not repo_path:
        return None
    path = Path(repo_path)
    if not path.is_dir():
        raise ValueError(f"既存リポジトリが存在しません: {path}")
    return path.resolve()


def build_parser() -> argparse.ArgumentParser:
    """CLI 引数のパーサーを構築する。"""
    parser = argparse.ArgumentParser(
        prog="run-spec.sh",
        description="Meta-Agent Orchestrator - 開発オーケストレーション中間層",
        usage="%(prog)s <spec.md> [existing_repo/] [options]",
    )
    parser.add_argument("spec", nargs="?", metavar="spec.md", help="仕様書（.md）パス（必須）")
    parser.add_argument(
        "existing_repo",
        nargs="?",
        metavar="existing_repo/",
        help="既存リポジトリのパス（任意）。指定時はコンテクスト構築で調査に使う",
    )
    parser.add_argument("--version", action="version", version=f"meta-agent-orchestrator {_version()}")

    # --- モデル / token / pi 設定の個別上書き (Task 2 設定へ引き渡す) ---
    parser.add_argument("--orchestrator-model", dest="orchestrator_model", metavar="MODEL",
                        help=f"オーケストレータ用モデル（env: {ENV_ORCHESTRATOR_MODEL}）")
    parser.add_argument("--exec-model", dest="exec_model", metavar="MODEL",
                        help=f"実行用モデル（env: {ENV_EXEC_MODEL}）")
    parser.add_argument("--qa-model", dest="qa_model", metavar="MODEL",
                        help=f"QA用モデル（env: {ENV_QA_MODEL}）")
    parser.add_argument("--survey-model", dest="survey_model", metavar="MODEL",
                        help=f"調査用モデル（env: {ENV_SURVEY_MODEL}）")
    parser.add_argument("--github-token", dest="github_token", metavar="TOKEN",
                        help="GitHub 認証トークン（env: GITHUB_TOKEN）")
    parser.add_argument("--log-dir", dest="log_dir", metavar="DIR", default=None,
                        help="ログ出力ディレクトリ（既定: cwd/logs）")
    return parser


def _version() -> str:
    import orchestrator

    return orchestrator.__version__


def _default_log_dir() -> Path:
    """既定のログディレクトリを生成して返す。"""
    log_dir = Path(os.getcwd()) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def run_pipeline(config: OrchestratorConfig, spec_path: Path, repo_path: Path | None, log_dir: Path) -> None:
    """パイプラインを開始する。

    ホスト側の軽量 CLI 呼び出しでは開始情報だけを表示する。一方、コンテナ内では
    ``ORCHESTRATOR_IN_CONTAINER`` が設定され、context → plan → execute → QA →
    delivery の再開可能な統合パイプラインを実行する。これにより仕様検証だけを
    行う既存の CLI テストや dry-run は外部 API / pi を起動しない。
    """
    print(f"[orchestrator] 仕様書: {spec_path}")
    print(f"[orchestrator] 既存リポジトリ: {repo_path if repo_path else '(なし)'}")
    print(f"[orchestrator] パイプラインを開始しました（config={config.orchestrator_model} 他）")
    print(f"[orchestrator] ログパス: {log_dir}")

    if os.getenv("ORCHESTRATOR_IN_CONTAINER") != "1":
        return

    from orchestrator.pipeline import run_pipeline as run_integrated_pipeline
    from orchestrator.workspace import Workspace

    # コンテナの --log-dir は /work/logs。ログの親を共有ワークスペースとして使う。
    workdir = log_dir.parent if log_dir.name == "logs" else log_dir
    result = run_integrated_pipeline(
        spec_path,
        Workspace.open(workdir),
        config,
        repo_path=repo_path,
        log_dir=log_dir,
    )
    print("[orchestrator] 統合パイプライン結果:")
    print(result.summary())
    # 承認待ち・QA不合格は安全な停止状態として保存済みであり、例外にせず
    # 再起動／HumanGate.resume() で続行できる。納品失敗も同様に状態を保持する。
    if result.delivery and result.delivery.ok:
        print("[orchestrator] 最終納品まで完了しました")


def main(argv: list[str] | None = None) -> int:
    """CLI のエントリポイント。終了コードを返す（検証失敗時は非0）。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        spec_path = validate_spec(args.spec)
        repo_path = validate_existing_repo(args.existing_repo)
    except ValueError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR

    # Task 2 の設定を読み込み、CLI 引数で個別上書き
    config = build_config(
        # 外部（.env 等）の読み込みは環境変数で行う。CLI 引数を上書きとして渡す。
        orchestrator_model=args.orchestrator_model,
        exec_model=args.exec_model,
        qa_model=args.qa_model,
        survey_model=args.survey_model,
        github_token=args.github_token,
    )

    log_dir = Path(args.log_dir) if args.log_dir else _default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    run_pipeline(config, spec_path, repo_path, log_dir)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
