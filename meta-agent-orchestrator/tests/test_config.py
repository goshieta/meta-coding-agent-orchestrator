"""Task 2: 設定管理 (config.py) のテスト。

優先順位 (CLI引数 > 環境変数 > デフォルト) を検証する。
"""

from __future__ import annotations

from orchestrator.config import (
    DEFAULT_EXEC_MODEL,
    DEFAULT_ORCHESTRATOR_MODEL,
    DEFAULT_QA_MODEL,
    DEFAULT_SURVEY_MODEL,
    ENV_EXEC_MODEL,
    ENV_GITHUB_TOKEN,
    ENV_ORCHESTRATOR_MODEL,
    ENV_PI_SESSION_DIR,
    ENV_QA_MODEL,
    ENV_SURVEY_MODEL,
    OrchestratorConfig,
    build_config,
)


def test_defaults_when_no_env() -> None:
    """環境変数なし -> デフォルト値が使われる。"""
    config = build_config(env={})
    assert config.orchestrator_model == DEFAULT_ORCHESTRATOR_MODEL
    assert config.exec_model == DEFAULT_EXEC_MODEL
    assert config.qa_model == DEFAULT_QA_MODEL
    assert config.survey_model == DEFAULT_SURVEY_MODEL
    assert config.github_token is None
    assert config.pi_session_dir is None


def test_env_overrides_default() -> None:
    """環境変数あり -> 環境変数が優先される。"""
    env = {
        ENV_ORCHESTRATOR_MODEL: "env-orch",
        ENV_EXEC_MODEL: "env-exec",
        ENV_QA_MODEL: "env-qa",
        ENV_SURVEY_MODEL: "env-survey",
        ENV_GITHUB_TOKEN: "env-token",
        ENV_PI_SESSION_DIR: "/tmp/pi-sessions",
    }
    config = build_config(env=env)
    assert config.orchestrator_model == "env-orch"
    assert config.exec_model == "env-exec"
    assert config.qa_model == "env-qa"
    assert config.survey_model == "env-survey"
    assert config.github_token == "env-token"
    assert config.pi_session_dir == "/tmp/pi-sessions"


def test_cli_overrides_env() -> None:
    """CLI 引数あり -> 引数が環境変数より優先される。"""
    env = {ENV_ORCHESTRATOR_MODEL: "env-orch", ENV_EXEC_MODEL: "env-exec"}
    config = build_config(env=env, orchestrator_model="cli-orch")
    assert config.orchestrator_model == "cli-orch"
    # 引数で指定されていない項目は env 由来のまま
    assert config.exec_model == "env-exec"


def test_none_override_is_ignored() -> None:
    """None の上書きは無視され、元の値を保持する。"""
    env = {ENV_ORCHESTRATOR_MODEL: "env-orch"}
    config = build_config(env=env, orchestrator_model=None)
    assert config.orchestrator_model == "env-orch"


def test_from_env_direct() -> None:
    """from_env が環境変数を正しく反映する。"""
    config = OrchestratorConfig.from_env(
        {ENV_QA_MODEL: "qa", ENV_GITHUB_TOKEN: "tok", ENV_ORCHESTRATOR_MODEL: "o"}
    )
    assert config.qa_model == "qa"
    assert config.github_token == "tok"
    assert config.orchestrator_model == "o"


def test_pi_thinking_bool_parsing() -> None:
    """PI_THINKING が bool として解釈される。"""
    from orchestrator.config import ENV_PI_THINKING

    assert build_config(env={ENV_PI_THINKING: "true"}).pi_thinking is True
    assert build_config(env={ENV_PI_THINKING: "false"}).pi_thinking is False
    # 未指定はデフォルト
    assert build_config(env={}).pi_thinking is True
