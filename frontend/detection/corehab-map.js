// How the tracker's names and scores map onto the CoRehab API (POST /api/result).

// CoRehab exercise ids -> tracker ids (only one differs).
export const TO_TRACKER = { side_lying_hip_abduction: "side_lying_abduction" };
export const trackerId = (id) => TO_TRACKER[id] ?? id;

// Tracker error ids -> the error names CoRehab's backend and simulator use.
export const ERROR_NAME = {
  hip_lifts: "hip_lift", partial: "partial_rom", too_fast: "too_fast", knee_bends: "knee_bent", too_high: "lift_too_high",
  back_arches: "back_arch", slides_forward: "slide_forward", thigh_swings: "hip_flexion", trunk_leans: "trunk_lean",
  not_straight: "partial_rom", kicks_fast: "too_fast", leans_back: "lean_back", pushes_hands: "uses_hands",
  leans_far: "trunk_lean", weight_shift: "weight_shift", too_deep: "too_deep", knee_caves: "knee_valgus",
  hip_drops: "hip_drop", arches: "arch", pelvis_tilts: "uneven_hips", hip_rolls: "hip_roll",
};
export const errorName = (id) => ERROR_NAME[id] ?? id;

/** 1-5 rep score -> CoRehab form score 0-1 (5 -> 1.0, 3 -> 0.5, 1 -> 0.0). */
export const toFormScore = (s) => Math.round(((s - 1) / 4) * 100) / 100;

/**
 * Body fields for POST /api/result from the camera's per-rep scores.
 * form_scores must have exactly one value per rep_done (the backend checks this): extra camera reps
 * are dropped, reps the camera missed get the average of the ones it saw.
 */
export function toApiResult(scores, errors, repsDone) {
  if (!scores?.length || !repsDone) return null;
  const s = scores.slice(0, repsDone);
  const mean = s.reduce((x, y) => x + y, 0) / s.length;
  while (s.length < repsDone) s.push(mean);
  return { form_scores: s.map(toFormScore), error_counts: { ...errors } };
}
