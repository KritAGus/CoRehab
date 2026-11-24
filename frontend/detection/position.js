// Where to be in front of the tablet, per exercise. Pure functions, tested in detection/tests/position.test.mjs.
//
// Ranges come from the PT-approved recordings (detection/reference/clips_index.csv): standing and seated
// bodies filled 64-79% of the frame height (feet to head), lying bodies 66-79% of the frame width, with
// the body near the middle and at least a few % from every edge. The accepted band is wider than that
// (50-90%) so a patient isn't held up by a few centimetres.
import { EXERCISES } from "./exercises.js";
import { viewCheck } from "./engine.js";
import { vis } from "./geometry.js";

const JOINTS = ["shoulder", "hip", "knee", "ankle", "heel", "toe"];
const NEED = ["shoulder", "hip", "knee", "ankle"];
export const SIZE = { lo: 0.5, hi: 0.9 };            // body length as a share of the frame
// Bridges are shorter (knees bent); sit-to-stand starts seated and needs room to stand up (seated is
// about 75% of standing height: PT clip 0.70 seated -> 0.93 standing).
const SIZE_BY = { glute_bridge: { lo: 0.4, hi: 0.9 }, sit_to_stand: { lo: 0.45, hi: 0.78 } };
const FAR_WHILE_EXERCISING = 0.35;
const EDGE = 0.02, MIDDLE = 0.24;                    // closest to an edge; how far off centre is fine

export function postureOf(exId, view) {
  const ex = EXERCISES[exId], v = ex?.views[view];
  if (!v) return null;
  const lying = /^Lying/i.test(ex.start) || !!v.lying;
  return { lying, seated: /^Seated/i.test(ex.start), side: view === "side", start: ex.start };
}

/** Short placement tip for the setup panel. */
export function setupTip(exId, view) {
  const p = postureOf(exId, view);
  if (!p) return "Place the tablet at hip height, 2 steps away.";
  if (p.lying) return p.side ? "Tablet on the floor, landscape, about 2 steps away. Lie side-on to it."
    : "Tablet on the floor, landscape, about 2 steps away. Face it.";
  const pose = p.seated ? "Sit" : "Stand";
  return `Tablet at hip height, about 2 steps away. ${pose} ${p.side ? "side-on to it" : "facing it"}.`;
}

/** Where the body should be, as a share of the frame: {x0, y0, x1, y1}. */
export function targetZone(exId, view) {
  const p = postureOf(exId, view);
  if (!p) return null;
  if (p.lying) return { x0: 0.13, y0: 0.42, x1: 0.87, y1: 0.95 };
  const w = p.side ? (p.seated ? 0.36 : 0.26) : 0.4;
  return { x0: 0.5 - w / 2, y0: 0.1, x1: 0.5 + w / 2, y1: 0.95 };
}

const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];

/**
 * Is the patient where the camera needs them?
 * @param f       current frame (px), or null when no one is seen
 * @param W, H    image size in px
 * @param recent  a second or so of recent frames, for the side-on / facing check
 * @param phase   "setup": all checks, before starting. "exercise": only what breaks tracking (out of the
 *                picture, a leg cut off, much too far, wrong posture), since moving changes the body's size.
 * @returns {ok, id, text, dir, box}  dir: [dx, dy] in image space for an arrow, or null
 */
export function checkPosition(exId, view, f, W, H, recent = [], phase = "setup") {
  const p = postureOf(exId, view);
  const res = (id, text, dir = null, box = null) => ({ ok: id === "ok", id, text, dir, box });
  if (!p) return res("ok", "");
  if (!f) return res("none", "Step into the picture");
  const pts = [f.nose, ...JOINTS.flatMap((j) => [f[j].left, f[j].right])].filter((q) => vis(q));
  if (pts.length < 4) return res("none", "Step into the picture");
  const xs = pts.map((q) => q[0] / W), ys = pts.map((q) => q[1] / H);
  const box = { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
  const cx = (box.x0 + box.x1) / 2, toMiddle = [Math.sign(0.5 - cx), 0];

  // 1. Lying vs sitting/standing: the trunk's direction on screen.
  for (const s of ["left", "right"]) {
    if (!vis(f.shoulder[s], f.hip[s])) continue;
    const dx = Math.abs(f.shoulder[s][0] - f.hip[s][0]), dy = Math.abs(f.shoulder[s][1] - f.hip[s][1]);
    if (p.lying && dy > 1.5 * dx) return res("lie_down", p.side ? "Lie down side-on to the tablet, as in the video" : "Lie down as in the video", null, box);
    if (!p.lying && dx > 1.5 * dy && phase === "setup") return res("get_up", p.seated ? "Sit on the chair, as in the video" : "Stand up, as in the video", null, box);
    break;
  }

  // 2. The whole leg (and trunk) in the picture.
  const whole = (s) => NEED.every((j) => vis(f[j][s]));
  const legsOk = view === "front" ? whole("left") && whole("right") : whole("left") || whole("right");
  if (!legsOk) {
    if (box.y1 > 1 - EDGE * 1.5) return res("feet_cut", p.lying ? "Move the tablet back so it sees your whole body" : "Step back so I can see your feet", null, box);
    if (box.x0 < EDGE * 1.5 || box.x1 > 1 - EDGE * 1.5) return res("off_side", "Move toward the middle", toMiddle, box);
    if (box.y0 < EDGE * 1.5) return res("head_cut", "Step back a little", null, box);
    return res("leg_hidden", "Make sure your whole leg is in view", null, box);
  }

  // 3. Size: too far away is hard to track, too close cuts the body off when moving.
  const size = p.lying ? box.x1 - box.x0 : box.y1 - box.y0, lim = SIZE_BY[exId] ?? SIZE;
  if (phase !== "setup") return size < FAR_WHILE_EXERCISING ? res("too_far", p.lying ? "Move the tablet a little closer" : "Come a little closer", null, box) : res("ok", "", null, box);
  if (size > lim.hi || box.y1 > 1 - EDGE || box.y0 < EDGE) return res("too_close", p.lying ? "Move the tablet back a little" : "Step back a little", null, box);
  if (size < lim.lo) return res("too_far", p.lying ? "Move the tablet a little closer" : "Come a little closer", null, box);

  // 4. Near the middle, with room to move.
  if (Math.abs(cx - 0.5) > MIDDLE || box.x0 < EDGE || box.x1 > 1 - EDGE) return res("off_centre", "Move toward the middle", toMiddle, box);

  // 5. Side-on or facing, as the exercise needs (standing and seated only: lying trunks read oddly).
  if (!p.lying && recent.length >= 8) {
    const vc = viewCheck(recent, view);
    if (vc.mismatch === "front_as_side") return res("turn_side", "Turn sideways to the tablet", null, box);
    if (vc.mismatch === "side_as_front") return res("face", "Face the tablet", null, box);
  }
  return res("ok", "You're in the right spot", null, box);
}
