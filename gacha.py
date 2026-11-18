#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
原神抽卡期望计算器（Python 版）
================================================
角色池：0.6% / 90 硬保底 / 74 抽软保底(+6%/抽) / 小保底 50% / 捕获明光 5% / 连歪 3 次必中
武器池：0.7% / 80 硬保底 / 63 抽软保底(+7%/抽) / 75% UP / 命定值上限 1

用精确动态规划（马尔可夫链 + 首达时间）计算：
    「在 N 抽内同时拿到 C 个角色 + W 把武器」的概率、
    达成所需抽数的分布 / 期望 / 分位数、
    以及「至少 i 角色 + j 武器」的组合概率矩阵。

策略假设：先抽角色池直到 C 个到手，剩余抽数全部投入武器池（顺序策略）。
这一策略在固定卡池下是随机占优最优的 —— 角色提前完成后不会浪费任何一抽。

用法示例：
    python gacha.py
    python gacha.py --pulls 300 --chars 2 --weapons 1
    python gacha.py --pulls 420 --chars 3 --weapons 2 --char-pity 45 --mc 200000
    python gacha.py --pulls 300 --chars 2 --weapons 1 --weapon-any-up
    python gacha.py --pulls 240 --chars 2 --weapons 1 --json
    python gacha.py --pulls 360 --chars 2 --weapons 1 --csv curve.csv
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass, field, asdict

try:
    import numpy as np
except ImportError:      # 只在 gacha.py 的 DP 里用到；starlight.py 不依赖
    np = None

# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------

DEFAULT_CHAR = dict(
    base=0.006,       # 基础五星率
    soft_start=74,    # 软保底起始抽数
    soft_inc=0.06,    # 软保底每抽增量
    hard=90,          # 硬保底
    up_rate=0.5,      # 小保底出 UP 概率
    rad_p=0.05,       # 捕获明光触发概率
    rad_max=3,        # 连歪 N 次后必中
)
DEFAULT_WEAPON = dict(
    base=0.007,
    soft_start=63,
    soft_inc=0.07,
    hard=80,
    featured_rate=0.75,   # 五星为 UP 武器的概率（两把均分）
    fate_max=1,           # 命定值上限
    accept_any=False,     # True = 接受任意一把 UP（命中率 37.5% → 75%）
)


def p5_at(pity: int, cfg: dict) -> float:
    """第 (pity+1) 抽出五星的概率。pity = 距上次出金已抽的抽数。"""
    n = pity + 1
    hard = int(cfg["hard"])
    if n >= hard:
        return 1.0
    if n >= cfg["soft_start"]:
        return min(1.0, cfg["base"] + cfg["soft_inc"] * (n - cfg["soft_start"] + 1))
    return cfg["base"]


def expected_pity(cfg: dict) -> float:
    """出五星的平均抽数（解析解）。"""
    survive, mean = 1.0, 0.0
    for n in range(1, int(cfg["hard"]) + 1):
        mean += survive
        survive *= 1.0 - p5_at(n - 1, cfg)
    return mean


def five_star_rate(cfg: dict) -> float:
    return 1.0 / expected_pity(cfg)


def long_run_up_rate(cfg: dict) -> float:
    """稳态下「小保底出 UP」的综合概率（含捕获明光与连歪必中）。"""
    S = int(cfg["rad_max"]) + 1
    w = [1.0 if i >= S - 1 else cfg["up_rate"] + (1 - cfg["up_rate"]) * cfg["rad_p"]
         for i in range(S)]
    l = [1.0 - x for x in w]
    pi0, prod = 1.0, 1.0
    for k in range(1, S):
        prod *= l[k - 1]
        pi0 += prod
    pi0 = 1.0 / pi0
    acc, pp = 0.0, pi0
    acc += pp * w[0]
    for k in range(1, S):
        pp *= l[k - 1]
        acc += pp * w[k]
    return acc


# --------------------------------------------------------------------------
# 动态规划：首达曲线  reach[i][t] = P(前 t 抽内已完成至少 i 个目标)
# --------------------------------------------------------------------------

