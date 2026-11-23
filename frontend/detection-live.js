/* CoRehab detection: the tablet camera counts reps, checks form and scores each rep.
   Pose: MediaPipe Pose Landmarker (Full) in the browser. Rep counting, error checks and the
   1-5 score come from frontend/detection/ (the rehab tracker engine, calibrated on a PT-approved
   recording session; see detection/README.md).
   patient.html calls window.CAM:
     CAM.afterRender()        after every render: shows the live camera on the setup/exercise screens
     CAM.startExercise(e)     when an exercise starts
     CAM.counting()           true while the camera (not the demo timer) counts reps
     CAM.resultFor(id, reps)  { form_scores, error_counts } for POST /api/result
   The camera calls CRAPP.camRep(fixText) once per finished rep.
   No camera, a refused permission or ?camera=0: the prototype's demo timer runs as before.
   Video is never stored or sent: only the counts and scores leave the tablet. */
import { FilesetResolver, PoseLandmarker } from
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.2.3/vision_bundle.mjs";
import { toFrame, facingOf, vis, rotationForHead, unrotateLandmarks, drawRotated } from "./detection/geometry.js";
import { analyze } from "./detection/engine.js";
import { PoseSmoother } from "./detection/smooth.js";
import { EXERCISES } from "./detection/exercises.js";
import { scoreRep5, FIX_TEXT } from "./detection/score.js";
import { TO_TRACKER, trackerId, errorName, toApiResult } from "./detection/corehab-map.js";

const WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.2.3/wasm";
const MODEL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task";
const ROTATION_CYCLE = [90, -90, 0];
const WINDOW_S = 90;                       // frames kept for rep counting
const CAM_SCREENS = ["setup", "intro", "exercise", "after"];   // keep the camera on between exercises

const BONES = [["shoulder", "hip"], ["hip", "knee"], ["knee", "ankle"], ["ankle", "heel"], ["heel", "toe"], ["ankle", "toe"]];

const CAM = {
  enabled: !/[?&]camera=0\b/.test(location.search) && !!navigator.mediaDevices?.getUserMedia && location.protocol !== "file:",
  stream: null, landmarker: null, starting: null, failed: false, looping: false,
  video: document.createElement("video"), canvas: document.createElement("canvas"),
  ex: null, exId: null, view: "side", op: "right", frames: [], smoother: new PoseSmoother(),
  counted: 0, scores: [], errors: {}, perEx: {}, lastTs: 0, t0: performance.now(), frameNo: 0, leg: null,
  rotation: 0, rotCanvas: null, noPoseSince: null, poseRun: 0, headVotes: 0, lyingRun: 0, flipRun: 0, uprightRun: 0,
  inFrameRun: 0, lastFrame: null,
};
window.CAM = CAM;
CAM.video.playsInline = true; CAM.video.muted = true;
CAM.canvas.className = "cr-live";
const g = CAM.canvas.getContext("2d");

const style = document.createElement("style");
style.textContent = `.cam>canvas.cr-live{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;zoom:1}
.cam.flip>canvas.cr-live{transform:scaleX(-1)}`;
document.head.appendChild(style);

const app = () => window.CRAPP;
const live = () => CAM.enabled && !CAM.failed && !!CAM.stream && !!CAM.landmarker;
CAM.counting = () => live() && app()?.S.screen === "exercise";
CAM.active = live;

// ---------- camera + model ----------
async function start() {
  if (CAM.starting) return CAM.starting;
  CAM.starting = (async () => {
    try {
      CAM.stream = await navigator.mediaDevices.getUserMedia({ audio: false,
        video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } } });
      CAM.video.srcObject = CAM.stream; await CAM.video.play();
      const fileset = await FilesetResolver.forVisionTasks(WASM);
      for (const delegate of ["GPU", "CPU"]) {
        try {
          CAM.landmarker = await PoseLandmarker.createFromOptions(fileset, {
            baseOptions: { modelAssetPath: MODEL, delegate }, runningMode: "VIDEO", numPoses: 1,
            minPoseDetectionConfidence: 0.5, minPosePresenceConfidence: 0.5, minTrackingConfidence: 0.5 });
          break;
        } catch (e) { if (delegate === "CPU") throw e; }
      }
      CAM.looping = true; requestAnimationFrame(loop);
      app()?.render();
    } catch (e) {
      console.warn("Camera tracking off:", e);
      CAM.failed = true; stop();
      window.CR?.toast?.("The camera isn't available, so reps are counted on a timer for now.", true);
      app()?.render();
    }
  })();
  return CAM.starting;
}
function stop() {
  CAM.looping = false;
  CAM.stream?.getTracks().forEach((t) => t.stop());
  CAM.stream = null; CAM.starting = null; CAM.video.srcObject = null;
}

