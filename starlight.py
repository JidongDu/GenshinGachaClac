#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星辉返利模拟（roster + 卡池配置 → 含返利的达成概率）

规则（2025-11 官方口径）：
    5★ 角色  第1次 0 星辉 / 第2–7次 10 / 第8次起 25
    4★ 角色  第1次 0 星辉 / 第2–7次  2 / 第8次起  5
    5★ 武器  第1次 0 星辉 / 之后      10
    4★ 武器  第1次 0 星辉 / 之后       2
    5 星辉 = 1 纠缠之缘（无限购）

「当前命座」直接影响每次出金的返还额，所以必须带一份个人 roster。
roster 来自米游社角色一览截图（data/roster.json），可手工编辑或用 --set 覆盖。

用法：
    python starlight.py --pulls 360 --chars 2 --weapons 1
    python starlight.py --pulls 360 --chars 2 --weapons 1 --mc 200000
    python starlight.py --pulls 360 --chars 2 --weapons 1 --up5 新角色名 --up4 A,B,C
    python starlight.py --set 玛薇卡=3 --pulls 300 --chars 2 --weapons 1
    python starlight.py --show-roster
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROSTER_PATH = os.path.join(HERE, "data", "roster.json")
BANNER_PATH = os.path.join(HERE, "data", "banner.json")

GLITTER_PER_PULL = 5
GLITTER_RULES = {
    "char5": (0, 10, 25),   # (首次, 重复, 满命)
    "char4": (0, 2, 5),
    "weap5": (0, 10, 10),
    "weap4": (0, 2, 2),
}
MAX_CONST = 6


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_roster(path=ROSTER_PATH):
    d = load_json(path, {})
    # 允许两种写法：{"名字": {"c":2,"star":5}} 或 {"名字": 2}
    chars = {}
    for n, v in (d.get("characters") or {}).items():
        if isinstance(v, dict):
            chars[n] = (int(v.get("c", 0)), int(v.get("star", 5)))
        else:
            chars[n] = (int(v), 5)
    weapons = {}
    wstar = {}
    for n, v in (d.get("weapons") or {}).items():
        if isinstance(v, dict):
            weapons[n] = int(v.get("count", 1))
            wstar[n] = int(v.get("star", 4))
        else:
            weapons[n] = int(v)
            wstar[n] = 4
    stars_path = os.path.join(os.path.dirname(path) or ".", "charstars.json")
    stars = load_json(stars_path, {}) if os.path.exists(stars_path) else {}
    std4 = sorted(n for n, s in stars.items() if s == 4) or \
        sorted(n for n, (c, s) in chars.items() if s == 4)
    return {
        "chars": chars,
        "weapons": weapons,
        "weapon_star": wstar,
        "standard5": d.get("standard5") or [],
        "standard5weapons": d.get("standard5weapons") or [],
        "std4chars": std4,
        "standard4weapons": d.get("standard4weapons") or [],
    }


class Sim:
    """一次抽卡进程：角色池 + 武器池 + 星辉账本（base roster 只读，改动写在 overlay）"""

    def __init__(self, roster, phase_c, phase_w=None):
        self.base = roster["chars"]
        self.overlay = {}
        self.wbase = roster["weapons"]
        self.woverlay = {}
        self.std5 = roster["standard5"]
        self.std5w = roster["standard5weapons"]
        self.std4chars = roster.get("std4chars") or [n for n, (c, s) in self.base.items() if s == 4]
        self.std4weapons = roster.get("standard4weapons") or \
            [n for n, s in roster.get("weapon_star", {}).items() if s == 4]
        self.phase_c = phase_c
        self.phase_w = phase_w
        self.star = 0
        self.bought = 0
        self.pulls = 0
        self.book = {"char5": 0, "char4": 0, "weap5": 0, "weap4": 0}

    def c_of(self, name):
        if name in self.overlay:
            return self.overlay[name]
        e = self.base.get(name)
        return None if e is None else e[0]

    def gain_char(self, name, star):
        c = self.c_of(name)
        key = "char5" if star == 5 else "char4"
        new, dup, mx = GLITTER_RULES[key]
        if c is None or c < 0:
            self.overlay[name] = 0
            g = new
        elif c >= MAX_CONST:
            g = mx
        else:
            self.overlay[name] = c + 1
            g = dup
        self.star += g
        self.book[key] += g
        return g

    def gain_weapon(self, name, star):
        key = "weap5" if star == 5 else "weap4"
        new, dup, mx = GLITTER_RULES[key]
        owned = name in self.woverlay or self.wbase.get(name, 0) > 0
        if not owned:
            self.woverlay[name] = 1
            g = new
        else:
            self.woverlay[name] = self.woverlay.get(name, self.wbase.get(name, 1)) + 1
            g = dup
        self.star += g
        self.book[key] += g
        return g

    def settle(self):
        while self.star >= GLITTER_PER_PULL:
            self.star -= GLITTER_PER_PULL
            self.bought += 1


