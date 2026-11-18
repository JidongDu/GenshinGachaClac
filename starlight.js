/*!
 * Starlight — 星辉返利模拟（浏览器 / Node 通用）
 *
 * 规则（2025-11 官方口径）：
 *   5★角色 首次0 / 重复10 / 满命25      4★角色 首次0 / 重复2 / 满命5
 *   5★武器 首次0 / 之后10               4★武器 首次0 / 之后2
 *   5 星辉 = 1 纠缠之缘（无限购）
 *
 * 需要一份个人仓库（data/roster.js 里的 window.GACHA_DATA）才能判断「这次出的是新还是旧」。
 */
(function (root) {
  'use strict';

  var PER_PULL = 5;
  var RULE = {
    c5: [0, 10, 25],   // 首次 / 重复 / 满命
    c4: [0, 2, 5],
    w5: [0, 10, 10],
    w4: [0, 2, 2]
  };
  var MAXC = 6;

  var DEFAULT_PHASES = [
    {
      name: '上半',
      char: { up5: '玛薇卡', up4: ['香菱', '行秋', '班尼特'] },
      weapon: { up5: ['焚曜千阳', '千夜浮梦'] }
    },
    {
      name: '下半',
      char: { up5: '芙宁娜', up4: ['北斗', '凝光', '坎蒂丝'] },
      weapon: { up5: ['静水流涌之辉', '万世流涌大典'] }
    }
  ];

  /* ---------------- 单次模拟 ---------------- */
  /* 目标：角色①A 个 + 角色②B 个 + 武器①P 把 + 武器②Q 把
     一期 = 角色池① + 角色池②（共享垫抽/大保底）+ 武器池（两把 UP，定轨先①后②） */
  function simulate(N, A, B, P_, Q, data, pools, cfg, rng, anyUp, init, noRefund) {
    var E = root.GachaEngine;
    init = init || {};
    var charCfg = Object.assign({}, E.DEFAULT_CONFIG.char, (cfg && cfg.char) || {});
    var wpCfg = Object.assign({}, E.DEFAULT_CONFIG.weapon, (cfg && cfg.weapon) || {});
    wpCfg.acceptAny = false;

    var chars = {}, k;
    for (k in data.chars) chars[k] = data.chars[k].c;
    var wown = {};
    for (k in data.weapons) if ((data.weapons[k].n || 0) > 0) wown[k] = true;

    var cA = pools.charA, cB = pools.charB, cW = pools.weapon;
    var std5 = (data.std5 && data.std5.length) ? data.std5 : [cA.up5, cB.up5];
    var std4chars = (data.std4chars && data.std4chars.length) ? data.std4chars : [cA.up5];
    var std4weapons = data.std4weapons || [];
    var std5weapons = (data.std5weapons && data.std5weapons.length) ? data.std5weapons : [cW.up5[1]];
    var w1 = cW.up5[0], w2 = cW.up5[1] || cW.up5[0];
    var wup4 = (cW.up4 && cW.up4.length) ? cW.up4 : std4weapons;

    var star = 0, bought = 0, pulls = 0;
    var book = { c5: 0, c4: 0, w5: 0, w4: 0 };
    function gainChar(name, rarity) {
      var rule = rarity === 5 ? RULE.c5 : RULE.c4;
      var key = rarity === 5 ? 'c5' : 'c4';
      var c = chars.hasOwnProperty(name) ? chars[name] : null;
      var g;
      if (c === null || c < 0) { chars[name] = 0; g = rule[0]; }
      else if (c >= MAXC) { g = rule[2]; }
      else { chars[name] = c + 1; g = rule[1]; }
      star += g; book[key] += g; return g;
    }
    function gainWeapon(name, rarity) {
      var rule = rarity === 5 ? RULE.w5 : RULE.w4;
      var key = rarity === 5 ? 'w5' : 'w4';
      var g = wown[name] ? rule[1] : rule[0];
      wown[name] = true;
      star += g; book[key] += g; return g;
    }
    function settle() { if (noRefund) return; while (star >= PER_PULL) { star -= PER_PULL; bought += 1; } }
    function pick(arr) { return arr[(rng() * arr.length) | 0]; }

    // 角色侧共享状态
    var cs = { pity: init.charPity || 0, g: !!init.charGuaranteed, s: init.charStreak || 0, p4: 0 };
    var ws = { pity: init.weaponPity || 0, f: init.weaponFate || 0, p4: 0 };
    var gotA = 0, gotB = 0, gotW1 = 0, gotW2 = 0;

    while (gotA < A || gotB < B || gotW1 < P_ || gotW2 < Q) {
      if (pulls >= N + bought) break;
      pulls += 1;

      if (gotA < A || gotB < B) {
        var onA = gotA < A;
        var pool = onA ? cA : cB;
        cs.pity += 1;
        if (rng() < E.p5At(cs.pity - 1, charCfg)) {
          cs.pity = 0; cs.p4 = 0;
          var isUp = false;
          if (cs.g) { cs.g = false; isUp = true; }
          else {
            var pUp = cs.s >= charCfg.radMax ? 1
              : charCfg.upRate + (1 - charCfg.upRate) * charCfg.radP;
            if (rng() < pUp) { cs.s = 0; isUp = true; }
            else { cs.g = true; cs.s = Math.min(cs.s + 1, charCfg.radMax); }
          }
          if (isUp) {
            gainChar(pool.up5, 5);
            if (onA) gotA++; else gotB++;
          } else {
            var stdName = pick(std5);
            gainChar(stdName, 5);
            if (stdName === pool.up5) { if (onA) gotA++; else gotB++; }
          }
          settle(); continue;
        }
        cs.p4 += 1;
        if (rng() < (cs.p4 >= 10 ? 1 : 0.051)) {
          cs.p4 = 0;
          if (rng() < 0.5) gainChar(pick(pool.up4), 4);
          else if (std4weapons.length && rng() >= 0.42) gainWeapon(pick(std4weapons), 4);
          else gainChar(pick(std4chars), 4);
          settle();
        }
      } else {
        var first = gotW1 < P_;
        ws.pity += 1;
        if (rng() < E.p5At(ws.pity - 1, wpCfg)) {
          ws.pity = 0; ws.p4 = 0;
          var guaranteed = wpCfg.fateMax > 0 && ws.f >= wpCfg.fateMax;
          var hitT = 0, hitO = 0;
          if (guaranteed) { if (first) hitT = 1; else hitO = 1; }
          else { hitT = wpCfg.featuredRate * 0.5; hitO = wpCfg.featuredRate * 0.5; }
          var rest = 1 - hitT - hitO;
          var r = rng();
          if (first) {
            if (r < hitT) { ws.f = 0; gainWeapon(w1, 5); gotW1++; }
            else if (r < hitT + hitO) { ws.f = Math.min(ws.f + 1, wpCfg.fateMax); gainWeapon(w2, 5); gotW2++; }
            else {
              var sw1 = pick(std5weapons);
              gainWeapon(sw1, 5);
              if (sw1 === w1) { ws.f = 0; gotW1++; }
              else if (sw1 === w2) { ws.f = Math.min(ws.f + 1, wpCfg.fateMax); gotW2++; }
              else ws.f = Math.min(ws.f + 1, wpCfg.fateMax);
            }
          } else {
            if (r < hitO) { ws.f = 0; gainWeapon(w2, 5); gotW2++; }
            else if (r < hitO + hitT) { ws.f = Math.min(ws.f + 1, wpCfg.fateMax); gainWeapon(w1, 5); gotW1++; }
            else {
              var sw2 = pick(std5weapons);
              gainWeapon(sw2, 5);
              if (sw2 === w2) { ws.f = 0; gotW2++; }
              else if (sw2 === w1) { ws.f = Math.min(ws.f + 1, wpCfg.fateMax); gotW1++; }
              else ws.f = Math.min(ws.f + 1, wpCfg.fateMax);
            }
          }
          settle(); continue;
        }
        ws.p4 += 1;
        if (rng() < (ws.p4 >= 10 ? 1 : 0.051)) {
          ws.p4 = 0;
          if (wup4.length && rng() < 0.75) gainWeapon(pick(wup4), 4);
          else gainWeapon(std4weapons.length ? pick(std4weapons) : w1, 4);
          settle();
        }
      }
    }

    return {
      done: gotA >= A && gotB >= B && gotW1 >= P_ && gotW2 >= Q,
      pulls: pulls, bought: bought, star: star, book: book,
      got: { a: gotA, b: gotB, w1: gotW1, w2: gotW2 }
    };
  }

  function monteCarlo(N, A, B, P_, Q, data, pools, opts) {
    opts = opts || {};
    var runs = opts.runs || 20000;
    var rng = opts.seed != null ? root.GachaEngine.mulberry32(opts.seed) : Math.random;
    var H = Math.max(N, Math.floor(opts.horizon || N));   // 曲线画到 horizon，统计仍按 N
    var ok = 0, extra = 0, used = 0;
    var book = { c5: 0, c4: 0, w5: 0, w4: 0 };
    var hist = new Float64Array(H + 1);
    for (var i = 0; i < runs; i++) {
      var r = simulate(H, A, B, P_, Q, data, pools, opts.cfg, rng, opts.anyUp, opts.init, opts.noRefund);
      extra += r.bought; used += r.pulls;
      book.c5 += r.book.c5; book.c4 += r.book.c4;
      book.w5 += r.book.w5; book.w4 += r.book.w4;
      if (!r.done) continue;
      var need = r.pulls - r.bought;
      if (need <= H) hist[need]++;
      if (need <= N) ok++;                    // 统计与「N 抽内达成」一致
    }
    var p = ok / runs;
    var cdf = new Float64Array(H + 1), acc = 0;
    for (var t = 0; t <= H; t++) { acc += hist[t] / runs; cdf[t] = acc; }
    return {
      runs: runs, p: p, se: Math.sqrt(Math.max(0, p * (1 - p) / runs)),
      extra: extra / runs, pulls: used / runs, cdf: cdf,
      book: { c5: book.c5 / runs, c4: book.c4 / runs, w5: book.w5 / runs, w4: book.w4 / runs }
    };
  }

  var Starlight = {
    DEFAULT_PHASES: DEFAULT_PHASES,
    DEFAULT_POOLS: {
      charA: { up5: '玛薇卡', up4: ['香菱', '行秋', '班尼特'] },
      charB: { up5: '芙宁娜', up4: ['北斗', '凝光', '坎蒂丝'] },
      weapon: { up5: ['焚曜千阳', '静水流涌之辉'], up4: ['西风剑', '祭礼剑', '匣里龙吟', '雨裁', '论剑'] }
    },
    PER_PULL: PER_PULL,
    RULE: RULE,
    simulate: simulate,
    monteCarlo: monteCarlo
  };
  root.Starlight = Starlight;
  if (typeof module === 'object' && module && module.exports) module.exports = Starlight;
})(typeof globalThis !== 'undefined' ? globalThis : this);
