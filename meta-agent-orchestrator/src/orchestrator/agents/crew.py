"""CrewAI エージェント群とクルー構成 (PLAN F-04/F-05/F-06/F-10 / TASK.md Task 8)。

オーケストレータ（manager）・実行・QA・調査の各エージェントを定義し、
**Process.HIERARCHICAL** のクルーとして組み立てる。

- **オーケストレータ（manager）**: 戦略・タスク分解・最終判定。コスパ系モデル。
- **実行インターフェーサー**: pi Tool で実装を進める。中〜高性能モデル。
- **QAクリティック**: 批判的視点で検証。高性能モデル。
- **調査エージェント**: 既存コード把握。コスパ系モデル。（必要時のみ）

**Manager-only 原則を厳守**するため、各エージェントは pi Tool（Task 4）のみで
コードに触れ、マネジメント層はコードへ直接アクセスしない。

モデルはすべて Task 2 の :class:`orchestrator.config.OrchestratorConfig`
（env / 引数で外部指定）から注入される。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from crewai import Agent, Crew, Process, Task as CrewTask
from crewai.llm import LLM

from orchestrator.config import OrchestratorConfig
from orchestrator.tools.pi_tools import build_pi_tools, make_survey_tool

logger = logging.getLogger(__name__)

# OpenRouter の OpenAI 互換エンドポイント（litellm が使う）
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# OpenRouter キーを読む環境変数名（複数表記を許容）。値は本モジュール内でのみ使用し、
# ログ・出力などに決して含めない（AGENTS.md の秘密情報扱い方針に準拠）。
_OPENROUTER_KEY_ENVS = ("OPENROUTER_API_KEY", "OPENROUTER_APIKEY", "OPENROUTOR_APIKEY")

# エージェントの役割キー
ROLE_ORCHESTRATOR = "orchestrator"
ROLE_EXEC = "exec"
ROLE_QA = "qa"
ROLE_SURVEY = "survey"


# ---------------------------------------------------------------------------
# pi 用 LLM の解決
# ---------------------------------------------------------------------------
def _openrouter_api_key() -> str | None:
    """OpenRouter API キーを環境変数から解決する（値は返すだけで出力しない）。"""
    for name in _OPENROUTER_KEY_ENVS:
        value = os.environ.get(name)
        if value:
            return value
    return None


def build_pi_llm(config: OrchestratorConfig, model: str) -> LLM:
    """OpenRouter 経由の CrewAI ``LLM`` を生成する。

    CrewAI >= 0.86 はモデル名中の ``openrouter/`` プレフィックスをネイティブ
    OpenRouter プロバイダとして解釈し、API キーが必須となる。本関数では
    代わりに OpenAI 互換プロバイダ（``provider="openai"``）＋ OpenRouter の
    ベース URL を用いることで、追加のインストール（litellm 等）なしに
    OpenRouter 経由のモデル呼び出しを実現する。

    OpenRouter API キーは環境変数から読み、値は一切出力しない。

    Args:
        config: 設定（provider 解決。直接は使わずモデル ID のみ使用）。
        model: 役割別モデル ID（例: ``deepseek/deepseek-v4-flash-0731``）。

    Returns:
        CrewAI ``LLM`` インスタンス（provider=openai, base_url=OpenRouter）。
        構成時に API は呼ばない。
    """
    # CrewAI >= 0.86 のネイティブ OpenRouter プロバイダは API キー必須のため
    # OpenAI 互換プロバイダ（provider="openai"）＋ OpenRouter のベース URL で代用
    clean_model = model.removeprefix("openrouter/")
    return LLM(
        model=clean_model,
        api_key=_openrouter_api_key(),
        base_url=OPENROUTER_BASE_URL,
        # OpenAI 互換プロバイダで OpenRouter にルーティング。
        # ネイティブ OpenRouter / DeepSeek 等の解析を回避するため明示指定。
        provider="openai",
    )


# ---------------------------------------------------------------------------
# 役割別システムプロンプト（Manager-only 原則を明記）
# ---------------------------------------------------------------------------
def _manager_only_note(tools_hint: str) -> str:
    return (
        "あなたはマネジメント層の一員です。Manager-only 原則により、コード・ファイル・"
        "検証・調査に**直接アクセスしてはなりません**。あらゆるコード接触は pi 連携ツール"
        f"（{tools_hint}）を介してのみ行い、結果は自然言語レポートとして扱ってください。"
    )


ORCHESTRATOR_BACKSTORY = (
    "あなたは開発オーケストレーションの司令塔（オーケストレータ / manager）です。"
    "仕様書と現状把握から戦略を立案し、作業を小さいタスクへ分解し、依存関係の順に"
    "各エージェント（実行・QA・調査）へ割り当て、進捗を監視して最終的な完了判定を行います。"
    "コスパと品質の両立を常に意識し、コンテクスト肥大を避けるため報告は簡潔に求めます。"
)

EXEC_BACKSTORY = (
    "あなたは実行インターフェーサーです。与えられたタスクを、pi エージェントを起動して"
    "実際に実装します。読み書き・検証はすべて pi を使い、その出力を自然言語レポートとして"
    "オーケストレータへ返します。仕様を過不足なく満たし、可読性を保ち、テストを実行して"
    "結果を報告します。"
)

QA_BACKSTORY = (
    "あなたは独立した QA クリティックです。オーケストレータや実行エージェントとは独立した"
    "批判的視点で、成果物が仕様を満たすか・テストが通るか・可読性があるかを pi による検証で"
    "確認します。問題があれば具体的な問題リストとして返し、通過なら合格と明記します。"
    "甘い判定はしません。"
)

SURVEY_BACKSTORY = (
    "あなたは調査エージェントです。既存のコードベースを pi の読み取り専用ツール"
    "（read / grep / find / ls）で解析し、オーケストレータが計画に使える現状レポートを"
    "自然言語で生成します。コードは一切変更しません。"
)


# ---------------------------------------------------------------------------
# エージェント生成
# ---------------------------------------------------------------------------
def build_orchestrator_agent(
    config: OrchestratorConfig,
    llm_factory: Callable[[OrchestratorConfig, str], Any] | None = None,
) -> Agent:
    """オーケストレータ（manager）エージェントを生成する。コスパ系モデル。"""
    factory = llm_factory or build_pi_llm
    llm = factory(config, config.orchestrator_model)
    return Agent(
        role="オーケストレータ（manager）",
        goal=(
            "仕様書を満たす最終成果物を、計画・分解・割当・最終判定により自律的に完成させる。"
            "費用を最適化し、品質は独立QAで担保する。"
        ),
        backstory=ORCHESTRATOR_BACKSTORY + " " + _manager_only_note("pi 連携ツール"),
        llm=llm,
        verbose=False,
    )


def build_exec_agent(
    config: OrchestratorConfig,
    tools: list[Any] | None = None,
    llm_factory: Callable[[OrchestratorConfig, str], Any] | None = None,
) -> Agent:
    """実行インターフェーサーエージェントを生成する。中〜高性能 + コスパモデル。"""
    factory = llm_factory or build_pi_llm
    llm = factory(config, config.exec_model)
    return Agent(
        role="実行インターフェーサー",
        goal="pi を使ってタスクを確実に実装し、仕様を満たすコードを自然言語レポートと共に返す。",
        backstory=EXEC_BACKSTORY + " " + _manager_only_note("run_pi_single / run_pi_continue / run_pi_fork 等"),
        llm=llm,
        tools=list(tools or []),
        verbose=False,
    )


def build_qa_agent(
    config: OrchestratorConfig,
    tools: list[Any] | None = None,
    llm_factory: Callable[[OrchestratorConfig, str], Any] | None = None,
) -> Agent:
    """QAクリティックエージェントを生成する。高性能モデル（品質の生命線）。"""
    factory = llm_factory or build_pi_llm
    llm = factory(config, config.qa_model)
    return Agent(
        role="QAクリティック",
        goal="独立した批判的視点で成果物を検証し、問題リストまたは合格判定を返す。",
        backstory=QA_BACKSTORY + " " + _manager_only_note("run_pi_single（検証）"),
        llm=llm,
        tools=list(tools or []),
        verbose=False,
    )


def build_survey_agent(
    config: OrchestratorConfig,
    tools: list[Any] | None = None,
    llm_factory: Callable[[OrchestratorConfig, str], Any] | None = None,
) -> Agent:
    """調査エージェントを生成する。コスパ系モデル。既存リポジトリ調査（F-10）。"""
    factory = llm_factory or build_pi_llm
    llm = factory(config, config.survey_model)
    return Agent(
        role="調査エージェント",
        goal="既存コードベースを読み取り専用 pi で解析し、計画に使える現状レポートを生成する。",
        backstory=SURVEY_BACKSTORY + " " + _manager_only_note("run_pi_survey"),
        llm=llm,
        tools=list(tools or []),
        verbose=False,
    )


# ---------------------------------------------------------------------------
# 委譲先エージェント一式（クルーの agents リスト用）
# ---------------------------------------------------------------------------
@dataclass
class AgentSet:
    """役割別エージェントの集合。``exec`` / ``qa`` は常に、``survey`` は既存時のみ。"""

    exec: Agent
    qa: Agent
    survey: Agent | None = None

    def as_list(self) -> list[Agent]:
        return [self.exec, self.qa] + ([self.survey] if self.survey else [])

    def as_dict(self) -> Mapping[str, Agent]:
        d: dict[str, Agent] = {ROLE_EXEC: self.exec, ROLE_QA: self.qa}
        if self.survey:
            d[ROLE_SURVEY] = self.survey
        return d


def build_delegatee_agents(
    config: OrchestratorConfig,
    repo_path: str | None = None,
    llm_factory: Callable[[OrchestratorConfig, str], Any] | None = None,
) -> AgentSet:
    """実行・QA・（既存時）調査の委譲先エージェントを生成する。

    Args:
        config: モデル割当等の設定。
        repo_path: 既存リポジトリパス。指定時は調査エージェントに読み取り専用 Tool を付与。
        llm_factory: テスト注入用の LLM 生成関数。

    Returns:
        :class:`AgentSet`。
    """
    tools = build_pi_tools(config)
    survey_agent = None
    if repo_path:
        survey_agent = build_survey_agent(config, [make_survey_tool(config, repo_dir=repo_path)], llm_factory)
    return AgentSet(
        exec=build_exec_agent(config, tools, llm_factory),
        qa=build_qa_agent(config, tools, llm_factory),
        survey=survey_agent,
    )


# ---------------------------------------------------------------------------
# 委譲先タスク
# ---------------------------------------------------------------------------
def build_implement_task(
    exec_agent: Agent,
    description: str = (
        "共有ボードの計画に基づき、指定された実装タスクを pi で実行してください。"
        "pi の実行結果（自然言語レポートと exit code）と、実装内容の要約を日本語で報告してください。"
    ),
    expected_output: str = "実装済みコードと、自然言語の実装レポート（成功/失敗・exit code）",
) -> CrewTask:
    """実行インターフェーサー用のタスクを生成する。"""
    return CrewTask(description=description, expected_output=expected_output, agent=exec_agent)


def build_verify_task(
    qa_agent: Agent,
    description: str = (
        "実装済みの成果物を、独立した批判的視点で pi を使って検証してください。"
        "仕様充足・テスト実行・可読性を確認し、問題があれば具体的な問題リストを、"
        "なければ合格と明記して自然言語で報告してください。甘い判定はしないでください。"
    ),
    expected_output: str = "QA 検証レポート（問題リスト or 合格）",
) -> CrewTask:
    """QAクリティック用のタスクを生成する。"""
    return CrewTask(description=description, expected_output=expected_output, agent=qa_agent)


def build_existing_project_task(
    survey_agent: Agent,
    description: str = (
        "既存コードベースを読み取り専用 pi（run_pi_survey）で調査し、"
        "現状レポート（概要・技術スタック・構成・主要モジュール・テスト有無・リスク）を"
        "自然言語で生成してください。コードは変更しないでください。"
    ),
    expected_output: str = "既存コードベースの現状レポート（自然言語）",
) -> CrewTask:
    """調査エージェント用のタスクを生成する（既存案件 F-10 のみ）。"""
    return CrewTask(description=description, expected_output=expected_output, agent=survey_agent)


def default_tasks(agent_set: AgentSet, include_survey: bool = False) -> list[CrewTask]:
    """既定の委譲先タスク一式（実装・検証・任意で調査）を生成する。"""
    tasks: list[CrewTask] = [build_implement_task(agent_set.exec), build_verify_task(agent_set.qa)]
    if include_survey and agent_set.survey:
        tasks.insert(0, build_existing_project_task(agent_set.survey))
    return tasks


# ---------------------------------------------------------------------------
# クルー構築（Process.HIERARCHICAL）
# ---------------------------------------------------------------------------
def build_crew(
    config: OrchestratorConfig,
    *,
    repo_path: str | None = None,
    tasks: list[CrewTask] | None = None,
    include_survey: bool = False,
    llm_factory: Callable[[OrchestratorConfig, str], Any] | None = None,
    verbose: bool = False,
) -> Crew:
    """HIERARCHICAL クルーを構築する。

    オーケストレータが **manager_agent** として振る舞い、実行・QA・（既存時）調査の
    委譲先エージェントが ``agents`` に、役割ごとのタスクが ``tasks`` に入る。

    Args:
        config: モデル割当（各エージェントの LLM に注入）。
        repo_path: 既存リポジトリパス（任意）。指定時は調査エージェントを付与。
        tasks: 委譲先タスク（省略時は既定の実装・検証・調査タスクを使う）。
        include_survey: 既定タスク生成時に調査タスクを含めるか。
        llm_factory: テスト注入用の LLM 生成関数。
        verbose: CrewAI の verbose フラグ。

    Returns:
        実行可能な :class:`Crew`（``crew.kickoff()`` で駆動; 実行は Task 9 以降）。
    """
    agent_set = build_delegatee_agents(config, repo_path=repo_path, llm_factory=llm_factory)
    orchestrator = build_orchestrator_agent(config, llm_factory=llm_factory)
    if tasks is None:
        tasks = default_tasks(agent_set, include_survey=include_survey)

    return Crew(
        agents=agent_set.as_list(),
        tasks=tasks,
        process=Process.hierarchical,
        manager_agent=orchestrator,
        verbose=verbose,
    )
