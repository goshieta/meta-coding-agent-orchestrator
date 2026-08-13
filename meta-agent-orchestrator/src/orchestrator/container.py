"""コンテナ隔離実行 (PLAN F-02 / Task 7)。

オーケストレータ（CrewAI マネジメント層）と pi プロセスをすべて Docker コンテナ内で
稼働させ、ホストを非汚染に保つ。

責務:
- `Dockerfile` のビルド（存在しなければ、または `--rebuild` で）
- `docker run` のコマンド構築（マウント・環境変数注入・コンテナ内引数）
- 起動したコンテナの ID とログパスの出力
- 永続データ（`data/`）を bind mount で保持し、再起動後も状態を復元（冪等性）

秘密情報（GITHUB_TOKEN / OPENROUTER_API_KEY 等）はイメージに埋め込まず、
`docker run` 時の `-e` で**起動時のみ**注入する。

使い方:
    python -m orchestrator.container <spec.md> [existing_repo/] [options]
    # またはホスト側ラッパー:
    ./run-spec.sh <spec.md> [existing_repo/] [options]
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.cli import (
    EXIT_OK,
    EXIT_VALIDATION_ERROR,
    build_parser,
    validate_existing_repo,
    validate_spec,
)
from orchestrator.config import (
    DEFAULT_PI_TRUST,
    OrchestratorConfig,
    build_config,
)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
DEFAULT_IMAGE = "meta-agent-orchestrator:latest"

# コンテナ内のパス（Dockerfile の WORKDIR / ENV と整合）
IN_APP_DIR = "/app"            # アプリ本体（uv プロジェクト / 仮想環境）
IN_WORK_DIR = "/work"          # 永続データ（bind mount 先）
IN_SPEC_PATH = f"{IN_WORK_DIR}/inputs/spec.md"  # 仕様書（data/inputs/ に退避したもの）
IN_REPO_PATH = "/existing"      # 既存リポジトリ（read-only）
IN_LOG_DIR = f"{IN_WORK_DIR}/logs"
IN_SESSION_DIR = f"{IN_WORK_DIR}/sessions"
IN_INPUTS_DIR = f"{IN_WORK_DIR}/inputs"

# ホスト側からコンテナへ透過する環境変数のプレフィックス（認証系のみ）。
# モデル割当（AGENT_*）・token（GITHUB_*）・pi 設定（PI_*）は config から明示的に
# 注入されるため、ここでは pi 実行に必要な外部キー（OpenRouter / スクレイプ API）のみ
# 透過する。これによりホストの pi セッション変数（PI_SESSION_ID 等）が混入しない。
ENV_PASSTHROUGH_PREFIXES = (
    "OPENROUTER_",
    "FASTCRW_",
    "SEARXNG_",
    "FIRECRAWL_",
)

# コンテナへ明示的に注入する名前
ENV_GITHUB_TOKEN = "GITHUB_TOKEN"
ENV_PI_SESSION_DIR = "PI_SESSION_DIR"
ENV_PI_TRUST = "PI_TRUST"


# ---------------------------------------------------------------------------
# データモデル
# ---------------------------------------------------------------------------
@dataclass
class RunResult:
    """コンテナ起動の結果。"""

    container_id: str
    container_name: str
    image: str
    host_log_dir: Path
    data_dir: Path
    command: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------
def _docker(cmd: list[str]) -> subprocess.CompletedProcess:
    """docker を subprocess で実行する（例外は呼び出し元へ）。"""
    return subprocess.run(["docker", *cmd], capture_output=True, text=True)


def image_exists(image: str = DEFAULT_IMAGE) -> bool:
    """指定イメージがローカルに存在するか。"""
    proc = _docker(["image", "inspect", image])
    return proc.returncode == 0


def find_script_dir() -> Path:
    """run-spec.sh / リポジトリ本体のあるディレクトリを返す。"""
    # このモジュール: <root>/src/orchestrator/container.py
    return Path(__file__).resolve().parents[2]


def default_data_dir(project_root: Path | None = None) -> Path:
    """永続データディレクトリ（既定: <プロジェクトルート>/data）。"""
    root = project_root or find_script_dir()
    return root / "data"


def default_container_name() -> str:
    """ユニークなコンテナ名を生成する。"""
    return f"meta-orchestrator-{uuid.uuid4().hex[:10]}"


def _current_container_bind_sources() -> dict[str, str]:
    """実行中コンテナの bind mount の ``{destination: source}`` を取得する。

    Docker-in-Docker で `docker run` の ``-v`` に渡すマウント元パスを、デーモンが
    解決できる実パスへ変換するために使う。コンテナ内でない／取得不能な場合は空を
    返す（その場合パスはそのまま使われる）。
    """
    hostname = socket.gethostname() or os.getenv("HOSTNAME")
    if not hostname:
        return {}
    try:
        proc = subprocess.run(
            ["docker", "inspect", hostname, "--format", "{{json .Mounts}}"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return {}
        mounts = json.loads(proc.stdout)
        return {
            m["Destination"]: m["Source"]
            for m in mounts
            if m.get("Type") == "bind" and m.get("Source") and m.get("Destination")
        }
    except Exception:
        return {}


def resolve_daemon_path(path: str | Path) -> str:
    """コンテナ内パスを、Docker デーモンが解決できる実パスへ変換する。

    ネストした Docker（Docker-in-Docker）では、コンテナ内で見えるパスとデーモンが
    解決するパスは異なる場合がある（例: コンテナ内 `/workspace` <=> デーモン
    `/home/user/dev/workspace`）。現在コンテナの bind mount の Source を参照し、
    もっとも長いプレフィックス優先で置換する。
    """
    path_str = str(Path(path).resolve())
    mounts = _current_container_bind_sources()
    for dest, source in sorted(mounts.items(), key=lambda kv: -len(kv[0])):
        base = dest.rstrip("/")
        if path_str == base or path_str.startswith(base + "/"):
            return source + path_str[len(base):]
    return path_str


# ---------------------------------------------------------------------------
# ビルド
# ---------------------------------------------------------------------------
def build_image(image: str = DEFAULT_IMAGE, no_cache: bool = False, project_root: Path | None = None) -> str:
    """ローカルの Dockerfile からイメージをビルドし、タグ名を返す。"""
    root = (project_root or find_script_dir()).resolve()
    dockerfile = root / "Dockerfile"
    if not dockerfile.is_file():
        raise FileNotFoundError(f"Dockerfile がありません: {dockerfile}")
    cmd = ["build", "-t", image, "-f", str(dockerfile)]
    if no_cache:
        cmd.append("--no-cache")
    cmd.append(str(root))  # ビルドコンテクスト
    proc = subprocess.run(["docker", *cmd], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"イメージのビルドに失敗しました（exit {proc.returncode}）:\n{proc.stderr}")
    return image


# ---------------------------------------------------------------------------
# コマンド構築（テスト可能な純粋関数）
# ---------------------------------------------------------------------------
def build_env_args(
    config: OrchestratorConfig,
    host_env: dict[str, str] | None = None,
) -> list[str]:
    """コンテナへ注入する環境変数を ``["-e", "K=V", ...]`` の形で返す。

    設定由来の env と、ホストから透過すべき認証系 env（OPENROUTER_API_KEY 等）を
    起動時のみ注入する（イメージには埋め込まない）。
    """
    host_env = dict(host_env) if host_env is not None else dict(os.environ)
    env: dict[str, str] = {
        "AGENT_ORCHESTRATOR_MODEL": config.orchestrator_model,
        "AGENT_EXEC_MODEL": config.exec_model,
        "AGENT_QA_MODEL": config.qa_model,
        "AGENT_SURVEY_MODEL": config.survey_model,
        "PI_PROVIDER": config.pi_provider,
        "PI_THINKING": str(config.pi_thinking).lower(),
        "PI_TRUST": config.pi_trust or DEFAULT_PI_TRUST,
    }
    if config.github_token:
        env[ENV_GITHUB_TOKEN] = config.github_token
    env[ENV_PI_SESSION_DIR] = IN_SESSION_DIR

    # ホストの認証系 env を透過（既に設定があればホスト値を優先）
    for key, value in host_env.items():
        if key.startswith(ENV_PASSTHROUGH_PREFIXES) and value:
            env.setdefault(key, value)
    env[ENV_PI_TRUST] = env.get(ENV_PI_TRUST) or host_env.get("PI_TRUST") or DEFAULT_PI_TRUST

    args: list[str] = []
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    return args


def build_mount_args(repo_abs: Path | None, data_abs: Path) -> list[str]:
    """コンテナへマウントするボリューム引数を ``["-v", "src:dst:ro", ...]`` で返す。

    - 永続データ ``data/`` -> ``/work``: ワークスペース・セッション・ログ・
      ``inputs/spec.md``（退避した仕様書）を保持し、再起動後も復元（冪等性）。
    - 既存リポジトリ: ``/existing`` (read-only, 任意)。

    ※ マウント元は Docker-in-Docker でもデーモンが解決できる実パスへ変換する。
    ※ 仕様書はファイル単体の bind mount だとネスト Docker で空ディレクトリ化する
    ため、永続領域にある ``data/inputs/spec.md`` を参照する方式にしている。
    """
    data_src = resolve_daemon_path(data_abs)
    args = ["-v", f"{data_src}:{IN_WORK_DIR}"]
    if repo_abs:
        repo_src = resolve_daemon_path(repo_abs)
        args += ["-v", f"{repo_src}:{IN_REPO_PATH}:ro"]
    return args


def prepare_inputs(spec_abs: Path, repo_abs: Path | None, data_abs: Path) -> Path:
    """仕様書を永続データ領域（``data/inputs/spec.md``）へ退避し、パスを返す。

    永続領域はホスト・Docker デーモン双方から見えるため、仕様書を確実に
    コンテナ内で参照できる。既存リポジトリは巨大なため read-only bind mount で
    対応し、ここではコピーしない。
    """
    inputs_dir = data_abs / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    dest = inputs_dir / "spec.md"
    shutil.copyfile(spec_abs, dest)
    return dest


def build_cli_args(repo_abs: Path | None, cli_extra: list[str] | None = None) -> list[str]:
    """コンテナ内で実行する orchestrator CLI への引数を作る。

    仕様書はコンテナ内固定パス（``/work/inputs/spec.md``）、既存リポジトリは ``/existing``。
    追加オプション（``--log-dir`` 等）があれば末尾へ付与する。
    """
    args = [IN_SPEC_PATH]
    if repo_abs:
        args.append(IN_REPO_PATH)
    if cli_extra:
        args += list(cli_extra)
    return args


def compose_run_args(
    config: OrchestratorConfig,
    spec_abs: Path,
    repo_abs: Path | None,
    data_abs: Path,
    image: str = DEFAULT_IMAGE,
    container_name: str | None = None,
    cli_extra: list[str] | None = None,
    host_env: dict[str, str] | None = None,
    detach: bool = True,
) -> list[str]:
    """完全な ``docker run`` コマンドの引数リストを構築する。"""
    name = container_name or default_container_name()
    args: list[str] = ["run"]
    if detach:
        args.append("-d")
    args += ["--name", name]
    if not detach:
        args += ["--rm", "-it"]
    args += build_mount_args(repo_abs, data_abs)
    args += build_env_args(config, host_env)
    args += ["-w", IN_WORK_DIR]
    args += [image, "uv", "run", "--project", IN_APP_DIR, "python", "-m", "orchestrator"]
    args += build_cli_args(repo_abs, cli_extra)
    return args


# ---------------------------------------------------------------------------
# 起動
# ---------------------------------------------------------------------------
def launch(
    config: OrchestratorConfig,
    spec_abs: Path,
    repo_abs: Path | None,
    data_abs: Path,
    image: str = DEFAULT_IMAGE,
    container_name: str | None = None,
    cli_extra: list[str] | None = None,
    host_env: dict[str, str] | None = None,
    build_if_missing: bool = True,
) -> RunResult:
    """イメージを用意してコンテナを起動し、結果（ID・ログパス）を返す。"""
    if not image_exists(image):
        if not build_if_missing:
            raise RuntimeError(f"イメージが存在しません（--no-build）: {image}")
        build_image(image, project_root=find_script_dir())

    data_abs.mkdir(parents=True, exist_ok=True)
    (data_abs / "logs").mkdir(parents=True, exist_ok=True)
    # 仕様書を永続領域へ退避（Docker デーモン / コンテナ双方から確実に参照可能に）
    prepare_inputs(spec_abs, repo_abs, data_abs)

    name = container_name or default_container_name()
    command = compose_run_args(
        config, spec_abs, repo_abs, data_abs,
        image=image, container_name=name, cli_extra=cli_extra, host_env=host_env,
    )
    proc = subprocess.run(["docker", *command], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"コンテナの起動に失敗しました（exit {proc.returncode}）:\n{proc.stderr}"
        )
    container_id = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else name

    return RunResult(
        container_id=container_id,
        container_name=name,
        image=image,
        host_log_dir=data_abs / "logs",
        data_dir=data_abs,
        command=["docker", *command],
    )


# ---------------------------------------------------------------------------
# CLI エントリポイント（ホスト側ラッパーから呼ばれる）
# ---------------------------------------------------------------------------
def build_container_parser() -> argparse.ArgumentParser | None:
    """コンテナ起動用のパーサー（docker 固有オプションを追記）。"""
    import argparse

    parser = build_parser()
    parser.add_argument("--docker-image", dest="docker_image", default=DEFAULT_IMAGE,
                        help=f"使用する Docker イメージ（既定: {DEFAULT_IMAGE}）")
    parser.add_argument("--no-build", action="store_true",
                        help="イメージが無い場合にビルドしない（エラーで終了）")
    parser.add_argument("--rebuild", action="store_true",
                        help="イメージを強制的に再ビルドしてから起動")
    parser.add_argument("--name", dest="container_name", default=None,
                        help="コンテナ名（既定: 自動生成）")
    parser.add_argument("--data-dir", dest="data_dir", default=None,
                        help="永続データディレクトリ（既定: <リポジトリ>/data）")
    parser.add_argument("--dry-run", action="store_true",
                        help="docker run コマンドを表示するだけで起動しない")
    parser.add_argument("--follow", action="store_true",
                        help="コンテナのログをフォローする（起動後 tail -f 相当）")
    parser.add_argument("--foreground", action="store_true",
                        help="デタッチせずフォアグラウンドで起動（-it）")
    return parser


def main(argv: list[str] | None = None) -> int:
    """コンテナ隔離実行の CLI エントリ。終了コードを返す。"""
    parser = build_container_parser()
    args = parser.parse_args(argv)

    try:
        spec_path = validate_spec(args.spec)
        repo_path = validate_existing_repo(args.existing_repo)
    except ValueError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return EXIT_VALIDATION_ERROR

    config = build_config(
        orchestrator_model=args.orchestrator_model,
        exec_model=args.exec_model,
        qa_model=args.qa_model,
        survey_model=args.survey_model,
        github_token=args.github_token,
    )

    project_root = find_script_dir()
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir(project_root)
    data_dir = data_dir.resolve()
    spec_abs = spec_path.resolve()
    repo_abs = repo_path.resolve() if repo_path else None

    if args.rebuild:
        if image_exists(args.docker_image):
            _docker(["image", "rm", "-f", args.docker_image])
        build_image(args.docker_image, project_root=project_root)

    cli_extra = [f"--log-dir={IN_LOG_DIR}"]

    if args.dry_run:
        command = compose_run_args(
            config, spec_abs, repo_abs, data_dir,
            image=args.docker_image, container_name=args.container_name or "meta-orchestrator",
            cli_extra=cli_extra, detach=not args.foreground,
        )
        print("docker " + " ".join(_shell_quote(c) for c in command))
        return EXIT_OK

    result = launch(
        config, spec_abs, repo_abs, data_dir,
        image=args.docker_image, container_name=args.container_name,
        cli_extra=cli_extra, build_if_missing=not args.no_build,
    )

    print(f"[container] コンテナID  : {result.container_id}")
    print(f"[container] コンテナ名  : {result.container_name}")
    print(f"[container] イメージ   : {result.image}")
    print(f"[container] データ領域 : {result.data_dir}")
    print(f"[container] ログパス   : {result.host_log_dir}")

    if args.follow:
        proc = subprocess.run(["docker", "logs", "-f", result.container_id])
        return proc.returncode

    return EXIT_OK


def _shell_quote(token: str) -> str:
    """表示用にシェルクォートする（dry-run 用）。"""
    if token and token.isalnum() and "-_./:=, ".find(token[0]) == -1:
        return token
    return "'" + token.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    sys.exit(main())
