"""开发自检：语法、未使用的导入、以及 Windows 批处理的行尾。

Windows 的 .bat 必须是 CRLF 行尾 —— 只有 LF 时 cmd.exe 会把 `echo` 解析成 `ho`
这种诡异错误（踩过一次就会记住）。修复：python tools\\lint_check.py --fix
"""

from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_bat_eol(fix: bool = False) -> list[str]:
    """检查（可选修复）.bat / .cmd 是否 CRLF 行尾。"""
    out: list[str] = []
    for dirpath, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("build", "__pycache__", ".git", "dist")]
        for n in names:
            if not n.lower().endswith((".bat", ".cmd")):
                continue
            path = os.path.join(dirpath, n)
            raw = open(path, "rb").read()
            if raw and raw.count(b"\n") == raw.count(b"\r\n"):
                continue
            rel = os.path.relpath(path, ROOT)
            if fix:
                open(path, "wb").write(raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
                out.append(f"已修复为 CRLF: {rel}")
            else:
                out.append(f"批处理行尾必须是 CRLF（现在是 LF，cmd 会解析出错）: {rel}")
    return out


def check_file(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src, path)
    except SyntaxError as e:
        return [f"语法错误 {path}:{e.lineno}: {e.msg}"]

    imported: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                name = (a.asname or a.name).split(".")[0]
                imported[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    continue
                imported[a.asname or a.name] = node.lineno

    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            n = node
            while isinstance(n, ast.Attribute):
                n = n.value
            if isinstance(n, ast.Name):
                used.add(n.id)

    # 字符串里出现（例如类型注解、__all__）也算用到
    out = []
    skip = {"annotations"}
    for name, line in imported.items():
        if name in skip:
            continue
        if name in used or name in src.split("import", 1)[0]:
            continue
        if src.count(name) > 1:
            continue
        out.append(f"未使用的导入 {path}:{line}: {name}")
    return out


def main() -> int:
    fix = "--fix" in sys.argv
    problems: list[str] = []
    files = []
    for base in ("rbar", "tools", "tests"):
        for dirpath, _dirs, names in os.walk(os.path.join(ROOT, base)):
            for n in names:
                if n.endswith(".py"):
                    files.append(os.path.join(dirpath, n))
    files.append(os.path.join(ROOT, "main.py"))
    for f in files:
        problems += check_file(f)
    eol = check_bat_eol(fix)
    if fix:
        for line in eol:
            print(line)
        eol = check_bat_eol(False)
    problems += eol
    if problems:
        print("\n".join(problems))
        print(f"\n共 {len(problems)} 处")
        return 1
    print(f"检查了 {len(files)} 个 Python 文件 + 所有 .bat："
          f"没有语法错误、明显的未使用导入，行尾也正确")
    return 0


if __name__ == "__main__":
    sys.exit(main())
