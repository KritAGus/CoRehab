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
   On the exercise screen it also shows a live guide (dashed cyan: where to move to, from the
   PT-approved reference), a gauge with the live angle against the target, each rep's 1-5 score,
   and, when the app's voice guide is on, says the score and what to fix.
   No camera, a refused permission or ?camera=0: the prototype's demo timer runs as before.
   Video is never stored or sent: only the counts and scores leave the tablet. */
import { FilesetResolver, PoseLandmarker } from
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.2.3/vision_bundle.mjs";
import { toFrame, facingOf, vis, rotationForHead, unrotateLandmarks, drawRotated } from "./detection/geometry.js";
import { analyze, liveCue } from "./detection/engine.js";
import { PoseSmoother } from "./detection/smooth.js";
import { EXERCISES } from "./detection/exercises.js";
import { scoreRep5, FIX_TEXT } from "./detection/score.js";
import { guideFor, guidePose, repSpeech } from "./detection/guide.js";
import { checkPosition, setupTip, targetZone } from "./detection/position.js";
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
  // live guide + feedback
  hud: document.createElement("canvas"), guide: null, result: null, rctx: null, gp: null, mem: {},
  repLive: null, flash: null, cue: null,
  // where to stand / lie
  pos: null, posIds: [], posBadSince: null, posShown: null, posSince: 0, posSaidAt: -1e9, posPrepId: null,
};
window.CAM = CAM;
CAM.video.playsInline = true; CAM.video.muted = true;
CAM.canvas.className = "cr-live";
CAM.hud.className = "cr-hud";
const g = CAM.canvas.getContext("2d");

const style = document.createElement("style");
style.textContent = `.cam>canvas.cr-live{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;zoom:1}
.cam.flip>canvas.cr-live{transform:scaleX(-1)}
.cam.cr-on .frame{display:none}
.cam>canvas.cr-hud{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;zoom:1}`;
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
    if (live() && CAM.canvas.isConnected && CAM.hud.previousSibling !== CAM.canvas) CAM.canvas.after(CAM.hud);
    document.querySelector(".cam")?.classList.toggle("cr-on", live());
    if (scr === "setup") prep(a.PLAN?.[a.S.exIdx]);
  }
};

// Which exercise the camera is set up for (setup screen: the next one; exercise screen: this one).
function prep(e) {
  if (!e || CAM.posPrepId === e.id) return;
  CAM.posPrepId = e.id; CAM.exId = e.id;
  CAM.ex = EXERCISES[trackerId(e.id)] ?? null;
  CAM.view = CAM.ex?.views.side ? "side" : "front";
  Object.assign(CAM, { pos: null, posIds: [], posBadSince: null, posShown: null });
}
CAM.setupTip = (e) => (e ? setupTip(trackerId(e.id), EXERCISES[trackerId(e.id)]?.views.side ? "side" : "front") : null);

CAM.startExercise = function (e) {
  CAM.posPrepId = null; prep(e);
  CAM.op = window.CR?.home?.side ?? window.CR?.side ?? "right";
  CAM.frames = []; CAM.counted = 0; CAM.scores = []; CAM.errors = {}; CAM.leg = null; CAM.lastRepEnd = undefined;
  CAM.smoother.reset?.(); setRotation(0);
  CAM.guide = CAM.ex ? guideFor(trackerId(e.id), CAM.view) : null;
  Object.assign(CAM, { result: null, rctx: null, gp: null, mem: {}, repLive: null, flash: null, cue: null });
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
  if (!f) { drawZone(Math.max(1, W / 1280)); return; }
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
  drawGhost(Math.max(1, s));
  drawZone(Math.max(1, s));
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
    onFrame(f, t);
    draw(f);
    drawHud(t);
  }
  requestAnimationFrame(loop);
}

