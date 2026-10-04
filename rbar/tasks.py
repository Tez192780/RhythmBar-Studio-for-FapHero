"""把耗时计算（解码 / 起音检测 / 导出）放到后台线程，避免界面卡死。"""

from __future__ import annotations

import threading
import traceback
from typing import Callable

from PySide6.QtCore import QObject, Signal


class Task(QObject):
    """在子线程里跑 fn，结果通过信号回到主线程。"""

    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, float)

    def __init__(self, fn: Callable, *args, parent=None, **kwargs):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._thread: threading.Thread | None = None

    def start(self) -> "Task":
        def runner():
            try:
                res = self._fn(*self._args, **self._kwargs)
                self.done.emit(res)
            except Exception as e:  # noqa: BLE001
                self.failed.emit(f"{e}\n\n{traceback.format_exc(limit=3)}")

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()
        return self
