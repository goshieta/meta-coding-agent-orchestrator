"""Task 8 クルー構成（agents/crew.py）のテスト。"""

from __future__ import annotations

import pytest

from crewai import Process

from orchestrator import agents as agents_pkg
from orchestrator.agents.crew import (
    OPENROUTER_BASE_URL,
    build_crew,
    build_delegatee_agents,
    build_exec_agent,
    build_orchestrator_agent,
    build_pi_llm,
    build_qa_agent,
    build_survey_agent,
)
from orchestrator.config import (
    DEFAULT_EXEC_MODEL,
    DEFAULT_ORCHESTRATOR_MODEL,
    DEFAULT_QA_MODEL,
    DEFAULT_SURVEY_MODEL,
    OrchestratorConfig,
)


# ---------------------------------------------------------------------------
# モデル割当デフォルト（調査結果に基づくコスパ優位モデル）
# ---------------------------------------------------------------------------
class TestModelDefaults:
    def test_research_backed_cost_effective_defaults(self):
        cfg = OrchestratorConfig()
        # オーケストレータ・調査 = コスパ系（フラッシュ）
        assert cfg.orchestrator_model == DEFAULT_ORCHESTRATOR_MODEL
        assert cfg.survey_model == DEFAULT_SURVEY_MODEL
        assert cfg.orchestrator_model.startswith("deepseek/")
        assert "flash" in cfg.orchestrator_model
        # 実行 = 中〜高性能 + コスパ
        assert cfg.exec_model == DEFAULT_EXEC_MODEL
        assert cfg.exec_model != cfg.orchestrator_model
        # QA = 高性能
        assert cfg.qa_model == DEFAULT_QA_MODEL
        assert cfg.qa_model != cfg.exec_model

    def test_all_models_externally_overrideable(self, monkeypatch):
        env = {
            "AGENT_ORCHESTRATOR_MODEL": "a/one",
            "AGENT_EXEC_MODEL": "b/two",
            "AGENT_QA_MODEL": "c/three",
            "AGENT_SURVEY_MODEL": "d/four",
        }
        cfg = OrchestratorConfig.from_env(env)
        assert cfg.orchestrator_model == "a/one"
        assert cfg.exec_model == "b/two"
        assert cfg.qa_model == "c/three"
        assert cfg.survey_model == "d/four"