function onFrame(f, t) {
  const a = app(); if (!a) return;
  const S = a.S;
  if ((!S.voice || S.paused || S.screen !== "exercise") && window.speechSynthesis?.speaking && performance.now() / 1000 - VOICE.lastAt > 0.3
      && !(S.screen === "after" && performance.now() / 1000 - VOICE.lastAt < 4)) window.speechSynthesis.cancel();
  // Setup: where to be for this exercise (position.js). Once the patient has been in the right spot
  // for about a second, a countdown starts and the exercise begins by itself, so they don't have to
  // walk to the screen and tap Start (which moved them out of the frame). Start still works by hand.
  if (S.screen !== "setup" && CAM.cdStart != null) { CAM.cdStart = null; S.countdown = null; }
  if (S.screen === "setup") {
    if (f) { CAM.posRecent = [...(CAM.posRecent ?? []), f].slice(-30); }
    const W = CAM.video.videoWidth, H = CAM.video.videoHeight;
    const pos = CAM.ex ? checkPosition(trackerId(CAM.exId), CAM.view, f, W, H, CAM.posRecent ?? [], "setup") : null;
    const ok = pos ? pos.ok : !!(f && ["left", "right"].some((s) => ["hip", "knee", "ankle"].every((j) => vis(f[j][s]))));
    CAM.pos = pos;
    CAM.inFrameRun = ok ? CAM.inFrameRun + 1 : 0;
    const inFrame = CAM.inFrameRun >= 20;
    const hint = stableHint(pos, t);
    if (ok) CAM.okAt = t;
    if (inFrame !== !!S.inFrame || (hint && hint !== S.posHint)) {
      if (inFrame && !S.inFrame && CAM.cdStart == null) say(`Good, hold still. Starting in ${AUTO_START_SEC} seconds.`, { urgent: true });
      S.inFrame = inFrame; if (hint) S.posHint = hint; a.render();
    }
    if (CAM.cdStart == null && inFrame) { CAM.cdStart = t; CAM.cdSaid = AUTO_START_SEC; }
    if (CAM.cdStart != null) {
      if (t - (CAM.okAt ?? t) > 0.8) {                       // left the spot (brief detection blips are ignored)
        CAM.cdStart = null; S.countdown = null; a.render();
        say("Paused. Move back into the box.", { urgent: true });
      } else {
        const left = Math.ceil(AUTO_START_SEC - (t - CAM.cdStart));
        if (left !== S.countdown) { S.countdown = left; if (left > 0) a.render(); }
        if (left <= 3 && left > 0 && left < CAM.cdSaid) { CAM.cdSaid = left; say(String(left), { urgent: true }); }
        if (left <= 0) {
          CAM.cdStart = null; S.countdown = null;
          const b = document.querySelector("[data-startex]");
          if (b) { b.disabled = false; b.click(); }
        }
      }
      return;
    }
    if (!inFrame && hint && t - CAM.posSince > 1.5 && t - CAM.posSaidAt > 5) { CAM.posSaidAt = t; say(hint, { urgent: true }); }
    return;
  }
  if (S.screen === "exercise" && CAM.ex) exercisePosition(f, t, S);
  if (S.screen !== "exercise" || !CAM.ex || !f) return;
  CAM.frames.push(f);
  while (CAM.frames.length && CAM.frames[0].t < t - WINDOW_S) CAM.frames.shift();
  liveGuide(f, t, S);
  if (++CAM.frameNo % 5 || CAM.frames.length < 10) return;
  const facing = CAM.view === "side" ? (CAM.frames.slice(-60).map(facingOf).reduce((x, y) => x + y, 0) >= 0 ? 1 : -1) : 1;
  const r = analyze(CAM.frames, CAM.ex, ctx(facing), {});
  CAM.leg = r.leg ?? CAM.leg;
  CAM.result = r; CAM.rctx = { ...ctx(facing), leg: CAM.leg ?? CAM.op };
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
    const reasons = [...new Set([...rep.errors.map((e) => e.id), ...sc.reasons])];
    CAM.perEx[CAM.exId] = { scores: CAM.scores.slice(), errors: { ...CAM.errors },
      reasons: [...(CAM.perEx[CAM.exId]?.reasons ?? []), ...reasons] };
    if (!a.camRep || !a.camRep(fix)) break;
    CAM.flash = { n: CAM.scores.length, score: sc.score, tip: tipFor(reasons), until: t + 3 };
    say(repSpeech(CAM.scores.length, sc.score, reasons, FIX_TEXT), { urgent: true });
  }
}