// ---------- screens ----------
CAM.afterRender = function () {
  const a = app(); if (!a || !CAM.enabled || CAM.failed) return;
  const scr = a.S.screen;
  if (!CAM_SCREENS.includes(scr)) { if (CAM.stream) stop(); return; }
  if (!CAM.stream && !CAM.starting) start();
  if (scr === "setup" || scr === "exercise") {
    const img = document.querySelector(".cam>img");
    if (img && live()) { img.replaceWith(CAM.canvas); }
  }
};

CAM.startExercise = function (e) {
  CAM.exId = e.id;
  CAM.ex = EXERCISES[trackerId(e.id)] ?? null;
  CAM.view = CAM.ex?.views.side ? "side" : "front";
  CAM.op = window.CR?.home?.side ?? window.CR?.side ?? "right";
  CAM.frames = []; CAM.counted = 0; CAM.scores = []; CAM.errors = {}; CAM.leg = null; CAM.lastRepEnd = undefined;
  CAM.smoother.reset?.(); setRotation(0);
};

function ctx(facing = 1) {
  return { view: CAM.view, op: CAM.op, good: CAM.op === "left" ? "right" : "left", facing, prefer: CAM.leg ?? CAM.op };
}

// ---------- per frame ----------
function nextTs(p) { CAM.lastTs = Math.max(p, CAM.lastTs + 1); return CAM.lastTs; }
function setRotation(r) {
  if (CAM.rotation === r) return;
  CAM.rotation = r; CAM.smoother.reset?.();
  Object.assign(CAM, { headVotes: 0, lyingRun: 0, flipRun: 0, uprightRun: 0 });
}
// Lying exercises: rotate the frame 90° so the model sees the person upright, then map back.
function detect(ts) {
  const v = CAM.video, W = v.videoWidth, H = v.videoHeight, now = ts / 1000;
  const lying = !!CAM.ex?.views[CAM.view]?.lying && app()?.S.screen === "exercise";
  if (!lying) setRotation(0);
  let source = v;
  if (CAM.rotation) {
    CAM.rotCanvas ??= document.createElement("canvas");
    const rc = CAM.rotCanvas;
    if (rc.width !== H || rc.height !== W) { rc.width = H; rc.height = W; }
    drawRotated(rc.getContext("2d"), v, W, H, CAM.rotation);
    source = rc;
  }
  const lm = CAM.landmarker.detectForVideo(source, ts).landmarks?.[0];
  if (!lm) {
    CAM.noPoseSince ??= now;
    if (lying && now - CAM.noPoseSince > (CAM.poseRun >= 30 ? 2.5 : 0.8)) {
      setRotation(ROTATION_CYCLE[(ROTATION_CYCLE.indexOf(CAM.rotation) + 1) % ROTATION_CYCLE.length]);
      CAM.noPoseSince = now; CAM.poseRun = 0;
    }
    return undefined;
  }
  CAM.noPoseSince = null; CAM.poseRun++;
  const rot = CAM.rotation, out = unrotateLandmarks(lm, rot);
  if (lying) {
    const sw = rot ? H : W, sh = rot ? W : H;
    const dx = (lm[0].x - (lm[23].x + lm[24].x) / 2) * sw, dy = (lm[0].y - (lm[23].y + lm[24].y) / 2) * sh;
    const sideways = Math.abs(dx) > Math.abs(dy);
    if (rot === 0) {
      CAM.lyingRun = sideways ? CAM.lyingRun + 1 : 0;
      CAM.headVotes = sideways ? CAM.headVotes + Math.sign(dx) : 0;
      if (CAM.lyingRun >= 5) setRotation(rotationForHead(CAM.headVotes < 0));
    } else {
      CAM.flipRun = !sideways && dy > 0 ? CAM.flipRun + 1 : 0;
      CAM.uprightRun = sideways ? CAM.uprightRun + 1 : 0;
      if (CAM.flipRun >= 8) setRotation(-rot);
      else if (CAM.uprightRun >= 15) setRotation(0);
    }
  }
  return out;
}

