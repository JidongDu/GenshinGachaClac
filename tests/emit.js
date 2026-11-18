// 供 tests/verify.py 调用：把 JS 引擎的计算结果以 JSON 打到 stdout
const E = require('../engine.js');
const spec = JSON.parse(process.argv[2]);
const cfg = E.resolveConfig(spec.cfg || {});
const C = spec.C, W = spec.W, N = spec.N;
const flat = spec.init || {};
const cInit = {
  pity: flat.charPity || 0,
  guaranteed: !!flat.charGuaranteed,
  streak: flat.charStreak || 0
};
const wInit = { pity: flat.weaponPity || 0, fate: flat.weaponFate || 0 };
const H = Math.min(4000, Math.max(N, 2 * cfg.char.hard * C + 2 * cfg.weapon.hard * W));
const cR = E.charCurves(H, C, cfg.char, cInit);
const wR = E.weaponCurves(H, W, cfg.weapon, wInit);
const cv = E.combinedCurve(cR.reach, wR.reach, C, W, H);
const st = E.stats(cv);
process.stdout.write(JSON.stringify({
  p: cv[N], mean: st.mean, q50: st.quantiles[0.5], q90: st.quantiles[0.9],
  q99: st.quantiles[0.99], pChar: cR.reach[C][N], pWeapon: wR.reach[W][N], H
}));
