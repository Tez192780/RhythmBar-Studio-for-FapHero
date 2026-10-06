"""检查 GitHub Releases 有没有新版本（只用标准库，不引入新依赖）。

两条路：
1. GitHub API（能拿到附件列表，但**未登录时每 IP 每小时只有 60 次**，会 403）
2. releases.atom 订阅源（不走 API 配额，只有标签/时间和说明）
所以先试 API，被限流或失败就自动回退到 atom。
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

REPO = "Tez192780/RhythmBar-Studio-for-FapHero"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ATOM = f"https://github.com/{REPO}/releases.atom"
PAGE = f"https://github.com/{REPO}/releases"
UA = "RhythmBar-Studio-Updater"


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


def _get(url: str, timeout: float, accept: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def fetch_via_api(timeout: float) -> dict:
    raw = _get(API, timeout, "application/vnd.github+json")
    data = json.loads(raw.decode("utf-8", "replace"))
    name = str(data.get("name") or data.get("tag_name") or "")
    assets = [{
        "name": str(a.get("name") or ""),
        "size": int(a.get("size") or 0),
        "url": str(a.get("browser_download_url") or ""),
    } for a in (data.get("assets") or [])]
    return {
        "tag": str(data.get("tag_name") or ""),
        "name": name,
        "body": str(data.get("body") or ""),
        "url": str(data.get("html_url") or PAGE),
        "published": str(data.get("published_at") or ""),
        "prerelease": bool(data.get("prerelease")),
        "assets": assets,
        "source": "api",
    }


def _tag_from_atom(entry, ns, title: str) -> str:
    """atom 里 <title> 是 Release 名字（可能不含版本号），标签在 link/id 里。

    例：link href=".../releases/tag/v0.991"、id="tag:github.com,2008:Repository/1/v0.991"
    """
    for link in entry.findall("a:link", ns):
        href = link.get("href") or ""
        if "/releases/tag/" in href:
            return href.rsplit("/releases/tag/", 1)[1].strip() or title
    eid = entry.findtext("a:id", "", ns)
    if eid:
        tail = eid.rsplit("/", 1)[-1].strip()
        if tail and any(ch.isdigit() for ch in tail):
            return tail
    return title


def fetch_via_atom(timeout: float) -> dict:
    """releases.atom：不吃 API 配额，限流时用它兜底（拿不到附件列表）。"""
    raw = _get(ATOM, timeout, "application/atom+xml").decode("utf-8", "replace")
    root = ET.fromstring(raw)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        return {"none": True}
    title = _strip_html(entry.findtext("a:title", "", ns))
    tag = _tag_from_atom(entry, ns, title)
    url = PAGE
    for link in entry.findall("a:link", ns):
        if link.get("rel") == "alternate" and link.get("href"):
            url = link.get("href") or PAGE
            break
    return {
        "tag": tag,
        "name": title or tag,
        "body": _strip_html(entry.findtext("a:content", "", ns)),
        "url": url,
        "published": entry.findtext("a:updated", "", ns),
        "prerelease": False,
        "assets": [],
        "source": "atom",
    }


def fetch_latest(timeout: float = 8.0) -> dict:
    """取最新 release；没有 release 返回 {'none': True}；两条路都失败才抛错。"""
    err: Exception | None = None
    try:
        return fetch_via_api(timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"none": True}            # 还没发过 Release
        err = e
    except Exception as e:  # noqa: BLE001
        err = e
    try:
        return fetch_via_atom(timeout)       # 回退：atom 不吃 API 配额
    except Exception as e2:  # noqa: BLE001
        if getattr(err, "code", None) in (403, 429):
            raise RuntimeError(
                "GitHub 接口调用次数用完了（未登录每小时 60 次），"
                "订阅源也没取到。稍后再试，或直接打开 Releases 页面看。") from e2
        raise RuntimeError(f"连不上 GitHub：{e2}") from e2
