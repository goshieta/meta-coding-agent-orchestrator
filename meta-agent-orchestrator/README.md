# Meta-Agent Orchestrator

仕様書（`md`）を**ワンライナーで渡すだけで**、コンテナ内で CrewAI 製マネジメント層が
[pi](https://github.com/earendil-labs/pi-coding-agent) のセッション・コンテクスト・品質を自律管理し、
人間の介入なしに最終成果物を GitHub リポジトリとして完成投稿まで行う開発オーケストレーション中間層です。

> **ステータス**: Task 1（雛形・環境構築）/ Task 2（設定管理）/ Task 3（CLI・仕様書検証）/
> Task 4（pi 連携 CrewAI Tool 群）完了。
> 本ファイルは後続タスクの進行に合わせて随時更新されます。

---

## 概要

- **ワンライナー起動**: `run-spec.sh <spec.md> [existing_repo/]` で開始（未実装）。
- **コンテナ隔離**: 全エージェント・pi プロセスをコンテナ内で稼働（未実装）。
- **品質担保**: 独立 QA クリティック通過のみ完了へ（未実装）。
- **費用最適化**: 役割別モデル割当を外部設定化（未実装）。
- **新規・既存両対応**: 既存コードベースの現状把握（未実装）。

## 技術スタック

| 項目 | 仕様 |
| --- | --- |
| 言語 / 環境 | Python 3.x（>=3.11）、[uv](https://docs.astral.sh/uv/) で環境管理 |
| マネジメント層 | CrewAI（Agent / Task / Crew / Process） |
| 実行方式 | `Process.HIERARCHICAL`（オーケストレータ = manager） |
| 実行ツール | pi coding agent（CLI を subprocess で呼ぶ） |
| コンテナ | Docker |

## クイックスタート

前提: [uv](https://docs.astral.sh/uv/) がインストールされていること。

```bash
# 依存関係を同期
uv sync

# 起動確認（hello メッセージ）
uv run python -m orchestrator

# テスト実行
uv run pytest tests/
```

　

## pi 連携 CrewAI Tool 群（Task 4）

マネジメント層（CrewAI）は本 Tool 経由でのみ pi プロセスを操作します。
**Manager-only 原則**に従い、エージェントはコードへ直接アクセスせず、常に pi を駆動して作業します。

- `run_pi_single` : `pi -p @spec.md "タスク"` のワンショット実行（新規タスク）
- `run_pi_continue` : `pi -c` / `--session <id>` によるセッション継続
- `run_pi_fork` : `pi --fork <id>` で新セッション生成（失敗・再実行時）
- `compact_context` : コンテクスト圧縮（`/compact` 相当）の指示を送信
- `export_session` : セッション（JSONL）の永続化

各 Tool は `OrchestratorConfig` から `--provider` / `--model` / `--thinking` / trust 制御
（`--approve` 等）を付与します。実行結果は自然言語レポート＋**exit code** として返り、
オーケストレータが消費します。

```python
from orchestrator.config import build_config
from orchestrator.tools import build_pi_tools

config = build_config()                 # 環境変数 / CLI 引数から設定を解決
tools = build_pi_tools(config)          # pi 連携 Tool 一式を生成
tools[0].run("実装してください")        # run_pi_single で pi を実行
```

> **ノート（headless 運用）**: pi の `/compact` / `/export` は対話型 TUI のスラッシュコマンドのため、
> 非対話（`-p`）オーケストレーションではそれぞれ「圧縮指示プロンプトの送信」「セッション JSONL の
> 永続コピー」で代替しています。

## 起動（ワンライナー）

```bash
./run-spec.sh <spec.md> [existing_repo/] [options]
```

- `spec.md`: 仕様書（必須。存在・非空を検証）
- `existing_repo/`: 既存リポジトリ（任意）
- オプション: `--orchestrator-model` / `--exec-model` / `--qa-model` / `--survey-model` / `--github-token` / `--log-dir`

仕様書未指定・不存在・空ファイルの場合はエラーメッセージを出して即終了（exit 非0）します。

## 設定（環境変数）

全設定は**環境変数 > デフォルト値**、CLI 引数 > 環境変数の優先順位で外部から制御できます（Task 3 で CLI 引数が接続されます）。

| 環境変数 | 役割 | デフォルト |
| --- | --- | --- |
| `AGENT_ORCHESTRATOR_MODEL` | オーケストレータ（コスパ系） | `deepseek/deepseek-chat` |
| `AGENT_EXEC_MODEL` | 実行インターフェーサー（中〜高性能） | `anthropic/claude-sonnet-4-5` |
| `AGENT_QA_MODEL` | QA クリティック（高性能） | `anthropic/claude-opus-4-1` |
| `AGENT_SURVEY_MODEL` | 調査エージェント（コスパ系） | `deepseek/deepseek-chat` |
| `GITHUB_TOKEN` | GitHub 認証トークン | なし |
| `PI_SESSION_DIR` | pi セッション保存ディレクトリ | なし |
| `PI_PROVIDER` | pi のプロバイダ | `openrouter` |
| `PI_THINKING` | pi の thinking 有効/無効 | `true` |
| `PI_TRUST` | pi の trust 制御 | `defaultProjectTrust` |

> モデル ID は PLAN の保留事項のため調整可能なプレースホルダです。すべて外部設定で上書きできます。

## ディレクトリ構成

```
meta-agent-orchestrator/
├── pyproject.toml            # uv による環境定義（Task 1）
├── README.md                 # 本ファイル
├── src/orchestrator/
│   ├── __init__.py
│   ├── __main__.py           # python -m orchestrator の入口
│   ├── config.py             # 設定管理（ORCHESTRATOR_MODEL 等 / Task 2）
│   ├── cli.py                # CLI入口・引数解析・仕様書検証（Task 3）
│   ├── tools/
│   │   ├── __init__.py
│   │   └── pi_tools.py       # pi 連携 CrewAI Tool 群（Task 4）
│   └── main.py               # CLI への薄い委譲エントリ
├── run-spec.sh               # ワンライナー起動（Task 3）
└── tests/                    # テスト（Task 1 はダミー）
```

> 構成は PLAN.md / TASK.md に沿って後続タスクで拡張されます。

## ロードマップ

- [x] Task 1: プロジェクト雛形・環境構築
- [x] Task 2: 設定管理（Config）×外部化
- [x] Task 3: CLI エントリポイントと仕様書検証
- [x] Task 4: pi 連携 CrewAI Tool 群
- [ ] Task 5: ワークスペース・共有ボード・状態管理
- [ ] Task 6: コンテクスト構築（新規・既存）
- [ ] Task 7: コンテナ隔離実行
- [ ] Task 8: CrewAI エージェント群とクルー構成
- [ ] Task 9: 実装ループ
- [ ] Task 10: 品質保証（独立 QA クリティック）
- [ ] Task 11: GitHub アップロードと完了・納品
- [ ] Task 12: 人間インターフェース
- [ ] Task 13: 統合テスト・最終検証・README 仕上げ
