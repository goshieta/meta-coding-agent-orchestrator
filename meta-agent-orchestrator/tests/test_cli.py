"""Task 3: CLI エントリポイント・仕様書検証 (cli.py / run-spec.sh) のテスト。"""

from __future__ import annotations

import os

import pytest

from orchestrator import cli


def _make_spec(tmp_path, content: str = "# 仕様書\n") -> str:
    spec = tmp_path / "spec.md"
    spec.write_text(content, encoding="utf-8")
    return str(spec)


def test_spec_not_given_exits_nonzero(capsys) -> None:
    """仕様書未指定 -> 即終了（exit 非0）。"""
    assert cli.main([]) == cli.EXIT_VALIDATION_ERROR
    err = capsys.readouterr().err
    assert "仕様書" in err or "指定されていません" in err


def test_nonexistent_spec_exits_nonzero(capsys) -> None:
    """存在しない仕様書 -> 即終了（exit 非0）。"""
    rc = cli.main(["/no/such/spec.md"])
    assert rc == cli.EXIT_VALIDATION_ERROR
    assert "存在しません" in capsys.readouterr().err


def test_empty_spec_exits_nonzero(tmp_path, capsys) -> None:
    """空の仕様書 -> 即終了（exit 非0）。"""
    spec = tmp_path / "empty.md"
    spec.write_text("", encoding="utf-8")
    rc = cli.main([str(spec)])
    assert rc == cli.EXIT_VALIDATION_ERROR
    assert "空です" in capsys.readouterr().err


def test_valid_spec_starts_pipeline(tmp_path, capsys, monkeypatch) -> None:
    """有効な仕様書でパイプラインが開始され、ログパスが出力される。"""
    # ログディレクトリを cwd に作らないよう制御
    log_dir = tmp_path / "logs"
    spec = _make_spec(tmp_path)
    rc = cli.main([spec, "--log-dir", str(log_dir)])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "パイプラインを開始" in out
    assert "ログパス" in out and str(log_dir) in out
    assert log_dir.is_dir()


def test_existing_repo_validated(tmp_path, capsys) -> None:
    """既存リポジトリ指定が存在しない場合 -> エラー。"""
    spec = _make_spec(tmp_path)
    rc = cli.main([spec, "/no/such/repo"])
    assert rc == cli.EXIT_VALIDATION_ERROR
    assert "既存リポジトリが存在しません" in capsys.readouterr().err


def test_model_override_flag(tmp_path, monkeypatch) -> None:
    """--orchestrator-model が設定へ反映される。"""
    spec = _make_spec(tmp_path)
    monkeypatch.delenv("AGENT_ORCHESTRATOR_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)
    # main は run_pipeline を呼ぶがモデル設定を出力に含むため、フックを捕捉
    seen: dict = {}

    def fake_pipeline(config, spec_path, repo_path, log_dir):
        seen["model"] = config.orchestrator_model

    monkeypatch.setattr(cli, "run_pipeline", fake_pipeline)
    rc = cli.main([spec, "--orchestrator-model", "cli-custom-model"])
    assert rc == cli.EXIT_OK
    assert seen["model"] == "cli-custom-model"


def test_run_spec_sh_exists_and_executable() -> None:
    """run-spec.sh が存在し実行可能である。"""
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "run-spec.sh"
    assert script.is_file()
    assert os.access(script, os.X_OK)
    # shebang を持つ
    assert script.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
