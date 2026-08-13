"""Task 7 コンテナ隔離実行（container.py）のテスト。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from orchestrator import container
from orchestrator.config import OrchestratorConfig


@pytest.fixture()
def config() -> OrchestratorConfig:
    return OrchestratorConfig(
        orchestrator_model="orch/default",
        exec_model="exec/default",
        qa_model="qa/default",
        survey_model="survey/default",
        github_token="gh_token_123",
        pi_provider="openrouter",
        pi_thinking=True,
        pi_trust="defaultProjectTrust",
    )


# ---------------------------------------------------------------------------
# build_env_args
# ---------------------------------------------------------------------------
class TestBuildEnvArgs:
    def test_config_values_are_injected(self, config):
        args = container.build_env_args(config, host_env={})
        env = dict(zip(args[0::2], args[1::2]))
        assert env["-e"]  # 全ペアが -e キー
        kv = {}
        for i in range(0, len(args), 2):
            assert args[i] == "-e"
            key, _, value = args[i + 1].partition("=")
            kv[key] = value
        assert kv["AGENT_ORCHESTRATOR_MODEL"] == "orch/default"
        assert kv["AGENT_EXEC_MODEL"] == "exec/default"
        assert kv["AGENT_QA_MODEL"] == "qa/default"
        assert kv["AGENT_SURVEY_MODEL"] == "survey/default"
        assert kv[container.ENV_GITHUB_TOKEN] == "gh_token_123"
        assert kv[container.ENV_PI_SESSION_DIR] == container.IN_SESSION_DIR
        assert kv["PI_PROVIDER"] == "openrouter"
        assert kv["PI_THINKING"] == "true"

    def test_host_creds_passthrough(self, config):
        host = {"OPENROUTER_API_KEY": "sk-X", "AGENT_QA_MODEL": "host/qa"}
        args = container.build_env_args(config, host_env=host)
        kv = {
            args[i + 1].split("=", 1)[0]: args[i + 1].split("=", 1)[1]
            for i in range(0, len(args), 2)
        }
        # 認証系は透過
        assert kv["OPENROUTER_API_KEY"] == "sk-X"
        # AGENT_* / PI_* は config 由来を優先（ホストの session 系変数は混入しない）
        assert kv["AGENT_QA_MODEL"] == "qa/default"
        assert "PI_SESSION_ID" not in kv

    def test_no_token_when_unset(self):
        c = OrchestratorConfig(github_token=None)
        args = container.build_env_args(c, host_env={})
        joined = " ".join(args)
        assert container.ENV_GITHUB_TOKEN not in joined


# ---------------------------------------------------------------------------
# build_mount_args
# ---------------------------------------------------------------------------
class TestBuildMountArgs:
    def test_data_mount_always(self, monkeypatch, tmp_path):
        monkeypatch.setattr(container, "resolve_daemon_path", lambda p: str(p))
        args = container.build_mount_args(None, tmp_path)
        assert f"{tmp_path}:{container.IN_WORK_DIR}" in args

    def test_repo_readonly(self, monkeypatch, tmp_path):
        monkeypatch.setattr(container, "resolve_daemon_path", lambda p: str(p))
        data = tmp_path / "data"
        repo = tmp_path / "repo"
        args = container.build_mount_args(repo, data)
        joined = " ".join(args)
        assert f"{repo}:{container.IN_REPO_PATH}:ro" in joined

    def test_daemon_path_resolution_called(self, monkeypatch, tmp_path):
        calls: list[str] = []
        monkeypatch.setattr(
            container, "resolve_daemon_path", lambda p: calls.append(str(p)) or str(p)
        )
        data = tmp_path / "data"
        repo = tmp_path / "repo"
        container.build_mount_args(repo, data)
        assert str(data) in calls and str(repo) in calls


# ---------------------------------------------------------------------------
# prepare_inputs / build_cli_args / resolve_daemon_path
# ---------------------------------------------------------------------------
class TestInputsAndCli:
    def test_prepare_inputs_copies_spec(self, tmp_path):
        data = tmp_path / "data"
        spec = tmp_path / "spec_orig.md"
        spec.write_text("# spec\n", encoding="utf-8")
        dest = container.prepare_inputs(spec, None, data)
        assert dest == data / "inputs" / "spec.md"
        assert dest.read_text(encoding="utf-8") == "# spec\n"

    def test_build_cli_args(self):
        args = container.build_cli_args(None)
        assert args[0] == container.IN_SPEC_PATH
        assert len(args) == 1
        args2 = container.build_cli_args(Path("/x/repo"), ["--log-dir=/work/logs"])
        assert args2[1] == container.IN_REPO_PATH
        assert args2[-1] == "--log-dir=/work/logs"

    def test_resolve_daemon_path_identity_when_not_in_container(self, monkeypatch):
        monkeypatch.setattr(container, "_current_container_bind_sources", lambda: {})
        assert container.resolve_daemon_path("/some/path") == str(Path("/some/path").resolve())

    def test_resolve_daemon_path_replaces_longest_prefix(self, monkeypatch):
        monkeypatch.setattr(
            container,
            "_current_container_bind_sources",
            lambda: {"/workspace": "/home/user/dev/workspace"},
        )
        assert (
            container.resolve_daemon_path("/workspace/data/logs")
            == "/home/user/dev/workspace/data/logs"
        )


# ---------------------------------------------------------------------------
# compose_run_args
# ---------------------------------------------------------------------------
class TestCompose:
    def test_compose_full(self, config, monkeypatch, tmp_path):
        monkeypatch.setattr(container, "resolve_daemon_path", lambda p: str(p))
        spec = tmp_path / "spec.md"
        repo = tmp_path / "repo"
        data = tmp_path / "data"
        args = container.compose_run_args(
            config, spec, repo, data, image="img:latest", container_name="c1",
            cli_extra=["--log-dir=/work/logs"],
        )
        assert args[0] == "run"
        assert "-d" in args
        assert args[args.index("--name") + 1] == "c1"
        assert f"{data}:{container.IN_WORK_DIR}" in args
        joined = " ".join(args)
        assert "img:latest" in joined
        assert "uv run --project /app python -m orchestrator" in joined
        assert container.IN_SPEC_PATH in joined
        assert container.IN_REPO_PATH in joined


# ---------------------------------------------------------------------------
# インポート整合（Docker が無い環境でも壊れない）
# ---------------------------------------------------------------------------
class TestModuleImport:
    def test_constants_exist(self):
        assert container.DEFAULT_IMAGE == "meta-agent-orchestrator:latest"
        assert container.IN_WORK_DIR == "/work"
        assert container.IN_LOG_DIR.endswith("/logs")

    def test_shutil_importable(self):
        assert shutil.copyfile
