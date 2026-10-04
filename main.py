"""节奏条工作室 启动入口：python main.py [工程或音频文件]"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rbar.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
