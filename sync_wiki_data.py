#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
依据 bwiki「角色」「武器一览」「祈愿（常驻池）」三处信息，刷新本地数据库：

  data/charstars.json     全角色星级
  data/weaponstars.json   全武器星级
  data/roster.json        只更新 standard5 / standard4chars / standard4weapons / standard5weapons
                          （你自己的 characters / weapons 不动）
  data/roster.js          最后由 build_data.py 重新生成

会打印「新增 / 消失」的角色和武器，方便你确认版本差了多少。

用法：
    python3 sync_wiki_data.py            # 预览 + 写入
    python3 sync_wiki_data.py --dry-run  # 只看差异，不写文件
"""
from __future__ import annotations

import argparse
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


def page_html(page: str) -> str:
    q = urllib.parse.urlencode({"action": "parse", "page": page, "prop": "text",
                                "format": "json", "formatversion": "2"})
    req = urllib.request.Request(f"{API}?{q}", headers=UA)
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8"))["parse"]["text"]


def load(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path: str, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def parse_chars() -> dict:
    t = page_html("角色")
    out = {}
    for star, _, name in re.findall(
            r'data-param1="(\d)星"[^>]*>(.*?)<div class="L">([^<]+)</div>', t, re.S):
        out[name.strip()] = int(star)
    return out


def parse_weapons() -> dict:
    t = page_html("武器一览")
    out = {}
    for star, name in re.findall(r'<div class="T g(\d)"></div><div class="L">([^<]+)</div>', t):
        out.setdefault(name.strip(), int(star))
    return out


def parse_standard_pool() -> dict:
    """常驻祈愿区块：8 个五星角色 / 若干四星角色 / 10 把五星武器 / 18 把四星武器"""
    t = page_html("祈愿")
    heads = [(m.start(), re.sub(r"<[^>]+>", "", m.group(1)).strip())
             for m in re.finditer(r"<h[1-4][^>]*>(.*?)</h[1-4]>", t, re.S)]
    start = next((p for p, h in heads if h.startswith("常驻祈愿")), None)
    if start is None:
        return {}
    end = next((p for p, h in heads if p > start and h.startswith("历史活动")), len(t))
    seg = t[start:end]

    names = []
    for n in re.findall(r'alt="([^"]+)"', seg):
        n = re.sub(r"\.png$", "", n)
        if not n or n.startswith("无背景") or "祈愿" in n or "活动" in n or n.endswith("图"):
            continue
        if n not in names:
            names.append(n)
    if len(names) < 80:
        return {}
    return {
        "standard5": names[0:8],
        "standard4chars": names[8:59],
        "standard5weapons": names[59:69],
        "standard4weapons": names[69:87],
    }


def diff(old: dict, new: dict, label: str):
    added = [k for k in new if k not in old]
    gone = [k for k in old if k not in new]
    changed = [k for k in new if k in old and old[k] != new[k]]
    print(f"{label}: 共 {len(new)}（原 {len(old)}）")
    if added:
        print("  + 新增:", "、".join(added[:40]) + (" …" if len(added) > 40 else ""))
    if gone:
        print("  - 消失:", "、".join(gone[:40]) + (" …" if len(gone) > 40 else ""))
    if changed:
        print("  ~ 星级变化:", "、".join(f"{k}({old[k]}→{new[k]})" for k in changed[:20]))
    if not (added or gone or changed):
        print("  无变化")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="依据 wiki 刷新角色/武器数据库")
    ap.add_argument("--dry-run", action="store_true", help="只看差异，不写文件")
    ap.add_argument("--no-build", action="store_true", help="不自动重建 roster.js")
    a = ap.parse_args()

    print("→ 拉取 wiki 数据 …")
    chars = parse_chars()
    weapons = parse_weapons()
    pools = parse_standard_pool()
    if not chars or not weapons:
        raise SystemExit("抓取失败：角色或武器为空，检查网络后重试")
    print(f"  角色 {len(chars)} · 武器 {len(weapons)} · 常驻池 {len(pools.get('standard5', []))}/"
          f"{len(pools.get('standard4chars', []))}/{len(pools.get('standard5weapons', []))}/"
          f"{len(pools.get('standard4weapons', []))}")

    old_chars = load(os.path.join(DATA, "charstars.json"), {})
    old_weps = load(os.path.join(DATA, "weaponstars.json"), {})
    diff(old_chars, chars, "角色表")
    diff(old_weps, weapons, "武器表")
    # 合并：wiki 上没有但本地有的条目保留（旅行者、奇偶等特殊角色）
    keep_c = [k for k in old_chars if k not in chars]
    keep_w = [k for k in old_weps if k not in weapons]
    if keep_c:
        print("  保留本地条目:", "、".join(keep_c[:20]))
    chars = {**old_chars, **chars}
    weapons = {**old_weps, **weapons}

    roster_path = os.path.join(DATA, "roster.json")
    roster = load(roster_path, {"characters": {}, "weapons": {}})
    if pools:
        for k, v in pools.items():
            if roster.get(k) != v:
                print(f"  常驻池 {k}: {len(roster.get(k, []))} → {len(v)}")
                roster[k] = v
    else:
        print("  ! 常驻池解析失败，保留原名单")

    if a.dry_run:
        print("预览模式，未写入。")
        return

    save(os.path.join(DATA, "charstars.json"), chars)
    save(os.path.join(DATA, "weaponstars.json"), weapons)
    save(roster_path, roster)
    print("已写入 charstars.json / weaponstars.json / roster.json")

    if not a.no_build:
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(HERE, "build_data.py")],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
    print("完成，刷新 index.html 即可。")


if __name__ == "__main__":
    main()
