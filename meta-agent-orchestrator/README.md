# Meta-Agent Orchestrator

仕様書（`md`）を**ワンライナーで渡すだけで**、コンテナ内で CrewAI 製マネジメント層が
[pi](https://github.com/earendil-labs/pi-coding-agent) のセッション・コンテクスト・品質を自律管理し、
人間の介入なしに最終成果物を GitHub リポジトリとして完成投稿まで行う開発オーケストレーション中間層です。

> **ステータス**: Task 1（雛形・環境構築）/ Task 2（設定管理）/ Task 3（CLI・仕様書検証）/
> Task 4（pi 連携 CrewAI Tool 群）/ Task 5（ワークスペース・共有ボード・状態管理）/
> Task 6（コンテクスト構築：新規・既存）/ Task 7（コンテナ隔離実行）/ Task 8（クルー構成）完了。
> 本ファイルは後続タスクの進行に合わせて随時更新されます。

---

## 概要

- **ワンライナー起動**: `run-spec.sh <spec.md> [existing_repo/]` で開始（未実装）。
- **コンテナ隔離**: 全エージェント・pi プロセスをコンテナ内で稼働（Task 7 実装済み）。
- **品質担保**: 独立 QA クリティック通過のみ完了へ（未実装）。
- **費用最適化**: 役割別モデル割当を外部設定化（Task 8 でクルーに注入）。
- **新規・既存両対応**: 既存コードベースの現状把握（Task 6 / Task 8 でクルー化）。

## 技術スタック

| 項目 | 仕様 |
| --- | --- |
| 言語 / 環境 | Python 3.x（>=3.11）、[uv](https://docs.astral.sh/uv/) で環境管理 |
| マネジメント層 | CrewAI（Agent / Task / Crew / Process） |
| 実行方式 | `Process.HIERARCHICAL`（オーケストレータ = manager） |
| 実行ツール | pi coding agent（CLI を subprocess で呼ぶ） |
| コンテナ | Docker（Task 7 実装済み） |

## コンテナ隔離実行（Task 7）

`run-spec.sh` はホスト側で `python -m orchestrator.container` を呼び、実処理をすべて
**Docker コンテナ内**で実行します（F-02）。CrewAI マネジメント層と pi の両方がコンテナ内で
動くため、ホストは汚染されません。

- **Dockerfile**: `node + uv + pi CLI` ベース（既定 `pi-sandbox`、`ARG BASE_IMAGE` で差し替え可）に
  CrewAI 等を `uv sync` で同梱。
- **仕様書**: 永続領域 `data/inputs/spec.md` へ退避して参照（ネストした Docker でも安全）。
- **既存リポジトリ**: 第2引数で `/existing` に **read-only** マウント（読み込みのみ）。
- **永続化（冪等性）**: ワークスペース・セッション・ログを `data/` -> `/work` に bind mount。
  `docker rm` / 再起動後も状態を復元。
- **秘密管理**: モデル割当・`GITHUB_TOKEN`・`OPENROUTER_API_KEY` 等は `docker run` の `-e` で
  **起動時のみ注入**し、イメージには埋め込まない。

```bash
./run-spec.sh <spec.md> [existing_repo/] [options]
# 出力: コンテナID・コンテナ名・データ領域・ログパス

# 補助オプション
--rebuild           # イメージを再ビルドしてから起動
--docker-image T     # 使用イメージ（既定: meta-agent-orchestrator:latest）
--data-dir DIR       # 永続データ領域（既定: <プロジェクト>/data）
--follow             # コンテナログをフォロー
--dry-run            # 実行する docker run コマンドを表示するだけ
--foreground         # フォアグラウンド（-it）で起動
```

> **Docker-in-Docker 対応**: ネストした Docker でも bind mount が機能するよう、`container.py` が
> 現在コンテナの bind mount 情報から、デーモンが解決できる実パスを自動変換します。

## CrewAI エージェント群とクルー構成（Task 8）

`agents/crew.py` はオーケストレータ・実行・QA・調査の各エージェントを定義し、
**Process.HIERARCHICAL** のクルーとして組み立てます。オーケストレータは manager として
振る舞い、実行/QA/調査の委譲先エージェントへ作業を振り分けます。

- **オーケストレータ（manager）**: 戦略・タスク分解・最終判定（コスパ系モデル）。
- **実行インターフェーサー**: pi Tool（Task 4）で実装（中〜高性能 + コスパ系モデル）。
- **QA クリティック**: 批判的視点で検証（高性能モデル）。
- **調査エージェント**: 既存コード把握（コスパ系モデル。既存案件時のみ）。

モデルはすべて Task 2 の `OrchestratorConfig`（env / CLI 引数）から注入され、
`build_pi_llm` が OpenRouter 経由（litellm）で解決します。


```python
from orchestrator.config import build_config
from orchestrator.agents import build_crew

config = build_config()
crew = build_crew(config, repo_path="existing/", include_survey=True)
print(crew.process)                 # Process.hierarchical
print(crew.manager_agent.role)      # オーケストレータ（manager）
# crew.kickoff()                     # 実行は Task 9 以降で駆動
```

> **Manager-only**: 全エージェントはコード実行を許可されず（`allow_code_execution=False`＝既定）、
> コード接触は pi Tool を介してのみ行います。`allow_code_execution` は非推奨のため明示指定せず、
> デフォルト（無効）に依拠しています。

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

　

## ワークスペース・共有ボード・状態管理（Task 5）

実装・計画ループの共通基盤。作業ディレクトリを一元管理し、共有ボード・
タスク別ログ・pi セッションの永続化を担います。

- **共有ボード `plan.md`**: タスク一覧・状態・依存・優先度を人間向けに描画。
- **状態 `state.json`**: 機械可読な状態（冪等性の要）。再起動時に読み込んで進行を復元。
- **タスク別ログ**: `logs/<task_id>.log` にタイムスタンプ付きで追記。
- **pi セッション**: `--session` / `/export` の JSONL を `sessions/` へ永続化し、
  再起動時に復元（冪等性）。

**状態遷移**: `planned → running → done / failed → qa → accepted / failed`
（`failed` は再実行 `running` / 再計画 `planned` へ。`accepted` は終端・QA 通過）。
不正な遷移は `ValueError` で検出します。

```python
from orchestrator import Workspace, TaskStatus

ws = Workspace.open("work/")
t = ws.add_task("機能Aを実装")
ws.transition(t.id, TaskStatus.RUNNING)   # planned → running
ws.log(t.id, "プロンプト送信")
ws.set_session(t.id, "pi-session-123")
ws.persist_session(t.id, "/tmp/sessions/abc.jsonl")  # JSONL 永続化

# 再起動時: Workspace.open("work/") で前回の状態・セッションを復元
```

## pi 連携 CrewAI Tool 群（Task 4）

マネジメント層（CrewAI）は本 Tool 経由でのみ pi プロセスを操作します。
**Manager-only 原則**に従い、エージェントはコードへ直接アクセスせず、常に pi を駆動して作業します。

- `run_pi_single` : `pi -p @spec.md "タスク"` のワンショット実行（新規タスク）
- `run_pi_continue` : `pi -c` / `--session <id>` によるセッション継続
- `run_pi_fork` : `pi --fork <id>` で新セッション生成（失敗・再実行時）
- `compact_context` : コンテクスト圧縮（`/compact` 相当）の指示を送信
- `export_session` : セッション（JSONL）の永続化
- `run_pi_survey` : 読み取り専用ツール（`--tools read,grep,find,ls`）で既存コードを調査（F-10）

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

## コンテクスト構築（新規・既存）（Task 6）

`context.py` は仕様書を起点に、タスク分解に必要な**初期コンテクスト**を構築します。
生成物はワークスペースの `context/` ディレクトリへ保存され、後続の計画・実装（Task 9 以降）が参照します。

- **新規**（`existing_repo` なし）: 仕様書を pi 初期プロンプトとして投入するための自己完結型コンテクスト
  （`context/initial_prompt.md`）を構築。`pi @spec.md "..."` の形で pi に渡せる。
- **既存**（`existing_repo` あり）: 調査エージェントが **pi 読み取り専用ツール**（`--tools read,grep,find,ls`）
  経由でコードベースを解析し、**現状レポート**（`context/survey_report.md`）を生成。
  オーケストレータが自然言語で参照できる。
- 仕様書のコピー（`context/spec.md` / `context/context.json` メタ情報）も保存。

```python
from orchestrator import Workspace
from orchestrator.config import build_config
from orchestrator.context import build_context, ContextResult

ws = Workspace.open("work/")
config = build_config()
result: ContextResult = build_context(
    "spec.md", ws, config, repo_path="existing/"  # 既存案件時のみ
)
print(result.describe())          # 新規/既存どちらの経路か・保存先
print(result.survey_report)       # 既存経路の現状レポート（自然言語）
```

> **Manager-only 原則**: 既存コードの調査も含め、マネジメント層は常に pi（読み取り専用 Tool）を介してのみ
> コードへアクセスします。読み取り専用のため調査中にリポジトリを書き換えることはありません。

## 起動（ワンライナー）

```bash
./run-spec.sh <spec.md> [existing_repo/] [options]
```

- `spec.md`: 仕様書（必須。存在・非空を検証）
- `existing_repo/`: 既存リポジトリ（任意。コンテナへ read-only マウントして調査に使う）
- オプション: `--orchestrator-model` / `--exec-model` / `--qa-model` / `--survey-model` /
  `--github-token` / `--log-dir` / `--rebuild` / `--docker-image` / `--data-dir` / `--follow` / `--dry-run`

仕様書未指定・不存在・空ファイルの場合はエラーメッセージを出して即終了（exit 非0）します。
起動成功時は**コンテナID・データ領域・ログパス**を出力して戻ります。

> **コンテナ内のパイプライン起動**: `run-spec.sh`（Host）→ `python -m orchestrator.container` が
> Docker コンテナを起動し、その中の `/app` で `python -m orchestrator`（コンテナ内エントリ）を実行します。

## 設定（環境変数）

全設定は**環境変数 > デフォルト値**、CLI 引数 > 環境変数の優先順位で外部から制御できます（Task 3 で CLI 引数が接続されます）。

| 環境変数 | 役割 | デフォルト |
| --- | --- | --- |
| `AGENT_ORCHESTRATOR_MODEL` | オーケストレータ（コスパ系） | `deepseek/deepseek-v4-flash-0731` |
| `AGENT_EXEC_MODEL` | 実行インターフェーサー（中〜高性能 + コスパ） | `deepseek/deepseek-v4-pro-0813` |
| `AGENT_QA_MODEL` | QA クリティック（高品質 + コスパ） | `anthropic/claude-sonnet-4.6` |
| `AGENT_SURVEY_MODEL` | 調査エージェント（コスパ系） | `deepseek/deepseek-v4-flash-0731` |
| `GITHUB_TOKEN` | GitHub 認証トークン | なし |
| `PI_SESSION_DIR` | pi セッション保存ディレクトリ | なし |
| `PI_PROVIDER` | pi のプロバイダ | `openrouter` |
| `PI_THINKING` | pi の thinking 有効/無効 | `true` |
| `PI_TRUST` | pi の trust 制御 | `defaultProjectTrust` |

> **モデル選定方針（2026 時点で OpenRouter 実価格を調査）**: コスパを最優先し、
> - オーケストレータ/調査 = `deepseek/deepseek-v4-flash-0731`（$0.08/$0.18 入力/出力 1M トークン）
> - 実行 = `deepseek/deepseek-v4-pro-0813`（$0.435/$0.87。推論・コード能力が高い）
> - QA = `anthropic/claude-sonnet-4.6`（$3/$15。オーパス $5/$25 より廉価で高品質）
>
> すべて外部設定で上書きできます（実行→`claude-sonnet-4.6`、QA→`claude-opus-4.6` 等へ差し替え可能）。
> モデルは Task 8 で `build_crew` の各エージェント LLM に注入されます。

## ディレクトリ構成

```
meta-agent-orchestrator/
├── pyproject.toml            # uv による環境定義（Task 1）
├── Dockerfile                # コンテナ隔離実行（Task 7）
├── .dockerignore             # ビルドコンテクスト除外（Task 7）
├── README.md                 # 本ファイル
├── src/orchestrator/
│   ├── __init__.py
│   ├── __main__.py           # python -m orchestrator の入口
│   ├── config.py             # 設定管理（ORCHESTRATOR_MODEL 等 / Task 2）
│   ├── cli.py                # CLI入口・引数解析・仕様書検証（Task 3）
│   ├── container.py          # コンテナ隔離実行（Task 7）
│   ├── context.py            # コンテクスト構築（新規・既存）（Task 6）
│   ├── tools/
│   │   ├── __init__.py
│   │   └── pi_tools.py       # pi 連携 CrewAI Tool 群（Task 4）
│   ├── agents/
│   │   ├── __init__.py
│   │   └── crew.py           # エージェント群・HIERARCHICAL クルー（Task 8）
│   ├── workspace.py          # 共有ボード・ログ・セッション状態管理（Task 5）
│   └── main.py               # CLI への薄い委譲エントリ
├── run-spec.sh               # ワンライナー起動（Task 3 / Task 7 コンテナ起動）
└── tests/                    # テスト（Task 1〜）
```

> 構成は PLAN.md / TASK.md に沿って後続タスクで拡張されます。

## ロードマップ

- [x] Task 1: プロジェクト雛形・環境構築
- [x] Task 2: 設定管理（Config）×外部化
- [x] Task 3: CLI エントリポイントと仕様書検証
- [x] Task 4: pi 連携 CrewAI Tool 群
- [x] Task 5: ワークスペース・共有ボード・状態管理
- [x] Task 6: コンテクスト構築（新規・既存）
- [x] Task 7: コンテナ隔離実行
- [x] Task 8: CrewAI エージェント群とクルー構成
- [ ] Task 9: 実装ループ
- [ ] Task 10: 品質保証（独立 QA クリティック）
- [ ] Task 11: GitHub アップロードと完了・納品
- [ ] Task 12: 人間インターフェース
- [ ] Task 13: 統合テスト・最終検証・README 仕上げ
