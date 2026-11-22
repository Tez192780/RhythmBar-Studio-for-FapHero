"""对比本地源码目录与上游 v1.1.1 的浅克隆，判定是否真的等价。"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys

LOCAL = r"D:\DSH WorkSPACE\Beatbar Maker\RhythmBar-Studio-for-FapHero-1.1.1"
CLONE = os.path.join(os.environ["TEMP"], "rbar_upstream_v111")


def norm(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def sha(data: bytes) -> str:
    return hashlib.sha256(norm(data)).hexdigest()[:12]


files = subprocess.run(
    ["git", "-C", CLONE, "ls-files"], capture_output=True, text=True, check=True
).stdout.split("\n")
files = [f for f in files if f.strip()]

same = diff = missing = 0
problems = []
for rel in files:
    up = subprocess.run(
        ["git", "-C", CLONE, "show", f"HEAD:{rel}"], capture_output=True, check=True
    ).stdout
    lp = os.path.join(LOCAL, rel.replace("/", os.sep))
    if not os.path.isfile(lp):
        missing += 1
        problems.append(f"缺失  {rel}")
        continue
    with open(lp, "rb") as f:
        lo = f.read()
    if sha(lo) == sha(up):
        same += 1
    else:
        diff += 1
        problems.append(f"内容不同  {rel}  (local {len(lo)}B, upstream {len(up)}B)")

print(f"上游 v1.1.1 跟踪文件: {len(files)}")
print(f"  完全一致(忽略换行符): {same}")
print(f"  内容不同: {diff}")
print(f"  本地缺失: {missing}")
if problems:
    print("\n差异明细:")
    for p in problems:
        print("  " + p)

extra = []
for root, dirs, names in os.walk(LOCAL):
    dirs[:] = [d for d in dirs if d not in (".git", ".venv", "skins", "__pycache__")]
    for n in names:
        rel = os.path.relpath(os.path.join(root, n), LOCAL).replace(os.sep, "/")
        if rel not in files:
            extra.append(rel)
print(f"\n本地额外文件(上游没有): {len(extra)}")
for e in sorted(extra):
    print("  + " + e)
