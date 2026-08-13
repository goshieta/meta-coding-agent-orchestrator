"""設定管理 (PLAN F-09 / F-12)。

全設定（モデル割当・GitHub トークン・pi セッション等）を外部（環境変数 / CLI 引数）
から制御し、単一の :class:`OrchestratorConfig` dataclass に集約する。

解決優先順位:
    CLI 引数 > 環境変数 > デフォルト値

デフォルト値は PLAN F-12（役割別モデル割当）に準拠する:
    - オーケストレータ: コスパ系
    - 実行            : 中〜高性能
    - QA              : 高性能
    - 調査            : コスパ系
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

# ---------------------------------------------------------------------------
# 環境変数名の定義（TASK.md / PLAN F-09 準拠）
# ---------------------------------------------------------------------------
ENV_ORCHESTRATOR_MODEL = "AGENT_ORCHESTRATOR_MODEL"
ENV_EXEC_MODEL = "AGENT_EXEC_MODEL"
ENV_QA_MODEL = "AGENT_QA_MODEL"
ENV_SURVEY_MODEL = "AGENT_SURVEY_MODEL"
ENV_GITHUB_TOKEN = "GITHUB_TOKEN"
ENV_PI_SESSION_DIR = "PI_SESSION_DIR"
ENV_PI_PROVIDER = "PI_PROVIDER"
ENV_PI_THINKING = "PI_THINKING"
ENV_PI_TRUST = "PI_TRUST"

# ---------------------------------------------------------------------------
# デフォルト値 (PLAN F-12 / TASK.md Task 8 で調査した最新モデル 2026 時点)
# ---------------------------------------------------------------------------
# 注: すべて外部（env / 引数）から上書きできる。コストパフォーマンスを優先。
#
# 調査結果（OpenRouter API の実価格、入力/出力 1M トークン当たり）：
#   - deepseek/deepseek-v4-flash-0731 : $0.08 / $0.18  —— 最安〜激安のフラッシュ系。
#     計画・要約・ナビゲーション用途（オーケストレータ/調査）に最適（PLAN の
#     "deepseek v4-flash 系" に相当）。
#   - deepseek/deepseek-v4-pro-0813   : $0.435 / $0.87 —— 推論・コード能力が高いコスパ系。
#     実装（実行エージェント）の中〜高性能 + 予算重視に最適。
#   - anthropic/claude-sonnet-4.6     : $3.0 / $15.0  —— フロンティア級の高品質を
#     オーパス（$5/$25）より廉価に。QA（品質生命線）のコスパ最良。
#
# ※ 実行・QA をさらに高機能にしたければ claude-sonnet-4.6 / claude-opus-4.6 等へ
#   外部設定で差し替え可能。
DEFAULT_ORCHESTRATOR_MODEL = "deepseek/deepseek-v4-flash-0731"  # コスパ系（計画・戦略）
DEFAULT_EXEC_MODEL = "deepseek/deepseek-v4-pro-0813"  # 中〜高性能 + コスパ併用
DEFAULT_QA_MODEL = "anthropic/claude-sonnet-4.6"  # 高品質 + コスパ最良（検証の生命線）
DEFAULT_SURVEY_MODEL = "deepseek/deepseek-v4-flash-0731"  # コスパ系（調査）

DEFAULT_PI_PROVIDER = "openrouter"
DEFAULT_PI_THINKING = True
DEFAULT_PI_TRUST = "defaultProjectTrust"

# 真偽値として解釈する文字列（環境変数 PI_THINKING 用）
_TRUTHY = {"1", "true", "yes", "on", "y"}


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUTHY


@dataclass
class OrchestratorConfig:
    """全設定を集約する単一の設定オブジェクト。全モジュールから参照される。"""

    # --- 役割別モデル割当 (F-12) ---
    orchestrator_model: str = DEFAULT_ORCHESTRATOR_MODEL
    exec_model: str = DEFAULT_EXEC_MODEL
    qa_model: str = DEFAULT_QA_MODEL
    survey_model: str = DEFAULT_SURVEY_MODEL

    # --- GitHub / pi セッション / pi 実行 ---
    github_token: str | None = None
    pi_session_dir: str | None = None
    pi_provider: str = DEFAULT_PI_PROVIDER
    pi_thinking: bool = DEFAULT_PI_THINKING
    pi_trust: str = DEFAULT_PI_TRUST

    # --- 補助 ---
    env_file: str | None = field(default=None, repr=False)
    """読み込んだ .env ファイルのパス（デバッグ用）。"""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "OrchestratorConfig":
        """環境変数（および .env ファイル）から設定を構築する。

        ``env`` が与えられない場合は ``os.environ`` を参照する。
        """
        source = env if env is not None else os.environ

        def get(key: str) -> str | None:
            return source.get(key)

        def get_bool(key: str, default: bool) -> bool:
            value = source.get(key)
            if value is None:
                return default
            return value.strip().lower() in _TRUTHY

        return cls(
            orchestrator_model=get(ENV_ORCHESTRATOR_MODEL) or DEFAULT_ORCHESTRATOR_MODEL,
            exec_model=get(ENV_EXEC_MODEL) or DEFAULT_EXEC_MODEL,
            qa_model=get(ENV_QA_MODEL) or DEFAULT_QA_MODEL,
            survey_model=get(ENV_SURVEY_MODEL) or DEFAULT_SURVEY_MODEL,
            github_token=get(ENV_GITHUB_TOKEN),
            pi_session_dir=get(ENV_PI_SESSION_DIR),
            pi_provider=get(ENV_PI_PROVIDER) or DEFAULT_PI_PROVIDER,
            pi_thinking=get_bool(ENV_PI_THINKING, DEFAULT_PI_THINKING),
            pi_trust=get(ENV_PI_TRUST) or DEFAULT_PI_TRUST,
        )

    def apply_overrides(self, **overrides: object) -> "OrchestratorConfig":
        """CLI 引数などの個別上書きを適用した新しい設定を返す。

        ``None`` の値は「未指定」として扱い、既存値を保持する。
        """
        filtered = {k: v for k, v in overrides.items() if v is not None}
        return replace(self, **filtered)


# ---------------------------------------------------------------------------
# スキーマ定義（CLI 引数 -> 設定フィールド / 環境変数の対応表）
# ---------------------------------------------------------------------------
# Task 3 の CLI 実装やタスク間連携で用いる。各要素:
#   dest    : CLI 引数の dest（--xxx-model に対応）
#   field   : OrchestratorConfig のフィールド名
#   env     : 対応する環境変数名
CONFIG_OVERRIDE_FIELDS: tuple[dict[str, str], ...] = (
    {"cli": "orchestrator_model", "field": "orchestrator_model", "env": ENV_ORCHESTRATOR_MODEL},
    {"cli": "exec_model", "field": "exec_model", "env": ENV_EXEC_MODEL},
    {"cli": "qa_model", "field": "qa_model", "env": ENV_QA_MODEL},
    {"cli": "survey_model", "field": "survey_model", "env": ENV_SURVEY_MODEL},
    {"cli": "github_token", "field": "github_token", "env": ENV_GITHUB_TOKEN},
    {"cli": "pi_session_dir", "field": "pi_session_dir", "env": ENV_PI_SESSION_DIR},
    {"cli": "pi_provider", "field": "pi_provider", "env": ENV_PI_PROVIDER},
    {"cli": "pi_thinking", "field": "pi_thinking", "env": ENV_PI_THINKING},
    {"cli": "pi_trust", "field": "pi_trust", "env": ENV_PI_TRUST},
)


def build_config(
    env: dict[str, str] | None = None,
    dotenv_path: str | None = None,
    **cli_overrides: object,
) -> OrchestratorConfig:
    """環境変数 + .env + CLI 引数上書きから最終設定を構築する。

    優先順位: CLI 引数 > 環境変数 > デフォルト値

    Args:
        env: 環境変数の dict（省略時は os.environ）。テストで注入する。
        dotenv_path: あれば .env を読み込み env へマージする。
        cli_overrides: CLI 引数由来の上書き（None は無視）。

    Returns:
        構築された :class:`OrchestratorConfig`。
    """
    merged = dict(env) if env is not None else dict(os.environ)

    # .env があれば読み込み、未設定のキーのみ補完（引数で明示された env を優先）
    if dotenv_path and os.path.isfile(dotenv_path):
        from dotenv import dotenv_values

        loaded = dotenv_values(dotenv_path)
        for key, value in loaded.items():
            merged.setdefault(key, value)

    config = OrchestratorConfig.from_env(env=merged)
    # .env を読み込んだ旨を記録（デバッグ用）
    if dotenv_path and os.path.isfile(dotenv_path):
        config = replace(config, env_file=dotenv_path)

    return config.apply_overrides(**cli_overrides)