def char_curves(N: int, Cmax: int, cfg: dict, init: dict | None = None) -> np.ndarray:
    """
    状态 (大保底标志 g, 连歪次数 s, 已获得数 c, 垫抽数 p)
    c 达到 Cmax 后吸收（停止抽取）。
    返回 shape=(Cmax+1, N+1) 的 reach。
    """
    init = init or {}
    P = int(cfg["hard"])
    S = int(cfg["rad_max"]) + 1
    CD = int(Cmax) + 1
    p5v = np.array([p5_at(p, cfg) for p in range(P)], dtype=float)
    nr = 1.0 - p5v

    A = np.zeros((2, S, CD, P), dtype=float)
    p0 = min(max(int(init.get("pity", 0)), 0), P - 1)
    g0 = 1 if init.get("guaranteed") else 0
    s0 = min(max(int(init.get("streak", 0)), 0), S - 1)
    A[g0, s0, 0, p0] = 1.0

    reach = np.zeros((CD, N + 1), dtype=float)
    p_up = np.array([1.0 if s >= S - 1 else cfg["up_rate"] + (1 - cfg["up_rate"]) * cfg["rad_p"]
                     for s in range(S)], dtype=float)

    for t in range(N + 1):
        total_c = A.sum(axis=(0, 1, 3))                     # (CD,)
        reach[:, t] = np.cumsum(total_c[::-1])[::-1]
        if t == N:
            break

        B = np.zeros_like(A)
        B[:, :, CD - 1, :] = A[:, :, CD - 1, :]             # 已达成的部分吸收
        act = A.copy()
        act[:, :, CD - 1, :] = 0.0

        # 未出五星：垫抽 +1
        B[:, :, :, 1:] += act[:, :, :, :-1] * nr[None, None, None, :-1]

        five = (act * p5v[None, None, None, :]).sum(axis=-1)   # (2, S, CD)
        # 大保底出金：必为 UP，连歪次数不变
        if CD > 1:
            B[0, :, 1:, 0] += five[1][:, :-1]
        # 小保底出金
        up = five[0] * p_up[:, None]
        miss = five[0] * (1.0 - p_up[:, None])
        if CD > 1:
            B[0, 0, 1:, 0] += up.sum(axis=0)[:-1]
        B[1, 1:, :, 0] += miss[:-1]                          # 歪 → 连歪+1
        B[1, S - 1, :, 0] += miss[S - 1]                     # 封顶
        A = B
    return reach


def weapon_curves(N: int, Wmax: int, cfg: dict, init: dict | None = None) -> np.ndarray:
    """状态 (命定值 f, 已获得数 c, 垫抽数 p)。返回 shape=(Wmax+1, N+1)。"""
    init = init or {}
    P = int(cfg["hard"])
    FD = int(cfg["fate_max"]) + 1
    CD = int(Wmax) + 1
    p5v = np.array([p5_at(p, cfg) for p in range(P)], dtype=float)
    nr = 1.0 - p5v
    pick = cfg["featured_rate"] if cfg.get("accept_any") else cfg["featured_rate"] * 0.5

    A = np.zeros((FD, CD, P), dtype=float)
    p0 = min(max(int(init.get("pity", 0)), 0), P - 1)
    f0 = min(max(int(init.get("fate", 0)), 0), FD - 1)
    A[f0, 0, p0] = 1.0

    reach = np.zeros((CD, N + 1), dtype=float)

    for t in range(N + 1):
        total_c = A.sum(axis=(0, 2))
        reach[:, t] = np.cumsum(total_c[::-1])[::-1]
        if t == N:
            break

        B = np.zeros_like(A)
        B[:, CD - 1, :] = A[:, CD - 1, :]
        act = A.copy()
        act[:, CD - 1, :] = 0.0

        B[:, :, 1:] += act[:, :, :-1] * nr[None, None, :-1]

        five = (act * p5v[None, None, :]).sum(axis=-1)       # (FD, CD)
        hit = five * pick
        hit[FD - 1] = five[FD - 1]                           # 命定值满 → 必中
        if CD > 1:
            B[0, 1:, 0] += hit.sum(axis=0)[:-1]
        if pick < 1:
            miss = five * (1.0 - pick)
            miss[FD - 1] = 0.0
            if FD > 1:
                B[1:, :, 0] += miss[:-1]                     # 歪 → 命定值 +1
            else:
                B[0, :, 0] += miss[0]                        # 无定轨：留在原状态
        A = B
    return reach


