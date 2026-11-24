"""Daily exercise node: pain check-in, rep adjustment, session logging, weekly summary.

Flow:
  PT plan (set_plan) -> daily_checkin(pain) -> today's adjusted plan
  -> movement analysis sends results -> log_exercise(...)
  -> end of week: weekly_summary(...) -> JSON for the plan-adjustment step
"""
import datetime as dt
import json
import sqlite3
import knee_rules
from statistics import mean

DB_PATH = "rehab.db"

# Daily pain rule: (highest pain in band, share of planned reps, action).
# The PT could later change these per patient.
PAIN_BANDS = [
    (3, 1.0, "as_planned"),
    (5, 0.7, "reduced"),
    (6, 0.5, "reduced"),
]
STOP_PAIN = 7  # pain at or above this: no exercise today, urgent alert to PT

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    patient_id    TEXT PRIMARY KEY,
    name          TEXT,
    surgery_date  TEXT,
    operated_side TEXT,
    goal          TEXT
);
CREATE TABLE IF NOT EXISTS plan_items (
    plan_item_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL,
    exercise_id   TEXT NOT NULL,
    exercise_name TEXT NOT NULL,
    sets          INTEGER NOT NULL,
    reps          INTEGER NOT NULL,
    start_date    TEXT NOT NULL,
    end_date      TEXT,              -- NULL = current plan
    approved_by   TEXT
);
CREATE TABLE IF NOT EXISTS checkins (
    checkin_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL,
    date          TEXT NOT NULL,
    pain_today    INTEGER NOT NULL,  -- 0-10, asked before the session
    pain_location TEXT,              -- front / back / inner / outer / whole / none
    action        TEXT NOT NULL,     -- as_planned / reduced / rest
    rep_factor    REAL NOT NULL,
    UNIQUE (patient_id, date)
);
CREATE TABLE IF NOT EXISTS exercise_logs (
    log_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    checkin_id    INTEGER NOT NULL,
    patient_id    TEXT NOT NULL,
    date          TEXT NOT NULL,
    exercise_id   TEXT NOT NULL,
    planned_sets  INTEGER NOT NULL,
    planned_reps  INTEGER NOT NULL,  -- per set, from the PT plan
    target_reps   INTEGER NOT NULL,  -- per set, after today's pain rule
    sets_done     INTEGER NOT NULL,
    reps_done     INTEGER NOT NULL,  -- total across sets
    form_score    REAL,              -- 0-1, mean over reps
    errors_json   TEXT,              -- {"trunk_lean": 3, ...}
    pain_after    INTEGER            -- 0-10, asked after the exercise
);
CREATE TABLE IF NOT EXISTS alerts (
    alert_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL,
    date          TEXT NOT NULL,
    tier          TEXT NOT NULL,     -- urgent / review
    reason        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adjustment_requests (
    request_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL,
    week_end      TEXT NOT NULL,
    summary_json  TEXT NOT NULL,     -- weekly_summary output
    draft_json    TEXT NOT NULL,     -- what the app proposed
    draft_source  TEXT NOT NULL,     -- rules / llm
    status        TEXT NOT NULL,     -- pending / approved / rejected
    final_json    TEXT,              -- what the PT approved (may differ from draft)
    pt_edited     INTEGER,           -- 1 if the PT changed the draft
    pt_note       TEXT,
    decided_at    TEXT
);
CREATE TABLE IF NOT EXISTS intakes (
    intake_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    answers_json  TEXT NOT NULL,
    summary       TEXT NOT NULL      -- for the PT; rule-based now, LLM later
);
"""

# Columns added after the first version. connect() adds any that are missing, so old databases keep working.
NEW_COLUMNS = {
    "patients": [("dob", "TEXT"), ("phone", "TEXT"), ("status", "TEXT DEFAULT 'active'"), ("rest_days", "TEXT"),
                 ("pt_private_notes", "TEXT"), ("consent_json", "TEXT"), ("consent_at", "TEXT"),
                 ("created_at", "TEXT"), ("join_code", "TEXT")],
    "checkins": [("next_morning", "TEXT"), ("worry_json", "TEXT"), ("pain_end", "INTEGER"), ("bothered_json", "TEXT"), ("end_note", "TEXT"),
                 ("stopped_early", "INTEGER DEFAULT 0"), ("stop_reason", "TEXT"), ("stop_pain", "INTEGER"),
                 ("ended_at", "TEXT")],
    "exercise_logs": [("feeling", "TEXT"), ("note", "TEXT")],
    "alerts": [("status", "TEXT DEFAULT 'open'"), ("source", "TEXT")],
    "adjustment_requests": [("kind", "TEXT DEFAULT 'weekly'"), ("decisions_json", "TEXT")],
}

PAIN_LOCATIONS = ["none", "front", "back", "inner", "outer", "whole knee"]
FEELINGS = ["easy", "just_right", "too_hard"]          # asked after each exercise
URGENT_SYMPTOMS = ["swelling", "redness or warmth", "fever", "calf pain"]  # "Is anything worrying you?"
BOTHER_REASONS = ["pain", "too hard to do", "swelling", "felt unsteady", "clicking or popping", "something else"]
STOP_REASONS = ["tired", "pain", "no time", "something else"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_REST_DAYS = ["Tue", "Sun"]

# The 10 exercises the PT can prescribe (all camera-checkable). The ID is what detection sends.
EXERCISES = {
    "heel_slide": "Heel slide",
    "straight_leg_raise": "Straight leg raise",
    "sitting_knee_bend": "Sitting knee bend",
    "standing_knee_bend": "Standing knee bend",
    "seated_knee_extension": "Seated knee extension",
    "sit_to_stand": "Sit-to-stand",
    "mini_squat": "Mini squat",
    "step_up": "Step-up",
    "glute_bridge": "Glute bridge",
    "side_lying_hip_abduction": "Side-lying hip abduction",
}

# Patient goals, lightest to hardest. "targets" = the program the goal needs (sets, reps).
# Starting values: confirm with PTs (add to the PT survey).
GOALS = {
    "home":   {"label": "Get through the day at home without pain", "focus": "range of motion",
               "min_week": 0, "targets": {"heel_slide": (3, 10), "straight_leg_raise": (2, 10),
                                          "sit_to_stand": (2, 10)}},
    "walk":   {"label": "Walk for 30 minutes", "focus": "endurance",
               "min_week": 2, "targets": {"heel_slide": (3, 10), "straight_leg_raise": (3, 10),
                                          "sit_to_stand": (3, 12), "glute_bridge": (3, 12)}},
    "stairs": {"label": "Climb stairs", "focus": "leg strength and control",
               "min_week": 3, "targets": {"heel_slide": (3, 10), "sit_to_stand": (3, 12), "glute_bridge": (3, 12),
                                          "step_up": (3, 10), "mini_squat": (3, 10),
                                          "side_lying_hip_abduction": (2, 12)}},
    "jog":    {"label": "Jog", "focus": "strength and power",
               "min_week": 12, "targets": {"sit_to_stand": (3, 15), "glute_bridge": (3, 15), "step_up": (3, 12),
                                           "mini_squat": (3, 12), "side_lying_hip_abduction": (3, 12),
                                           "standing_knee_bend": (3, 12)}},
}
DEFAULT_GOAL = "home"


def connect(path=DB_PATH):
    con = sqlite3.connect(path, check_same_thread=False)  # Gradio calls from worker threads
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA + knee_rules.SCHEMA)
    for table, cols in NEW_COLUMNS.items():
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, kind in cols:
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    con.commit()
    return con


def _today(date):
    return date or dt.date.today().isoformat()


def rep_rule(pain_today):
    """Return (action, rep_factor) for today's pain score."""
    if not 0 <= pain_today <= 10:
        raise ValueError("pain_today must be 0-10")
    if pain_today >= STOP_PAIN:
        return "rest", 0.0
    for top, factor, action in PAIN_BANDS:
        if pain_today <= top:
            return action, factor
    return "rest", 0.0


def adjusted_reps(reps, factor):
    """Round half up, never below 1 unless resting (10 -> 7 at 0.7, 10 -> 5 at 0.5)."""
    if factor == 0:
        return 0
    return max(1, int(reps * factor + 0.5))


# ---------- PT side ----------

def add_patient(con, patient_id, name, surgery_date, operated_side, goal="", **extra):
    """Create or update a patient. extra: dob, phone, status, rest_days (list), join_code."""
    con.execute(
        "INSERT INTO patients (patient_id, name, surgery_date, operated_side, goal, created_at) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(patient_id) DO UPDATE SET name=excluded.name, surgery_date=excluded.surgery_date,"
        " operated_side=excluded.operated_side, goal=excluded.goal",
        (patient_id, name, surgery_date, operated_side, goal, dt.datetime.now().isoformat(timespec="seconds")),
    )
    for key in ("dob", "phone", "status", "join_code"):
        if extra.get(key) is not None:
            con.execute(f"UPDATE patients SET {key}=? WHERE patient_id=?", (extra[key], patient_id))
    if extra.get("rest_days") is not None:
        set_rest_days(con, patient_id, extra["rest_days"], commit=False)
    con.commit()


def set_rest_days(con, patient_id, days, commit=True):
    bad = [d for d in days if d not in WEEKDAYS]
    if bad:
        raise ValueError(f"Unknown day(s) {bad}. Use {WEEKDAYS}.")
    con.execute("UPDATE patients SET rest_days=? WHERE patient_id=?", (json.dumps(list(days)), patient_id))
    if commit:
        con.commit()


def rest_days(con, patient_id):
    r = con.execute("SELECT rest_days FROM patients WHERE patient_id=?", (patient_id,)).fetchone()
    return json.loads(r["rest_days"]) if r and r["rest_days"] else list(DEFAULT_REST_DAYS)


def set_plan(con, patient_id, items, approved_by, start_date=None):
    """Replace the current plan. items: [{"exercise_id","exercise_name","sets","reps"}]."""
    start = _today(start_date)
    con.execute(
        "UPDATE plan_items SET end_date=? WHERE patient_id=? AND end_date IS NULL",
        (start, patient_id),
    )
    for it in items:
        con.execute(
            "INSERT INTO plan_items (patient_id, exercise_id, exercise_name, sets, reps, start_date, approved_by)"
            " VALUES (?,?,?,?,?,?,?)",
            (patient_id, it["exercise_id"], it["exercise_name"], int(it["sets"]), int(it["reps"]), start, approved_by),
        )
    con.commit()


def current_plan(con, patient_id):
    rows = con.execute(
        "SELECT exercise_id, exercise_name, sets, reps FROM plan_items"
        " WHERE patient_id=? AND end_date IS NULL ORDER BY plan_item_id",
        (patient_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- Patient side: daily ----------

def add_alert(con, patient_id, date, tier, reason, source=""):
    con.execute("INSERT INTO alerts (patient_id, date, tier, reason, status, source) VALUES (?,?,?,?, 'open', ?)",
                (patient_id, date, tier, reason, source))


def daily_checkin(con, patient_id, pain_today, date=None, pain_location="none", worry=None, next_morning=None):
    """Ask pain before the session; return today's plan with adjusted reps.

    worry: {"symptoms": [...from URGENT_SYMPTOMS], "other": "free text"} from "Is anything worrying you today?"
    Pain >= 7 or any named symptom: urgent alert to the PT and no exercise today.
    Free text only: a review alert, the session goes ahead.
    pain_location is stored for the PT and the weekly summary; it does not change reps yet.
    """
    date = _today(date)
    if not current_plan(con, patient_id):
        raise ValueError(f"No plan for {patient_id}. The PT must enter a plan first.")
    worry = worry or {}
    symptoms = [str(x).lower() for x in worry.get("symptoms") or []]
    assessment = knee_rules.assessment(con, patient_id)
    named = URGENT_SYMPTOMS + (list(knee_rules.GENERAL_URGENT) if assessment["pathway"] == "general" else [])
    unknown = [x for x in symptoms if x not in named]
    if unknown:
        raise ValueError(f"Unknown symptom(s) {unknown}. Use {named}, or put it in 'other'.")
    other = (worry.get("other") or "").strip()
    action, factor = rep_rule(pain_today)
    if next_morning not in (None, "unknown", "baseline", "worse"):
        raise ValueError("Unknown next-morning response.")
    hold = ""
    if assessment["pathway"] == "general":
        hold = knee_rules.session_hold(con, patient_id, assessment, current_plan(con, patient_id))
        if any(x in knee_rules.GENERAL_URGENT for x in symptoms):
            hold = "Arrange urgent medical assessment for the new knee symptoms before exercise."
    if symptoms or hold:
        action, factor = "rest", 0.0
    con.execute(
        "INSERT INTO checkins (patient_id, date, pain_today, pain_location, action, rep_factor, worry_json)"
        " VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(patient_id, date) DO UPDATE SET pain_today=excluded.pain_today,"
        " pain_location=excluded.pain_location, action=excluded.action, rep_factor=excluded.rep_factor,"
        " worry_json=excluded.worry_json",
        (patient_id, date, pain_today, pain_location, action, factor,
         json.dumps({"symptoms": symptoms, "other": other}) if (symptoms or other) else None),
    )
    if next_morning is not None:
        con.execute("UPDATE checkins SET next_morning=? WHERE patient_id=? AND date=?", (next_morning, patient_id, date))
    if hold:
        add_alert(con, patient_id, date, "urgent" if assessment["clearance"] == "urgent" else "review", hold, "knee_screen")
    if pain_today >= STOP_PAIN:
        add_alert(con, patient_id, date, "urgent", f"Pain {pain_today}/10 before session; exercise skipped today", "checkin")
    if symptoms:
        add_alert(con, patient_id, date, "urgent", f"Reported {', '.join(symptoms)} before session; exercise skipped today", "checkin")
    if other:
        add_alert(con, patient_id, date, "review", f"Worried about: {other}", "checkin")
    con.commit()
    checkin_id = con.execute(
        "SELECT checkin_id FROM checkins WHERE patient_id=? AND date=?", (patient_id, date)
    ).fetchone()[0]

    today = []
    for it in current_plan(con, patient_id):
        today.append({
            "exercise_id": it["exercise_id"],
            "exercise_name": it["exercise_name"],
            "sets": 0 if action == "rest" else it["sets"],
            "planned_reps": it["reps"],
            "target_reps": adjusted_reps(it["reps"], factor),
        })
    messages = {
        "as_planned": "Your usual plan today.",
        "reduced": "Lighter session today because of your pain.",
        "rest": "Please rest today. We've told your PT.",
    }
    return {"checkin_id": checkin_id, "date": date, "pain_today": pain_today, "urgent": action == "rest",
            "action": action, "message": hold or messages[action], "exercises": today}


def log_exercise(con, checkin_id, result):
    """Store one exercise result from movement analysis.

    result: {"exercise_id", "sets_done", "reps_done", "form_scores": [0-1 per rep],
             "error_counts": {...}, "feeling": easy / just_right / too_hard,
             "pain_after": 0-10 (asked only when too hard), "note": "..."}
    Detection fills reps and form; the patient fills feeling, pain and note.
    """
    ck = con.execute("SELECT * FROM checkins WHERE checkin_id=?", (checkin_id,)).fetchone()
    if ck is None:
        raise ValueError("unknown checkin_id")
    plan = {p["exercise_id"]: p for p in current_plan(con, ck["patient_id"])}
    item = plan.get(result["exercise_id"])
    if item is None:
        raise ValueError(f"{result['exercise_id']} is not in the current plan")

    scores = result.get("form_scores") or []
    form = round(mean(scores), 3) if scores else None
    pain_after = result.get("pain_after")
    feeling = result.get("feeling")
    if feeling is not None and feeling not in FEELINGS:
        raise ValueError(f"feeling must be one of {FEELINGS}")
    con.execute(
        "INSERT INTO exercise_logs (checkin_id, patient_id, date, exercise_id, planned_sets, planned_reps,"
        " target_reps, sets_done, reps_done, form_score, errors_json, pain_after, feeling, note)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (checkin_id, ck["patient_id"], ck["date"], result["exercise_id"], item["sets"], item["reps"],
         adjusted_reps(item["reps"], ck["rep_factor"]), int(result["sets_done"]), int(result["reps_done"]),
         form, json.dumps(result.get("error_counts") or {}), pain_after, feeling, result.get("note")),
    )
    if pain_after is not None and pain_after >= STOP_PAIN:
        add_alert(con, ck["patient_id"], ck["date"], "urgent", f"Pain {pain_after}/10 after {item['exercise_name']}", "exercise")
    con.commit()


def end_session(con, checkin_id, pain_end, bothered=None, note=""):
    """End-of-session check-in. bothered: [{"exercise_id", "reason", "other"}]; pain >= 7 is urgent."""
    ck = con.execute("SELECT * FROM checkins WHERE checkin_id=?", (checkin_id,)).fetchone()
    if ck is None:
        raise ValueError("unknown checkin_id")
    if not 0 <= int(pain_end) <= 10:
        raise ValueError("pain_end must be 0-10")
    bothered = bothered or []
    for b in bothered:
        if b.get("exercise_id") not in EXERCISES:
            raise ValueError(f"Unknown exercise '{b.get('exercise_id')}'")
        if str(b.get("reason", "")).lower() not in BOTHER_REASONS:
            raise ValueError(f"reason must be one of {BOTHER_REASONS}")
        b["reason"] = b["reason"].lower()
    con.execute("UPDATE checkins SET pain_end=?, bothered_json=?, end_note=?, ended_at=? WHERE checkin_id=?",
                (int(pain_end), json.dumps(bothered), note or None, dt.datetime.now().isoformat(timespec="seconds"),
                 checkin_id))
    if int(pain_end) >= STOP_PAIN:
        add_alert(con, ck["patient_id"], ck["date"], "urgent", f"Pain {pain_end}/10 at the end of the session", "end")
    con.commit()
    return {"ok": True, "urgent": int(pain_end) >= STOP_PAIN}


def stop_session(con, checkin_id, reason, other="", pain=None):
    """Ending early. Reps done so far are already saved; the day still counts toward the streak."""
    ck = con.execute("SELECT * FROM checkins WHERE checkin_id=?", (checkin_id,)).fetchone()
    if ck is None:
        raise ValueError("unknown checkin_id")
    reason = str(reason or "").lower()
    if reason and reason not in STOP_REASONS:
        raise ValueError(f"reason must be one of {STOP_REASONS}")
    if pain is not None and not 0 <= int(pain) <= 10:
        raise ValueError("pain must be 0-10")
    con.execute("UPDATE checkins SET stopped_early=1, stop_reason=?, stop_pain=?, ended_at=? WHERE checkin_id=?",
                ((reason + (f": {other}" if other else "")) or None, pain,
                 dt.datetime.now().isoformat(timespec="seconds"), checkin_id))
    if pain is not None and int(pain) >= STOP_PAIN:
        add_alert(con, ck["patient_id"], ck["date"], "urgent", f"Stopped early with pain {pain}/10", "stop")
    con.commit()
    return {"ok": True, "urgent": pain is not None and int(pain) >= STOP_PAIN}


# ---------- End of week: input for plan adjustment ----------

def weekly_summary(con, patient_id, week_end=None, days=7):
    """Aggregate the last `days` days into one JSON for the plan-adjustment step."""
    end = dt.date.fromisoformat(_today(week_end))
    start = end - dt.timedelta(days=days - 1)
    s, e = start.isoformat(), end.isoformat()

    checkins = [dict(r) for r in con.execute(
        "SELECT date, pain_today, pain_location, action, pain_end, bothered_json, stopped_early, worry_json, next_morning FROM checkins"
        " WHERE patient_id=? AND date BETWEEN ? AND ? ORDER BY date",
        (patient_id, s, e)).fetchall()]
    patient = con.execute("SELECT * FROM patients WHERE patient_id=?", (patient_id,)).fetchone()
    logs = [dict(r) for r in con.execute(
        "SELECT * FROM exercise_logs WHERE patient_id=? AND date BETWEEN ? AND ? ORDER BY date",
        (patient_id, s, e)).fetchall()]
    alerts = [dict(r) for r in con.execute(
        "SELECT date, tier, reason FROM alerts WHERE patient_id=? AND date BETWEEN ? AND ? ORDER BY date",
        (patient_id, s, e)).fetchall()]

    exercises = []
    for item in current_plan(con, patient_id):
        rows = [l for l in logs if l["exercise_id"] == item["exercise_id"]]
        planned_total = item["sets"] * item["reps"]
        forms = [r["form_score"] for r in rows if r["form_score"] is not None]
        pains = [r["pain_after"] for r in rows if r["pain_after"] is not None]
        high_days = len({r["date"] for r in rows if r["pain_after"] is not None and r["pain_after"] >= STOP_PAIN})
        errors = {}
        for r in rows:
            for k, v in json.loads(r["errors_json"] or "{}").items():
                errors[k] = errors.get(k, 0) + v
        half = len(forms) // 2
        feel = {f: sum(1 for r in rows if r.get("feeling") == f) for f in FEELINGS}
        bothered = sum(1 for c in checkins for b in json.loads(c["bothered_json"] or "[]")
                       if b.get("exercise_id") == item["exercise_id"])
        exercises.append({
            "exercise_id": item["exercise_id"],
            "exercise_name": item["exercise_name"],
            "planned": f"{item['sets']} x {item['reps']}",
            "days_done": len(rows),
            "reps_done_share": round(mean(min(1.0, r["reps_done"] / max(1, r["planned_sets"] * r["target_reps"])) for r in rows), 2) if rows else 0.0,
            "form_avg": round(mean(forms), 2) if forms else None,
            "form_trend": round(mean(forms[half:]) - mean(forms[:half]), 2) if half >= 1 else None,
            "pain_after_avg": round(mean(pains), 1) if pains else None,
            "pain_after_max": max(pains) if pains else None,
            "high_pain_days": high_days,
            "error_counts": errors,
            "feeling_counts": feel,          # easy / just_right / too_hard, one per session
            "bothered_days": bothered,       # named at the end of a session as a problem
        })

    locations = {}
    for c in checkins:
        loc = c["pain_location"] or "none"
        locations[loc] = locations.get(loc, 0) + 1

    goal = patient["goal"] if patient and patient["goal"] in GOALS else DEFAULT_GOAL
    week_post_op = None
    if patient and patient["surgery_date"]:
        try:
            week_post_op = max(0, (end - dt.date.fromisoformat(patient["surgery_date"])).days // 7)
        except ValueError:
            pass

    return {
        "patient_id": patient_id,
        "goal": goal,
        "goal_label": GOALS[goal]["label"],
        "week_post_op": week_post_op,
        "week": {"start": s, "end": e},
        "days_checked_in": len(checkins),
        "pain_location_days": locations,
        "next_morning": [c["next_morning"] or "unknown" for c in checkins],
        "days_reduced": sum(c["action"] == "reduced" for c in checkins),
        "days_rest": sum(c["action"] == "rest" for c in checkins),
        "pain_today_avg": round(mean(c["pain_today"] for c in checkins), 1) if checkins else None,
        "pain_today_by_day": {c["date"]: c["pain_today"] for c in checkins},
        "pain_end_avg": round(mean(c["pain_end"] for c in checkins if c["pain_end"] is not None), 1)
                        if any(c["pain_end"] is not None for c in checkins) else None,
        "sessions_stopped_early": sum(1 for c in checkins if c["stopped_early"]),
        "worry_days": sum(1 for c in checkins if c["worry_json"]),
        "planned_days": 7 - len(rest_days(con, patient_id)),
        "exercises": exercises,
        "alerts": alerts,
    }


# ---------- Plan adjustment draft (sent to the PT, never straight to the patient) ----------

# Starting thresholds; tune them with the PT ground-truth survey.
PROGRESS_FORM, PROGRESS_PAIN, PROGRESS_SHARE, MIN_DAYS = 0.8, 3, 0.9, 4
EASIER_FORM, EASIER_PAIN, EASIER_TREND = 0.6, 5, -0.15
MAX_REPS = 15
PAUSE_DAYS = 2  # pause an exercise after pain >= 7 on this many days in the week; one day -> lighter


TOO_HARD_DAYS = 2  # "too hard" on this many sessions in a week -> lighter
EASY_DAYS = 3      # "easy" on this many sessions counts as a sign of readiness


def _pain_signal(ex, summary):
    """Pain after this exercise. Patients rate pain only when it felt too hard, so fall back to end-of-session pain."""
    return ex["pain_after_avg"] if ex["pain_after_avg"] is not None else summary.get("pain_end_avg")


def _ready(ex, summary=None):
    """This exercise went well enough this week to make it harder.
    Form counts only when the camera measured it; never ready if it felt too hard."""
    feel = ex.get("feeling_counts") or {}
    pain = _pain_signal(ex, summary or {})
    form_ok = ex["form_avg"] is None or ex["form_avg"] >= PROGRESS_FORM
    return (ex["days_done"] >= MIN_DAYS and form_ok and feel.get("too_hard", 0) == 0
            and (ex["form_avg"] is not None or feel.get("easy", 0) + feel.get("just_right", 0) >= MIN_DAYS)
            and (pain is None or pain <= PROGRESS_PAIN)
            and ex["reps_done_share"] >= PROGRESS_SHARE)


def _dose(sets, reps):
    return f"{sets} x {reps}"


def goal_draft(summary):
    """Rule-based draft toward the patient's goal.

    1. Safety first: pause or make easier, whatever the goal.
    2. Exercises below the goal's dose move one step toward it, if this week went well.
    3. At most one exercise the goal needs is added per week, if the whole week went well.
    Returns the new plan, a direction (harder / same / lighter / mixed) and an explanation.
    """
    goal_key = summary.get("goal") if summary.get("goal") in GOALS else DEFAULT_GOAL
    goal = GOALS[goal_key]
    targets = goal["targets"]
    items = []
    for ex in summary["exercises"]:
        sets, reps = (int(x) for x in ex["planned"].split(" x "))
        name = ex["exercise_name"]
        form, pain_avg, pain_max, trend = ex["form_avg"], ex["pain_after_avg"], ex["pain_after_max"], ex["form_trend"]
        target = targets.get(ex["exercise_id"])
        new_sets, new_reps = sets, reps
        if ex["days_done"] == 0:
            action, why = "hold", "Not done this week, so we keep it the same."
        elif ex.get("high_pain_days", 0) >= PAUSE_DAYS:
            action, why, new_sets, new_reps = ("pause", f"Pain reached {pain_max}/10 after it on {ex['high_pain_days']} days. "
                                               "Paused for a week.", 0, 0)
        elif (pain_max is not None and pain_max >= STOP_PAIN) or (form is not None and form < EASIER_FORM) \
                or (pain_avg is not None and pain_avg >= EASIER_PAIN) or (trend is not None and trend <= EASIER_TREND) \
                or (ex.get("feeling_counts") or {}).get("too_hard", 0) >= TOO_HARD_DAYS \
                or ex.get("bothered_days", 0) >= TOO_HARD_DAYS:
            new_reps = adjusted_reps(reps, 0.7)
            bits = []
            if (ex.get("feeling_counts") or {}).get("too_hard", 0) >= TOO_HARD_DAYS:
                bits.append(f"it felt too hard {ex['feeling_counts']['too_hard']} times")
            if ex.get("bothered_days", 0) >= TOO_HARD_DAYS:
                bits.append(f"you said it bothered you on {ex['bothered_days']} days")
            if pain_max is not None and pain_max >= STOP_PAIN:
                bits.append(f"pain reached {pain_max}/10 once")
            if pain_avg is not None and pain_avg >= EASIER_PAIN:
                bits.append(f"pain after was {pain_avg}/10")
            if form is not None and form < EASIER_FORM:
                bits.append(f"form was {form}")
            if trend is not None and trend <= EASIER_TREND:
                bits.append("form got worse during the week")
            action, why = "easier", f"Fewer reps because {' and '.join(bits)}."
        elif target and sets * reps < target[0] * target[1]:
            if _ready(ex, summary):
                if reps < target[1]:
                    new_reps = min(target[1], reps + 2)
                else:
                    new_sets = min(target[0], sets + 1)
                action = "progress"
                easy = (ex.get("feeling_counts") or {}).get("easy", 0)
                signs = []
                if easy:
                    signs.append(f"it felt easy {easy} time{'s' if easy > 1 else ''}")
                if form is not None:
                    signs.append(f"form was {form}")
                pain = _pain_signal(ex, summary)
                if pain is not None:
                    signs.append(f"pain stayed at {pain}/10")
                why = f"Your goal needs {_dose(*target)}. {' and '.join(signs).capitalize() or 'This week went well'}, so you're ready for the next step."
            else:
                action = "hold"
                why = f"Your goal needs {_dose(*target)}; we'll step up once it feels comfortable and pain stays 3/10 or less."
        elif target:
            action, why = "hold", "Already at the level your goal needs."
        else:
            action, why = "hold", "Not needed for your goal, but kept as part of your PT's plan."
        items.append({"exercise_id": ex["exercise_id"], "exercise_name": name, "action": action,
                      "before": ex["planned"], "sets": new_sets, "reps": new_reps, "reason": why})

    # Add one missing exercise the goal needs, only after a good week with nothing made easier.
    in_plan = {e["exercise_id"] for e in summary["exercises"]}
    missing = [eid for eid in targets if eid not in in_plan]
    days = summary["days_checked_in"] - summary["days_rest"]
    pain_week = summary["pain_today_avg"]
    week_ok = (days >= min(MIN_DAYS, summary.get("planned_days", 7)) and pain_week is not None and pain_week <= PROGRESS_PAIN
               and not any(i["action"] in ("easier", "pause") for i in items))
    waiting = []
    if missing and week_ok:
        eid = missing[0]
        t = targets[eid]
        items.append({"exercise_id": eid, "exercise_name": EXERCISES[eid], "action": "add", "before": None,
                      "sets": 2, "reps": min(8, t[1]),
                      "reason": f"New: your goal needs it at {_dose(*t)}. Starting light at 2 x {min(8, t[1])}."})
        waiting = [EXERCISES[e] for e in missing[1:]]
    elif missing:
        waiting = [EXERCISES[e] for e in missing]

    harder = any(i["action"] in ("progress", "add") for i in items)
    lighter = any(i["action"] in ("easier", "pause") for i in items)
    direction = "mixed" if harder and lighter else "harder" if harder else "lighter" if lighter else "same"

    forms = [e["form_avg"] for e in summary["exercises"] if e["form_avg"] is not None]
    week_line = (f"This week you exercised {days} of {summary.get('planned_days', 7)} planned days, with average pain {pain_week if pain_week is not None else '–'}/10"
                 + (f" and form {round(mean(forms), 2)}." if forms else "."))
    lead = {
        "harder": f"Your goal, \"{goal['label']}\", needs more {goal['focus']} than your current plan, and you're ready for more.",
        "mixed": f"Your goal, \"{goal['label']}\", needs more {goal['focus']}. Some exercises step up, but some need to be lighter first.",
        "lighter": f"Some exercises need to be lighter this week. We'll build toward \"{goal['label']}\" again once they go smoothly.",
        "same": f"Your plan stays the same this week while you build toward \"{goal['label']}\".",
    }[direction]
    changes = [f"{i['exercise_name']}: {i['before'].replace(' x ', ' × ') if i['before'] else 'new'} → "
               f"{'paused' if i['sets'] == 0 else _dose(i['sets'], i['reps']).replace(' x ', ' × ')}"
               for i in items if i["action"] != "hold"]
    explanation = " ".join([lead, week_line] + (["Changes: " + "; ".join(changes) + "."] if changes else []))
    if waiting and direction != "lighter":
        explanation += f" Coming later for your goal: {', '.join(waiting)}."

    note = []
    wk = summary.get("week_post_op")
    if wk is not None and wk < goal["min_week"]:
        note.append(f"Goal \"{goal['label']}\" is usually started from week {goal['min_week']}; patient is in week {wk}. "
                    "Worth discussing before progressing further.")
    if not week_ok and missing:
        note.append("New exercises held back until a week with 4+ sessions, pain 3/10 or less, and nothing made easier.")
    return {"source": "rules", "goal": goal_key, "goal_label": goal["label"], "direction": direction,
            "explanation": explanation, "items": items, "note_for_pt": " ".join(note)}


rule_draft = goal_draft  # older name


LLM_PROMPT = """You draft next week's home-exercise plan for a physical therapist (PT) to review.
The PT decides; you only propose. Work toward the patient's goal, using the goal's target program.
For each exercise in the plan choose: progress, hold, easier, or pause. You may also add ONE exercise
from the goal's target program that is not in the plan (action "add"), only after a good week.
Never progress or add after pain of 7 or more; safety comes before the goal.
Keep reps between 1 and 15 and sets between 0 and 5.
Return JSON: {"direction":"harder|same|lighter|mixed","explanation":"2-3 plain sentences for the patient",
"items":[{"exercise_id","exercise_name","action","before","sets","reps","reason"}],"note_for_pt":"..."}
A rule-based draft is included as a starting point; change it only with a clear reason.

Goal target program:
{targets}

Weekly summary:
{summary}

Rule-based draft:
{rules}
"""


LLM_STATE = {"last_error": None, "last_ok": None}   # shown at /api/llm-status


def llm_model():
    import os
    return os.environ.get("LLM_MODEL", "gpt-6.2-sol")


def _chat_http(prompt, model, params):
    """Chat Completions over plain HTTPS (standard library only), used when the openai package is not installed."""
    import os
    import urllib.error
    import urllib.request
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], **params}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except Exception:
            pass
        raise RuntimeError(f"Error code: {e.code} - {detail}") from None


def _parse_json(text):
    """JSON from the reply, also when the model wraps it in ```json fences or adds a sentence around it."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            return json.loads(text[a:b + 1])
        raise


def llm_json(prompt, model=None):
    """One JSON-mode call to OpenAI. Returns the parsed JSON, or None (no key, or the call failed).
    Failures are printed and kept in LLM_STATE so a wrong key or model doesn't fail silently."""
    import os
    import datetime as _dt
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        try:
            from openai import OpenAI
        except ImportError:
            OpenAI = None
        # Optional settings; some models reject them (e.g. temperature), so drop the rejected one and retry.
        params = {"response_format": {"type": "json_object"}}
        if os.environ.get("LLM_TEMPERATURE"):
            params["temperature"] = float(os.environ["LLM_TEMPERATURE"])
        for _ in range(3):
            try:
                if OpenAI is not None:
                    resp = OpenAI().chat.completions.create(model=model or llm_model(),
                                                            messages=[{"role": "user", "content": prompt}], **params)
                    content = resp.choices[0].message.content
                else:
                    content = _chat_http(prompt, model or llm_model(), params)
                break
            except Exception as e:
                bad = next((k for k in list(params) if k in str(e) and ("400" in str(e) or "nsupported" in str(e))), None)
                if not bad:
                    raise
                print(f"[LLM] model does not accept '{bad}', retrying without it")
                params.pop(bad)
        out = _parse_json(content)
        LLM_STATE.update(last_ok=_dt.datetime.now().isoformat(timespec="seconds"), last_error=None)
        return out
    except Exception as e:
        LLM_STATE["last_error"] = f"{type(e).__name__}: {e}"[:300]
        print(f"[LLM error] {LLM_STATE['last_error']}  -> using the rules instead")
        return None


def llm_status(test_call=True):
    """Is the LLM set up? test_call=True makes one tiny request to check the key and model."""
    import os
    key = os.environ.get("OPENAI_API_KEY", "")
    out = {"key_set": bool(key), "key_hint": (key[:3] + "…" + key[-4:]) if len(key) > 8 else "", "model": llm_model(),
           "connected": False, "error": None}
    if not key:
        out["error"] = "OPENAI_API_KEY is not set. Put it in backend/.env (see .env.example)."
        return out
    if test_call:
        r = llm_json('Reply with this JSON only: {"ok": true}')
        out["connected"] = bool(r and r.get("ok"))
        out["error"] = None if out["connected"] else LLM_STATE["last_error"] or "No valid reply."
    return out


def ai_note(use_llm):
    """Why a draft came from the rules, shown to the PT next to the draft."""
    import os
    if not use_llm:
        return "AI off for this draft"
    if not os.environ.get("OPENAI_API_KEY"):
        return "AI not connected"
    return "AI failed: " + (LLM_STATE.get("last_error") or "no answer")


def llm_draft(summary, rules, model=None):
    """Optional LLM draft (OpenAI). Returns None if no API key or the call fails."""
    targets = {EXERCISES[k]: _dose(*v) for k, v in GOALS[rules["goal"]]["targets"].items()}
    draft = llm_json(LLM_PROMPT.replace("{summary}", json.dumps(summary)).replace("{rules}", json.dumps(rules))
                     .replace("{targets}", json.dumps(targets)), model)
    if draft is None:
        return None
    out = validate_draft(draft, summary, fallback=rules)
    if out is rules:
        LLM_STATE["last_error"] = "the AI draft did not pass the code check"
        print("[LLM] weekly draft did not pass the code check -> using the rules draft")
        return None
    return out


def validate_draft(draft, summary, fallback):
    """Code check: known exercises, valid actions and doses, adds only from the goal, no progress after pain >= 7."""
    in_plan = {e["exercise_id"]: e for e in summary["exercises"]}
    goal_ids = set(GOALS[fallback["goal"]]["targets"])
    items = draft.get("items") if isinstance(draft, dict) else None
    if not items or draft.get("direction") not in ("harder", "same", "lighter", "mixed"):
        return fallback
    for it in items:
        eid, action = it.get("exercise_id"), it.get("action")
        if action not in ("progress", "hold", "easier", "pause", "add"):
            return fallback
        if action == "add" and (eid in in_plan or eid not in goal_ids):
            return fallback
        if action != "add" and eid not in in_plan:
            return fallback
        if not (0 <= int(it.get("sets", -1)) <= 5 and 0 <= int(it.get("reps", -1)) <= MAX_REPS):
            return fallback
        if action == "progress" and (in_plan[eid]["pain_after_max"] or 0) >= STOP_PAIN:
            return fallback
        it["exercise_name"] = EXERCISES.get(eid, it.get("exercise_name", eid))
    return {**fallback, "source": "llm", "items": items, "direction": draft["direction"],
            "explanation": draft.get("explanation") or fallback["explanation"],
            "note_for_pt": draft.get("note_for_pt", fallback["note_for_pt"])}


def create_adjustment_request(con, patient_id, week_end=None, use_llm=True):
    """Build the weekly summary and goal-based draft, store it as a pending request for the PT."""
    summary = weekly_summary(con, patient_id, week_end)
    assessment = knee_rules.assessment(con, patient_id)
    if assessment["pathway"] == "replacement":
        rules = goal_draft(summary)
        draft = (llm_draft(summary, rules) if use_llm else None) or dict(rules, ai_note=ai_note(use_llm))
    else:
        summary["knee_assessment"] = assessment
        draft = knee_rules.draft(con, patient_id, assessment, summary["goal"],
                                 week=summary, use_llm=use_llm)
    con.execute("UPDATE adjustment_requests SET status='superseded' WHERE patient_id=? AND status='pending'"
                " AND COALESCE(kind, 'weekly')='weekly'", (patient_id,))
    cur = con.execute(
        "INSERT INTO adjustment_requests (patient_id, week_end, summary_json, draft_json, draft_source, status, kind)"
        " VALUES (?,?,?,?,?, 'pending', 'weekly')",
        (patient_id, summary["week"]["end"], json.dumps(summary), json.dumps(draft), draft["source"]),
    )
    con.commit()
    return {"request_id": cur.lastrowid, "summary": summary, "draft": draft}


def pending_requests(con):
    rows = con.execute(
        "SELECT request_id, patient_id, week_end, draft_source FROM adjustment_requests"
        " WHERE status='pending' ORDER BY request_id").fetchall()
    return [dict(r) for r in rows]


def apply_decisions(draft_items, before_items, decisions):
    """Per-suggestion accept/reject. A rejected change keeps the exercise as it was (a rejected 'add' is dropped)."""
    before = {i["exercise_id"]: i for i in before_items}
    out = []
    for it in draft_items:
        d = decisions.get(it["exercise_id"], "accept")
        if d not in ("accept", "reject"):
            raise ValueError("Each decision must be 'accept' or 'reject'.")
        if d == "accept" or it.get("action") == "hold":
            out.append(it)
        elif it["exercise_id"] in before:
            b = before[it["exercise_id"]]
            out.append({**it, "action": "hold", "sets": b["sets"], "reps": b["reps"],
                        "reason": "Kept as it was (PT's choice)."})
    return out


def decide_request(con, request_id, approve, final_items=None, pt_name="PT", pt_note="", decisions=None):
    """PT approves (optionally edited, or accept/reject per suggestion) or rejects ("Keep current plan").
    Approval replaces the patient's plan. decisions: {exercise_id: "accept" | "reject"}."""
    req = con.execute("SELECT * FROM adjustment_requests WHERE request_id=?", (request_id,)).fetchone()
    if req is None or req["status"] != "pending":
        raise ValueError("No pending request with that id.")
    draft_items = json.loads(req["draft_json"])["items"]
    if final_items is None and decisions:
        final_items = apply_decisions(draft_items, current_plan(con, req["patient_id"]), decisions)
    final_items = final_items if final_items is not None else draft_items
    if approve:
        assessment = knee_rules.assessment(con, req["patient_id"])
        expected_path = json.loads(req["draft_json"]).get("pathway", "replacement")
        if assessment["pathway"] != expected_path:
            raise ValueError("The care pathway changed. Generate a fresh draft.")
        if expected_path != "replacement":
            knee_rules.check_approval(con, req, assessment, final_items, current_plan(con, req["patient_id"]))
    if approve and not any(int(i["sets"]) > 0 for i in final_items):
        raise ValueError("This plan has no exercises left. Keep at least one, or keep the current plan.")
    edited = int(json.dumps(final_items, sort_keys=True) != json.dumps(draft_items, sort_keys=True))
    now = dt.datetime.now().isoformat(timespec="seconds")
    if approve:
        new_plan = [{"exercise_id": i["exercise_id"], "exercise_name": i["exercise_name"],
                     "sets": i["sets"], "reps": i["reps"]} for i in final_items if int(i["sets"]) > 0]
        week_end = dt.date.fromisoformat(req["week_end"])
        start = dt.date.today() if (req["kind"] or "weekly") == "first" else week_end + dt.timedelta(days=1)
        set_plan(con, req["patient_id"], new_plan, approved_by=pt_name, start_date=start.isoformat())
    con.execute(
        "UPDATE adjustment_requests SET status=?, final_json=?, pt_edited=?, pt_note=?, decided_at=?, decisions_json=?"
        " WHERE request_id=?",
        ("approved" if approve else "rejected", json.dumps(final_items), edited, pt_note, now,
         json.dumps(decisions) if decisions else None, request_id),
    )
    con.commit()
    return {"request_id": request_id, "status": "approved" if approve else "rejected", "pt_edited": bool(edited),
            "plan": current_plan(con, req["patient_id"])}


# ---------- Intake (tab 1) ----------

def intake_summary(a):
    """Short summary for the PT. Rule-based placeholder until the LLM summary is added."""
    def j(x):
        return ", ".join(x) if isinstance(x, list) and x else (x or "not given")
    lines = [
        f"{a.get('age', '?')} y/o, {a.get('daily_activity', 'activity not given')}; "
        f"walking aid: {a.get('walking_aid', 'not given')}.",
        f"Knee pain at {j(a.get('pain_locations'))}, {j(a.get('pain_type'))}, for {a.get('duration', '?')}; "
        f"{a.get('pain_pattern', '?')}. Now {a.get('pain_now', '?')}/10, worst {a.get('pain_worst', '?')}/10.",
        f"Worse with: {j(a.get('worse_with'))}. Better with: {a.get('better_with') or 'not given'}. "
        f"Mostly in the {j(a.get('time_of_day'))}.",
    ]
    if a.get("numbness") == "yes":
        lines.append("Reports numbness: check in person.")
    if a.get("surgery") and a.get("surgery") != "none":
        lines.append(f"Surgery: {a['surgery']} on {a.get('surgery_date') or 'date not given'}.")
    goal = GOALS.get(a.get("goal"), {}).get("label", a.get("goal") or "not given")
    lines.append(f"Goal: {goal}. Home: {j(a.get('home_equipment'))}; "
                 f"can get to the floor: {a.get('can_get_to_floor', '?')}.")
    return "\n".join(lines)


def save_intake(con, patient_id, name, answers):
    """Store intake answers, create or update the patient, return the summary for the PT."""
    old = con.execute("SELECT * FROM patients WHERE patient_id=?", (patient_id,)).fetchone()
    add_patient(con, patient_id, name or (old["name"] if old else ""),
                answers.get("surgery_date") or (old["surgery_date"] if old else ""),
                answers.get("side") or (old["operated_side"] if old else ""),
                answers.get("goal") or (old["goal"] if old else ""))
    summary = intake_summary(answers)
    con.execute("INSERT INTO intakes (patient_id, created_at, answers_json, summary) VALUES (?,?,?,?)",
                (patient_id, dt.datetime.now().isoformat(timespec="seconds"), json.dumps(answers), summary))
    con.commit()
    return summary


def latest_intake(con, patient_id):
    r = con.execute("SELECT * FROM intakes WHERE patient_id=? ORDER BY intake_id DESC LIMIT 1",
                    (patient_id,)).fetchone()
    if r is None:
        return None
    return {"created_at": r["created_at"], "answers": json.loads(r["answers_json"]), "summary": r["summary"]}


def set_goal(con, patient_id, goal):
    if goal not in GOALS:
        raise ValueError(f"Unknown goal '{goal}'. Choose one of: {', '.join(GOALS)}")
    con.execute("UPDATE patients SET goal=? WHERE patient_id=?", (goal, patient_id))
    con.commit()


# ---------- Plan views (tab 3) ----------

def latest_request(con, patient_id, status=None):
    sql = "SELECT * FROM adjustment_requests WHERE patient_id=?"
    args = [patient_id]
    if status:
        sql += " AND status=?"
        args.append(status)
    r = con.execute(sql + " ORDER BY request_id DESC LIMIT 1", args).fetchone()
    if r is None:
        return None
    return {"request_id": r["request_id"], "kind": r["kind"] or "weekly", "week_end": r["week_end"], "status": r["status"],
            "decisions": json.loads(r["decisions_json"]) if r["decisions_json"] else None,
            "draft_source": r["draft_source"], "draft": json.loads(r["draft_json"]),
            "summary": json.loads(r["summary_json"]),
            "final": json.loads(r["final_json"]) if r["final_json"] else None,
            "pt_note": r["pt_note"], "decided_at": r["decided_at"]}


def plan_change_for_patient(con, patient_id):
    """What the patient sees after the PT approves: old vs new per exercise, with reasons."""
    req = latest_request(con, patient_id, "approved")
    if req is None:
        return None
    old = {e["exercise_id"]: e for e in req["summary"]["exercises"]}
    reasons = {i["exercise_id"]: i.get("reason", "") for i in req["draft"]["items"]}
    actions = {i["exercise_id"]: i.get("action") for i in req["final"]}
    changes = []
    for it in req["final"]:
        prev = old.get(it["exercise_id"])
        before = prev["planned"] if prev else None
        after = f"{it['sets']} x {it['reps']}" if int(it["sets"]) > 0 else None
        kind = "new" if before is None else "paused" if after is None else "same" if before == after else "changed"
        changes.append({"exercise_id": it["exercise_id"], "exercise_name": it["exercise_name"],
                        "kind": kind, "action": actions.get(it["exercise_id"]), "before": before, "after": after, "reason": reasons.get(it["exercise_id"], "")})
    return {"approved_at": req["decided_at"], "pt_note": req["pt_note"], "changes": changes,
            "direction": req["draft"].get("direction"), "explanation": req["draft"].get("explanation", ""),
            "goal_label": req["draft"].get("goal_label", "")}


# ---------- Mock data for testing ----------

DEMO_PLAN = [
    {"exercise_id": "heel_slide", "exercise_name": "Heel slide", "sets": 3, "reps": 10},
    {"exercise_id": "sit_to_stand", "exercise_name": "Sit-to-stand", "sets": 3, "reps": 10},
    {"exercise_id": "mini_squat", "exercise_name": "Mini squat", "sets": 3, "reps": 10},
]


def load_demo_week(con, patient_id="u_demo", week_end="2026-11-22"):
    """Seed one patient with a PT plan and 7 days of sessions (pain rises after mini squats)."""
    end = dt.date.fromisoformat(week_end)
    start = (end - dt.timedelta(days=6)).isoformat()
    for table in ("exercise_logs", "checkins", "alerts"):  # loading twice must not double the data
        con.execute(f"DELETE FROM {table} WHERE patient_id=? AND date BETWEEN ? AND ?", (patient_id, start, week_end))
    con.execute("UPDATE adjustment_requests SET status='superseded' WHERE patient_id=? AND status='pending'", (patient_id,))
    con.commit()
    if con.execute("SELECT 1 FROM patients WHERE patient_id=?", (patient_id,)).fetchone() is None:
        add_patient(con, patient_id, "Walter M.", "2026-10-28", "left", "stairs")
    if not current_plan(con, patient_id):  # keep the PT's plan if there is one
        set_plan(con, patient_id, DEMO_PLAN, approved_by="Dana Reyes, DPT",
                 start_date=(end - dt.timedelta(days=6)).isoformat())
    pains = [2, 2, 3, 3, 2, 4, 2]
    for i, pain in enumerate(pains):
        date = (end - dt.timedelta(days=6 - i)).isoformat()
        if i == 3:  # one missed day
            continue
        today = daily_checkin(con, patient_id, pain, date, pain_location="front" if pain >= 4 else "none")
        for ex in today["exercises"]:
            n = ex["sets"] * ex["target_reps"]
            squat = ex["exercise_id"] == "mini_squat"
            log_exercise(con, today["checkin_id"], {
                "exercise_id": ex["exercise_id"],
                "sets_done": ex["sets"],
                "reps_done": n - (2 if squat else 0),
                "form_scores": [0.9 - (0.05 * i if squat else 0.0)] * max(n, 1),
                "error_counts": {"trunk_lean": i} if squat else {},
                "pain_after": min(10, pain + (2 if squat else 0)),
            })