// ---------- where to be ----------
// The instruction shown is the most common one over the last half second, so it doesn't flicker.
function stableHint(pos, t) {
  if (!pos) return null;
  CAM.posIds.push(pos.ok ? "" : pos.text);
  if (CAM.posIds.length > 15) CAM.posIds.shift();
  const c = {}; for (const x of CAM.posIds) c[x] = (c[x] || 0) + 1;
  const top = Object.entries(c).sort((a, b) => b[1] - a[1])[0][0];
  if (top !== CAM.posShown) { CAM.posShown = top; CAM.posSince = t; }
  return top || null;
}
// While exercising: only what breaks tracking, after it has lasted a second; said between reps
// (or mid-rep once it has lasted 3 s).
function exercisePosition(f, t, S) {
  if (S.paused || S.ending || S.resting) { CAM.posBadSince = null; CAM.posIssue = null; return; }
  const W = CAM.video.videoWidth, H = CAM.video.videoHeight;
  const pos = checkPosition(trackerId(CAM.exId), CAM.view, f, W, H, [], "exercise");
  CAM.pos = pos;
  if (pos.ok) { CAM.posBadSince = null; CAM.posIssue = null; return; }
  CAM.posBadSince ??= t;
  if (t - CAM.posBadSince < 1) return;
  CAM.posIssue = pos;
  if ((!CAM.repLive || t - CAM.posBadSince > 3) && t - CAM.posSaidAt > 6) { CAM.posSaidAt = t; say(pos.text, { urgent: true }); }
}

// Target zone and arrow on the video (flips with it, so the arrow points the right way on screen).
function drawZone(s) {
  const S = app()?.S, scr = S?.screen;
  const show = scr === "setup" || (scr === "exercise" && CAM.posIssue);
  if (!show || !CAM.ex) return;
  const z = targetZone(trackerId(CAM.exId), CAM.view); if (!z) return;
  const W = CAM.canvas.width, H = CAM.canvas.height, ok = scr === "setup" ? !!S.inFrame : false;
  g.save();
  g.strokeStyle = ok ? GREEN : "rgba(255,255,255,.85)"; g.lineWidth = 5 * s; g.setLineDash(ok ? [] : [18 * s, 14 * s]);
  rr(g, z.x0 * W, z.y0 * H, (z.x1 - z.x0) * W, (z.y1 - z.y0) * H, 28 * s); g.stroke();
  const pos = scr === "setup" ? CAM.pos : CAM.posIssue;
  if (pos?.dir && pos.box) {
    const cx = ((pos.box.x0 + pos.box.x1) / 2) * W, cy = ((pos.box.y0 + pos.box.y1) / 2) * H, L = 150 * s, d = pos.dir[0];
    g.setLineDash([]); g.strokeStyle = AMBER; g.fillStyle = AMBER; g.lineWidth = 12 * s;
    g.beginPath(); g.moveTo(cx, cy); g.lineTo(cx + d * L, cy); g.stroke();
    g.beginPath(); g.moveTo(cx + d * (L + 34 * s), cy); g.lineTo(cx + d * L, cy - 28 * s); g.lineTo(cx + d * L, cy + 28 * s); g.fill();
  }
  g.restore();
}

// ---------- live guide, voice and on-screen score ----------
const SAY_RATE = [0.85, 1, 1.15];             // the app's Voice guide speed: Slower / Normal / Faster
const VOICE = { lastAt: -1e9, lastText: "", lastTextAt: -1e9 };
const AUTO_START_SEC = 5;   // seconds in the right spot before the exercise starts by itself

function say(text, { urgent = false, gap = 1.5 } = {}) {
  const S = app()?.S, sp = window.speechSynthesis;
  if (!text || !S?.voice || !sp) return;
  const now = performance.now() / 1000;
  if (!urgent && (sp.speaking || now - VOICE.lastAt < gap)) return;
  if (!urgent && text === VOICE.lastText && now - VOICE.lastTextAt < 6) return;   // don't nag
  if (urgent) sp.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = SAY_RATE[S.voiceSpeed ?? 1] ?? 1; u.lang = "en-US";
  sp.speak(u);
  VOICE.lastAt = now; VOICE.lastText = text; VOICE.lastTextAt = now;
}
CAM.say = say;

const TIP = { much_less_movement: "Go a little further", less_movement: "Go a little further", much_quicker: "Slow down" };
function tipFor(reasons) { const r = reasons[0]; return r ? FIX_TEXT[r] ?? TIP[r] ?? "" : ""; }