# --------------------------------------------------------------------------
# 组合
# --------------------------------------------------------------------------

def density(reach: np.ndarray, i: int, N: int) -> np.ndarray:
    """恰好第 t 抽完成第 i 个目标的首达密度。"""
    r = reach[i, :N + 1]
    d = np.empty_like(r)
    d[0] = r[0]
    d[1:] = np.diff(r)
    return d


def combined_prob(c_reach, w_reach, ci: int, wj: int, n: int) -> float:
    if n < 0:
        return 0.0
    d = density(c_reach, ci, n)
    return float(np.dot(d, w_reach[wj, :n + 1][::-1]))


def combined_curve(c_reach, w_reach, ci: int, wj: int, N: int) -> np.ndarray:
    out = np.empty(N + 1, dtype=float)
    for n in range(N + 1):
        out[n] = combined_prob(c_reach, w_reach, ci, wj, n)
    return out


def stats(curve: np.ndarray) -> dict:
    N = len(curve) - 1
    n = np.arange(N + 1)
    pdf = np.diff(np.concatenate(([0.0], curve)))
    mean = float((n * pdf).sum())
    qs = {}
    for q in (0.5, 0.75, 0.9, 0.95, 0.99):
        idx = int(np.searchsorted(curve, q, side="left"))
        qs[q] = min(idx, N)
    return dict(mean=mean, median=qs[0.5], quantiles=qs)


def horizon(N: int, C: int, W: int, char_cfg: dict, weapon_cfg: dict) -> int:
    """支撑上界：每个目标最多消耗 2 个五星（歪一次 + 保底）。"""
    bound = 2 * int(char_cfg["hard"]) * C + 2 * int(weapon_cfg["hard"]) * W
    return min(4000, max(N, bound))


# --------------------------------------------------------------------------
# 模拟（校验 + 回放）
# --------------------------------------------------------------------------

def char_pull(st: dict, cfg: dict, rng: random.Random) -> dict:
    st["pity"] += 1
    pity = st["pity"]
    if rng.random() >= p5_at(pity - 1, cfg):
        return dict(five=False, pity=pity)
    st["pity"] = 0
    if st["g"]:
        st["g"] = False
        return dict(five=True, up=True, kind="guaranteed", pity=pity)
    if st["s"] >= cfg["rad_max"]:
        st["s"] = 0
        return dict(five=True, up=True, kind="radiance-max", pity=pity)
    if rng.random() < cfg["rad_p"]:
        st["s"] = 0
        return dict(five=True, up=True, kind="radiance", pity=pity)
    if rng.random() < cfg["up_rate"]:
        st["s"] = 0
        return dict(five=True, up=True, kind="up", pity=pity)
    st["g"] = True
    st["s"] = min(st["s"] + 1, cfg["rad_max"])
    return dict(five=True, up=False, kind="off", pity=pity)


def weapon_pull(st: dict, cfg: dict, rng: random.Random) -> dict:
    st["pity"] += 1
    pity = st["pity"]
    if rng.random() >= p5_at(pity - 1, cfg):
        return dict(five=False, pity=pity)
    st["pity"] = 0
    pick = cfg["featured_rate"] if cfg.get("accept_any") else cfg["featured_rate"] * 0.5
    if st["f"] >= cfg["fate_max"] > 0:
        st["f"] = 0
        return dict(five=True, target=True, kind="fate", pity=pity)
    if rng.random() < pick:
        st["f"] = 0
        return dict(five=True, target=True, kind="target", pity=pity)
    st["f"] = min(st["f"] + 1, cfg["fate_max"])
    return dict(five=True, target=False, kind="off", pity=pity)


