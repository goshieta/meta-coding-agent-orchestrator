"""`python -m orchestrator` のエントリポイント。"""

from __future__ import annotations

import sys

from orchestrator.main import main

if __name__ == "__main__":
    sys.exit(main())