// Per frame: where the guide is, and live spoken cues during the rep.
//   stuck short of the target for 0.7 s -> "A little further"
//   past the target where that's an error, or a form warning from the exercise -> say it
//   target reached -> "Good"
// "Slow down" comes with the rep's score (score.js: much quicker than the PT reps).
function liveGuide(f, t, S) {
  const r = CAM.result;
  CAM.gp = null; CAM.cue = null;
  if (!CAM.guide || !r || !Number.isFinite(r.base?.signal) || S.paused || S.ending || S.resting) return;
  const gp = guidePose(CAM.guide, CAM.rctx, f, r.base, r.scale, CAM.mem);
  CAM.gp = gp;
  if (!gp) return;
  CAM.cue = liveCue(CAM.ex, CAM.rctx, f, r.base);
  const L = CAM.repLive;
  if (gp.status === "rest") { CAM.repLive = null; return; }
  if (!L) { CAM.repLive = { t0: t, max: gp.exc, maxAt: t, saidGood: false, saidMore: false, saidOver: false }; return; }
  if (gp.exc > L.max + 0.02 * gp.band.p50) { L.max = gp.exc; L.maxAt = t; }
  if ((gp.status === "good" || gp.status === "over") && !L.reached) L.reached = t;
  if (gp.status === "over" && !L.saidOver) { L.saidOver = true; say(CAM.cue ?? "Not so far", { urgent: true }); return; }
  if (CAM.cue && gp.status !== "over") { say(CAM.cue); return; }
  if (gp.status === "good" && !L.saidGood && !L.saidOver) { L.saidGood = true; say("Good"); return; }
  if (gp.status === "more" && !L.saidMore && !L.reached && gp.exc > 0.4 * gp.band.p50 && t - L.maxAt > 0.7) {
    L.saidMore = true; say("A little further");
  }
}

const CYAN = "#50DCFF", GREEN = "#3CDC78", AMBER = "#FFBE3C", RED = "#FF5A50";
const statusColor = (st) => (st === "good" ? GREEN : st === "over" ? RED : st === "more" ? AMBER : "#F5F5F5");
const scoreColor = (n) => (n >= 5 ? GREEN : n >= 3 ? AMBER : RED);

// The guide on the video (flips with it): dashed cyan where to move, ring at the target.
function drawGhost(s) {
  const gp = CAM.gp, gh = gp?.ghost;
  if (!gh || app()?.S.screen !== "exercise") return;
  const ring = gp.status === "good" ? GREEN : gp.status === "over" ? RED : CYAN;
  g.save(); g.strokeStyle = CYAN; g.lineWidth = 7 * s; g.lineCap = "round"; g.setLineDash([16 * s, 14 * s]);
  if (gh.kind === "rot") {
    for (const [p, q] of gh.segments) { g.beginPath(); g.moveTo(p[0], p[1]); g.lineTo(q[0], q[1]); g.stroke(); }
    g.setLineDash([]); g.lineWidth = 5 * s; g.strokeStyle = ring;
    g.beginPath(); g.arc(gh.end[0], gh.end[1], 22 * s, 0, 7); g.stroke();
  } else {
    // a target line across the body where the hips (or foot) should get to, and an arrow to it
    const [x, y] = gh.at, half = 170 * s, horiz = gh.axis === "y";
    g.beginPath();
    if (horiz) { g.moveTo(x - half, y); g.lineTo(x + half, y); } else { g.moveTo(x, y - half); g.lineTo(x, y + half); }
    g.stroke(); g.setLineDash([]);
    const [nx, ny] = gh.now, d = Math.hypot(x - nx, y - ny);
    if (d > 30 * s) {
      const ux = (x - nx) / d, uy = (y - ny) / d, hx = x - ux * 16 * s, hy = y - uy * 16 * s;
      g.strokeStyle = ring; g.lineWidth = 5 * s;
      g.beginPath(); g.moveTo(nx, ny); g.lineTo(hx, hy); g.stroke();
      g.fillStyle = ring; g.beginPath(); g.moveTo(x, y);
      g.lineTo(hx - uy * 12 * s, hy + ux * 12 * s); g.lineTo(hx + uy * 12 * s, hy - ux * 12 * s); g.fill();
    }
  }
  g.restore();
}

