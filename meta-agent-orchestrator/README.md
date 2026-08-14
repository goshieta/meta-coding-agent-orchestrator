# Meta-Agent Orchestrator

仕様書（`md`）を**ワンライナーで渡すだけで**、コンテナ内で CrewAI 製マネジメント層が
[pi](https://github.com/earendil-labs/pi-coding-agent) のセッション・コンテクスト・品質を自律管理し、
人間の介入なしに最終成果物を GitHub リポジトリとして完成投稿まで行う開発オーケストレーション中間層です。

> **ステータス**: Task 1〜12 の実装、および Task 13（統合テスト・最終検証）完了。
> `uv run pytest tests/` で全テストが通過する状態を維持します。

---

## 概要

- **ワンライナー起動**: `run-spec.sh <spec.md> [existing_repo/]` で開始。コンテナ内では context → plan → execute → 独立 QA → 人間承認付き納品までを再開可能な統合パイプラインとして実行します。
- **コンテナ隔離**: 全エージェント・pi プロセスをコンテナ内で稼働（Task 7 実装済み）。
- **実装ループ**: 共有ボードを依存・優先度順に処理し、pi実行・再試行・compact・commitを行う（Task 9 実装済み）。
- **品質担保**: 実装セッションから独立したQA piが検証し、PASSのみ `accepted` へ進める。不合格はTask9へ再送（Task 10 実装済み）。
- **最終納品**: 全タスクのQA通過と最終承認後、GitHubリポジトリ作成・pushを一度だけ実行し、状態を永続化（Task 11 実装済み）。
- **人間ゲート**: 仕様質問への回答と強制停止・安全な再開を状態付きで管理（Task 12 実装済み）。
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

## 実装ループ（Task 9 / F-05）

`executor.py` の `run_loop()` は、Task 5 の共有ボードを読み取り、依存関係と優先度を考慮して
タスクを順番に処理します。コードの読み書き・検証は pi に委譲し、executor は指示・レポート・
状態・ログ・git commit を管理します。

```python
from orchestrator import LoopOptions, Workspace, run_loop
from orchestrator.config import build_config

workspace = Workspace.open("work/")
config = build_config()
result = run_loop(
    workspace,
    config,
    LoopOptions(
        repo_dir="work/",
        spec_file="work/context/spec.md",
        max_attempts=3,
    ),
)
print(result.summary())
```

主な動作:

- 新規タスクは `run_pi_single`、保存済みセッションは `run_pi_continue` で実行。
- pi が失敗した場合は既存セッションを `run_pi_fork` して再試行（セッションが無い場合は単発再試行）。
- `LoopOptions.steering` により、実行中の follow-up 指示を `run_pi_continue` で注入。
- レポートが `compact_threshold` を超えた場合、`compact_context`（`/compact` 相当）を実行。
- 成功タスクごとに `git add -A` / `git commit` を実行し、コミット失敗時はタスクを `failed` に保持。
- 中断時に `running` だったタスクは、保存済みセッションから再開。
- `LoopResult.summary()` が成功/失敗、試行回数、セッション、コミット数を自然言語で返す。

`run_loop()` は pi 実行関数と git runner を注入できるため、piを起動しない単体テストや
障害時の再試行テストも可能です。

## 品質保証ゲート（Task 10 / F-06）

`qa.py` の `run_qa()` は、`done` タスクを `qa` へ遷移させ、実装エージェントのセッションを継続しない
独立した `run_pi_single` 検証を実行します。QA用モデルは `OrchestratorConfig.qa_model` から解決されます。

QAプロンプトは以下の基準を必ず要求します。

- 仕様書の機能・入出力・エラー処理の充足
- テストの実行結果と不足テスト
- 可読性・責務分離・保守性
- セキュリティ・秘密情報・回帰リスク

piレポートは `判定: PASS` / `判定: FAIL` と構造化された問題リストを返します。判定が欠落した場合は安全側に
`FAIL` とし、問題を `QAIssue` として保持します。PASSの場合だけ `qa → accepted` へ遷移します。
FAILの場合は、問題をsteering指示へ変換してTask9の `run_loop()` に対象タスクだけを再送し、最大ラウンド数まで
修正後の独立QAを繰り返します。検証結果・再送・判定はタスク別ログとレポートに保存されます。

```python
from orchestrator import QAOptions, Workspace, run_qa
from orchestrator.config import build_config

workspace = Workspace.open("work/")
qa_report = run_qa(
    workspace,
    build_config(),
    QAOptions(repo_dir="work/", spec_file="work/context/spec.md", max_rounds=2),
)
print(qa_report.summary())
```

テストでは `verify` とTask9のpi/git runnersを注入できるため、外部pi/APIを起動せずにPASS、FAIL、再実装、
再検証、QAプロセス障害を検証できます。

## GitHub 最終納品（Task 11 / F-07・F-11）

`deliver.py` の `deliver()` は、共有ボード上の**全タスクが `accepted`** であることを確認し、
オーケストレータの最終判定（`final_decision`）と人間の最終承認（`approval`）を経て、GitHub APIで
リポジトリを作成し、成果物の現在のGitリポジトリを一度だけ `push` します。

```python
from orchestrator import DeliveryOptions, Workspace, deliver
from orchestrator.config import build_config

workspace = Workspace.open("work/")
result = deliver(
    workspace,
    build_config(),  # GITHUB_TOKEN は環境変数から解決し、状態やログには保存しない
    DeliveryOptions(owner="example", repo_name="my-project"),
    approval=lambda: True,  # Task 12のhuman_gateへ接続する箇所
)
print(result.summary())
```

## 人間インターフェース（Task 12 / F-08）

`human_gate.py` の `HumanGate` は、人間へ仕様の不明点を提出し、回答または強制停止を
受け付ける最小限の接点です。質問・回答履歴、待機状態、停止理由は `state.json` の
`human_gate` に保存され、`plan.md` にも状態が表示されます。

```python
from orchestrator import HumanGate, Workspace

workspace = Workspace.open("work/")
gate = HumanGate(workspace)

# 質問だけ保存して、外部UIや後続処理から回答する
question = gate.submit_question(
    "認証方式をOAuthにしてよいですか？",
    context="仕様書に認証方式の記載がありません",
)
answer = gate.wait_for_answer(question.question_id)

# 実行中に停止する場合。状態保存後に戻るため、セッションやタスクは破棄されない
stop = gate.handle_command("/stop")
# 再起動後: HumanGate(Workspace.open("work/")).resume()
```

### 質問・回答

- `submit_question()` は質問を `awaiting_answer` として永続化し、重複した質問を防ぎます。
- `answer_question()` または `wait_for_answer()` で回答を保存し、処理を続行可能にします。
- `ask()` は提出と回答待ちをまとめた対話APIです。stdinだけでなく `input_fn` を注入できるため、
  CLI、Web UI、テストから利用できます。
- Task11の最終承認は `HumanGate.request_approval` をcallbackとして渡せます。
  `yes`、`はい`、`承認`、`approve` のみ承認し、それ以外は納品を拒否します。

### 強制停止・再開

- `stop`、`force-stop`、`halt`、`abort`、`/stop`、`停止`、`強制停止` などを受け付けます。
- EOF / Ctrl-Cも安全停止として扱います。
- 停止前に `state.json` と `plan.md` を保存し、状態を `stopped` にします。
- 保存済みのタスク状態、piセッションID、ログ、質問履歴は削除しません。
- `resume()` または `resume(workspace)` で停止状態を解除します。未回答の質問があれば
  `awaiting_answer` へ戻し、回答後に続行できます。
- 停止・再開は冪等で、プロセスを再起動しても `Workspace.open()` が状態を復元します。

テストでは入力関数を注入し、質問→回答→続行、停止→状態保存→再起動→再開、納品承認を
実際のstdinやGitHubへ接続せず検証しています。

### 冪等性・障害復旧

- `state.json` の `delivery` に `status`、リポジトリURL、作成・push時刻、エラーを保存します。
- `delivered` 状態ではGitHub APIもpushも再実行せず、`idempotent=True` を返します。
- リポジトリ作成後にpushが失敗した場合はURLを保持し、次回はリポジトリ作成を繰り返さずpushだけを再試行します。
- 承認未取得時は `awaiting_approval` で停止し、認証トークン未設定やQA未通過は `blocked` / `failed` として明示します。
- POSIX環境ではワークスペースのファイルロックで同時実行を直列化します。
- GitHubトークンはGit remote URLへ埋め込まず、一時的な `http.extraheader` としてGit subprocessへ渡します。
  `state.json`、`plan.md`、タスクログにはトークンを保存しません。
- APIの401/403、接続エラー、git push失敗は秘密情報を漏らさない一般化されたエラーとして返し、状態を保持します。

テストではGitHub APIクライアントとGit実行器を注入できるため、実ネットワークや実pushなしで、
初回納品、二重実行防止、push失敗後の再試行、承認待ち、認証エラーを検証できます。

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

## 統合パイプライン（Task 13 / F-01〜F-08）

コンテナ内の `python -m orchestrator` は、次の段階を一度の起動で接続します。

1. `context.py`: 仕様書を保存し、新規なら初期コンテクストを生成。既存案件なら pi の読み取り専用調査を実行。
2. `planner.py`: 共有ボードに冪等な初期タスクを作成（再起動時は既存計画を再利用）。
3. `executor.py`: pi で実装し、失敗時の fork、compact、タスク単位 commit を実行。
4. `qa.py`: 独立 pi セッションで検証し、FAIL は実装ループへ戻す。
5. `deliver.py`: 全タスクが `accepted` で人間承認済みの場合だけ GitHub を一度だけ作成・push。

ホスト側の CLI テストや `--dry-run` は Docker / pi / OpenRouter を起動しません。実行コンテナには
`ORCHESTRATOR_IN_CONTAINER=1` が注入され、この統合経路が有効になります。QA 不合格、承認待ち、
認証エラー、強制停止は `state.json` と `plan.md` に保存され、例外で状態を失わず再開できます。

```bash
# 外部 API を使わない統合テスト
uv run pytest tests/

# 実処理（pi と OpenRouter が必要。GITHUB_TOKEN が無い場合は納品待ち/失敗状態で停止）
./run-spec.sh spec.md --data-dir ./data --follow
```

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

- **共有ボード `plan.md`**: タスク一覧・状態・依存・優先度を人間向けに描画。最終納品状態も表示。
- **状態 `state.json`**: 機械可読な状態（冪等性の要）。再起動時に読み込んで進行を復元。GitHub納品の作成済みURL・push状態も保持。
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
│   ├── planner.py             # 仕様書/調査レポートから冪等な共有計画を作成（F-04 / Task 13）
│   ├── pipeline.py            # context → plan → execute → QA → delivery の統合接続
│   ├── __main__.py           # python -m orchestrator の入口
│   ├── config.py             # 設定管理（ORCHESTRATOR_MODEL 等 / Task 2）
│   ├── cli.py                # CLI入口・引数解析・仕様書検証（Task 3）
│   ├── container.py          # コンテナ隔離実行（Task 7）
│   ├── context.py            # コンテクスト構築（新規・既存）（Task 6）
│   ├── executor.py           # 依存順pi実装・再試行・compact・commit（Task 9）
│   ├── qa.py                 # 独立QA・問題抽出・Task9再送・合否ゲート（Task 10）
│   ├── deliver.py            # GitHub作成・一回push・納品状態・復旧（Task 11）
│   ├── human_gate.py          # 質問・回答・強制停止・再開（Task 12）
│   ├── git.py                # タスク完了ごとのgit commitヘルパー（Task 9）
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
- [x] Task 9: 実装ループ
- [x] Task 10: 品質保証（独立 QA クリティック）
- [x] Task 11: GitHubアップロードと完了・納品
- [x] Task 12: 人間インターフェース
- [x] Task 13: 統合テスト・最終検証・README 仕上げ
