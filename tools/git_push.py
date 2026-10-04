r"""一键：把当前改动提交并推送到 GitHub。

用法（在项目根目录，或任意位置都行，脚本会自己找仓库根）：

    python tools\git_push.py                     # 自动生成提交信息
    python tools\git_push.py "修复播放头拖动"     # 自定义提交信息
    python tools\git_push.py --pull              # 先拉远程改动(rebase)再推
    python tools\git_push.py --status            # 只看当前状态，不动仓库

也可以直接双击根目录的「上传更新.bat」。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 控制台/重定向都用 UTF-8，免得中文变成乱码
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def git(*args: str, check: bool = False) -> tuple[int, str]:
    """跑一条 git 命令，返回 (退出码, 输出)。"""
    env = dict(os.environ)
    env.setdefault("GIT_PAGER", "cat")
    env["LC_ALL"] = "C.UTF-8"
    p = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, env=env,
        creationflags=CREATE_NO_WINDOW,
    )
    out = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：\n{out}")
    return p.returncode, out


def say(msg: str = "") -> None:
    print(msg, flush=True)


def rule(title: str = "") -> None:
    say("=" * 56)
    if title:
        say(f"  {title}")
        say("=" * 56)


def summarize_staged() -> tuple[int, str]:
    """根据暂存区生成一句人话提交信息。"""
    code, out = git("diff", "--cached", "--name-status")
    if code != 0 or not out:
        return 0, ""
    added = mod = deleted = 0
    names: list[str] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        st, path = parts[0][:1], parts[-1]
        if st == "A":
            added += 1
        elif st == "D":
            deleted += 1
        else:
            mod += 1
        names.append(os.path.basename(path))
    bits = []
    if mod:
        bits.append(f"修改 {mod}")
    if added:
        bits.append(f"新增 {added}")
    if deleted:
        bits.append(f"删除 {deleted}")
    head = "、".join(bits) or "更新"
    sample = "、".join(dict.fromkeys(names[:3]))
    if len(names) > 3:
        sample += " 等"
    return len(names), f"{head}：{sample}"


def main(argv: list[str]) -> int:
    rules = [a for a in argv[1:] if a.startswith("--")]
    msgs = [a for a in argv[1:] if not a.startswith("--")]
    message = msgs[0] if msgs else ""

    rule("提交并上传到 GitHub")
    code, top = git("rev-parse", "--show-toplevel")
    if code != 0:
        say("× 这个目录不是 git 仓库。")
        return 1
    say(f"仓库：{top}")

    code, remote = git("remote", "-v")
    if code != 0 or "origin" not in remote:
        say("× 没有配置 origin 远程仓库。")
        say("  修复：git remote add origin https://github.com/Tez192780/RhythmBar-Studio-for-FapHero.git")
        return 1
    say(f"分支：{git('rev-parse', '--abbrev-ref', 'HEAD')[1]}")

    code, st = git("status", "--short")
    changed = len([line for line in st.splitlines() if line.strip()])
    if "--status" in rules:
        say("")
        say(st or "（工作区干净，没有改动）")
        return 0

    if changed == 0:
        say("· 工作区没有改动，看看有没有还没推上去的提交…")
    else:
        say(f"· 检测到 {changed} 处改动")

    if changed:
        git("add", "-A", check=True)
        n, auto = summarize_staged()
        if n == 0:
            say("· 暂存区为空（可能都被 .gitignore 忽略了）")
        else:
            msg = message or auto or f"更新 {time.strftime('%Y-%m-%d %H:%M')}"
            code, out = git("commit", "-m", msg)
            if code != 0:
                say("× 提交失败：")
                say(out)
                return 1
            say(f"· 已提交（{n} 个文件）：{msg}")

    if "--pull" in rules:
        say("· 先拉远程改动（rebase）…")
        code, out = git("pull", "--rebase", "--autostash", "origin",
                        git("rev-parse", "--abbrev-ref", "HEAD")[1])
        say(out or "（已是最新）")
        if code != 0:
            say("× 拉取失败，请手动处理后重试（可能是冲突）。")
            return 1

    code, ahead = git("rev-list", "--count", "@{u}..HEAD")
    if code == 0 and ahead.strip() == "0":
        say("· 本地没有新提交，无需推送。")
        return 0

    say("· 推送到 origin …（如果弹出 GitHub 登录窗口，登录一次以后就不用再登）")
    code, out = git("push", "origin", "HEAD")
    say(out or "（无输出）")
    if code != 0:
        say("")
        say("× 推送失败。常见原因：")
        say("  1. 网络连不上 github（可开代理后重试）")
        say("  2. 没登录 / 凭据过期：随便跑一次 git push 会弹登录窗口")
        say("  3. 远程有你没拉下来的提交：python tools\\git_push.py --pull \"信息\"")
        return 1

    say("")
    say("√ 已上传完成。")
    code, out = git("log", "--oneline", "-3")
    if out:
        say("最近提交：")
        for line in out.splitlines():
            say("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
