"""Task 1: プロジェクト雛形の基礎確認テスト。"""

from __future__ import annotations

import orchestrator  # noqa: F401  (パッケージが import できることを確認)


def test_package_importable() -> None:
    assert orchestrator.__version__ is not None


def test_main_entrypoint_help() -> None:
    """python -m orchestrator --help が正常にヘルプを表示して終了する。"""
    from orchestrator.main import main
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
