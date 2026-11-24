import test from "node:test";
import assert from "node:assert/strict";
import { synth, period } from "./synthBody.mjs";
import { toFrame, facingOf } from "../../frontend/detection/geometry.js";
import { analyze } from "../../frontend/detection/engine.js";
import { EXERCISES } from "../../frontend/detection/exercises.js";
import { REFERENCE } from "../../frontend/detection/reference.js";
import { FIX_TEXT } from "../../frontend/detection/score.js";
import { guideFor, guidePose, repSpeech } from "../../frontend/detection/guide.js";

// Synthetic session of one exercise, then the guide on every frame as the live camera would.
function session(ex, view, periods = 3) {
  const frames = [];
  for (let t = 0; t < periods * period(); t += 1 / 15) { const { lm, W, H } = synth(ex, view, t, { op: "right" }); frames.push(toFrame(lm, W, H, t)); }
  const facing = view === "side" ? (frames.map(facingOf).reduce((a, b) => a + b, 0) >= 0 ? 1 : -1) : 1;
  const ctx = { view, op: "right", good: "left", facing, prefer: "right" };
  const r = analyze(frames, EXERCISES[ex], ctx, {});
  return { frames, r, ctx: { ...ctx, leg: r.leg ?? "right" } };
}

for (const [ex, ref] of Object.entries(REFERENCE)) {
  test(`${ex}: the guide pose reads exactly the PT target`, () => {
    const g = guideFor(ex, ref.view);
    assert.ok(g, "no guide");
    const { frames, r, ctx } = session(ex, ref.view);
    assert.ok(r.reps.length >= 2, "synthetic reps not counted");
    const mem = {}; let shown = 0, checked = 0;
    for (const f of frames) {
      const p = guidePose(g, ctx, f, r.base, r.scale, mem);
      if (!p) continue;
      assert.ok(["rest", "more", "good", "over"].includes(p.status));
      if (!p.ghost) continue;
      shown++;
      const m = g.v.metrics(p.ghost.frame, ctx);
      assert.ok(Math.abs(m[g.sig.key] / r.scale - p.target) < 0.01 * Math.max(1, Math.abs(p.target)), `${m[g.sig.key] / r.scale} vs ${p.target}`);
      checked++;
    }
    assert.ok(shown > frames.length * 0.4, `guide shown on only ${shown} of ${frames.length} frames`);
    assert.ok(checked > 0);
  });
}

test("no guide for a camera view the PT reference wasn't recorded from", () => {
  assert.equal(guideFor("mini_squat", "front"), null);
  assert.equal(guideFor("not_an_exercise", "side"), null);
});

test("angle guides wait for the first movement to know which way to go", () => {
  const g = guideFor("standing_knee_bend", "side");
  const { frames, r, ctx } = session("standing_knee_bend", "side");
  const p = guidePose(g, ctx, frames[0], r.base, r.scale, {});       // at rest, nothing learned yet
  assert.equal(p.status, "rest");
  assert.equal(p.ghost, null);
});

test("heel slide guide keeps the hip and heel on the floor line and lifts the knee", () => {
  const g = guideFor("heel_slide", "side");
  const { frames, r, ctx } = session("heel_slide", "side");
  const f = frames[2], p = guidePose(g, ctx, f, r.base, r.scale, {}), q = p.ghost.frame, L = ctx.leg;
  assert.deepEqual(q.hip[L], f.hip[L]);
  const H = f.hip[L], A = f.ankle[L], A2 = q.ankle[L];
  const cross = (A[0] - H[0]) * (A2[1] - H[1]) - (A[1] - H[1]) * (A2[0] - H[0]);
  assert.ok(Math.abs(cross) / Math.hypot(A[0] - H[0], A[1] - H[1]) < 1, "heel left the floor line");
  assert.ok(q.knee[L][1] < f.knee[L][1] - 20, "knee should rise (up the screen)");
});

test("'over' only where going too far is an error", () => {
  const sq = guideFor("step_up", "side"), ab = guideFor("side_lying_abduction", "front");
  const { frames, r, ctx } = session("side_lying_abduction", "front");
  const p = guidePose(ab, ctx, frames[0], r.base, r.scale, {});
  assert.ok(Number.isFinite(p.over));
  const s = session("step_up", "side"), ps = guidePose(sq, s.ctx, s.frames[0], s.r.base, s.r.scale, {});
  assert.equal(ps.over, Infinity);
});

test("spoken rep feedback", () => {
  assert.equal(repSpeech(3, 5, [], FIX_TEXT), "Rep 3. 5 out of 5. Great.");
  assert.equal(repSpeech(4, 3, ["trunk_leans"], FIX_TEXT), "Rep 4. 3 out of 5. Stand up tall.");
  assert.equal(repSpeech(2, 4, ["much_quicker"], FIX_TEXT), "Rep 2. 4 out of 5. Slow down.");
  assert.equal(repSpeech(1, 3, ["much_less_movement"], FIX_TEXT), "Rep 1. 3 out of 5. Go a little further.");
  assert.equal(repSpeech(5, 3, ["too_high"], FIX_TEXT), "Rep 5. 3 out of 5. Don't lift so high.");
});
