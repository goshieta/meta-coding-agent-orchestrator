# syntax=docker/dockerfile:1
# Meta-Agent Orchestrator 実行コンテナ (PLAN F-02)
#
# 全エージェント（CrewAI マネジメント層）と pi プロセスをコンテナ内で稼働させ、
# ホストを非汚染に保つための隔離実行環境。
#
# ベースイメージは「node + uv + pi CLI」を同梱したものを使う。
# 既定は手元 / CI で使用する pi-sandbox だが、`ARG BASE_IMAGE` で差し替え可能
# （例: node:24-bookworm-slim に `npm i -g @earendil-works/pi-coding-agent` と
#       `uv` を仕込んだ独自ベース）。
#
# セキュリティ: トークン等の秘密情報はイメージに埋め込まず、`docker run` 時の
# `-e` で起動時のみ注入する（F-02）。
ARG BASE_IMAGE=pi-sandbox
FROM ${BASE_IMAGE}

# uv（ベースで ~/.local/bin/uv にインストール済みの場合）を PATH へ
ENV PATH="/root/.local/bin:${PATH}"
# 再現性・速度のための uv 設定
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app

# 依存定義とメタデータ（hatchling が README.md を要求）を先にコピーし、
# レイヤキャッシュを効かせる
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# CrewAI 等の実行環境をインストール（dev 依存の pytest 等は不要 -> --no-dev）
RUN uv sync --frozen --no-dev

# 実行時の既定設定
ENV PI_SESSION_DIR=/work/sessions
# ※ モデル割当・GITHUB_TOKEN 等は run-spec.sh / docker run -e で注入（ここには書かない）

# 実行時は永続化された /work（ホストの data/ を bind mount）を作業ディレクトリとする
WORKDIR /work
# コンテナ内エントリ: アプリの仮想環境（/app/.venv）で orchestrator CLI を起動
CMD ["uv", "run", "--project", "/app", "python", "-m", "orchestrator"]
