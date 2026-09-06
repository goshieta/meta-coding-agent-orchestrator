# Meta-Agent Orchestrator 🚀

<div align="center">

**仕様書（`.md`）をワンライナーで渡すだけで、AI エージェント群がコードを実装・検証・GitHub 納品まで完遂する開発オーケストレーション中間層**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://python.org)
[![CrewAI](https://img.shields.io/badge/CrewAI-0.86%2B-ff6b6b)](https://crewai.com)
[![Docker](https://img.shields.io/badge/Docker-✓-2496ed)](https://docker.com)
[![pi](https://img.shields.io/badge/pi-Agent-4k4k4k)](https://pi.dev)

</div>

---

## ✨ クイックスタート

```bash
# 1. インストール
git clone <this-repo> && cd meta-agent-orchestrator
uv sync

# 2. テストを実行
uv run pytest tests/

# 3. ワンライナーで起動！
./run-spec.sh path/to/spec.md

# 4. 既存リポジトリがある場合
./run-spec.sh path/to/spec.md path/to/existing/repo/
```

たったこれだけで、コンテナ内で CrewAI が仕様書を解析 → タスク分解 → pi で実装 → 独立 QA → GitHub 公開 までを自動実行します。

---

## 📋 使い方（コマンドリファレンス）

### 基本

```bash
./run-spec.sh <spec.md> [existing_repo/] [options]
```

### 主なオプション

| オプション | 説明 |
| --- | --- |
| `--rebuild` | Docker イメージを再ビルド |
| `--follow` | コンテナログをリアルタイム表示 |
| `--foreground` | フォアグラウンド（`-it`）で起動 |
| `--dry-run` | 実行する `docker run` コマンドだけ表示 |
| `--data-dir DIR` | 永続データディレクトリ（既定: `./data`） |
| `--docker-image TAG` | 使用イメージ（既定: `meta-agent-orchestrator:latest`） |

### モデル設定（環境変数 or CLI 引数）

```bash
./run-spec.sh spec.md \
  --orchestrator-model "deepseek/deepseek-v4-flash-0731" \
  --exec-model "deepseek/deepseek-v4-pro-0813" \
  --qa-model "anthropic/claude-sonnet-4.6"
```

環境変数でも設定可能: `AGENT_ORCHESTRATOR_MODEL`, `AGENT_EXEC_MODEL`, `AGENT_QA_MODEL`, `AGENT_SURVEY_MODEL`

---

## 🔧 開発者向け：モジュール一覧

| モジュール | 役割 |
| --- | --- |
| `orchestrator.cli` | CLI 入口・仕様書検証 |
| `orchestrator.config` | 設定管理（環境変数 / 引数 / デフォルト） |
| `orchestrator.container` | Docker コンテナ隔離実行 |
| `orchestrator.context` | コンテクスト構築（新規・既存） |
| `orchestrator.planner` | タスク分解・共有ボード生成 |
| `orchestrator.executor` | pi 実装ループ・再試行・compact・commit |
| `orchestrator.qa` | 独立 QA 検証・不合格タスクの再送 |
| `orchestrator.deliver` | GitHub リポジトリ作成・push（1回のみ） |
| `orchestrator.human_gate` | 人間への質問・強制停止 |
| `orchestrator.workspace` | 共有ボード・ログ・セッション管理 |
| `orchestrator.pipeline` | 全フェーズ統合パイプライン |
| `orchestrator.tools.pi_tools` | pi 連携 CrewAI Tool 群 |
| `orchestrator.agents.crew` | CrewAI エージェント群・クルー構成 |

### Python API サンプル

```python
from orchestrator import run_loop, QAOptions, Workspace
from orchestrator.config import build_config

config = build_config()
ws = Workspace.open("work/")

# 実装ループ
result = run_loop(ws, config, LoopOptions(repo_dir="work/"))
print(result.summary())
```

---

## 🔄 アーキテクチャ

```
仕様書(.md)
   │
   ▼
┌─────────────────────────────────────────────────────┐
│  Docker コンテナ（隔離実行環境）                      │
│                                                     │
│  CrewAI マネジメント層（Manager-only）               │
│  ├─ オーケストレータ（manager）                      │
│  │   戦略立案・タスク分解・最終判定                   │
│  ├─ 実行インターフェーサー ──▶ pi ──▶ 実装・commit    │
│  ├─ QA クリティック     ──▶ pi ──▶ 検証・PASS/FAIL  │
│  └─ 調査エージェント    ──▶ pi ──▶ 既存コード解析   │
│                                                     │
│  全コード接触は pi 経由。マネジメント層は直接操作しない │
└─────────────────────────────────────────────────────┘
   │
   ▼
GitHub リポジトリ（全タスク QA 通過 + 承認後に 1 回 push）
```

---

## 📦 技術スタック

| 項目 | 内容 |
| --- | --- |
| 言語 | Python 3.11+ |
| 環境管理 | [uv](https://docs.astral.sh/uv) |
| マネジメント層 | [CrewAI](https://crewai.com)（Process.HIERARCHICAL） |
| 実行エージェント | [pi coding agent](https://pi.dev)（subprocess） |
| コンテナ | Docker |
| モデルプロバイダ | OpenRouter（litellm / OpenAI 互換） |

---

## ⚙️ 環境変数一覧

| 変数 | 既定値 | 説明 |
| --- | --- | --- |
| `AGENT_ORCHESTRATOR_MODEL` | `deepseek/...-flash` | オーケストレータモデル |
| `AGENT_EXEC_MODEL` | `deepseek/...-pro` | 実行モデル |
| `AGENT_QA_MODEL` | `anthropic/claude-sonnet-4.6` | QA 検証モデル |
| `AGENT_SURVEY_MODEL` | `deepseek/...-flash` | 調査モデル |
| `OPENROUTER_API_KEY` | — | **必須** OpenRouter 認証キー |
| `GITHUB_TOKEN` | — | GitHub 認証トークン（納品時） |
| `PI_PROVIDER` | `openrouter` | pi のプロバイダ |
| `PI_THINKING` | `true` | pi の thinking モード |
| `PI_TRUST` | `defaultProjectTrust` | pi の trust 制御 |

---

## 🧪 テスト

```bash
# 全テスト実行（外部 API 不要）
uv run pytest tests/

# 特定のテストのみ
uv run pytest tests/test_workspace.py -v
```

---

## 🐳 Docker コンテナの詳細

コンテナイメージは **pi coding agent + uv + CrewAI** をプリインストール。  
`node:24-bookworm-slim` ベース（約 1.2GB）。

手動ビルド:

```bash
docker build -t meta-agent-orchestrator:latest .
```

---

## 📁 ディレクトリ構成

```
meta-agent-orchestrator/
├── pyproject.toml          # 依存定義
├── Dockerfile              # コンテナ定義
├── run-spec.sh             # ワンライナー起動スクリプト
├── src/orchestrator/       # 本体
│   ├── cli.py / config.py / container.py / ...
│   ├── tools/              # pi 連携 Tool 群
│   └── agents/             # CrewAI エージェント群
├── tests/                  # 全テスト（135件）
└── data/                   # 永続データ（自動生成）
```

---

## 📜 ライセンス

MIT