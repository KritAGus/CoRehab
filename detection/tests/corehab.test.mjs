import test from "node:test";
import assert from "node:assert/strict";
import { EXERCISES } from "../../frontend/detection/exercises.js";
import { REFERENCE } from "../../frontend/detection/reference.js";
import { ERROR_NAME, trackerId, toApiResult, toFormScore } from "../../frontend/detection/corehab-map.js";

// The exercise ids CoRehab's backend uses (backend/rehab_node.py EXERCISES).
const COREHAB_IDS = ["heel_slide", "straight_leg_raise", "sitting_knee_bend", "standing_knee_bend", "seated_knee_extension",
  "sit_to_stand", "mini_squat", "step_up", "glute_bridge", "side_lying_hip_abduction"];

test("every CoRehab exercise has a tracker exercise and a reference range", () => {
  for (const id of COREHAB_IDS) {
    assert.ok(EXERCISES[trackerId(id)], `no tracker exercise for ${id}`);
    assert.ok(REFERENCE[trackerId(id)], `no reference range for ${id}`);
  }
});

test("every tracker error check has a CoRehab error name", () => {
  for (const ex of Object.values(EXERCISES))
    for (const v of Object.values(ex.views))
      for (const c of v.checks) assert.ok(ERROR_NAME[c.id], `${ex.id}: no CoRehab name for ${c.id}`);
});

test("form score mapping: 5 -> 1, 3 -> 0.5, 1 -> 0", () => {
  assert.equal(toFormScore(5), 1); assert.equal(toFormScore(3), 0.5); assert.equal(toFormScore(1), 0);
});

// The same rules as backend/test_server.py check_detection().
function backendAccepts(body) {
  const fs = body.form_scores, ec = body.error_counts;
  return Array.isArray(fs) && fs.every((x) => typeof x === "number" && x >= 0 && x <= 1) && fs.length === body.reps_done &&
    Object.values(ec).every((v) => Number.isInteger(v) && v >= 0);
}

test("API result has exactly one form score per rep, whatever the camera saw", () => {
  for (const [seen, done] of [[[5, 5, 3], 3], [[5, 4], 5], [[5, 5, 5, 5, 3, 3], 4]]) {
    const r = toApiResult(seen, { trunk_lean: 2 }, done);
    assert.ok(backendAccepts({ reps_done: done, ...r }), JSON.stringify(r));
  }
  assert.equal(toApiResult([], {}, 10), null);
  assert.equal(toApiResult([5], {}, 0), null);
});
