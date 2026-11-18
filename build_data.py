#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 data/roster.json + data/charstars.json 打包成网页用的 data/roster.js"""
import json, io, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
roster = json.load(io.open(os.path.join(HERE, 'data/roster.json'), encoding='utf-8'))
stars = json.load(io.open(os.path.join(HERE, 'data/charstars.json'), encoding='utf-8'))
wstars = {}
wp = os.path.join(HERE, 'data', 'weaponstars.json')
if os.path.exists(wp):
    wstars = json.load(io.open(wp, encoding='utf-8'))
std4chars = sorted(n for n, s in stars.items() if s == 4)
sigp = os.path.join(HERE, 'data', 'signature.json')
signature = {}
if os.path.exists(sigp):
    signature = json.load(io.open(sigp, encoding='utf-8'))
data = {
    "updated": roster.get("updated", ""),
    "note": "角色 c=命座(0-6)，未列出=未拥有；武器 n=已获得次数",
    "chars": {n: {"c": v["c"], "s": v.get("star", stars.get(n, 5))} for n, v in roster["characters"].items()},
    "weapons": {n: {"n": v.get("count", 1), "s": v.get("star", 4)} for n, v in roster["weapons"].items()},
    "std5": roster["standard5"],
    "std5weapons": roster["standard5weapons"],
    "std4chars": roster.get("standard4chars") or std4chars,
    "std4weapons": roster.get("standard4weapons") or sorted(n for n, v in roster["weapons"].items() if v.get("star") == 4),
    "allChars": stars,
    "allWeapons": wstars,
    "signature": signature,
}
out = os.path.join(HERE, 'data', 'roster.js')
with io.open(out, 'w', encoding='utf-8', newline='\n') as f:
    f.write("/* 由 build_data.py 从 data/roster.json 生成，请勿手改 */\n")
    f.write("window.GACHA_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n")
print(f"roster.js: {len(data['chars'])} 角色 / {len(data['std4chars'])} 四星角色 / "
      f"{len(data['std4weapons'])} 四星武器 / {len(signature)} 组专武")
