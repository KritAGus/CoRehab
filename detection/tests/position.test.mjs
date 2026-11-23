import test from "node:test";
import assert from "node:assert/strict";
import { synth } from "./synthBody.mjs";
import { toFrame } from "../../frontend/detection/geometry.js";
import { REFERENCE } from "../../frontend/detection/reference.js";
import { checkPosition, setupTip, targetZone, postureOf } from "../../frontend/detection/position.js";

// A resting synthetic body, moved and scaled so it fills `fill` of the frame around (cx, cy).
function body(ex, view, { fill = 0.72, cx = 0.5, cy = null } = {}) {
  const { lm, W, H } = synth(ex, view, 0, { op: "right" });
  const f = toFrame(lm, W, H, 0), lying = postureOf(ex, view).lying;
  const pts = []; for (const [k, v] of Object.entries(f)) if (k !== "t") (Array.isArray(v) ? [v] : Object.values(v)).forEach((p) => pts.push(p));
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const k = lying ? (fill * W) / (x1 - x0) : (fill * H) / (y1 - y0);
  const ty = cy ?? (lying ? 0.7 : 0.52);
  const tr = (p) => [cx * W + (p[0] - (x0 + x1) / 2) * k, ty * H + (p[1] - (y0 + y1) / 2) * k, p[2]];
  const g = { t: 0 };
  for (const [key, v] of Object.entries(f)) if (key !== "t") g[key] = Array.isArray(v) ? tr(v) : { left: tr(v.left), right: tr(v.right) };
  return { f: g, W, H };
}

for (const [ex, ref] of Object.entries(REFERENCE)) {
  test(`${ex}: a body like the PT recordings is in the right spot`, () => {
    const { f, W, H } = body(ex, ref.view, { fill: ex === "glute_bridge" ? 0.5 : ex === "sit_to_stand" ? 0.7 : 0.72 });
    const r = checkPosition(ex, ref.view, f, W, H, [], "setup");
    assert.equal(r.id, "ok", r.text);
    assert.ok(checkPosition(ex, ref.view, f, W, H, [], "exercise").ok);
  });
  test(`${ex}: too far, too close and off to the side are caught`, () => {
    const far = body(ex, ref.view, { fill: 0.3 });
    assert.equal(checkPosition(ex, ref.view, far.f, far.W, far.H).id, "too_far");
    assert.equal(checkPosition(ex, ref.view, far.f, far.W, far.H, [], "exercise").id, "too_far");
    const side = body(ex, ref.view, { fill: postureOf(ex, ref.view).lying ? 0.55 : 0.6, cx: postureOf(ex, ref.view).lying ? 0.74 : 0.9 });
    const r = checkPosition(ex, ref.view, side.f, side.W, side.H);
    assert.ok(["off_centre", "off_side"].includes(r.id), r.id);
    assert.deepEqual(r.dir, [-1, 0], "arrow should point back to the middle");
  });
}

test("too close at setup", () => {
  const { f, W, H } = body("standing_knee_bend", "side", { fill: 0.95 });
  assert.equal(checkPosition("standing_knee_bend", "side", f, W, H).id, "too_close");
  const s = body("sit_to_stand", "side", { fill: 0.85 });      // seated this tall: no room to stand up
  assert.equal(checkPosition("sit_to_stand", "side", s.f, s.W, s.H).id, "too_close");
});

test("nobody, and the wrong posture", () => {
  assert.equal(checkPosition("heel_slide", "side", null, 1280, 720).id, "none");
  const up = body("standing_knee_bend", "side");
  assert.equal(checkPosition("heel_slide", "side", up.f, up.W, up.H).id, "lie_down");
  const down = body("heel_slide", "side");
  assert.equal(checkPosition("standing_knee_bend", "side", down.f, down.W, down.H).id, "get_up");
});

test("feet cut off at the bottom", () => {
  const { f, W, H } = body("standing_knee_bend", "side", { fill: 0.75, cy: 0.62 });
  for (const j of ["ankle", "heel", "toe"]) for (const s of ["left", "right"]) f[j][s][2] = 0.1;   // not seen
  f.knee.right[1] = f.knee.left[1] = 0.99 * H;
  assert.equal(checkPosition("standing_knee_bend", "side", f, W, H).id, "feet_cut");
});

test("setup tip and target zone per posture", () => {
  assert.match(setupTip("heel_slide", "side"), /Lie side-on/);
  assert.match(setupTip("side_lying_abduction", "front"), /Face it/);
  assert.match(setupTip("seated_knee_extension", "side"), /Sit side-on/);
  assert.match(setupTip("standing_knee_bend", "side"), /Stand side-on/);
  const z = targetZone("heel_slide", "side"), u = targetZone("standing_knee_bend", "side");
  assert.ok(z.x1 - z.x0 > z.y1 - z.y0 && u.y1 - u.y0 > u.x1 - u.x0);
});