# ---------------------------------------------------------------------------
# LLM 解決（OpenRouter 経由）
# ---------------------------------------------------------------------------
class TestLLM:
    def test_build_pi_llm_uses_openrouter(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        llm = build_pi_llm(OrchestratorConfig(), "deepseek/deepseek-v4-flash-0731")
        assert llm.provider == "openrouter"
        assert llm.model == "deepseek/deepseek-v4-flash-0731"

    def test_build_pi_llm_accepts_existing_prefix(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        llm = build_pi_llm(OrchestratorConfig(), "openrouter/deepseek/x")
        assert llm.model == "deepseek/x"
        assert llm.provider == "openrouter"

    def test_openrouter_base_url(self):
        assert OPENROUTER_BASE_URL == "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# エージェント生成とモデル注入
# ---------------------------------------------------------------------------
def _recording_factory(models: list):
    """呼び出しを記録しつつ実 LLM を返すファクトリ。"""
    def factory(config, model):
        models.append(model)
        return build_pi_llm(config, model)
    return factory


class TestAgents:
    def test_orchestrator_uses_orchestrator_model(self):
        cfg = OrchestratorConfig()
        agent = build_orchestrator_agent(cfg)
        assert agent.llm.model == cfg.orchestrator_model
        assert agent.llm.provider == "openrouter"
        assert agent.allow_code_execution is False

    def test_exec_uses_exec_model_and_tools(self):
        from orchestrator.tools.pi_tools import build_pi_tools

        cfg = OrchestratorConfig()
        tools = build_pi_tools(cfg)
        agent = build_exec_agent(cfg, tools=tools)
        assert agent.llm.model == cfg.exec_model
        assert [t.name for t in agent.tools][0] == "run_pi_single"
        assert agent.llm.model != cfg.qa_model

    def test_qa_uses_qa_model(self):
        cfg = OrchestratorConfig()
        agent = build_qa_agent(cfg)
        assert agent.llm.model == cfg.qa_model

    def test_survey_uses_survey_model(self):
        cfg = OrchestratorConfig()
        agent = build_survey_agent(cfg)
        assert agent.llm.model == cfg.survey_model

    def test_factory_injects_expected_models(self):
        cfg = OrchestratorConfig()
        seen: list[str] = []
        factory = _recording_factory(seen)
        a = build_orchestrator_agent(cfg, factory)
        build_exec_agent(cfg, llm_factory=factory)
        build_qa_agent(cfg, llm_factory=factory)
        build_survey_agent(cfg, llm_factory=factory)
        assert seen == [
            cfg.orchestrator_model,
            cfg.exec_model,
            cfg.qa_model,
            cfg.survey_model,
        ]

    def test_manager_only_no_code_tools(self):
        cfg = OrchestratorConfig()
        # マネジメント層エージェントはコード実行を許可しない（Manager-only）
        for build in (build_orchestrator_agent, build_exec_agent, build_qa_agent, build_survey_agent):
            assert build(cfg).allow_code_execution is False


class TestDelegateeAgents:
    def test_exec_and_qa_always_present(self):
        cfg = OrchestratorConfig()
        aset = build_delegatee_agents(cfg)
        assert aset.exec is not None and aset.qa is not None
        assert aset.survey is None
        assert len(aset.as_list()) == 2

    def test_survey_added_only_with_repo(self):
        cfg = OrchestratorConfig()
        aset = build_delegatee_agents(cfg, repo_path="/workspace/meta-agent-orchestrator")
        assert aset.survey is not None
        assert len(aset.as_list()) == 3
        assert any(t.name == "run_pi_survey" for t in aset.survey.tools)


# ---------------------------------------------------------------------------
# クルー構成（Process.HIERARCHICAL）
# ---------------------------------------------------------------------------
class TestCrew:
    def test_hierarchical_with_orchestrator_manager(self):
        cfg = OrchestratorConfig()
        crew = build_crew(cfg, repo_path="/workspace/meta-agent-orchestrator", include_survey=True)
        assert crew.process == Process.hierarchical
        # manager は agents リストに含まれず、manager_agent として別扱い
        assert crew.manager_agent is not None
        assert crew.manager_agent.role == "オーケストレータ（manager）"
        assert all(a.role != "オーケストレータ（manager）" for a in crew.agents)
        # 委譲先エージェントとタスク
        assert len(crew.agents) == 3
        assert len(crew.tasks) == 3

    def test_crew_minimal_no_survey(self):
        cfg = OrchestratorConfig()
        crew = build_crew(cfg)
        assert len(crew.agents) == 2
        assert len(crew.tasks) == 2
        assert crew.process == Process.hierarchical

    def test_roles_share_pi_tools(self):
        cfg = OrchestratorConfig()
        aset = build_delegatee_agents(cfg)
        exec_names = {t.name for t in aset.exec.tools}
        assert {"run_pi_single", "run_pi_continue", "run_pi_fork", "compact_context"} <= exec_names
        qa_names = {t.name for t in aset.qa.tools}
        assert exec_names == qa_names  # 全エージェントで pi Tool を共有


# ---------------------------------------------------------------------------
# パッケージ公開API
# ---------------------------------------------------------------------------
class TestPublicAPI:
    def test_init_exports(self):
        for name in (
            "build_crew",
            "build_delegatee_agents",
            "build_orchestrator_agent",
            "build_pi_llm",
            "build_implement_task",
            "build_verify_task",
            "build_existing_project_task",
        ):
            assert hasattr(agents_pkg, name)

    def test_task_builders(self):
        from crewai import Task as CrewTask
        from orchestrator.agents.crew import (
            build_existing_project_task,
            build_implement_task,
            build_verify_task,
            build_delegatee_agents,
        )
        cfg = OrchestratorConfig()
        aset = build_delegatee_agents(cfg)
        assert isinstance(build_implement_task(aset.exec), CrewTask)
        assert isinstance(build_verify_task(aset.qa), CrewTask)
        assert build_implement_task(aset.exec).agent is aset.exec
        aset2 = build_delegatee_agents(cfg, repo_path="/workspace/meta-agent-orchestrator")
        assert isinstance(build_existing_project_task(aset2.survey), CrewTask)
