# 开发自检：静态检查未使用的导入 / 语法（不依赖第三方 linter）

from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    if problems:
        print("\n".join(problems))
        print(f"\n共 {len(problems)} 处")
        return 1
    print(f"检查了 {len(files)} 个文件，没有语法错误和明显的未使用导入")
    return 0


if __name__ == "__main__":
    sys.exit(main())
