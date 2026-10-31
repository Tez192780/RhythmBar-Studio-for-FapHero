"""检查 GitHub Releases 有没有新版本（只用标准库，不引入新依赖）。

自动检查优先走 releases.atom：
  * 不吃 GitHub API 配额；
  * 能拿到版本、说明和 Releases 页面地址；
只有在发现新版本时，才额外请求一次 API 补附件列表（方便直接下载）。

API 的未登录配额是每 IP 每小时 60 次，所以绝不能每次启动都先用 API。
atom 失败时才回退到 API；如果 atom 的 ETag 命中 304，直接视为“没有变化”。
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

REPO = "Tez192780/RhythmBar-Studio-for-FapHero"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
API_TAG = f"https://api.github.com/repos/{REPO}/releases/tags/{{tag}}"
ATOM = f"https://github.com/{REPO}/releases.atom"
PAGE = f"https://github.com/{REPO}/releases"
UA = "RhythmBar-Studio-Updater"

_PRERELEASE_RE = re.compile(r"(?i)(?:alpha|beta|rc|preview|pre|dev)\d*$")


def parse_version(text: str) -> tuple:
    """把 'v0.2.1' / '1.0.0-rc1' 变成可比较的元组。

    返回 (major, minor, patch, is_final, prerelease_rank, prerelease_num)：
      1.0.0-rc1 < 1.0.0
      0.999   < 1.0.0
    """
    s = str(text or "").strip().lstrip("vV")
    s = s.split("+", 1)[0]
    core, sep, pre = s.partition("-")
    nums: list[int] = []
    for part in re.split(r"[._]", core):
        m = re.match(r"\d+", part)
        nums.append(int(m.group()) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    major, minor, patch = nums[:3]
    if not sep:
        return (major, minor, patch, 1, 0, 0)
    m = re.match(r"(?i)(alpha|beta|preview|pre|rc|dev)(\d*)", pre)
    if not m:
        return (major, minor, patch, 0, 0, 0)
    ranks = {"dev": 0, "alpha": 1, "beta": 2, "pre": 3, "preview": 4, "rc": 5}
    return (major, minor, patch, 0, ranks.get(m.group(1).lower(), 0),
            int(m.group(2) or 0))


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def _looks_prerelease(tag: str) -> bool:
    return bool(_PRERELEASE_RE.search(str(tag or "")))


def _get(url: str, timeout: float, accept: str, etag: str = ""):
    """返回 (body, headers, not_modified)。304 不抛异常。"""
    headers = {"User-Agent": UA, "Accept": accept}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), dict(r.headers), False
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return b"", dict(e.headers), True
        raise


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _atom_tag(entry, ns, title: str) -> str:
    """atom 里 <title> 是 Release 名字（可能不含版本号），标签在 link/id 里。"""
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


def _atom_entry(entry, ns) -> dict:
    title = _strip_html(entry.findtext("a:title", "", ns))
    tag = _atom_tag(entry, ns, title)
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
        "published": (entry.findtext("a:published", "", ns)
                      or entry.findtext("a:updated", "", ns)),
        "prerelease": _looks_prerelease(tag),
        "assets": [],
        "download_url": (
            f"https://github.com/{REPO}/releases/download/{tag}/"
            f"RhythmBar-Studio-{tag}-win64.zip"),
        "source": "atom",
    }


def fetch_via_atom(timeout: float, etag: str = "") -> dict:
    """releases.atom：不吃 API 配额；有多个条目时优先选第一个正式版。"""
    raw, headers, not_modified = _get(ATOM, timeout, "application/atom+xml", etag)
    new_etag = headers.get("ETag") or headers.get("Etag") or ""
    if not_modified:
        return {"not_modified": True, "etag": new_etag or etag, "source": "atom"}
    root = ET.fromstring(raw.decode("utf-8", "replace"))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)
    if not entries:
        return {"none": True, "etag": new_etag, "source": "atom"}
    first = None
    for entry in entries:
        data = _atom_entry(entry, ns)
        data["etag"] = new_etag
        if first is None:
            first = data
        if not data["prerelease"]:
            return data
    return first                    # 全是预发布版时至少返回第一条，由上层决定


def _api_release(data: dict) -> dict:
    assets = [{
        "name": str(a.get("name") or ""),
        "size": int(a.get("size") or 0),
        "url": str(a.get("browser_download_url") or ""),
    } for a in (data.get("assets") or [])]
    return {
        "tag": str(data.get("tag_name") or ""),
        "name": str(data.get("name") or data.get("tag_name") or ""),
        "body": str(data.get("body") or ""),
        "url": str(data.get("html_url") or PAGE),
        "published": str(data.get("published_at") or ""),
        "prerelease": bool(data.get("prerelease")),
        "assets": assets,
        "source": "api",
    }


def fetch_via_api_latest(timeout: float) -> dict:
    raw, _headers, _nm = _get(API_LATEST, timeout, "application/vnd.github+json")
    return _api_release(json.loads(raw.decode("utf-8", "replace")))


def fetch_release_detail(tag: str, timeout: float = 6.0) -> dict:
    """新版本才调用：拿附件列表和完整说明。失败由调用方决定是否忽略。"""
    url = API_TAG.format(tag=urllib.parse.quote(tag, safe=""))
    raw, _headers, _nm = _get(url, timeout, "application/vnd.github+json")
    return _api_release(json.loads(raw.decode("utf-8", "replace")))


def fetch_latest(timeout: float = 8.0, current: str = "", etag: str = "") -> dict:
    """取最新 release；没有 release 返回 {'none': True}。

    正常路径：atom -> （有新版本时）API 补附件。
    只有 atom 失败/只拿到预发布版时，才回退 API。
    """
    try:
        res = fetch_via_atom(timeout, etag)
    except Exception as atom_err:  # noqa: BLE001
        try:
            return fetch_via_api_latest(timeout)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"none": True}
            if e.code in (403, 429):
                raise RuntimeError(
                    "GitHub 接口调用次数用完了（未登录每小时 60 次），"
                    "订阅源也没取到。稍后再试，或直接打开 Releases 页面看。") from e
            raise RuntimeError(f"连不上 GitHub：{e}") from atom_err
        except Exception as e2:  # noqa: BLE001
            raise RuntimeError(f"连不上 GitHub：{e2}") from atom_err

    if res.get("not_modified") or res.get("none"):
        return res
    if res.get("prerelease"):
        # atom 只有预发布版时，用 API 的 latest（正式版）兜底
        try:
            return fetch_via_api_latest(timeout)
        except Exception:  # noqa: BLE001
            return res
    if current and is_newer(str(res.get("tag", "")), current):
        try:
            detail = fetch_release_detail(str(res.get("tag", "")), timeout)
        except Exception:  # noqa: BLE001
            detail = None
        if detail:
            if detail.get("body"):
                res["body"] = detail["body"]
            if detail.get("assets"):
                res["assets"] = detail["assets"]
            if detail.get("url"):
                res["url"] = detail["url"]
            res["source"] = "atom+api"
    return res
