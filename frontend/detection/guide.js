// Live guide: where the patient should move to, from the PT-approved reference.
// Pure functions (no DOM), so they are tested in detection/tests/guide.test.mjs.
//
// The target is the median movement of the PT-approved reps (reference.js, movement.p50),
// added to the patient's own resting position (the engine's baseline). The guide pose is
// found by moving the patient's live joints until the exercise's own metric (exercises.js)
// reads the target, so the guide always agrees with how reps are counted and scored.
import { EXERCISES } from "./exercises.js";
import { REFERENCE } from "./reference.js";

// How the guide moves the body for each rep signal.
//   rot:   turn the `move` joints around `pivot` (angles: knee bend, leg lift, abduction, bridge line)
//   shift: slide the `move` joints (heights: hips up/down, foot on the step)
//   fold:  hip and heel stay on the floor, the heel slides toward the hip and the knee rises (heel slide)
const ROT = (pivot, move, draw) => ({ kind: "rot", pivot, move, draw });
const SHIFT = (move, both, at) => ({ kind: "shift", move, both, at });
const SHAPES = {
  knee: ROT("knee", ["ankle", "heel", "toe"], [["knee", "ankle"]]),
  lift: ROT("hip", ["knee", "ankle", "heel", "toe"], [["hip", "knee"], ["knee", "ankle"]]),
  spread: ROT("hip", ["knee", "ankle", "heel", "toe"], [["hip", "knee"], ["knee", "ankle"]]),
  line: ROT("knee", ["hip"], [["knee", "hip"], ["hip", "shoulder"]]),
  footUp: SHIFT(["ankle", "heel", "toe"], false, "ankle"),
  hipUp: SHIFT(["hip"], true, "hip"),
  hipDown: SHIFT(["hip"], true, "hip"),
};
// Whole-body knee exercises: the thigh turns over the planted shank, not the other way round.
const OVER_IS_ERROR = new Set(["straight_leg_raise", "side_lying_abduction", "mini_squat", "glute_bridge"]);
const BY_EXERCISE = {
  heel_slide: { knee: { kind: "fold", draw: [["hip", "knee"], ["knee", "ankle"]] } },
  sit_to_stand: { knee: ROT("knee", ["hip"], [["knee", "hip"]]) },
  mini_squat: { knee: ROT("knee", ["hip"], [["knee", "hip"]]) },
};

/** Guide set-up for an exercise and camera view, or null when there's no PT reference for it. */
export function guideFor(exId, view) {
  const ex = EXERCISES[exId], ref = REFERENCE[exId], v = ex?.views[view];
  if (!v || !ref || ref.view !== view || !(ref.movement?.p50 > 0)) return null;
  const sig = v.signal, shape = BY_EXERCISE[exId]?.[sig.key] ?? SHAPES[sig.key];
  if (!shape) return null;
  return { exId, view, v, sig, ref, shape };
}

const clone = (f) => JSON.parse(JSON.stringify(f));
const turn = (p, c, deg) => {
  const a = (deg * Math.PI) / 180, x = p[0] - c[0], y = p[1] - c[1];
  return [c[0] + x * Math.cos(a) - y * Math.sin(a), c[1] + x * Math.sin(a) + y * Math.cos(a), p[2]];
};

/**
 * Where the patient is now and where the guide is.
 * @param g       guideFor(...)
 * @param ctx     engine ctx with leg set
 * @param f       current (smoothed) frame
 * @param base    the engine's baseline (result.base; base.signal is in signal units)
 * @param scale   the engine's signal scale (result.scale; 1 for angles)
 * @param memory  {sign} the side the guide turned to last time; updated in place
 * @returns null, or {value, target, exc, band, status, ghost, unit, label}
 *   exc: movement from rest in signal units; band: {p10,p50,p90}; status: rest|more|good|over
 */
