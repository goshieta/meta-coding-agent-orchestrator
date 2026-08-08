"""CLI エントリの薄い委譲。

`run-spec.sh` / `python -m orchestrator` から呼ばれる実体は :mod:`orchestrator.cli`
に集約する。
"""

from __future__ import annotations

import sys

from orchestrator.cli import main

if __name__ == "__main__":
    sys.exit(main())
