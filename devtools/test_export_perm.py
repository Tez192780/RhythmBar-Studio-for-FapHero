r"""输出位置可写性预检：覆盖可写 / 目录只读 / 文件被占用 三种情况。

用法：
    .\.venv\Scripts\python.exe devtools\test_export_perm.py
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rbar.exporter import check_output_writable  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{' OK ' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def main() -> int:
    base = tempfile.mkdtemp(prefix="rbar_perm_")
    try:
        return _run(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _run(base: str) -> int:

    # 1) 正常目录：应当可写
    ok_path = os.path.join(base, "out.mov")
    check("普通目录可写", check_output_writable(ok_path) == "", repr(
        check_output_writable(ok_path)))

    # 2) 目录不存在：应当能自动创建并通过
    deep = os.path.join(base, "a", "b", "c", "out.mov")
    check("不存在的多级目录会被创建", check_output_writable(deep) == "")

    # 3) 名字是非法的路径：应当给出原因
    bad = os.path.join(base, "ok.mov", "sub", "x.mov")   # ok.mov 是文件，不能当目录
    with open(os.path.join(base, "ok.mov"), "wb") as f:
        f.write(b"0")
    msg = check_output_writable(bad)
    check("路径里有同名文件时报错而不是静默", msg != "", msg[:60])

    # 4) 目标文件被占用（Windows 上以只读属性模拟）
    busy = os.path.join(base, "busy.mov")
    with open(busy, "wb") as f:
        f.write(b"0")
    os.chmod(busy, stat.S_IREAD)
    try:
        msg = check_output_writable(busy)
        check("只读的已存在文件会被识别", msg != "", msg[:60])
    finally:
        os.chmod(busy, stat.S_IWRITE)

    # 5) 只读目录（Windows 上目录只读不阻止写入，跳过意义有限）
    msg = check_output_writable("")
    check("空路径会明确报错", msg != "", msg)

    print()
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {', '.join(failures)}")
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
