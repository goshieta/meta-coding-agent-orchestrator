"""Task 4: pi 連携 CrewAI Tool 群 (tools/pi_tools.py) のテスト。

達成基準:
- 各 Tool（run_pi_single / run_pi_continue / run_pi_fork / compact_context /
  export_session）が存在し、pi の利用可否や引数構成を検証できる。
- 失敗時は exit code とエラー内容が Tool の戻り値に含まれる。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from orchestrator.config import OrchestratorConfig
from orchestrator.tools import (
    CompactContextTool,
    ExportSessionTool,
    RunPiContinueTool,
    RunPiForkTool,
    RunPiSingleTool,
    build_pi_tools,
    pi_available,
)
from orchestrator.tools import pi_tools as pt

DEFAULT_CFG = OrchestratorConfig()


def _all_names() -> list[str]:
    return [t.name for t in build_pi_tools(DEFAULT_CFG)]


# ---------------------------------------------------------------------------
# Tool の存在・構成
# ---------------------------------------------------------------------------
def test_build_pi_tools_returns_expected_tools() -> None:
    names = _all_names()
    assert set(names) == {
        "run_pi_single",
        "run_pi_continue",
        "run_pi_fork",
        "compact_context",
        "export_session",
    }


def test_expected_tool_classes_exist() -> None:
    tools = build_pi_tools(DEFAULT_CFG)
    by_name = {t.name: t for t in tools}
    assert isinstance(by_name["run_pi_single"], RunPiSingleTool)
    assert isinstance(by_name["run_pi_continue"], RunPiContinueTool)
    assert isinstance(by_name["run_pi_fork"], RunPiForkTool)
    assert isinstance(by_name["compact_context"], CompactContextTool)
    assert isinstance(by_name["export_session"], ExportSessionTool)


def test_pi_available_is_bool() -> None:
    assert isinstance(pi_available(), bool)
    # pi コマンドが実際に存在するなら True・存在しなければ False
    assert pi_available() == (pt.shutil.which(pt.PI_COMMAND) is not None)


def test_config_is_injected_into_tools() -> None:
    cfg = OrchestratorConfig(exec_model="custom-exec", pi_provider="openrouter", pi_trust="approve")
    tools = build_pi_tools(cfg)
    single = next(t for t in tools if isinstance(t, RunPiSingleTool))
    assert single._config is cfg
    # モデル / trust がコマンドへ反映される
    cmd = pt.build_single_cmd(single._config, "task")
    joined = " ".join(cmd)
    assert "custom-exec" in joined
    assert "--approve" in joined


# ---------------------------------------------------------------------------
# コマンド構築（引数構成の検証）
# ---------------------------------------------------------------------------
def test_single_cmd_includes_print_and_model() -> None:
    cfg = OrchestratorConfig(exec_model="m/exec")
    cmd = pt.build_single_cmd(cfg, "タスク", spec_file="/s/spec.md")
    assert cmd[0] == pt._pi_bin()
    assert "-p" in cmd
    assert "--model" in cmd and "m/exec" in cmd
    assert "@/s/spec.md" in cmd
    assert cmd[-1] == "タスク"


def test_single_cmd_default_spec_none() -> None:
    cmd = pt.build_single_cmd(DEFAULT_CFG, "タスク")
    assert not any(a.startswith("@") for a in cmd)


def test_continue_cmd_session_vs_minus_c() -> None:
    assert "--session" in pt.build_continue_cmd(DEFAULT_CFG, "続き", session="sess1")
    assert "-c" in pt.build_continue_cmd(DEFAULT_CFG, "続き")


def test_fork_cmd_has_fork_and_new_session() -> None:
    cmd = pt.build_fork_cmd(DEFAULT_CFG, "old-session", "やり直す")
    assert "--fork" in cmd
    assert "old-session" in cmd


def test_compact_cmd_has_session_and_prompt() -> None:
    cmd = pt.build_compact_cmd(DEFAULT_CFG, "sess1", instructions="要点のみ")
    assert "--session" in cmd and "sess1" in cmd
    assert any("圧縮" in a for a in cmd)


def test_thinking_flag_respects_config() -> None:
    on = pt.build_single_cmd(OrchestratorConfig(pi_thinking=True), "t")
    off = pt.build_single_cmd(OrchestratorConfig(pi_thinking=False), "t")
    assert "--thinking" in on and on[on.index("--thinking") + 1] == "high"
    assert off[off.index("--thinking") + 1] == "off"


def test_trust_flags_mapping() -> None:
    assert pt._pi_trust_flags(OrchestratorConfig(pi_trust="approve")) == ["--approve"]
    assert "--approve" in pt.build_single_cmd(OrchestratorConfig(pi_trust="yes"), "t")
    assert pt._pi_trust_flags(OrchestratorConfig(pi_trust="no-approve")) == ["--no-approve"]
    assert "--no-approve" in pt.build_single_cmd(OrchestratorConfig(pi_trust="none"), "t")
    # defaultProjectTrust（既定）はフラグなし
    assert pt._pi_trust_flags(OrchestratorConfig(pi_trust="defaultProjectTrust")) == []


def test_session_dir_flag_when_configured() -> None:
    cfg = OrchestratorConfig(pi_session_dir="/tmp/pi-sessions")
    cmd = pt.build_single_cmd(cfg, "t")
    assert "--session-dir" in cmd and "/tmp/pi-sessions" in cmd
    assert pt.build_single_cmd(DEFAULT_CFG, "t").count("--session-dir") == 0


def test_model_override_flag() -> None:
    cmd = pt.build_single_cmd(DEFAULT_CFG, "t", model="qa-model")
    assert "qa-model" in cmd


# ---------------------------------------------------------------------------
# 実行結果レポート（exit code とエラー内容を含む）
# ---------------------------------------------------------------------------
def test_report_formats_success() -> None:
    cmd = ["pi", "-p", "task"]
    result = {"returncode": 0, "stdout": "done", "stderr": ""}
    rep = pt._format_report(cmd, result)
    assert "exit code: 0" in rep
    assert "ステータス: 成功" in rep
    assert "done" in rep


def test_report_includes_exit_code_and_error_on_failure() -> None:
    cmd = ["pi", "-p", "task"]
    result = {"returncode": 1, "stdout": "", "stderr": "some error"}
    rep = pt._format_report(cmd, result)
    assert "exit code: 1" in rep
    assert "some error" in rep
    assert "失敗" in rep


def test_tool_failure_returns_exit_code_and_error(monkeypatch) -> None:
    """失敗時、Tool の戻り値に exit code とエラー内容が含まれる。"""

    def fake_run(argv, cwd=None, timeout=3600):
        return {"returncode": 2, "stdout": "", "stderr": "boom: failed to run pi"}

    monkeypatch.setattr(pt, "_run_pi", fake_run)
    tool = RunPiSingleTool()
    report = tool._run("タスク")
    assert "exit code: 2" in report
    assert "boom: failed to run pi" in report
    assert "失敗" in report


def test_run_pi_not_found_reports_error(monkeypatch) -> None:
    """pi 実行ファイルが無い場合、エラー内容と擬似 exit code が含まれる。"""

    def fake_run(argv, cwd=None, timeout=3600):
        return {"returncode": pt.RC_NOT_FOUND, "stdout": "", "stderr": "pi 実行ファイルが見つかりません"}

    monkeypatch.setattr(pt, "_run_pi", fake_run)
    tool = RunPiContinueTool()
    report = tool._run("続き")
    assert "見つかりません" in report
    assert str(pt.RC_NOT_FOUND) in report


# ---------------------------------------------------------------------------
# export_session（JSONL 永続化）
# ---------------------------------------------------------------------------
def test_export_session_copies_jsonl(tmp_path, monkeypatch) -> None:
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    src = session_dir / "abc123.jsonl"
    src.write_text('{"type":"user"}\n', encoding="utf-8")
    cfg = OrchestratorConfig(pi_session_dir=str(session_dir))

    tool = ExportSessionTool()
    tool._config = cfg
    dest = tmp_path / "out.jsonl"
    report = tool._run("abc123", str(dest))
    assert "exit code: 0" in report
    assert "永続化" in report
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == '{"type":"user"}\n'


def test_export_session_not_found_reports_error(tmp_path) -> None:
    cfg = OrchestratorConfig(pi_session_dir=str(tmp_path / "empty"))
    tool = ExportSessionTool()
    tool._config = cfg
    report = tool._run("nosuchid")
    assert "見つかりません" in report
    assert str(pt.RC_SESSION_NOT_FOUND) in report


def test_run_tools_produce_reports_without_pi(monkeypatch) -> None:
    """各 Tool が（pi 実呼び出しなしで）レポート文字列を返すことを確認。"""
    monkeypatch.setattr(pt, "_run_pi", lambda argv, cwd=None, timeout=3600: {
        "returncode": 0, "stdout": "ok", "stderr": ""
    })
    tools = build_pi_tools(DEFAULT_CFG)
    for t in tools:
        if t.name == "export_session":
            continue  # 別途テスト
        if t.name == "run_pi_fork":
            report = t._run("sess1", "タスク")
        elif t.name == "compact_context":
            report = t._run("sess1")
        elif t.name == "run_pi_continue":
            report = t._run("タスク", session="sess1")
        else:
            report = t._run("タスク")
        assert isinstance(report, str) and "exit code" in report
