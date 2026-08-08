"""Meta-Agent Orchestrator CLI エントリポイント.

`python -m orchestrator` で起動できる最小エントリポイント。
後続タスク（Task 3 の CLI 実装）で拡張される。
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator",
        description="Meta-Agent Orchestrator - 開発オーケストレーション中間層",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"meta-agent-orchestrator {__import__('orchestrator').__version__}",
        help="バージョン情報を表示して終了する",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    print("Meta-Agent Orchestrator: 起動しました (hello from orchestrator)")
    print("使い方: python -m orchestrator --help")
    return 0


if __name__ == "__main__":
    sys.exit(main())