def simulate(N, A, B, P, Q, roster, pools, cfg, rng):
    """一期：角色池①A个 + 角色池②B个（共享垫抽/大保底）+ 武器池①P把 + 武器池②Q把"""
    from gacha import p5_at, DEFAULT_CHAR, DEFAULT_WEAPON

    char_cfg = dict(DEFAULT_CHAR)
    char_cfg.update({k: v for k, v in (cfg.get("char") or {}).items() if k in DEFAULT_CHAR})
    wp_cfg = dict(DEFAULT_WEAPON)
    wp_cfg.update({k: v for k, v in (cfg.get("weapon") or {}).items() if k in DEFAULT_WEAPON})

    st = Sim(roster, pools["charA"], pools["charB"])
    cA, cB, cW = pools["charA"], pools["charB"], pools["weapon"]
    std5 = [n for n in st.std5 if n] or [cA["up5"], cB["up5"]]
    std4chars = st.std4chars
    std4weapons = st.std4weapons
    std5w = [n for n in st.std5w if n] or [cW["up5"][1]]
    wup4 = [n for n in (cW.get("up4") or []) if n] or std4weapons
    w1, w2 = cW["up5"][0], (cW["up5"][1] if len(cW["up5"]) > 1 else cW["up5"][0])

    cs = dict(pity=0, g=False, s=0, p4=0)
    ws = dict(pity=0, f=0, p4=0)
    gotA = gotB = gotW1 = gotW2 = 0

    while gotA < A or gotB < B or gotW1 < P or gotW2 < Q:
        if st.pulls >= N + st.bought:
            break
        st.pulls += 1

        if gotA < A or gotB < B:
            onA = gotA < A
            pool = cA if onA else cB
            cs["pity"] += 1
            if rng.random() < p5_at(cs["pity"] - 1, char_cfg):
                cs["pity"] = 0
                cs["p4"] = 0
                is_up = False
                if cs["g"]:
                    cs["g"] = False
                    is_up = True
                else:
                    p_up = 1.0 if cs["s"] >= char_cfg["rad_max"] else (
                        char_cfg["up_rate"] + (1 - char_cfg["up_rate"]) * char_cfg["rad_p"])
                    if rng.random() < p_up:
                        cs["s"] = 0
                        is_up = True
                    else:
                        cs["g"] = True
                        cs["s"] = min(cs["s"] + 1, char_cfg["rad_max"])
                if is_up:
                    st.gain_char(pool["up5"], 5)
                    if onA:
                        gotA += 1
                    else:
                        gotB += 1
                else:
                    std_name = rng.choice(std5)
                    st.gain_char(std_name, 5)
                    if std_name == pool["up5"]:
                        if onA:
                            gotA += 1
                        else:
                            gotB += 1
                st.settle()
                continue
            cs["p4"] += 1
            if rng.random() < (1.0 if cs["p4"] >= 10 else 0.051):
                cs["p4"] = 0
                if rng.random() < 0.5:
                    st.gain_char(rng.choice([n for n in pool["up4"] if n] or [pool["up5"]]), 4)
                elif std4weapons and rng.random() >= 0.42:
                    st.gain_weapon(rng.choice(std4weapons), 4)
                else:
                    st.gain_char(rng.choice(std4chars), 4)
                st.settle()
        else:
            first = gotW1 < P
            ws["pity"] += 1
            if rng.random() < p5_at(ws["pity"] - 1, wp_cfg):
                ws["pity"] = 0
                ws["p4"] = 0
                guaranteed = wp_cfg["fate_max"] > 0 and ws["f"] >= wp_cfg["fate_max"]
                hit_t = hit_o = 0.0
                if guaranteed:
                    if first:
                        hit_t = 1.0
                    else:
                        hit_o = 1.0
                else:
                    hit_t = hit_o = wp_cfg["featured_rate"] * 0.5
                r = rng.random()
                if first:
                    if r < hit_t:
                        ws["f"] = 0
                        st.gain_weapon(w1, 5)
                        gotW1 += 1
                    elif r < hit_t + hit_o:
                        ws["f"] = min(ws["f"] + 1, wp_cfg["fate_max"])
                        st.gain_weapon(w2, 5)
                        gotW2 += 1
                    else:
                        sw = rng.choice(std5w)
                        st.gain_weapon(sw, 5)
                        if sw == w1:
                            ws["f"] = 0
                            gotW1 += 1
                        elif sw == w2:
                            ws["f"] = min(ws["f"] + 1, wp_cfg["fate_max"])
                            gotW2 += 1
                        else:
                            ws["f"] = min(ws["f"] + 1, wp_cfg["fate_max"])
                else:
                    if r < hit_o:
                        ws["f"] = 0
                        st.gain_weapon(w2, 5)
                        gotW2 += 1
                    elif r < hit_o + hit_t:
                        ws["f"] = min(ws["f"] + 1, wp_cfg["fate_max"])
                        st.gain_weapon(w1, 5)
                        gotW1 += 1
                    else:
                        sw = rng.choice(std5w)
                        st.gain_weapon(sw, 5)
                        if sw == w2:
                            ws["f"] = 0
                            gotW2 += 1
                        elif sw == w1:
                            ws["f"] = min(ws["f"] + 1, wp_cfg["fate_max"])
                            gotW1 += 1
                        else:
                            ws["f"] = min(ws["f"] + 1, wp_cfg["fate_max"])
                st.settle()
                continue
            ws["p4"] += 1
            if rng.random() < (1.0 if ws["p4"] >= 10 else 0.051):
                ws["p4"] = 0
                if wup4 and rng.random() < 0.75:
                    st.gain_weapon(rng.choice(wup4), 4)
                else:
                    st.gain_weapon(rng.choice(std4weapons or [w1]), 4)
                st.settle()

    return {"done": gotA >= A and gotB >= B and gotW1 >= P and gotW2 >= Q,
            "pulls": st.pulls, "bought": st.bought,
            "got": (gotA, gotB, gotW1, gotW2), "book": st.book}


