"""节奏条工作室 启动入口：python main.py [工程或音频文件]"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _setup_crash_log() -> None:
    """pythonw 启动时没有 stderr：把异常/崩溃写到 %TEMP%\\rhythmbar.log。

    Start.bat 一直是这么注释的，但之前没有实现；用户双击启动后如果缺 DLL、
    缺依赖或 Qt 崩溃，完全没有任何线索。
    """
    exe = os.path.basename(sys.executable).lower()
    frozen = bool(getattr(sys, "frozen", False))
    if sys.stderr is not None and not (exe.startswith("pythonw") or frozen):
        return                              # 控制台模式（Start-debug）保持原样
    try:
        import faulthandler
        import tempfile
        import time
        import traceback

        path = os.path.join(tempfile.gettempdir(), "rhythmbar.log")
        log = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = log
        sys.stderr = log
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 =====\n")
        faulthandler.enable(file=log, all_threads=True)

        def hook(exc_type, exc, tb) -> None:
            log.write("".join(traceback.format_exception(exc_type, exc, tb)) + "\n")
            try:
                log.flush()
            except Exception:
                pass

        sys.excepthook = hook
    except Exception:
        pass


_setup_crash_log()

from rbar.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
