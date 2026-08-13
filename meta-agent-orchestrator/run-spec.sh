#!/usr/bin/env bash
# Meta-Agent Orchestrator ワンライナー起動スクリプト (PLAN F-01 / F-02)
#
# 使い方:
#   ./run-spec.sh <spec.md> [existing_repo/] [options]
#
# 役割:
#   1) ホスト側で `python -m orchestrator.container` を呼び、Docker コンテナを
#      ビルド（未作成時）・起動し、コンテナID とログパスを出力する (Task 7)。
#   2) 実処理（CrewAI マネジメント層 + pi）はすべてコンテナ内で実行されるため、
#      ホストは汚染されない (F-02)。
#
# オプション例:
#   --docker-image <tag>  使用イメージ（既定: meta-agent-orchestrator:latest）
#   --rebuild             イメージを再ビルドしてから起動
#   --data-dir <dir>      永続データ領域（既定: <プロジェクト>/data）
#   --follow              コンテナログをフォロー
#   --dry-run             実行する docker run コマンドを表示するだけ
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ホスト側コンテナ起動ラッパーへ引数をそのまま渡す
exec uv run python -m orchestrator.container "$@"
