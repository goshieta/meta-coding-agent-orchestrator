# syntax=docker/dockerfile:1
# Meta-Agent Orchestrator 実行コンテナ (PLAN F-02)
#
# 全エージェント（CrewAI マネジメント層）と pi プロセスをコンテナ内で稼働させ、
# ホストを非汚染に保つための隔離実行環境。
#
# ベースイメージ: node:24-bookworm-slim に uv + pi CLI をインストール。
# (pi.dev/docs/latest/containerization の Plain Docker パターンに準拠)
#
# セキュリティ: トークン等の秘密情報はイメージに埋め込まず、`docker run` 時の
# `-e` で起動時のみ注入する（F-02）。

FROM node:24-bookworm-slim AS base

# --- システム依存 (bash, git, ripgrep, curl, python3, pip) ---
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    ripgrep \
    python3 \
    python3-pip \
    python3-venv \
  && rm -rf /var/lib/apt/lists/*

# --- uv のインストール (Astral 公式インストーラ) ---
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# --- pi CLI のインストール ---
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent

# --- uv 設定 (再現性・速度) ---
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# ============================================================
# 本番イメージ
# ============================================================
FROM base

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