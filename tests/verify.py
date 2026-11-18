#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一致性验证：Python 引擎 vs JS 引擎 vs 蒙特卡洛。

    python tests/verify.py            # 全部检查
    python tests/verify.py --quick    # 跳过蒙特卡洛

全部通过时退出码为 0。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import gacha as G  # noqa: E402

TOL = 1e-9

CASES = [
    dict(name="默认 360/2/1", N=360, C=2, W=1, init={}),
    dict(name="紧凑 240/2/1", N=240, C=2, W=1, init={}),
    dict(name="带初始状态 300/3/2", N=300, C=3, W=2,
         init=dict(char_pity=45, char_guaranteed=True, char_streak=1,
                   weapon_pity=30, weapon_fate=1)),
    dict(name="单角色 120/1/0", N=120, C=1, W=0, init={}),
    dict(name="纯武器 500/0/3", N=500, C=0, W=3, init={}),
    dict(name="高目标 400/4/2", N=400, C=4, W=2, init={}),
    dict(name="大预算 800/7/5", N=800, C=7, W=5, init={}),
    dict(name="任意UP 300/2/1", N=300, C=2, W=1, init={},
         weapon=dict(accept_any=True)),
    dict(name="任意UP+初始 420/3/2", N=420, C=3, W=2,
         init=dict(char_pity=20, char_streak=2, weapon_pity=50, weapon_fate=1),
         weapon=dict(accept_any=True)),
]

JS_INIT_KEYS = dict(char_pity="charPity", char_guaranteed="charGuaranteed",
                    char_streak="charStreak", weapon_pity="weaponPity",
                    weapon_fate="weaponFate")


def py_case(spec):
    C, W, N = spec["C"], spec["W"], spec["N"]
    i = spec["init"]
    char_cfg = dict(G.DEFAULT_CHAR, **(spec.get("char") or {}))
    weapon_cfg = dict(G.DEFAULT_WEAPON, **(spec.get("weapon") or {}))
    H = G.horizon(N, C, W, char_cfg, weapon_cfg)
    c_reach = G.char_curves(H, C, char_cfg,
                            dict(pity=i.get("char_pity", 0),
                                 guaranteed=i.get("char_guaranteed", False),
                                 streak=i.get("char_streak", 0)))
    w_reach = G.weapon_curves(H, W, weapon_cfg,
                              dict(pity=i.get("weapon_pity", 0),
                                   fate=i.get("weapon_fate", 0)))
    curve = G.combined_curve(c_reach, w_reach, C, W, H)
    st = G.stats(curve)
    return dict(p=float(curve[N]), mean=st["mean"], q50=st["median"],
                q90=st["quantiles"][0.9], q99=st["quantiles"][0.99],
                pChar=float(c_reach[C, N]), pWeapon=float(w_reach[W, N]), H=H)


def js_case(spec):
    payload = dict(N=spec["N"], C=spec["C"], W=spec["W"])
    cfg = {}
    if spec.get("weapon"):
        cfg["weapon"] = {("acceptAny" if k == "accept_any" else k): v
                         for k, v in spec["weapon"].items()}
    if spec.get("char"):
        cfg["char"] = spec["char"]
    if cfg:
        payload["cfg"] = cfg
    init = {}
    for k, v in spec["init"].items():
        init[JS_INIT_KEYS[k]] = v
    payload["init"] = init
    out = subprocess.run(["node", os.path.join(HERE, "emit.js"), json.dumps(payload)],
                         capture_output=True, text=True, check=True, cwd=ROOT)
    return json.loads(out.stdout)


def close(a, b, tol=TOL):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def main():
    quick = "--quick" in sys.argv
    failures = 0

    print("— Python 引擎 vs JS 引擎 —")
    for spec in CASES:
        py, js = py_case(spec), js_case(spec)
        bad = [k for k in ("p", "mean", "pChar", "pWeapon") if not close(py[k], js[k])]
        bad += [k for k in ("q50", "q90", "q99") if py[k] != js[k]]
        status = "OK " if not bad else "FAIL"
        if bad:
            failures += 1
        print(f"  [{status}] {spec['name']:<22} p={py['p']*100:6.2f}%  "
              f"均值={py['mean']:6.1f}  q90={py['q90']:>4}"
              + ("" if not bad else f"  差异字段: {bad}"))

    print("\n— 口径自检 —")
    checks = [
        ("角色池平均出金 62.3 抽", G.expected_pity(G.DEFAULT_CHAR), 62.3, 0.1),
        ("武器池平均出金 53.25 抽", G.expected_pity(G.DEFAULT_WEAPON), 53.25, 0.1),
        ("角色池综合五星率 1.6%", G.five_star_rate(G.DEFAULT_CHAR) * 100, 1.6, 0.02),
        ("武器池综合五星率 1.85%", G.five_star_rate(G.DEFAULT_WEAPON) * 100, 1.85, 0.05),
        ("小保底出 UP 综合 55%", G.long_run_up_rate(G.DEFAULT_CHAR) * 100, 55.0, 1.0),
        ("1 角色 90 抽内 61.4%", G.char_curves(90, 1, G.DEFAULT_CHAR, {})[1, 90] * 100, 61.4, 0.2),
    ]
    for name, got, want, tol in checks:
        ok = abs(got - want) <= tol
        failures += 0 if ok else 1
        print(f"  [{'OK ' if ok else 'FAIL'}] {name:<24} 实测 {got:.3f}")

    print("\n— 概率合法性（单调 / 有界 / 边界）—")
    for spec in CASES:
        r = py_case(spec)
        ok = -1e-12 <= r["p"] <= 1 + 1e-12
        print(f"  [{'OK ' if ok else 'FAIL'}] {spec['name']:<22} {r['p']*100:.2f}% 在 [0,100%]")
        failures += 0 if ok else 1
    # C=0,W=0 必达成
    triv = G.combined_prob(G.char_curves(10, 0, G.DEFAULT_CHAR, {}),
                           G.weapon_curves(10, 0, G.DEFAULT_WEAPON, {}), 0, 0, 10)
    ok = abs(triv - 1) < 1e-12
    print(f"  [{'OK ' if ok else 'FAIL'}] 目标为空时概率恒为 1（实测 {triv:.6f}）")
    failures += 0 if ok else 1

    if not quick:
        print("\n— 蒙特卡洛对照（20 万次）—")
        spec = dict(N=300, C=2, W=1, init={})
        py = py_case(spec)
        mc = G.monte_carlo(300, 2, 1, G.DEFAULT_CHAR, G.DEFAULT_WEAPON,
                           runs=200_000, seed=777)
        z = abs(mc["p"] - py["p"]) / mc["se"] if mc["se"] else 0.0
        ok = z < 4.0
        failures += 0 if ok else 1
        print(f"  [{'OK ' if ok else 'FAIL'}] 300/2/1  DP {py['p']*100:.3f}%  "
              f"MC {mc['p']*100:.3f}% ± {mc['se']*100:.3f}%  z={z:.2f}")

    print("\n" + ("全部通过 OK" if failures == 0 else f"{failures} 项未通过 FAIL"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
