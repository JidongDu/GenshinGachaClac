/*!
 * Gacha Engine — 原神风格双池概率计算
 *   角色池：0.6% / 90 硬保底 / 74 抽软保底 / 小保底 50% / 捕获明光 / 连歪必中
 *   武器池：0.7% / 80 硬保底 / 63 抽软保底 / 75% UP / 定轨命定值
 *
 * 纯函数、零依赖；浏览器（window.GachaEngine）与 Node（module.exports）通用。
 *
 * 核心方法：把「达成 C 个角色 + W 把武器」拆成两条相互独立的马尔可夫链，
 * 用精确 DP 求出「恰好 t 抽时完成第 i 个目标」的首达密度，
 * 再按「先角色后武器」的顺序策略做卷积 —— 该策略在给定卡池下是随机占优最优的。
 */
(function (root) {
  'use strict';

  var DEFAULT_CONFIG = Object.freeze({
    char: Object.freeze({
      base: 0.006,      // 基础五星率
      softStart: 74,    // 软保底起始抽数（第 74 抽）
      softInc: 0.06,    // 每抽增量
      hard: 90,         // 硬保底
      upRate: 0.5,      // 小保底出 UP 的概率
      radP: 0.05,       // 捕获明光触发概率（小保底时）
      radMax: 3         // 连歪 N 次后必中
    }),
    weapon: Object.freeze({
      base: 0.007,
      softStart: 63,
      softInc: 0.07,
      hard: 80,
      featuredRate: 0.75, // 五星为 UP 武器的概率（两把均分）
      fateMax: 1,         // 命定值上限（5.0 起为 1）
      acceptAny: false    // true = 接受任意一把 UP 武器（命中率 37.5% → 75%）
    })
  });

  function num(v, d) {
    v = Number(v);
    return Number.isFinite(v) ? v : d;
  }

  function resolveConfig(partial) {
    var p = partial || {};
    var c = Object.assign({}, DEFAULT_CONFIG.char, p.char || {});
    var w = Object.assign({}, DEFAULT_CONFIG.weapon, p.weapon || {});
    // 基本合法性
    c.base = Math.min(Math.max(num(c.base, 0.006), 0), 1);
    c.softStart = Math.max(Math.round(num(c.softStart, 74)), 1);
    c.softInc = Math.max(num(c.softInc, 0.06), 0);
    c.hard = Math.max(Math.round(num(c.hard, 90)), c.softStart);
    c.upRate = Math.min(Math.max(num(c.upRate, 0.5), 0), 1);
    c.radP = Math.min(Math.max(num(c.radP, 0.05), 0), 1);
    c.radMax = Math.max(Math.round(num(c.radMax, 3)), 1);
    w.base = Math.min(Math.max(num(w.base, 0.007), 0), 1);
    w.softStart = Math.max(Math.round(num(w.softStart, 63)), 1);
    w.softInc = Math.max(num(w.softInc, 0.07), 0);
    w.hard = Math.max(Math.round(num(w.hard, 80)), w.softStart);
    w.featuredRate = Math.min(Math.max(num(w.featuredRate, 0.75), 0), 1);
    w.fateMax = Math.max(Math.round(num(w.fateMax, 1)), 0);
    w.acceptAny = !!w.acceptAny;
    return { char: c, weapon: w };
  }

  /** 第 (pity+1) 抽出五星的概率 */
  function p5At(pity, cfg) {
    var n = pity + 1;
    if (n >= cfg.hard) return 1;
    if (n >= cfg.softStart) {
      var v = cfg.base + cfg.softInc * (n - cfg.softStart + 1);
      return v > 1 ? 1 : v;
    }
    return cfg.base;
  }

  /** 出五星的平均抽数（解析解） */
  function expectedPity(cfg) {
    var P = Math.round(cfg.hard);
    var prod = 1, mean = 0;
    for (var n = 1; n <= P; n++) {
      mean += prod;
      prod *= 1 - p5At(n - 1, cfg);
    }
    return mean;
  }

  /** 含保底的五星综合概率 */
  function fiveStarRate(cfg) {
    return 1 / expectedPity(cfg);
  }

  /* ------------------------------------------------------------------ *
   * 角色池 DP
   * 状态：(pity, 大保底, 连歪次数, 已获得数量)
   * 已获得数量达到 Cmax 后吸收（停止抽取）。
   * ------------------------------------------------------------------ */
  function charCurves(N, Cmax, cfg, init) {
    N = Math.max(0, Math.round(N));
    Cmax = Math.max(0, Math.round(Cmax));
    init = init || {};
    var P = Math.max(1, Math.round(cfg.hard));
    var S = Math.max(1, Math.round(cfg.radMax)) + 1;
    var CD = Cmax + 1;
    var p5v = new Float64Array(P);
    for (var p = 0; p < P; p++) p5v[p] = p5At(p, cfg);

    var idx = function (pp, g, s, c) { return (((pp * 2 + g) * S + s) * CD + c); };
    var size = P * 2 * S * CD;
    var A = new Float64Array(size);
    var B = new Float64Array(size);

    var p0 = Math.min(Math.max(Math.round(num(init.pity, 0)), 0), P - 1);
    var g0 = init.guaranteed ? 1 : 0;
    var s0 = Math.min(Math.max(Math.round(num(init.streak, 0)), 0), S - 1);
    A[idx(p0, g0, s0, 0)] = 1;

    var reach = [];
    for (var i = 0; i <= Cmax; i++) reach.push(new Float64Array(N + 1));
    var tot = new Float64Array(CD);

    for (var t = 0; t <= N; t++) {
      tot.fill(0);
      for (var pp = 0; pp < P; pp++) {
        for (var g = 0; g < 2; g++) {
          for (var s = 0; s < S; s++) {
            var base = ((pp * 2 + g) * S + s) * CD;
            for (var c = 0; c < CD; c++) {
              var v = A[base + c];
              if (v !== 0) tot[c] += v;
            }
          }
        }
      }
      var acc = 0;
      for (var k = Cmax; k >= 0; k--) { acc += tot[k]; reach[k][t] = acc; }
      if (t === N) break;

      B.fill(0);
      for (pp = 0; pp < P; pp++) {
        var pr = p5v[pp], nr = 1 - pr;
        var np = pp + 1 < P ? pp + 1 : P - 1;
        for (g = 0; g < 2; g++) {
          for (s = 0; s < S; s++) {
            var b0 = ((pp * 2 + g) * S + s) * CD;
            var nb = ((np * 2 + g) * S + s) * CD;
            // 该状态下五星后出 UP 的概率：大保底恒为 1；连歪到上限也为 1
            var pUp = (g === 1 || s >= S - 1)
              ? 1
              : (cfg.upRate + (1 - cfg.upRate) * cfg.radP);
            for (c = 0; c < CD; c++) {
              var m = A[b0 + c];
              if (m === 0) continue;
              if (c === Cmax) { B[b0 + c] += m; continue; } // 已达成，吸收
              if (nr > 0) B[nb + c] += m * nr;
              if (pr > 0) {
                if (g === 1) {
                  B[idx(0, 0, s, c + 1)] += m * pr;          // 大保底到手
                } else if (pUp >= 1) {
                  B[idx(0, 0, 0, c + 1)] += m * pr;          // 连歪必中
                } else {
                  B[idx(0, 0, 0, c + 1)] += m * pr * pUp;    // 小保底没歪
                  B[idx(0, 1, Math.min(s + 1, S - 1), c)] += m * pr * (1 - pUp); // 歪
                }
              }
            }
          }
        }
      }
      var tmp = A; A = B; B = tmp;
    }

    var expCopies = 0;
    for (var e = 1; e <= Cmax; e++) expCopies += reach[e][N];
    return { reach: reach, expCopies: expCopies, N: N, Cmax: Cmax };
  }

  /* ------------------------------------------------------------------ *
   * 武器池 DP
   * 状态：(pity, 命定值, 已获得数量)
   * ------------------------------------------------------------------ */
  function weaponCurves(N, Wmax, cfg, init) {
    N = Math.max(0, Math.round(N));
    Wmax = Math.max(0, Math.round(Wmax));
    init = init || {};
    var P = Math.max(1, Math.round(cfg.hard));
    var FD = Math.max(1, Math.round(cfg.fateMax) + 1); // 命定值 0..fateMax
    var CD = Wmax + 1;
    var p5v = new Float64Array(P);
    for (var p = 0; p < P; p++) p5v[p] = p5At(p, cfg);

    var idx = function (pp, f, c) { return ((pp * FD + f) * CD + c); };
    var size = P * FD * CD;
    var A = new Float64Array(size);
    var B = new Float64Array(size);

    var p0 = Math.min(Math.max(Math.round(num(init.pity, 0)), 0), P - 1);
    var f0 = Math.min(Math.max(Math.round(num(init.fate, 0)), 0), FD - 1);
    A[idx(p0, f0, 0)] = 1;

    var reach = [];
    for (var i = 0; i <= Wmax; i++) reach.push(new Float64Array(N + 1));
    var tot = new Float64Array(CD);
    // 单抽五星算作达成的概率：只认定轨那把 → 两把均分；接受任意 UP → featuredRate
    var pick = cfg.acceptAny ? cfg.featuredRate : cfg.featuredRate * 0.5;

    for (var t = 0; t <= N; t++) {
      tot.fill(0);
      for (var pp = 0; pp < P; pp++) {
        for (var f = 0; f < FD; f++) {
          var base = (pp * FD + f) * CD;
          for (var c = 0; c < CD; c++) {
            var v = A[base + c];
            if (v !== 0) tot[c] += v;
          }
        }
      }
      var acc = 0;
      for (var k = Wmax; k >= 0; k--) { acc += tot[k]; reach[k][t] = acc; }
      if (t === N) break;

      B.fill(0);
      for (pp = 0; pp < P; pp++) {
        var pr = p5v[pp], nr = 1 - pr;
        var np = pp + 1 < P ? pp + 1 : P - 1;
        for (f = 0; f < FD; f++) {
          var b0 = (pp * FD + f) * CD;
          var nb = (np * FD + f) * CD;
          var guaranteed = (FD > 1 && f >= FD - 1);
          for (c = 0; c < CD; c++) {
            var m = A[b0 + c];
            if (m === 0) continue;
            if (c === Wmax) { B[b0 + c] += m; continue; }
            if (nr > 0) B[nb + c] += m * nr;
            if (pr > 0) {
              if (guaranteed) {
                B[idx(0, 0, c + 1)] += m * pr;
              } else if (pick <= 0) {
                B[idx(0, Math.min(f + 1, FD - 1), c)] += m * pr;
              } else {
                B[idx(0, 0, c + 1)] += m * pr * pick;
                B[idx(0, Math.min(f + 1, FD - 1), c)] += m * pr * (1 - pick);
              }
            }
          }
        }
      }
      var tmp = A; A = B; B = tmp;
    }

    var expCopies = 0;
    for (var e = 1; e <= Wmax; e++) expCopies += reach[e][N];
    return { reach: reach, expCopies: expCopies, N: N, Wmax: Wmax };
  }

  /** 首达密度：恰好 t 抽完成第 i 个目标 */
  function density(cReach, i, N) {
    var d = new Float64Array(N + 1);
    var r = cReach[i];
    d[0] = r[0];
    for (var t = 1; t <= N; t++) d[t] = r[t] - r[t - 1];
    return d;
  }

  /** 顺序策略：P(角色 ≥ ci 且武器 ≥ wj  在 n 抽内) */
  function combinedProb(cReach, wReach, ci, wj, n) {
    var d = density(cReach, ci, n);
    var wr = wReach[wj];
    var sum = 0;
    for (var t = 0; t <= n; t++) {
      var x = d[t];
      if (x !== 0) sum += x * wr[n - t];
    }
    return sum;
  }

  /** 顺序策略：0..N 的达成概率曲线 */
  function combinedCurve(cReach, wReach, ci, wj, N) {
    var d = density(cReach, ci, N);
    var wr = wReach[wj];
    var out = new Float64Array(N + 1);
    var sum = 0;
    for (var n = 0; n <= N; n++) {
      sum = 0;
      for (var t = 0; t <= n; t++) {
        var x = d[t];
        if (x !== 0) sum += x * wr[n - t];
      }
      out[n] = sum;
    }
    return out;
  }

  /** 0..N 的 CDF 统计量 */
  function stats(curve) {
    var N = curve.length - 1;
    var mean = 0, prev = 0;
    for (var n = 0; n <= N; n++) {
      mean += n * (curve[n] - prev);
      prev = curve[n];
    }
    var qs = [0.5, 0.75, 0.9, 0.95, 0.99];
    var q = {};
    for (var i = 0; i < qs.length; i++) q[qs[i]] = quantile(curve, qs[i]);
    return { mean: mean, median: q[0.5], quantiles: q };
  }

  function quantile(curve, prob) {
    for (var n = 0; n < curve.length; n++) if (curve[n] >= prob) return n;
    return curve.length - 1;
  }

  /* ------------------------------------------------------------------ *
   * 单次模拟（同一套规则，用于回放与蒙特卡洛校验）
   * ------------------------------------------------------------------ */
  function charPull(st, cfg, rng) {
    var pity = ++st.pity;
    var p = p5At(pity - 1, cfg);
    if (rng() >= p) return { five: false, pity: pity };
    st.pity = 0;
    if (st.g) {
      st.g = false;
      return { five: true, up: true, kind: 'guaranteed', pity: pity };
    }
    var s = st.s;
    if (s >= cfg.radMax) {
      st.s = 0;
      return { five: true, up: true, kind: 'radiance-max', pity: pity };
    }
    if (rng() < cfg.radP) {
      st.s = 0;
      return { five: true, up: true, kind: 'radiance', pity: pity };
    }
    if (rng() < cfg.upRate) {
      st.s = 0;
      return { five: true, up: true, kind: 'up', pity: pity };
    }
    st.g = true;
    st.s = Math.min(s + 1, cfg.radMax);
    return { five: true, up: false, kind: 'off', pity: pity };
  }

  function weaponPull(st, cfg, rng) {
    var pity = ++st.pity;
    var p = p5At(pity - 1, cfg);
    if (rng() >= p) return { five: false, pity: pity };
    st.pity = 0;
    var pick = cfg.acceptAny ? cfg.featuredRate : cfg.featuredRate * 0.5;
    if (st.f >= cfg.fateMax) {
      st.f = 0;
      return { five: true, target: true, kind: 'fate', pity: pity };
    }
    if (rng() < pick) {
      st.f = 0;
      return { five: true, target: true, kind: 'target', pity: pity };
    }
    st.f = Math.min(st.f + 1, cfg.fateMax);
    return { five: true, target: false, kind: 'off', pity: pity };
  }

  function simulateOnce(N, C, W, cfg, init, rng) {
    rng = rng || Math.random;
    init = init || {};
    var cs = {
      pity: Math.round(num(init.charPity, 0)),
      g: !!init.charGuaranteed,
      s: Math.round(num(init.charStreak, 0))
    };
    var ws = {
      pity: Math.round(num(init.weaponPity, 0)),
      f: Math.round(num(init.weaponFate, 0))
    };
    var chars = 0, weapons = 0, used = 0;
    var events = [];

    while (used < N && chars < C) {
      used++;
      var r = charPull(cs, cfg.char, rng);
      if (r.five) {
        if (r.up) chars++;
        events.push({ pull: used, banner: 'char', kind: r.kind, up: !!r.up, pity: r.pity });
      }
    }
    var charUsed = used;

    while (used < N && weapons < W) {
      used++;
      var rw = weaponPull(ws, cfg.weapon, rng);
      if (rw.five) {
        if (rw.target) weapons++;
        events.push({ pull: used, banner: 'weapon', kind: rw.kind, target: !!rw.target, pity: rw.pity });
      }
    }

    var done = chars >= C && weapons >= W;
    return {
      events: events,
      done: done,
      pulls: done ? used : null,
      chars: chars,
      weapons: weapons,
      charUsed: charUsed,
      weaponUsed: used - charUsed
    };
  }

  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function monteCarlo(N, C, W, cfg, init, K, seed) {
    var rng = (seed === undefined || seed === null) ? Math.random : mulberry32(seed);
    var ok = 0, sum = 0, sumSq = 0;
    for (var i = 0; i < K; i++) {
      var r = simulateOnce(N, C, W, cfg, init, rng);
      if (r.done) { ok++; sum += r.pulls; sumSq += r.pulls * r.pulls; }
    }
    var p = ok / K;
    var mean = ok ? sum / ok : NaN;
    var sd = ok > 1 ? Math.sqrt(Math.max(0, sumSq / ok - mean * mean)) : NaN;
    return {
      runs: K,
      p: p,
      se: Math.sqrt(Math.max(0, p * (1 - p)) / K),
      meanPulls: mean,
      sdPulls: sd
    };
  }

  /* ------------------------------------------------------------------ *
   * 稳态「小保底出 UP」综合概率（含捕获明光与连歪必中）
   * ------------------------------------------------------------------ */
  function longRunUpRate(cfg) {
    var S = Math.max(1, Math.round(cfg.radMax)) + 1;
    var w = [], l = [], i;
    for (i = 0; i < S; i++) {
      var pUp = (i >= S - 1) ? 1 : (cfg.upRate + (1 - cfg.upRate) * cfg.radP);
      w.push(pUp);
      l.push(1 - pUp);
    }
    var pi0 = 1, prod = 1, k;
    for (k = 1; k < S; k++) { prod *= l[k - 1]; pi0 += prod; }
    pi0 = 1 / pi0;
    var acc = 0, pp = pi0;
    acc += pp * w[0];
    for (k = 1; k < S; k++) {
      pp *= l[k - 1];
      acc += pp * w[k];
    }
    return acc;
  }


  /* ================================================================== *
   * 双角色池 / 双武器池
   *   一期 = 角色池① + 角色池②（共享垫抽与大保底）+ 武器池（两把 UP）
   *   目标 = 角色①a 个 + 角色②b 个 + 武器①p 把 + 武器②q 把
   *
   *   策略仍是「先把角色拿齐，再抽武器」；角色①拿满后自动切到②，
   *   武器①拿满后自动把定轨切到②。
   * ================================================================== */

  /** 角色侧：返回首达密度 pdf[t] = P(恰好 t 抽时 a/b 都达成) */
  function charPassage2(N, A, B, cfg, init, opts) {
    A = Math.max(0, A | 0); B = Math.max(0, B | 0);
    opts = opts || {};
    var stdCount = Math.max(1, opts.stdCount | 0);
    // UP 角色本身在常驻池里时，歪了也可能正好是他
    var pStdA = opts.upAInStd ? 1 / stdCount : 0;
    var pStdB = opts.upBInStd ? 1 / stdCount : 0;
    var pdf = new Float64Array(N + 1);
    if (A === 0 && B === 0) { pdf[0] = 1; return pdf; }
    init = init || {};
    var P = Math.max(1, Math.round(cfg.hard));
    var S = Math.max(1, Math.round(cfg.radMax)) + 1;
    var I = A + 1, J = B + 1, CD = I * J;
    var p5v = new Float64Array(P);
    for (var q = 0; q < P; q++) p5v[q] = p5At(q, cfg);
    var size = P * 2 * S * CD;
    var A1 = new Float64Array(size), B1 = new Float64Array(size);
    var idx = function (p, g, s, i, j) { return (((p * 2 + g) * S + s) * CD + i * J + j); };
    var p0 = Math.min(Math.max(Math.round(num(init.pity, 0)), 0), P - 1);
    var g0 = init.guaranteed ? 1 : 0;
    var s0 = Math.min(Math.max(Math.round(num(init.streak, 0)), 0), S - 1);
    A1[idx(p0, g0, s0, 0, 0)] = 1;

    for (var t = 0; t <= N; t++) {
      var done = 0;
      B1.fill(0);
      for (var p = 0; p < P; p++) {
        var pr = p5v[p], nr = 1 - pr;
        var np = p + 1 < P ? p + 1 : P - 1;
        for (var g = 0; g < 2; g++) {
          for (var s = 0; s < S; s++) {
            var pUp = (g === 1 || s >= S - 1) ? 1
              : (cfg.upRate + (1 - cfg.upRate) * cfg.radP);
            for (var i = 0; i <= A; i++) {
              for (var j = 0; j <= B; j++) {
                var v = A1[idx(p, g, s, i, j)];
                if (v === 0) continue;
                if (i === A && j === B) { done += v; continue; }
                if (nr > 0) B1[idx(np, g, s, i, j)] += v * nr;
                if (pr <= 0) continue;
                var ni = i, nj = j;
                if (g === 1) {
                  if (i < A) ni = i + 1; else nj = j + 1;
                  B1[idx(0, 0, s, ni, nj)] += v * pr;
                } else if (pUp >= 1) {
                  if (i < A) ni = i + 1; else nj = j + 1;
                  B1[idx(0, 0, 0, ni, nj)] += v * pr;
                } else {
                  if (i < A) ni = i + 1; else nj = j + 1;
                  B1[idx(0, 0, 0, ni, nj)] += v * pr * pUp;
                  // 歪了：先看这个常驻角色是不是正好就是 UP 角色
                  var pS = (i < A) ? pStdA : pStdB;
                  if (pS > 0) {
                    var hi = i, hj = j;
                    if (i < A) hi = Math.min(i + 1, A); else hj = Math.min(j + 1, B);
                    B1[idx(0, 1, Math.min(s + 1, S - 1), hi, hj)] += v * pr * (1 - pUp) * pS;
                  }
                  if (pS < 1) {
                    B1[idx(0, 1, Math.min(s + 1, S - 1), i, j)] += v * pr * (1 - pUp) * (1 - pS);
                  }
                }
              }
            }
          }
        }
      }
      pdf[t] = done;
      if (t === N) break;
      var tmp = A1; A1 = B1; B1 = tmp;
    }
    return pdf;
  }

  /** 武器侧：返回首达密度 pdf[t] = P(恰好 t 抽时 p/q 都达成) */
  function weaponPassage2(N, P_, Q, cfg, init, opts) {
    P_ = Math.max(0, P_ | 0); Q = Math.max(0, Q | 0);
    opts = opts || {};
    var stdWCount = Math.max(1, opts.stdWCount | 0);
    var pStdW = 0.25 / stdWCount;   // 25% 常驻分支里正好抽到某一把的概率
    var pdf = new Float64Array(N + 1);
    if (P_ === 0 && Q === 0) { pdf[0] = 1; return pdf; }
    init = init || {};
    var PM = Math.max(1, Math.round(cfg.hard));
    var FD = Math.max(1, Math.round(cfg.fateMax) + 1);
    var I = P_ + 1, J = Q + 1, CD = I * J;
    var p5v = new Float64Array(PM);
    for (var q = 0; q < PM; q++) p5v[q] = p5At(q, cfg);
    var pick = cfg.acceptAny ? cfg.featuredRate : cfg.featuredRate * 0.5;
    var size = PM * FD * CD;
    var A1 = new Float64Array(size), B1 = new Float64Array(size);
    var idx = function (p, f, i, j) { return ((p * FD + f) * CD + i * J + j); };
    var p0 = Math.min(Math.max(Math.round(num(init.pity, 0)), 0), PM - 1);
    var f0 = Math.min(Math.max(Math.round(num(init.fate, 0)), 0), FD - 1);
    A1[idx(p0, f0, 0, 0)] = 1;

    for (var t = 0; t <= N; t++) {
      var done = 0;
      B1.fill(0);
      for (var p = 0; p < PM; p++) {
        var pr = p5v[p], nr = 1 - pr;
        var np = p + 1 < PM ? p + 1 : PM - 1;
        for (var f = 0; f < FD; f++) {
          var guaranteed = (FD > 1 && f >= FD - 1);
          for (var i = 0; i <= P_; i++) {
            for (var j = 0; j <= Q; j++) {
              var v = A1[idx(p, f, i, j)];
              if (v === 0) continue;
              if (i === P_ && j === Q) { done += v; continue; }
              if (nr > 0) B1[idx(np, f, i, j)] += v * nr;
              if (pr <= 0) continue;
              var firstTarget = (i < P_);   // 定轨：先①后②
              // 定轨武器 / 另一把 UP 各自的命中概率（含“常驻分支正好抽到它”的部分）
              var tHit = pick + ((firstTarget ? opts.upW1InStd : opts.upW2InStd) ? pStdW : 0);
              var oHit = pick + ((firstTarget ? opts.upW2InStd : opts.upW1InStd) ? pStdW : 0);
              var rest = 1 - tHit - oHit;
              if (guaranteed) { tHit = 1; oHit = 0; rest = 0; }
              var ni = Math.min(i + 1, P_), nj = Math.min(j + 1, Q);
              // 定轨命中：定轨是①就加 i，是②就加 j
              var tI = firstTarget ? ni : i, tJ = firstTarget ? j : nj;
              var oI = firstTarget ? i : ni, oJ = firstTarget ? nj : j;
              if (tHit > 0) B1[idx(0, 0, tI, tJ)] += v * pr * tHit;
              if (oHit > 0) B1[idx(0, Math.min(f + 1, FD - 1), oI, oJ)] += v * pr * oHit;
              if (rest > 0) B1[idx(0, Math.min(f + 1, FD - 1), i, j)] += v * pr * rest;
            }
          }
        }
      }
      pdf[t] = done;
      if (t === N) break;
      var tmp = A1; A1 = B1; B1 = tmp;
    }
    return pdf;
  }

  /** 两个独立链的卷积：P(T1 + T2 <= n) */
  function convCdf(f1, f2, N) {
    var out = new Float64Array(N + 1);
    var c2 = new Float64Array(N + 1);
    var acc = 0;
    for (var t = 0; t <= N; t++) { acc += f2[t]; c2[t] = acc; }
    for (var n = 0; n <= N; n++) {
      var s = 0;
      for (var t = 0; t <= n; t++) {
        var x = f1[t];
        if (x !== 0) s += x * c2[n - t];
      }
      out[n] = s;
    }
    return out;
  }

  var GachaEngine = {
    DEFAULT_CONFIG: DEFAULT_CONFIG,
    resolveConfig: resolveConfig,
    p5At: p5At,
    expectedPity: expectedPity,
    fiveStarRate: fiveStarRate,
    charCurves: charCurves,
    weaponCurves: weaponCurves,
    density: density,
    combinedProb: combinedProb,
    combinedCurve: combinedCurve,
    charPassage2: charPassage2,
    weaponPassage2: weaponPassage2,
    convCdf: convCdf,
    stats: stats,
    quantile: quantile,
    simulateOnce: simulateOnce,
    monteCarlo: monteCarlo,
    longRunUpRate: longRunUpRate,
    mulberry32: mulberry32
  };

  root.GachaEngine = GachaEngine;
  if (typeof module === 'object' && module && module.exports) module.exports = GachaEngine;
})(typeof globalThis !== 'undefined' ? globalThis : this);
