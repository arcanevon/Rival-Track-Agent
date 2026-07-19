"""奶茶赛道真实流程的兼容入口。"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.run_real_pipeline import cli


if __name__ == "__main__":
    raise SystemExit(cli(["--preset", "milktea"]))
