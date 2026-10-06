"""检查 GitHub Releases 是否有新版本（只用标准库 urllib，不引入新依赖）。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

REPO = "Tez192780/RhythmBar-Studio-for-FapHero"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases"


def parse_version(text: str) -> tuple:
    """把 'v0.2.1' / '0.2.1-beta' 变成可比较的元组。"""
    out: list = []
    for part in str(text or "").lstrip("vV").replace("-", ".").split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:4])


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def fetch_latest(timeout: float = 8.0) -> dict:
    """取最新 release；没有 release / 网络不通都返回 {}。"""
    req = urllib.request.Request(
        API,
        headers={
            "User-Agent": "RhythmBar-Studio-Updater",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:          # 还没有发布过 release
            return {"none": True}
        raise RuntimeError(f"GitHub 返回 {e.code}") from e
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"连不上 GitHub：{e}") from e
    name = str(data.get("name") or data.get("tag_name") or "")
    body = str(data.get("body") or "")
    assets = []
    for a in data.get("assets") or []:
        assets.append({
            "name": str(a.get("name") or ""),
            "size": int(a.get("size") or 0),
            "url": str(a.get("browser_download_url") or ""),
        })
    return {
        "tag": str(data.get("tag_name") or ""),
        "name": name,
        "body": body,
        "url": str(data.get("html_url") or PAGE),
        "published": str(data.get("published_at") or ""),
        "prerelease": bool(data.get("prerelease")),
        "assets": assets,
    }