def simulate_once(N, C, W, char_cfg, weapon_cfg, init=None, rng=None, record=False):
    init = init or {}
    rng = rng or random.Random()
    cs = dict(pity=int(init.get("char_pity", 0)),
              g=bool(init.get("char_guaranteed", False)),
              s=int(init.get("char_streak", 0)))
    ws = dict(pity=int(init.get("weapon_pity", 0)),
              f=int(init.get("weapon_fate", 0)))
    chars = weapons = used = 0
    events = []

    while used < N and chars < C:
        used += 1
        r = char_pull(cs, char_cfg, rng)
        if r["five"]:
            if r["up"]:
                chars += 1
            if record:
                events.append(dict(pull=used, banner="char", **r))
    char_used = used

    while used < N and weapons < W:
        used += 1
        r = weapon_pull(ws, weapon_cfg, rng)
        if r["five"]:
            if r["target"]:
                weapons += 1
            if record:
                events.append(dict(pull=used, banner="weapon", **r))
    weapon_used = used - char_used

    done = chars >= C and weapons >= W
    return dict(done=done, pulls=used if done else None, chars=chars, weapons=weapons,
                char_used=char_used, weapon_used=weapon_used, events=events)


def monte_carlo(N, C, W, char_cfg, weapon_cfg, init=None, runs=100_000, seed=20240828):
    rng = random.Random(seed)
    ok = 0
    total = 0
    total_sq = 0
    for _ in range(runs):
        r = simulate_once(N, C, W, char_cfg, weapon_cfg, init, rng)
        if r["done"]:
            ok += 1
            total += r["pulls"]
            total_sq += r["pulls"] ** 2
    p = ok / runs
    se = math.sqrt(max(0.0, p * (1 - p) / runs))
    mean = total / ok if ok else float("nan")
    sd = math.sqrt(max(0.0, total_sq / ok - mean ** 2)) if ok > 1 else float("nan")
    return dict(runs=runs, p=p, se=se, mean_pulls=mean, sd_pulls=sd)


# --------------------------------------------------------------------------
# 展示
# --------------------------------------------------------------------------

BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values, width=56) -> str:
    if len(values) > width:
        step = len(values) / width
        values = [values[min(len(values) - 1, int(i * step))] for i in range(width)]
    return "".join(BLOCKS[min(7, max(0, int(v * 7.999)))] for v in values)


def bar(p: float, width=24) -> str:
    n = int(round(p * width))
    return "█" * n + "░" * (width - n)


def pct(v, d=2) -> str:
    return f"{v * 100:.{d}f}%"