def monte_carlo(N, A, B, P, Q, roster, pools, runs=100_000, seed=20240828, cfg=None):
    cfg = cfg or {}
    ok = 0
    extra = 0.0
    used = 0.0
    book = {"char5": 0.0, "char4": 0.0, "weap5": 0.0, "weap4": 0.0}
    rng = random.Random(seed)
    for _ in range(runs):
        r = simulate(N, A, B, P, Q, roster, pools, cfg, rng)
        if r["done"]:
            ok += 1
        extra += r["bought"]
        used += r["pulls"]
        for k in book:
            book[k] += r["book"][k]
    p = ok / runs
    return {"runs": runs, "p": p, "se": math.sqrt(max(0.0, p * (1 - p) / runs)),
            "extra": extra / runs, "book": {k: v / runs for k, v in book.items()},
            "pulls": used / runs}


def baseline_prob(N, A, B, P, Q, roster, pools, runs=40000, seed=1):
    """不含返利的概率（把兑换比例临时设成无穷大）"""
    save = GLITTER_PER_PULL
    globals()["GLITTER_PER_PULL"] = 10 ** 9
    try:
        return monte_carlo(N, A, B, P, Q, roster, pools, runs=runs, seed=seed)["p"]
    finally:
        globals()["GLITTER_PER_PULL"] = save


def fmt_roster(roster):
    chars = roster["chars"]
    groups = {}
    for n, (c, s) in chars.items():
        groups.setdefault(min(c, MAX_CONST), []).append(n)
    n5 = sum(1 for c, s in chars.values() if s == 5)
    lines = [f"roster：{len(chars)} 个角色（{n5} 五星 / {len(chars)-n5} 四星），武器 {len(roster['weapons'])} 把"]
    for k in range(MAX_CONST, -1, -1):
        if k in groups:
            lines.append(f"  C{k}（{len(groups[k])}）: " + "、".join(sorted(groups[k])))
    return "\n".join(lines)


