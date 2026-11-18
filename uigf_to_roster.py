#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 UIGF 格式的抽卡记录生成个人仓库 data/roster.json

UIGF（Uniformed Gacha data Format）是社区统一的抽卡导出格式，
常见导出工具（uigf、hoyo-gacha、原神抽卡记录导出 等）都支持。

支持的输入：
  · 标准 UIGF：{"info": {...}, "hk4e": [{"uid": "...", "list": [...]}]}
  · 直接一个记录数组：[{...}, {...}]

规则：
  · 角色命座 = 抽到的总个数 − 1（首次即 C0），上限 6
  · 武器只记「是否拥有」（次数不影响星辉，首次 0 星辉、之后按重复算）
  · 旅行者 / 埃洛伊 / 奇偶 不在卡池里，抽卡记录里永远不会出现，需要手动补

用法：
    python3 uigf_to_roster.py 抽卡记录.json
    python3 uigf_to_roster.py 抽卡记录.json --merge     # 保留已有的手动条目
    python3 uigf_to_roster.py 抽卡记录.json --no-build  # 只写 json，不重建 roster.js
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
MAXC = 6
NO_GACHA = ("旅行者", "埃洛伊", "奇偶", "派蒙")


def load_records(path: str):
    with io.open(path, encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, list):
        return [d]                      # 直接是记录数组
    if "hk4e" in d:                     # 标准 UIGF
        return [x.get("list", []) for x in d["hk4e"]]
    if "list" in d:                     # 单个账号
        return [d["list"]]
    raise SystemExit("认不出这个文件的格式：既不是 UIGF，也不是记录数组")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="UIGF 抽卡记录 → data/roster.json")
    ap.add_argument("uigf", help="UIGF 格式的 json 文件")
    ap.add_argument("--out", default=os.path.join(DATA, "roster.json"))
    ap.add_argument("--merge", action="store_true", help="保留 roster.json 里已有的角色/武器")
    ap.add_argument("--all-weapons", action="store_true", help="连 3 星及以下武器一起记录")
    ap.add_argument("--no-build", action="store_true", help="不自动重建 roster.js")
    a = ap.parse_args()

    lists = load_records(a.uigf)
    records = [r for lst in lists for r in lst]
    if not records:
        raise SystemExit("文件里没有抽卡记录")
    print(f"读入 {len(records)} 条记录（{len(lists)} 个账号）")

    chars = collections.Counter()
    weps = collections.Counter()
    wep_star, char_star = {}, {}
    bad = 0
    for r in records:
        name = r.get("name")
        typ = r.get("item_type") or ""
        rank = int(r.get("rank_type") or 0)
        if not name:
            bad += 1
            continue
        if typ == "角色":
            chars[name] += 1
            char_star[name] = max(char_star.get(name, 0), rank)
        elif typ == "武器":
            weps[name] += 1
            wep_star[name] = max(wep_star.get(name, 0), rank)
        else:
            bad += 1
    if bad:
        print(f"跳过 {bad} 条没有 item_type / name 的记录")

    roster = load_json(a.out, {})
    std5 = roster.get("standard5") or []
    std4c = roster.get("standard4chars") or []
    std4w = roster.get("standard4weapons") or []
    std5w = roster.get("standard5weapons") or []

    out_chars = {}
    if a.merge:
        for k, v in (roster.get("characters") or {}).items():
            out_chars[k] = v
    for n, cnt in chars.items():
        out_chars[n] = {"c": min(cnt - 1, MAXC), "star": char_star[n] or 5}

    out_weps = {}
    if a.merge:
        for k, v in (roster.get("weapons") or {}).items():
            out_weps[k] = v
    for n, cnt in weps.items():
        st = wep_star[n] or 4
        if st < 4 and not a.all_weapons:
            continue
        out_weps[n] = {"count": cnt, "star": st}

    data = {
        "_comment": "个人仓库：characters 里 c=命座(0-6)，未列出=未拥有；weapons 里 count>0 表示已拥有。",
        "updated": __import__("datetime").date.today().isoformat(),
        "source": "由 uigf_to_roster.py 从抽卡记录生成",
        "characters": dict(sorted(out_chars.items())),
        "weapons": dict(sorted(out_weps.items())),
        "standard5": std5, "standard4chars": std4c,
        "standard4weapons": std4w, "standard5weapons": std5w,
    }
    with io.open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    c5 = sum(1 for v in out_weps.values() if v.get("star") == 5)
    print(f"写入 {a.out}")
    print(f"  角色 {len(out_chars)} 个（满命 {sum(1 for v in out_chars.values() if v['c'] >= MAXC)} 个）")
    print(f"  武器 {len(out_weps)} 把（五星 {c5} 把）")
    missing = [n for n in NO_GACHA if n not in out_chars]
    if missing:
        print(f"  提示：{'、'.join(missing)} 不会出现在抽卡记录里，需要手动补进 characters")

    if not a.no_build:
        r = subprocess.run([sys.executable, os.path.join(HERE, "build_data.py")],
                           capture_output=True, text=True)
        print((r.stdout or r.stderr).strip())


if __name__ == "__main__":
    main()
