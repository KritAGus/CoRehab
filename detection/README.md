# Detection: pose, rep counting, form checks and scoring

The tablet camera counts the patient's reps, checks their form and scores every rep from 1 to 5.
The result goes to `POST /api/result` as `reps_done`, `form_scores` (one 0-1 value per rep) and `error_counts`.
Video is never stored or sent; only the numbers leave the tablet.

## Where the code is

| File | What it does |
|---|---|
| `frontend/detection-live.js` | Runs on the patient tablet: camera, MediaPipe Pose (Full model), skeleton overlay, live rep counting, per-rep score, result for the API |
| `frontend/detection/engine.js` | Rep counting (signal per exercise, start/end thresholds, leg choice on side views) and error checks |
| `frontend/detection/exercises.js` | The 10 exercises: joints tracked, rep signal, error checks and their limits (`T`) |
| `frontend/detection/score.js` | 1-5 score per rep and per session (`scoreRep5`, `scoreSession5`) |
| `frontend/detection/reference.js` | Reference ranges per exercise from the PT-approved session |
| `frontend/detection/geometry.js`, `smooth.js` | Joint angles, 90° rotation for lying exercises, One Euro smoothing |
| `frontend/detection/corehab-map.js` | Names and scores mapped onto the CoRehab API (see below) |

The code lives under `frontend/` because the backend serves only that folder. It comes from the rehab tracker
(github.com/pakiino/rehabtracker), where the same files sit in `js/`.

## How it plugs into the patient app

`patient.html` calls `window.CAM` in four places; with no camera it runs exactly as before (demo timer):

- `render()` -> `CAM.afterRender()`: on the setup and exercise screens the photo is replaced by the live camera
  with the skeleton drawn on it. Setup shows "You're in the frame" once a whole leg is seen for about a second.
- `startExercise()` -> `CAM.startExercise(e)`; `tick()` (the demo timer) stops while `CAM.counting()` is true.
- Each finished rep -> `CRAPP.camRep(fix)`: same as one timer tick, plus the camera's correction for that rep
  (e.g. "Stand up tall."), shown in the cue bar. Pause, rest and the "End" sheet are respected.
- `patient-live.js` `postResult()` adds `CAM.resultFor(exercise_id, reps)` to the body.

Turn the camera off with `?camera=0`. Opened as a file (no server), the camera is off too.

## Mapping onto the CoRehab API (`corehab-map.js`)

- Exercise id: `side_lying_hip_abduction` -> tracker `side_lying_abduction`; the other 9 are the same.
- Score: 1-5 -> 0-1 as `(score - 1) / 4` (5 -> 1.0, 4 -> 0.75, 3 -> 0.5).
- `form_scores` always has exactly `reps_done` values (the backend checks this): extra camera reps are dropped,
  missed ones get the average of the reps the camera saw.
- Error names follow `backend/simulate.py`, e.g. `trunk_leans` -> `trunk_lean`, `hip_lifts` -> `hip_lift`,
  `knee_caves` -> `knee_valgus`, `pushes_hands` -> `uses_hands`, `partial` / `not_straight` -> `partial_rom`.

## How a rep is scored (1-5)

Every rep starts at 5 and loses points: each form error **-2**; moved clearly less than the reference (below p10
by more than 10% of the median) **-1**, or much less (under 60% of the median) **-2** instead; much quicker than any
approved rep (under 60% of the p10 rep time, when no speed error was flagged) **-1**. Never below 1.
The reference numbers per exercise are in `reference/<exercise>/<exercise>_profile.json` and `frontend/detection/reference.js`.

## Data: `reference/`

From a PT-supervised recording session (2026-11-20, 14 clips, 2 people, side views; side-lying abduction from the
front). Every rep was approved except standing knee bend reps 11-12 (trunk leans) and side-lying abduction reps 2
and 10 (too high). Per exercise:

- `<exercise>_profile.json`: per-measure n / mean / SD / p10 / p50 / p90 from approved reps, mean curve, the app's limits
- `<exercise>_profile.png`: all approved rep curves, their mean ±1 SD, wrong reps in red
- `<exercise>_reps.csv`: one row per rep with measures, the app's flags and the PT label
- `<exercise>_curves.csv`: each rep's main signal resampled to 101 points
- `clips_index.csv`: every clip and what was cut

Folder names use the tracker ids (`side_lying_abduction`). The trimmed videos and per-frame data (~70 MB) are kept
outside git; ask PK for the Google Drive folder.

The error limits in `exercises.js` were calibrated so the app flags exactly the PT's reps on these clips. On those
clips all 136 approved reps score 5 and the 4 flagged reps score 3; that checks the system against the data it was
tuned on, so the next session with new people is the real test.

## Tools: `tools/`

- `clip_labeler.html`: open a long recording, mark each take, copy a CSV.
- `cut_clips.py`: cut the takes with ffmpeg from that CSV.
- `export_reference.mjs` + `build_reference.py`: rebuild `reference/` from new PT-approved clips
  (`node tools/export_reference.mjs <clips_folder> refdb_out`, then `python3 tools/build_reference.py` inside
  `refdb_out`; list the PT's wrong reps in `PT_FLAGS` first). Then update `frontend/detection/reference.js`.

## Tests

```
cd detection && npm test     # 112 tests: exercises on synthetic poses, smoothing, scoring, API mapping
```

## Known limits

- 8 of 10 exercises come from one person; add 2-3 people per exercise before relying on the score.
- Angles are 2D and have not been checked against a goniometer yet.
- Front-view checks (knee caving in, hip drop, weight shift, pelvis tilt) have no recorded data yet.
- The tracked leg on side views is the one that moves; the patient's operated side is used for front views
  (from the server when available, otherwise right).
- Not a clinical score: limits and weights need PT sign-off before use with patients.