function rr(c, x, y, w, h, r) { c.beginPath(); c.roundRect ? c.roundRect(x, y, w, h, r) : c.rect(x, y, w, h); }
function stars(c, x, y, n, size) {
  for (let i = 0; i < 5; i++) {
    const cx = x + i * size * 1.15 + size / 2, cy = y + size / 2;
    c.fillStyle = i < n ? AMBER : "rgba(255,255,255,.25)"; c.beginPath();
    for (let k = 0; k < 10; k++) {
      const a = -Math.PI / 2 + (k * Math.PI) / 5, rad = k % 2 ? size * 0.21 : size * 0.5;
      c.lineTo(cx + rad * Math.cos(a), cy + rad * Math.sin(a));
    }
    c.fill();
  }
}

// Gauge, rep scores and the score after each rep. Not mirrored, so text stays readable.
function drawHud(t) {
  const h = CAM.hud, S = app()?.S;
  if (!h.isConnected) return;
  const dpr = window.devicePixelRatio || 1, W = h.clientWidth, H = h.clientHeight;
  if (h.width !== Math.round(W * dpr) || h.height !== Math.round(H * dpr)) { h.width = Math.round(W * dpr); h.height = Math.round(H * dpr); }
  const c = h.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, H);
  if (S?.screen !== "exercise" || S.paused || S.resting || S.ending) return;
  const font = (px, w = 600) => `${w} ${px}px system-ui, -apple-system, "Segoe UI", sans-serif`;
  const gp = CAM.gp, G = CAM.guide;

  // left: the gauge (below the exercise name, above the cue bar)
  const x = 24, y = 150, pw = 200, ph = Math.min(380, H - y - 260);
  if (G && ph > 200) {
    c.fillStyle = "rgba(14,17,22,.72)"; rr(c, x, y, pw, ph, 20); c.fill();
    c.fillStyle = "#C8CDD4"; c.font = font(18); c.fillText(G.sig.label, x + 18, y + 32);
    const deg = (G.sig.unit ?? "°") === "°";
    const val = !gp ? "--" : deg ? `${Math.round(Math.abs(gp.value))}°` : `${Math.max(0, Math.round((100 * gp.exc) / gp.band.p50))}%`;
    c.fillStyle = gp ? statusColor(gp.status) : "#F5F5F5"; c.font = font(48, 700); c.fillText(val, x + 18, y + 84);
    if (gp) {
      c.fillStyle = CYAN; c.font = font(20);
      c.fillText(deg ? `Aim ${Math.round(Math.abs(gp.target))}°` : "Aim 100%", x + 18, y + 114);
    }
    // bar: 0 at the bottom, PT range in green, target tick in cyan, you = white marker
    const bx = x + pw - 46, by = y + 24, bw = 22, bh = ph - 100;
    const m = G.ref.movement, top = Math.max(m.p90 * 1.5, (gp?.exc ?? 0) * 1.05), Y = (v) => by + bh - (bh * Math.max(0, Math.min(v, top))) / top;
    c.fillStyle = "rgba(255,255,255,.18)"; rr(c, bx, by, bw, bh, 11); c.fill();
    const hiEdge = gp && Number.isFinite(gp.over) ? gp.over : m.p90 * 1.15;
    c.fillStyle = "rgba(60,220,120,.55)"; rr(c, bx, Y(hiEdge), bw, Y(m.p10 - 0.1 * m.p50) - Y(hiEdge), 6); c.fill();
    c.strokeStyle = CYAN; c.lineWidth = 4; c.beginPath(); c.moveTo(bx - 8, Y(m.p50)); c.lineTo(bx + bw + 8, Y(m.p50)); c.stroke();
    if (gp) {
      const my = Y(Math.max(0, gp.exc));
      c.fillStyle = statusColor(gp.status); c.beginPath(); c.moveTo(bx - 4, my); c.lineTo(bx - 20, my - 10); c.lineTo(bx - 20, my + 10); c.fill();
      c.fillRect(bx - 2, my - 3, bw + 4, 6);
    }
    const word = !gp ? "Getting ready" : CAM.cue ?? { rest: "Ready", more: "Keep going", good: "Good", over: "Too far" }[gp.status];
    c.fillStyle = gp ? statusColor(CAM.cue ? "over" : gp.status) : "#C8CDD4"; c.font = font(19);
    wrap(c, word, x + 18, y + 148, pw - 80, 24, 3);
    // rep scores so far
    const sc = CAM.scores, cs = 30, per = 5;
    c.font = font(17, 700); c.textAlign = "center";
    sc.slice(-15).forEach((v, i) => {
      const cx = x + 18 + (i % per) * (cs + 4), cy = y + ph - 22 - (Math.ceil(Math.min(sc.length, 15) / per) - 1 - Math.floor(i / per)) * (cs + 4) - cs;
      c.fillStyle = scoreColor(v); rr(c, cx, cy, cs, cs, 7); c.fill();
      c.fillStyle = "#0E1116"; c.fillText(String(v), cx + cs / 2, cy + cs / 2 + 6);
    });
    c.textAlign = "left";
  }

  // centre: where to move, when the patient has drifted out of position
  if (CAM.posIssue) {
    c.font = font(30, 700); const tw = c.measureText(CAM.posIssue.text).width, bw = tw + 64, bx = (W - bw) / 2, by = H * 0.36;
    c.fillStyle = "rgba(255,190,60,.95)"; rr(c, bx, by, bw, 70, 35); c.fill();
    c.fillStyle = "#0E1116"; c.fillText(CAM.posIssue.text, bx + 32, by + 46);
  }
  // top centre: the score of the rep just finished
  const fl = CAM.flash;
  if (fl && t < fl.until) {
    const fw = 360, fh = fl.tip ? 112 : 84, fx = (W - fw) / 2, fy = 24;
    c.fillStyle = "rgba(14,17,22,.85)"; rr(c, fx, fy, fw, fh, 20); c.fill();
    c.fillStyle = scoreColor(fl.score); c.font = font(30, 700); c.fillText(`Rep ${fl.n}: ${fl.score}/5`, fx + 22, fy + 50);
    stars(c, fx + fw - 22 - 5 * 26 * 1.15, fy + 28, fl.score, 26);
    if (fl.tip) { c.fillStyle = "#F5F5F5"; c.font = font(20); c.fillText(fl.tip, fx + 22, fy + 90); }
  }
}
function wrap(c, text, x, y, w, lh, max) {
  const words = String(text).split(" "); let line = "", n = 0;
  for (const wd of words) {
    const test = line ? line + " " + wd : wd;
    if (c.measureText(test).width > w && line) { c.fillText(line, x, y + n * lh); line = wd; if (++n >= max) return; }
    else line = test;
  }
  if (line) c.fillText(line, x, y + n * lh);
}

