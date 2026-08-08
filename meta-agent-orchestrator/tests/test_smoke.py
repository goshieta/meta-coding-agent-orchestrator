"""Task 1: プロジェクト雛形のダミーテスト。

後続タスクで各モジュールの本格的なテストに置き換え・拡張される。
"""

from __future__ import annotations

import orchestrator  # noqa: F401  (パッケージが import できることを確認)


def test_package_importable() -> None:
    assert orchestrator.__version__ is not None


def test_main_greets(capsys) -> None:
    from orchestrator.main import main

    assert main([]) == 0
    captured = capsys.readouterr()
    assert "Meta-Agent Orchestrator" in captured.out
