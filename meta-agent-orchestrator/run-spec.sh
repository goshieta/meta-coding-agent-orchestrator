#!/usr/bin/env bash
# Meta-Agent Orchestrator ワンライナー起動スクリプト (PLAN F-01)
#
# 使い方:
#   ./run-spec.sh <spec.md> [existing_repo/] [options]
#
# 薄いラッパーとして uv 経由で Python 側エントリ (orchestrator.cli) を起動する。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# スクリプトへの引数をそのまま Python へ渡す
exec uv run python -m orchestrator "$@"