def main(argv=None):
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="星辉返利模拟（一期两角色池 + 一武器池）")
    ap.add_argument("--pulls", type=int, default=360)
    ap.add_argument("--targets", default="1,1,1,0", metavar="A,B,P,Q",
                    help="角色①A个,角色②B个,武器①P把,武器②Q把（默认 1,1,1,0）")
    ap.add_argument("--up5a", default=None, help="角色池①的 UP 五星")
    ap.add_argument("--up4a", default=None, help="角色池①的 UP 四星，逗号分隔")
    ap.add_argument("--up5b", default=None, help="角色池②的 UP 五星")
    ap.add_argument("--up4b", default=None, help="角色池②的 UP 四星，逗号分隔")
    ap.add_argument("--w1", default=None, help="武器池 UP 武器①（定轨目标）")
    ap.add_argument("--w2", default=None, help="武器池 UP 武器②")
    ap.add_argument("--mc", type=int, default=100000)
    ap.add_argument("--seed", type=int, default=20240828)
    ap.add_argument("--set", action="append", default=[], metavar="角色=命座",
                    help="临时覆盖命座；-1 表示未拥有")
    ap.add_argument("--roster", default=ROSTER_PATH)
    ap.add_argument("--banner", default=BANNER_PATH)
    ap.add_argument("--show-roster", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    roster = load_roster(a.roster)
    banner = load_json(a.banner, {})
    pools = {
        "charA": dict(banner.get("charA") or {"up5": "UP五星①", "up4": ["四星A", "四星B", "四星C"]}),
        "charB": dict(banner.get("charB") or {"up5": "UP五星②", "up4": ["四星D", "四星E", "四星F"]}),
        "weapon": dict(banner.get("weapon") or {"up5": ["UP武器①", "UP武器②"], "up4": []}),
    }
    if a.up5a:
        pools["charA"]["up5"] = a.up5a
    if a.up4a:
        pools["charA"]["up4"] = [x.strip() for x in a.up4a.split(",") if x.strip()]
    if a.up5b:
        pools["charB"]["up5"] = a.up5b
    if a.up4b:
        pools["charB"]["up4"] = [x.strip() for x in a.up4b.split(",") if x.strip()]
    if a.w1:
        pools["weapon"]["up5"][0] = a.w1
    if a.w2:
        pools["weapon"]["up5"] = [pools["weapon"]["up5"][0], a.w2]

    for item in a.set:
        if "=" not in item:
            ap.error("--set 需要 名称=命座")
        n, c = item.split("=", 1)
        c = int(c)
        star = roster["chars"].get(n, (0, 5))[1]
        if c < 0:
            roster["chars"].pop(n, None)
        else:
            roster["chars"][n] = (c, star)

    if a.show_roster:
        print(fmt_roster(roster))
        return 0

    try:
        A, B, P_, Q = [int(x) for x in a.targets.split(",")]
    except ValueError:
        ap.error("--targets 需要 4 个整数，例如 1,1,1,0")

    res = monte_carlo(a.pulls, A, B, P_, Q, roster, pools, runs=a.mc, seed=a.seed)
    base = baseline_prob(a.pulls, A, B, P_, Q, roster, pools)

    if a.json:
        print(json.dumps(dict(pulls=a.pulls, targets=[A, B, P_, Q], p_base=base,
                              p_refund=res["p"], se=res["se"], extra_pulls=res["extra"],
                              book=res["book"], pools=pools), ensure_ascii=False, indent=2))
        return 0

    print("星辉返利模拟")
    print("═" * 62)
    print(f"目标        角色①×{A}  角色②×{B}  武器①×{P_}  武器②×{Q}    预算 {a.pulls} 抽")
    print(f"角色池①     UP5={pools['charA']['up5']}  UP4={','.join(pools['charA']['up4'])}")
    print(f"角色池②     UP5={pools['charB']['up5']}  UP4={','.join(pools['charB']['up4'])}")
    print(f"武器池       UP={','.join(pools['weapon']['up5'])}")
    print()
    print(f"不含返利    {base * 100:.2f}%")
    print(f"含返利      {res['p'] * 100:.2f}%   （MC {a.mc:,} 次 ±{res['se'] * 100:.2f}%）")
    print(f"差值        {(res['p'] - base) * 100:+.2f} 个百分点")
    print(f"平均多抽    +{res['extra']:.1f} 抽（平均实际消耗 {res['pulls']:.0f} 抽）")
    print()
    b = res["book"]
    print("星辉来源（每局平均换算成抽）")
    print(f"  5★角色 {b['char5'] / 5:6.2f}    4★角色 {b['char4'] / 5:6.2f}")
    print(f"  5★武器 {b['weap5'] / 5:6.2f}    4★武器 {b['weap4'] / 5:6.2f}")
    tot = sum(b.values()) / 5
    print(f"  合计   {tot:.2f} 抽 ≈ 预算的 {tot / a.pulls * 100:.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
