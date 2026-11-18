#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从原神 bwiki 抓角色头像 / 武器图标（高清，稳）

不再自己拼缩略图地址（拼错过：wiki 的 thumb 名用的是“原图文件名”，不是 hash），
改成问 MediaWiki 的 imageinfo 接口要现成地址：

    action=query&prop=imageinfo&iiprop=url&iiurlwidth=220&titles=文件:无背景-角色-奥黛塔.png
    → { thumburl: ".../220px-无背景-角色-奥黛塔.png", url: "..." }

流程：
  1. 列表页拿「名字 → 原图地址」，作为兜底；
  2. 按 `无背景-角色-<名字>.png` / `<武器名>.png` 猜文件名，批量问 imageinfo；
  3. 有 thumburl 就用它（220px），没有就用 url（原图）；
  4. 兜底失败时用列表页的原图地址。

Pillow 可选：装了就把图压成 WebP；没装就直接保存原图（浏览器按内容识别格式）。

用法：
    python3 build_assets.py                 # 增量补齐
    python3 build_assets.py --size 256
    python3 build_assets.py --only weapon
    python3 build_assets.py --force
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    Image = None
    HAVE_PIL = False

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://wiki.biligame.com/ys/api.php"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) as r:
        return r.read()


def api(**params) -> dict:
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    u = API + "?" + urllib.parse.urlencode(params)
    return json.loads(get(u).decode("utf-8"))


def list_items(page: str, pattern: str):
    d = api(action="parse", page=page, prop="text")
    html = d["parse"]["text"]
    out, seen = [], set()
    for m in re.finditer(pattern, html):
        name, src = m.group(1).strip(), m.group(2)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append((name, src))
    return out


def lookup(titles, width):
    """批量查 imageinfo：{文件标题: 图片地址}（分批 + 失败重试一次）"""
    got = {}
    for i in range(0, len(titles), 20):
        chunk = titles[i:i + 20]
        for attempt in (1, 2):
            try:
                d = api(action="query", prop="imageinfo", iiprop="url",
                        iiurlwidth=width, titles="|".join(chunk))
                break
            except Exception as e:  # noqa: BLE001
                d = None
                if attempt == 2:
                    print("  批量查询失败，改用列表页原图:", e)
        if not d:
            continue
        for p in d.get("query", {}).get("pages", []):
            if "missing" in p:
                continue
            info = (p.get("imageinfo") or [{}])[0]
            src = info.get("thumburl") or info.get("url")
            if src:
                got[p["title"]] = src
    return got


def save_image(raw: bytes, dst: str, size: int) -> int:
    if not HAVE_PIL:
        with open(dst, "wb") as f:
            f.write(raw)
        return size
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    if im.width < 40:
        return 0
    im = im.resize((size, size), Image.LANCZOS)
    im.save(dst, "WEBP", quality=90, method=5)
    return im.width


def current_px(path: str) -> int:
    if not os.path.exists(path):
        return 0
    if not HAVE_PIL:
        return 1
    try:
        with Image.open(path) as im:
            return im.width
    except Exception:  # noqa: BLE001
        return 0


def run(kind: str, items, outdir: str, size: int, force: bool, file_title, use_info: bool = True):
    os.makedirs(outdir, exist_ok=True)

    todo = []
    for name, src in items:
        dst = os.path.join(outdir, name.replace("/", "_") + ".webp")
        if not force and current_px(dst) >= size:
            continue
        todo.append((name, src, dst))
    print(f"{kind}: 共 {len(items)} 个，待处理 {len(todo)}")

    titles = ["文件:" + file_title(n) for n, _, _ in todo]
    urls = lookup(titles, size) if use_info else {}

    ok = fail = 0
    for i, (name, src, dst) in enumerate(todo, 1):
        cand = urls.get("文件:" + file_title(name)) or urls.get("文件:" + file_title(name).replace(" ", "_")) or src
        raw = None
        for u in (cand, src):           # 接口给的地址失败就退回列表页原图
            try:
                raw = get(u)
                break
            except Exception:  # noqa: BLE001
                continue
        if raw is None:
            fail += 1
            if fail <= 10:
                print("  !", name, "下载失败")
            continue
        try:
            if save_image(raw, dst, size):
                ok += 1
            else:
                fail += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            if fail <= 10:
                print("  !", name, e)
        if i % 20 == 0 or i == len(todo):
            print(f"  {kind} {i}/{len(todo)}  成功 {ok} · 失败 {fail}")
    print(f"{kind} 完成：成功 {ok} · 失败 {fail}")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="抓取原神 wiki 角色/武器图片")
    ap.add_argument("--size", type=int, default=220)
    ap.add_argument("--only", choices=["char", "weapon"], default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if not HAVE_PIL:
        print("提示：未安装 Pillow，将直接保存 220px 原图（PNG），体积偏大。")
        print("      可选安装：pip install pillow  或  sudo apt install python3-pil")
        print()

    if a.only != "weapon":
        chars = list_items("角色", r'<img alt="无背景-角色-([^"]+?)\.png" src="([^"]+)"')
        run("角色", chars, os.path.join(HERE, "assets", "char"), a.size, a.force,
            lambda n: f"无背景-角色-{n}.png")

    if a.only != "char":
        weapons = list_items("武器一览", r'<img alt="([^"]+?)\.png"[^>]*?src="([^"]+/images/ys/[^"]+?\.png)"')
        run("武器", weapons, os.path.join(HERE, "assets", "weapon"), a.size, a.force,
            lambda n: f"{n}.png", use_info=False)

    print("完成。刷新 index.html 即可看到新图。")


if __name__ == "__main__":
    main()
