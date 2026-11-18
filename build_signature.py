#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 data/signature.json —— 五星角色 → 专属武器

原理：bwiki 的专属武器页面都有一句「仅限 XXX 装备时，武器外观会发生改变」，
从武器页反查即可得到一一对应关系，不靠人工维护。

用法：
    python3 build_signature.py            # 只处理五星武器
    python3 build_signature.py --all      # 所有武器（慢）
"""
from __future__ import annotations

import argparse
import io
import time
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
API = "https://wiki.biligame.com/ys/api.php"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
PAT = re.compile(r"仅限\s*([\u4e00-\u9fa5·]{2,10}?)\s*装备时")


def page_text(page: str) -> str:
    q = urllib.parse.urlencode({"action": "parse", "page": page, "prop": "text",
                                "format": "json", "formatversion": "2"})
    req = urllib.request.Request(f"{API}?{q}", headers=UA)
    last = None
    for attempt, wait in enumerate((0, 3, 8, 15)):
        if wait:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))["parse"]["text"]
        except Exception as e:  # noqa: BLE001
            last = e
    raise last


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="包含四星及以下武器")
    a = ap.parse_args()

    with io.open(os.path.join(DATA, "weaponstars.json"), encoding="utf-8") as f:
        stars = json.load(f)
    names = [n for n, s in stars.items() if a.all or s == 5]

    out, fail = {}, 0
    for i, w in enumerate(sorted(names), 1):
        try:
            txt = page_text(w)
        except Exception as e:  # noqa: BLE001
            fail += 1
            print("  !", w, e)
            continue
        time.sleep(0.5)   # 放慢，避免被 WAF 拦
        plain = re.sub(r"<[^>]+>", " ", txt)
        m = PAT.search(plain)
        if m:
            out[m.group(1)] = w
        if i % 15 == 0:
            print(f"  {i}/{len(names)}  已匹配 {len(out)}")

    dst = os.path.join(DATA, "signature.json")
    with io.open(dst, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(out.items())), f, ensure_ascii=False, indent=1)
    print(f"写入 {dst}：{len(out)} 组对应（失败 {fail}）")
    for k, v in list(out.items())[:8]:
        print("   ", k, "→", v)


if __name__ == "__main__":
    main()