// "After each exercise" screen: the form score for that exercise.
CAM.summaryHtml = function (exerciseId) {
  const d = CAM.perEx[exerciseId];
  if (!d?.scores.length) return "";
  const avg = d.scores.reduce((a, b) => a + b, 0) / d.scores.length, st = Math.min(5, Math.max(1, Math.floor(avg + 0.25)));
  const counts = {}; for (const r of d.reasons ?? []) counts[r] = (counts[r] || 0) + 1;
  const main = Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0];
  const tip = main ? tipFor([main]) : "Nice form. Keep it up!";
  const star = (on) => `<svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true"><path d="M12 2.5l2.9 6.1 6.6.8-4.9 4.6 1.3 6.6L12 17.3l-5.9 3.3 1.3-6.6-4.9-4.6 6.6-.8z" fill="${on ? "#F5A524" : "#D5D9DF"}"/></svg>`;
  return `<div class="card crscore" role="status" aria-label="Form score ${avg.toFixed(1)} out of 5">
   <div class="crs-top"><span class="crs-stars">${[0, 1, 2, 3, 4].map((i) => star(i < st)).join("")}</span><b>${avg.toFixed(1)}<small> / 5</small></b></div>
   <div class="crs-reps">${d.scores.map((v) => `<i class="s${v >= 5 ? 5 : v >= 3 ? 3 : 1}">${v}</i>`).join("")}</div>
   <p>${tip}</p></div>`;
};
style.textContent += `.crscore{margin-top:20px;padding:22px 26px;display:flex;flex-direction:column;gap:12px}
.crs-top{display:flex;align-items:center;gap:18px}.crs-top b{font-size:40px;font-weight:700}.crs-top small{font-size:22px;color:var(--ink-muted)}
.crs-stars{display:flex;gap:2px}.crs-reps{display:flex;flex-wrap:wrap;gap:6px}
.crs-reps i{font-style:normal;width:38px;height:38px;border-radius:9px;display:grid;place-items:center;font-weight:700;font-size:18px;color:#0E1116}
.crs-reps .s5{background:#3CDC78}.crs-reps .s3{background:#FFBE3C}.crs-reps .s1{background:#FF8A80}
.crscore p{margin:0;font-size:21px;font-weight:600}`;

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