function draw(f) {
  const W = CAM.canvas.width, H = CAM.canvas.height;
  g.drawImage(CAM.video, 0, 0, W, H);
  if (!f) return;
  const leg = CAM.leg ?? CAM.op, other = leg === "left" ? "right" : "left", s = W / 1280;
  const seg = (a, b, side, col, w) => {
    const p = f[a][side], q = f[b][side];
    if (!vis(p) || !vis(q)) return;
    g.strokeStyle = col; g.lineWidth = w * Math.max(1, s); g.lineCap = "round";
    g.beginPath(); g.moveTo(p[0], p[1]); g.lineTo(q[0], q[1]); g.stroke();
  };
  for (const [a, b] of BONES) seg(a, b, other, "rgba(200,200,200,.7)", 4);
  for (const [a, b] of BONES) seg(a, b, leg, "#3CDC78", 8);
  for (const j of ["hip", "knee", "ankle"]) {
    const p = f[j][leg]; if (!vis(p)) continue;
    g.fillStyle = "#3CDC78"; g.beginPath(); g.arc(p[0], p[1], 9 * Math.max(1, s), 0, 7); g.fill();
  }
}

function loop() {
  if (!CAM.looping) return;
  const v = CAM.video;
  if (v.readyState >= 2 && v.currentTime !== CAM.lastVideoTime && CAM.landmarker) {
    CAM.lastVideoTime = v.currentTime;
    if (CAM.canvas.width !== v.videoWidth) { CAM.canvas.width = v.videoWidth; CAM.canvas.height = v.videoHeight; }
    const now = performance.now(), t = (now - CAM.t0) / 1000;
    const lm = detect(nextTs(now));
    const f = lm ? toFrame(CAM.smoother.update(lm, t, v.videoWidth, v.videoHeight), v.videoWidth, v.videoHeight, t) : null;
    draw(f);
    onFrame(f, t);
  }
  requestAnimationFrame(loop);
}

function onFrame(f, t) {
  const a = app(); if (!a) return;
  const S = a.S;
  // Setup: "You're in the frame" once a whole leg (hip, knee, ankle) is seen for about a second.
  if (S.screen === "setup") {
    const seen = f && ["left", "right"].some((s) => ["hip", "knee", "ankle"].every((j) => vis(f[j][s])));
    CAM.inFrameRun = seen ? CAM.inFrameRun + 1 : 0;
    const inFrame = CAM.inFrameRun >= 20;
    if (inFrame !== !!S.inFrame) { S.inFrame = inFrame; a.render(); }
    return;
  }
  if (S.screen !== "exercise" || !CAM.ex || !f) return;
  CAM.frames.push(f);
  while (CAM.frames.length && CAM.frames[0].t < t - WINDOW_S) CAM.frames.shift();
  if (++CAM.frameNo % 5 || CAM.frames.length < 10) return;
  const facing = CAM.view === "side" ? (CAM.frames.slice(-60).map(facingOf).reduce((x, y) => x + y, 0) >= 0 ? 1 : -1) : 1;
  const r = analyze(CAM.frames, CAM.ex, ctx(facing), {});
  CAM.leg = r.leg ?? CAM.leg;
  // Count only reps that finished after the last one we counted; while paused or resting, skip them.
  const reps = r.reps.filter((x) => x.t_end > (CAM.lastRepEnd ?? -Infinity));
  if (!reps.length) return;
  CAM.lastRepEnd = reps[reps.length - 1].t_end;
  if (S.paused || S.ending || S.resting) return;
  for (const rep of reps) {
    const sc = scoreRep5(rep, trackerId(CAM.exId));
    CAM.scores.push(sc.score);
    for (const e of rep.errors) { const k = errorName(e.id); CAM.errors[k] = (CAM.errors[k] || 0) + 1; }
    const fix = rep.errors.length ? (FIX_TEXT[rep.errors[0].id] ?? rep.errors[0].label) + "." : "";
    CAM.perEx[CAM.exId] = { scores: CAM.scores.slice(), errors: { ...CAM.errors } };
    if (!a.camRep || !a.camRep(fix)) break;
  }
}

// ---------- results for POST /api/result ----------
// form_scores: one value 0-1 per rep (1-5 score mapped: 5 -> 1.0, 3 -> 0.5, 1 -> 0.0).
CAM.resultFor = function (exerciseId, reps) {
  const d = CAM.perEx[exerciseId];
  return d ? toApiResult(d.scores, d.errors, reps) : null;
};

// Testing hook: run the engine on recorded frames without a camera (see detection/tests).
CAM._analyzeFrames = (exerciseId, frames, view = "side", op = "right") => {
  const ex = EXERCISES[trackerId(exerciseId)];
  return analyze(frames, ex, { view, op, good: op === "left" ? "right" : "left", facing: 1, prefer: op }, {});
};
CAM.TO_TRACKER = TO_TRACKER;

app()?.render();