export function guidePose(g, ctx, f, base, scale = 1, memory = {}) {
  if (!g || !f || !base || !Number.isFinite(base.signal)) return null;
  const { v, sig, ref, shape } = g, leg = ctx.leg ?? ctx.op;
  const read = (fr) => { const m = v.metrics(fr, { ...ctx, leg }); return m.ok && Number.isFinite(m[sig.key]) ? m[sig.key] : NaN; };
  const raw = read(f);
  if (!Number.isFinite(raw)) return null;
  const value = raw / scale, exc = sig.dir * (value - base.signal);
  const m = ref.movement, band = { p10: m.p10, p50: m.p50, p90: m.p90 };
  // "more" matches the score's "moved less than the PT reps" rule; "over" only where going
  // too far is an error (lifting too high, squatting too deep, pushing the hips too high).
  const enough = m.p10 - 0.1 * m.p50;
  const over = OVER_IS_ERROR.has(g.exId) ? m.p90 + Math.max(m.sd ?? 0, 0.15 * m.p50) : Infinity;
  const status = exc < sig.start ? "rest" : exc < enough ? "more" : exc <= over ? "good" : "over";
  const target = base.signal + sig.dir * m.p50, targetRaw = target * scale;
  const out = { value, target, exc, band, enough, over, status, unit: sig.unit ?? "°", label: sig.label, ghost: null };

  if (shape.kind === "rot") {
    const c = f[shape.pivot][leg];
    if (!c || !(c[2] > 0)) return out;
    const pose = (deg) => {
      const q = clone(f);
      for (const j of shape.move) q[j][leg] = turn(f[j][leg], c, deg);
      return q;
    };
    // Every turn that reaches the target, then the one nearest to where the leg is going.
    const hits = [];
    let prev = null;
    for (let d = -178; d <= 178; d += 2) {
      const err = read(pose(d)) - targetRaw;
      if (prev && Number.isFinite(err) && Number.isFinite(prev.err) && Math.sign(err) !== Math.sign(prev.err) && Math.abs(err - prev.err) < 40) {
        let lo = prev.d, hi = d, elo = prev.err;                 // refine by bisection
        for (let k = 0; k < 12; k++) {
          const mid = (lo + hi) / 2, em = read(pose(mid)) - targetRaw;
          if (Math.sign(em) === Math.sign(elo)) { lo = mid; elo = em; } else hi = mid;
        }
        hits.push((lo + hi) / 2);
      }
      prev = { d, err };
    }
    if (!hits.length) return out;
    // Moving: the nearest turn continues the movement. At rest both sides look the same, so
    // use the side the guide went to during the last rep, and show nothing before the first one.
    let pick;
    if (status !== "rest") pick = hits.reduce((a, b) => (Math.abs(b) < Math.abs(a) ? b : a));
    else if (memory.sign) { const same = hits.filter((h) => Math.sign(h) === memory.sign); pick = same.length ? same.reduce((a, b) => (Math.abs(b) < Math.abs(a) ? b : a)) : null; }
    if (pick == null) return out;
    if (status !== "rest" && Math.abs(pick) > 3) memory.sign = Math.sign(pick);
    const q = pose(pick);
    out.ghost = { kind: "rot", segments: shape.draw.map(([a, b]) => [q[a][leg], q[b][leg]]), end: q[shape.draw.at(-1)[1]][leg], pivot: c, turn: pick, frame: q };
    return out;
  }

  if (shape.kind === "fold") {
    const H = f.hip[leg], K = f.knee[leg], A = f.ankle[leg];
    if (![H, K, A].every((p) => p && p[2] > 0)) return out;
    const L1 = Math.hypot(K[0] - H[0], K[1] - H[1]), L2 = Math.hypot(A[0] - K[0], A[1] - K[1]);
    const D = Math.hypot(A[0] - H[0], A[1] - H[1]); if (!(D > 1)) return out;
    const u = [(A[0] - H[0]) / D, (A[1] - H[1]) / D];
    // the knee rises on the side it's on now; with the leg straight, up the screen
    const cr = u[0] * (K[1] - H[1]) - u[1] * (K[0] - H[0]);
    let n = [-u[1], u[0]];
    if (Math.abs(cr) > 0.05 * L1 ? Math.sign(cr) < 0 : n[1] > 0) n = [-n[0], -n[1]];
    const pose = (d) => {
      const a = (L1 * L1 - L2 * L2 + d * d) / (2 * d), h = Math.sqrt(Math.max(0, L1 * L1 - a * a));
      const q = clone(f), A2 = [H[0] + u[0] * d, H[1] + u[1] * d], dx = A2[0] - A[0], dy = A2[1] - A[1];
      q.knee[leg] = [H[0] + u[0] * a + n[0] * h, H[1] + u[1] * a + n[1] * h, K[2]];
      for (const j of ["ankle", "heel", "toe"]) q[j][leg] = [f[j][leg][0] + dx, f[j][leg][1] + dy, f[j][leg][2]];
      return q;
    };
    let hi = (L1 + L2) * 0.999, lo = Math.max(Math.abs(L1 - L2) + 1, 0.15 * (L1 + L2));
    let ehi = read(pose(hi)) - targetRaw, elo = read(pose(lo)) - targetRaw;
    if (!(Math.sign(ehi) !== Math.sign(elo))) return out;
    for (let k = 0; k < 30; k++) {
      const mid = (lo + hi) / 2, em = read(pose(mid)) - targetRaw;
      if (Math.sign(em) === Math.sign(ehi)) { hi = mid; ehi = em; } else { lo = mid; elo = em; }
    }
    const q = pose((lo + hi) / 2);
    out.ghost = { kind: "rot", segments: shape.draw.map(([a, b]) => [q[a][leg], q[b][leg]]), end: q.ankle[leg], pivot: H, frame: q };
    return out;
  }

  // shift: find which way the joints move the signal (down the screen, or across when lying)
  const shifted = (dx, dy) => {
    const q = clone(f);
    for (const j of shape.move) for (const s of shape.both ? ["left", "right"] : [leg]) q[j][s] = [f[j][s][0] + dx, f[j][s][1] + dy, f[j][s][2]];
    return q;
  };
  const sy = (read(shifted(0, 10)) - raw) / 10, sx = (read(shifted(10, 0)) - raw) / 10;
  const useY = Math.abs(sy) >= Math.abs(sx), slope = useY ? sy : sx;
  if (!(Math.abs(slope) > 1e-9)) return out;
  const d = (targetRaw - raw) / slope;
  const q = useY ? shifted(0, d) : shifted(d, 0);
  const at = shape.both ? [(q[shape.at].left[0] + q[shape.at].right[0]) / 2, (q[shape.at].left[1] + q[shape.at].right[1]) / 2]
    : q[shape.at][leg];
  const now = shape.both ? [(f[shape.at].left[0] + f[shape.at].right[0]) / 2, (f[shape.at].left[1] + f[shape.at].right[1]) / 2]
    : f[shape.at][leg];
  out.ghost = { kind: "shift", at, now, axis: useY ? "y" : "x", frame: q };
  return out;
}

/** Short spoken feedback for a finished rep: "Rep 3. 4 out of 5. Slow down." */
const SAY_REASON = { much_less_movement: "Go a little further", less_movement: "Go a little further", much_quicker: "Slow down" };
export function repSpeech(n, score, reasons = [], fixText = {}) {
  const r = reasons[0];
  const tip = r ? (fixText[r] ?? SAY_REASON[r] ?? "") : score >= 5 ? "Great" : "";
  return `Rep ${n}. ${score} out of 5.${tip ? " " + tip + "." : ""}`;
}
