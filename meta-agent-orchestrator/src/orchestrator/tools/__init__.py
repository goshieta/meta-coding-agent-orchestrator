"""CrewAI Tool 群（pi 連携）パッケージ。

マネジメント層が pi プロセスを操作するための CrewAI Tool 群を提供する
（PLAN F-05 / TASK.md Task 4）。

主な公開 API:
    - :func:`build_pi_tools`: 設定に紐づいた pi Tool 一式を生成
    - :func:`pi_available`: pi 実行ファイルの利用可否を返す
    - 個別 Tool クラス（run_pi_single / run_pi_continue / run_pi_fork /
      compact_context / export_session）
"""

from __future__ import annotations

from orchestrator.tools.pi_tools import (
    CompactContextTool,
    ExportSessionTool,
    RunPiContinueTool,
    RunPiForkTool,
    RunPiSingleTool,
    RunPiSurveyTool,
    SURVEY_TOOLS,
    build_pi_tools,
    make_survey_tool,
    pi_available,
)

__all__ = [
    "build_pi_tools",
    "make_survey_tool",
    "pi_available",
    "RunPiSingleTool",
    "RunPiContinueTool",
    "RunPiForkTool",
    "RunPiSurveyTool",
    "CompactContextTool",
    "ExportSessionTool",
    "SURVEY_TOOLS",
]
