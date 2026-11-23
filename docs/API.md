# CoRehab API

Base URL: `http://127.0.0.1:8000` (run `python backend/test_server.py`). All bodies are JSON.
Errors: `400 {"ok": false, "errors": [...]}` for bad input, `422` for a bad detection result.
Demo patients load at start: `walter` (join code RIV284), `frank`, `ruth`, `dorothy`, `george`, `maria`, `thomas`.

## Patient app (`patient.html`)

| Screen | Call |
|---|---|
| Join clinic | `POST /api/join {code}` → `{patient_id, clinic}` (any code goes to the demo clinic) |
| Create account (after the questionnaire) | `POST /api/register {name, dob, phone, surgery_date, side}` → `{patient_id}` |
| Health questionnaire | `POST /api/intake {patient_id, name, answers}` (answers: any fields; `goal`, `side`, `surgery_date` are used) |
| Setup: privacy | `POST /api/consent {patient_id, items: [...]}` |
| Setup: safety check | `POST /api/setup-check {patient_id, safe: bool, details}` (not safe → urgent alert) |
| Home | `GET /api/home?patient_id=` → `phase` (intake / waiting_for_visit / active / plan_updated / discharged), `next_visit`, `plan`, `plan_change`, `rest_days`, `rest_today`, `done_today`, `streak`, `discharge` |
| My plan / What changed | in `/api/home` (`plan`, `plan_change`); weekly numbers: `GET /api/weekly-summary?patient_id=` |
| Progress / Medals | `GET /api/progress?patient_id=` → `streak`, `medals`, `this_week`, `last_week` (status per day), `pain_by_week` |
| Pain before | `POST /api/checkin {patient_id, pain_today, worry: {symptoms: [swelling, redness or warmth, fever, calf pain], other}}` → `checkin_id`, `action` (as_planned / reduced / rest), `urgent`, `exercises` with `target_reps` |
| After each exercise | `POST /api/result {checkin_id, exercise_id, sets_done, reps_done, feeling: easy / just_right / too_hard, pain_after (only if too hard), note, form_scores (from the camera, optional)}` |
| End of session | `POST /api/session/end {checkin_id, pain_end, bothered: [{exercise_id, reason, other}], note}` (reasons: pain, too hard to do, swelling, felt unsteady, clicking or popping, something else) |
| Stop early | `POST /api/session/stop {checkin_id, reason: tired / pain / no time / something else, other, pain}` |

Pain 7+ anywhere, or any named symptom before the session, creates an urgent alert for the PT.

## PT app (`pt.html`)

| Screen | Call |
|---|---|
| Patients list | `GET /api/patients` → per patient: `group` (urgent, ready_for_review, gone_quiet, done_today, new, on_track, discharged), `next`, `last_session`, `pain_after_this_week`, `pain_after_last_week`, `data_days_30`, `pending_request_id` |
| Add patient | `POST /api/patients/add {name, phone, surgery, surgery_date, side}` → status intake_sent |
| Patient profile | `GET /api/profile?patient_id=` → sessions by day, `pain_by_week`, `plan_history`, `visits`, `calls`, `alerts`, `pt_private_notes`, `intake`, `findings` |
| Private notes | `POST /api/pt-notes {patient_id, text}` (never shown to the patient) |
| Clinic visit (autosave) | `POST /api/visit/findings {patient_id, fields}` — fields: bend_deg, short_of_straight_deg, rom_mode, quad_lag, quad_grade, pain_today, tug_sec, chair_stand_30s, walking_aid, stairs, swelling_cm {left,right}, incision, goals_week6 [], precautions [], notes, done_checklist [] |
| First plan draft | `POST /api/first-plan/draft {patient_id}` → `request_id`, `draft.items` (each with `reason`) |
| Weekly review | `GET /api/request?patient_id=&status=pending` → draft + summary. Drafts are made automatically at the start of each week (not for patients who have gone quiet). |
| Accept / reject | `POST /api/adjustment/decide {request_id, approve: true, decisions: {exercise_id: accept/reject}, items (optional edited), note}`; "Keep current plan" = `approve: false` |
| Schedule a visit | `POST /api/visits/schedule {patient_id, date, time, place, reason}` |
| Log a call | `POST /api/calls {patient_id, minutes, agreed}` (closes open alerts) |
| Discharge | `POST /api/discharge {patient_id, note, checklist, community_program, final_measures}` |
| Rest days | `POST /api/rest-days {patient_id, days: ["Tue","Sun"]}` |

## How the front end is connected

`frontend/patient-live.js` and `frontend/pt-live.js` connect the prototype pages to this API. They only load when the
page is served by the backend; opened as a plain file, the pages run as the original prototype.

- Patient app: keeps the prototype's screens. It loads the real plan, streak and week, and sends pain before,
  each exercise (reps, feeling, pain if too hard, note), end of session and stopping early. The server's answer
  decides today's dose. Signing up creates a new patient, who waits until the PT sends the first plan.
- PT app: the patient list, intake, clinic visit (autosaved), first-plan draft, weekly review and profile are built
  from server data. Messages and Schedule stay as the prototype.
- The prototype's "Simulate" buttons are hidden when connected. "Restart" goes back to the demo patient (Walter).