def build_report(args_, char_cfg, weapon_cfg, N, C, W, init):
    H = horizon(N, C, W, char_cfg, weapon_cfg)
    c_reach = char_curves(H, C, char_cfg, dict(pity=init["char_pity"],
                                                guaranteed=init["char_guaranteed"],
                                                streak=init["char_streak"]))
    w_reach = weapon_curves(H, W, weapon_cfg, dict(pity=init["weapon_pity"],
                                                   fate=init["weapon_fate"]))
    curve = combined_curve(c_reach, w_reach, C, W, H)
    st = stats(curve)

    mean_c = float((np.arange(H + 1) * density(c_reach, C, H)).sum())
    mean_w = float((np.arange(H + 1) * density(w_reach, W, H)).sum())

    matrix = [[combined_prob(c_reach, w_reach, i, j, N) for j in range(W + 1)]
              for i in range(C + 1)]

    lines = []
    add = lines.append
    add("原神抽卡期望计算 · 精确动态规划")
    add("═" * 64)
    add(f"目标        {C} 个角色 + {W} 把武器")
    add(f"预算        {N} 抽")
    add(f"初始状态    角色池：{'大保底' if init['char_guaranteed'] else '小保底'}"
        f" · 垫 {init['char_pity']} 抽 · 连歪 {init['char_streak']} 次")
    add(f"            武器池：垫 {init['weapon_pity']} 抽 · 命定值 {init['weapon_fate']}")
    add("")
    add(f"达成概率    {pct(curve[N])}    （{N} 抽内全部达成）  {bar(curve[N])}")
    add(f"期望抽数    {st['mean']:.1f} 抽    中位数 {st['median']} 抽")
    q = st["quantiles"]
    add(f"分位数      50% {q[0.5]} · 75% {q[0.75]} · 90% {q[0.9]} · 95% {q[0.95]} · 99% {q[0.99]}")
    add("")
    add(f"角色单独    {pct(c_reach[C, N])}   期望 {mean_c:.1f} 抽")
    wpick = weapon_cfg["featured_rate"] * (1.0 if weapon_cfg.get("accept_any") else 0.5)
    add(f"武器单独    {pct(1.0 if W == 0 else w_reach[W, N])}   期望 {mean_w:.1f} 抽（从零开始）· "
        f"单金命中 {pct(wpick, 1)}")
    add(f"角色未达成  {pct(1 - c_reach[C, N])}   角色达成但武器不足 "
        f"{pct(max(0.0, c_reach[C, N] - curve[N]))}")
    add("")
    add("累计概率曲线（0 → {} 抽）".format(H))
    add("  " + sparkline([float(x) for x in curve[:: max(1, H // 200)]]))
    add(f"  0 抽 {pct(curve[0])} · {N} 抽 {pct(curve[N])} · "
        f"{int(q[0.9])} 抽 {pct(curve[int(q[0.9])])} · {int(q[0.99])} 抽 {pct(curve[int(q[0.99])])}")
    add("")
    add("目标组合矩阵（行 = 至少 i 角色，列 = 至少 j 武器）")
    head = "         " + "".join(f"  ≥{j} 武器   " for j in range(W + 1))
    add(head)
    for i in range(C + 1):
        add(f"  ≥{i} 角色  " + "".join(f"   {pct(matrix[i][j], 1):>7}  " for j in range(W + 1)))
    add("")
    if C > 0 and W > 0:
        best_a, best_v = 0, -1.0
        for a in range(N + 1):
            v = c_reach[C, a] * w_reach[W, N - a]
            if v > best_v:
                best_v, best_a = v, a
        add(f"对照        若固定分配 {best_a} 抽角色 + {N - best_a} 抽武器，最优也只有 {pct(best_v)}；")
        add(f"            顺序策略为 {pct(curve[N])}，高出 {pct(curve[N] - best_v)}。")
        add("")
    add(f"口径检查    角色池综合五星率 {pct(five_star_rate(char_cfg))} · "
        f"平均 {expected_pity(char_cfg):.2f} 抽")
    add(f"            武器池综合五星率 {pct(five_star_rate(weapon_cfg))} · "
        f"平均 {expected_pity(weapon_cfg):.2f} 抽")
    add(f"            小保底出 UP 综合 {pct(long_run_up_rate(char_cfg))}（官方口径 55%）")
    return "\n".join(lines), curve, matrix, st, c_reach, w_reach


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="原神抽卡期望计算：N 抽内达成 C 角色 + W 武器的概率",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--pulls", type=int, default=360, help="总投入抽数（默认 360）")
    p.add_argument("--chars", type=int, default=2, help="目标角色个数（默认 2）")
    p.add_argument("--weapons", type=int, default=1, help="目标武器个数（默认 1）")
    p.add_argument("--char-pity", type=int, default=0, help="角色池已垫抽数")
    p.add_argument("--char-guaranteed", action="store_true", help="首金为大保底")
    p.add_argument("--char-streak", type=int, default=0, help="角色池连歪次数 0-3")
    p.add_argument("--weapon-pity", type=int, default=0, help="武器池已垫抽数")
    p.add_argument("--weapon-fate", type=int, default=0, help="武器池命定值 0-1")
    p.add_argument("--weapon-any-up", action="store_true",
                   help="接受任意一把 UP 武器（单金命中 37.5%% → 75%%）")
    p.add_argument("--mc", type=int, default=0, help="额外跑蒙特卡洛校验（次数）")
    p.add_argument("--seed", type=int, default=20240828, help="蒙特卡洛随机种子")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.add_argument("--csv", metavar="PATH", help="把累计概率曲线写入 CSV")
    p.add_argument("--replay", action="store_true", help="打印一次模拟回放")
    p.add_argument("--char-base", type=float, default=None, help="覆盖角色池基础五星率（%%）")
    p.add_argument("--char-hard", type=int, default=None, help="覆盖角色池硬保底")
    p.add_argument("--weapon-base", type=float, default=None, help="覆盖武器池基础五星率（%%）")
    p.add_argument("--weapon-hard", type=int, default=None, help="覆盖武器池硬保底")
    p.add_argument("--rad-p", type=float, default=None, help="覆盖捕获明光概率（%%）")
    p.add_argument("--rad-max", type=int, default=None, help="覆盖连歪必中次数")
    p.add_argument("--fate-max", type=int, default=None, help="覆盖武器池命定值上限")
    a = p.parse_args(argv)

    if a.pulls < 1:
        p.error("--pulls 必须 ≥ 1")
    if not (0 <= a.chars <= 7):
        p.error("--chars 取值范围 0-7")
    if not (0 <= a.weapons <= 5):
        p.error("--weapons 取值范围 0-5")
    return a


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    a = parse_args(argv)
    char_cfg = dict(DEFAULT_CHAR)
    weapon_cfg = dict(DEFAULT_WEAPON)
    if a.char_base is not None:
        char_cfg["base"] = a.char_base / 100
    if a.char_hard is not None:
        char_cfg["hard"] = a.char_hard
    if a.weapon_base is not None:
        weapon_cfg["base"] = a.weapon_base / 100
    if a.weapon_hard is not None:
        weapon_cfg["hard"] = a.weapon_hard
    if a.rad_p is not None:
        char_cfg["rad_p"] = a.rad_p / 100
    if a.rad_max is not None:
        char_cfg["rad_max"] = a.rad_max
    if a.fate_max is not None:
        weapon_cfg["fate_max"] = a.fate_max
    if a.weapon_any_up:
        weapon_cfg["accept_any"] = True

    init = dict(char_pity=max(0, a.char_pity), char_guaranteed=bool(a.char_guaranteed),
                char_streak=max(0, a.char_streak), weapon_pity=max(0, a.weapon_pity),
                weapon_fate=max(0, a.weapon_fate))

    text, curve, matrix, st, c_reach, w_reach = build_report(
        a, char_cfg, weapon_cfg, a.pulls, a.chars, a.weapons, init)

    if a.mc:
        mc = monte_carlo(a.pulls, a.chars, a.weapons, char_cfg, weapon_cfg, init,
                         runs=a.mc, seed=a.seed)
        text += (f"\n蒙特卡洛    {a.mc:,} 次 → {pct(mc['p'])}（±{mc['se'] * 100:.2f}%，1σ）"
                 f" · 成功均值 {mc['mean_pulls']:.0f} 抽\n")

    if a.replay:
        rng = random.Random(a.seed + 7)
        r = simulate_once(a.pulls, a.chars, a.weapons, char_cfg, weapon_cfg, init,
                          rng=rng, record=True)
        kind_txt = {"guaranteed": "大保底出金", "radiance": "捕获明光",
                    "radiance-max": "连歪必中", "up": "小保底没歪", "off": "歪了",
                    "fate": "命定值保底", "target": "命中定轨"}
        text += "\n单次回放（seed=%d）\n" % (a.seed + 7)
        for ev in r["events"]:
            name = kind_txt.get(ev["kind"], ev["kind"])
            banner = "角色池" if ev["banner"] == "char" else "武器池"
            got = ev.get("up", ev.get("target"))
            text += (f"  第 {ev['pull']:>4} 抽  {banner}  {name:<6}"
                     f"{'✓' if got else '✗'}  （{ev['pity']} 抽出金）\n")
        text += ("  结果：%s\n" % ("达成，共 %d 抽" % r["pulls"] if r["done"]
                                  else "未达成（%d/%d 角色，%d/%d 武器）"
                                       % (r["chars"], a.chars, r["weapons"], a.weapons)))

    if a.json:
        out = dict(
            pulls=a.pulls, chars=a.chars, weapons=a.weapons,
            probability=float(curve[a.pulls]),
            expected_pulls=st["mean"], median_pulls=int(st["median"]),
            quantiles={str(k): int(v) for k, v in st["quantiles"].items()},
            p_char=float(c_reach[a.chars, a.pulls]),
            matrix=[[float(x) for x in row] for row in matrix],
            config=dict(char=char_cfg, weapon=weapon_cfg),
            init=init,
        )
        if a.mc:
            out["mc"] = dict(runs=a.mc, p=float(mc["p"]), se=float(mc["se"]))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(text)

    if a.csv:
        with open(a.csv, "w", encoding="utf-8-sig") as f:
            f.write("pulls,probability\n")
            for i, v in enumerate(curve):
                f.write(f"{i},{v:.8f}\n")
        print(f"\n曲线已写入 {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
