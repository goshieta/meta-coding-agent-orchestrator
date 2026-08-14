"""Task 6: コンテクスト構築（新規・既存）(context.py, F-03/F-10) のテスト。

達成基準:
- 新規・既存どちらの経路でも、タスク分解に必要なコンテクストが揃う。
- 既存経路では現状レポートが生成され、オーケストレータが自然言語で参照できる。
- 読み取り専用ツール（--tools read,grep,find,ls）で既存コードを解析する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.config import OrchestratorConfig
from orchestrator.context import (
    INITIAL_PROMPT_FILENAME,
    META_FILENAME,
    SPEC_COPY_FILENAME,
    SURVEY_REPORT_FILENAME,
    build_context,
    build_initial_prompt,
    build_survey_prompt,
    run_survey,
)
from orchestrator.workspace import Workspace


def _make_spec(tmp_path: Path, text: str = "# サンプル仕様\n\n何かを作る。") -> Path:
    spec = tmp_path / "spec.md"
    spec.write_text(text, encoding="utf-8")
    return spec


def _fake_pi_ok(captured: dict):
    def fake_run(argv, cwd=None, timeout=3600):
        captured["argv"] = list(argv)
        captured["cwd"] = cwd
        return {"returncode": 0, "stdout": "# 現状レポート\n\n- 技術スタック: Python\n", "stderr": ""}
    return fake_run


def _fake_pi_fail():
    def fake_run(argv, cwd=None, timeout=3600):
        return {"returncode": 1, "stdout": "", "stderr": "pi が失敗しました"}
    return fake_run


# ---------------------------------------------------------------------------
# 新規経路: 初期コンテクスト構築
# ---------------------------------------------------------------------------
def test_build_initial_prompt_embeds_spec(tmp_path) -> None:
    spec = _make_spec(tmp_path, "# 専用仕様\n\nAI を活用する。")
    cfg = OrchestratorConfig(exec_model="exec-model")
    prompt = build_initial_prompt(spec, cfg)
    assert "専用仕様" in prompt
    assert "AI を活用する。" in prompt
    assert "exec-model" in prompt


def test_build_context_new_defaults(tmp_path) -> None:
    spec = _make_spec(tmp_path)
    ws = Workspace(tmp_path / "ws")
    result = build_context(spec, ws, OrchestratorConfig())

    assert result.is_existing is False
    assert result.repo_path is None
    assert result.survey_report is None
    # 保存先の存在確認
    assert (ws.workdir / "context" / SPEC_COPY_FILENAME).is_file()
    assert (ws.workdir / "context" / INITIAL_PROMPT_FILENAME).is_file()
    assert (ws.workdir / "context" / META_FILENAME).is_file()
    # タスク分解に使える初期コンテクスト本文が揃う
    assert result.initial_prompt is not None
    assert "# サンプル仕様" in result.initial_prompt


# ---------------------------------------------------------------------------
# 既存経路: 現状レポート生成
# ---------------------------------------------------------------------------
def test_build_context_existing_creates_survey_report(tmp_path) -> None:
    spec = _make_spec(tmp_path)
    repo = tmp_path / "existing"
    repo.mkdir()
    ws = Workspace(tmp_path / "ws")
    captured: dict = {}

    result = build_context(
        spec, ws, OrchestratorConfig(), repo_path=repo, run_pi=_fake_pi_ok(captured)
    )

    assert result.is_existing is True
    assert result.repo_path == repo.resolve()
    # 現状レポートが生成され、オーケストレータが自然言語で参照できる
    assert result.survey_report is not None
    assert "現状レポート" in result.survey_report
    report_path = ws.workdir / "context" / SURVEY_REPORT_FILENAME
    assert report_path.is_file()
    assert "現状レポート" in report_path.read_text(encoding="utf-8")
    # pi は cwd=repo で読み取り専用ツールにより実行される
    argv = captured["argv"]
    assert captured["cwd"] == str(repo.resolve())
    assert "--tools" in argv
    assert argv[argv.index("--tools") + 1] == "read,grep,find,ls"
    assert argv[argv.index("--model") + 1] == OrchestratorConfig().survey_model


def test_build_survey_prompt_is_readonly_instruction() -> None:
    prompt = build_survey_prompt()
    assert "読み取り専用" in prompt
    assert "read / grep / find / ls" in prompt
    assert "変更" in prompt


def test_run_survey_returns_failure_report(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    report = run_survey(repo, OrchestratorConfig(), run_pi=_fake_pi_fail())
    assert "調査失敗" in report
    assert "pi が失敗しました" in report


# ---------------------------------------------------------------------------
# その他
# ---------------------------------------------------------------------------
def test_context_result_describe() -> None:
    from orchestrator.context import ContextResult

    new = ContextResult(is_existing=False, spec_path=Path("s.md"), spec_copy_path=Path("c/s.md"),
                        initial_prompt_path=Path("c/initial_prompt.md"))
    assert "新規案件" in new.describe()

    old = ContextResult(is_existing=True, spec_path=Path("s.md"), spec_copy_path=Path("c/s.md"),
                        repo_path=Path("/repo"))
    assert "既存案件" in old.describe()
