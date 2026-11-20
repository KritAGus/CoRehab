"""Daily exercise node: pain check-in, rep adjustment, session logging, weekly summary.

Flow:
  PT plan (set_plan) -> daily_checkin(pain) -> today's adjusted plan
  -> movement analysis sends results -> log_exercise(...)
  -> end of week: weekly_summary(...) -> JSON for the plan-adjustment step
"""
import datetime as dt
import json
import sqlite3
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

PAIN_LOCATIONS = ["none", "front", "back", "inner", "outer", "whole knee"]

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
    con.executescript(SCHEMA)
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

def add_patient(con, patient_id, name, surgery_date, operated_side, goal=""):
    con.execute(
        "INSERT OR REPLACE INTO patients VALUES (?,?,?,?,?)",
        (patient_id, name, surgery_date, operated_side, goal),
    )
    con.commit()


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

def daily_checkin(con, patient_id, pain_today, date=None, pain_location="none"):
    """Ask pain before the session; return today's plan with adjusted reps.

    pain_location is stored for the PT and the weekly summary; it does not change reps yet.
    """
    date = _today(date)
    if not current_plan(con, patient_id):
        raise ValueError(f"No plan for {patient_id}. The PT must enter a plan first.")
    action, factor = rep_rule(pain_today)
    con.execute(
        "INSERT INTO checkins (patient_id, date, pain_today, pain_location, action, rep_factor) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(patient_id, date) DO UPDATE SET pain_today=excluded.pain_today,"
        " pain_location=excluded.pain_location, action=excluded.action, rep_factor=excluded.rep_factor",
        (patient_id, date, pain_today, pain_location, action, factor),
    )
    if action == "rest":
        con.execute(
            "INSERT INTO alerts (patient_id, date, tier, reason) VALUES (?,?,?,?)",
            (patient_id, date, "urgent", f"Pain {pain_today}/10 before session; exercise skipped today"),
        )
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
    return {"checkin_id": checkin_id, "date": date, "pain_today": pain_today,
            "action": action, "message": messages[action], "exercises": today}


def log_exercise(con, checkin_id, result):
    """Store one exercise result from movement analysis.

    result: {"exercise_id", "sets_done", "reps_done", "form_scores": [0-1 per rep],
             "error_counts": {...}, "pain_after": 0-10}
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
    con.execute(
        "INSERT INTO exercise_logs (checkin_id, patient_id, date, exercise_id, planned_sets, planned_reps,"
        " target_reps, sets_done, reps_done, form_score, errors_json, pain_after)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (checkin_id, ck["patient_id"], ck["date"], result["exercise_id"], item["sets"], item["reps"],
         adjusted_reps(item["reps"], ck["rep_factor"]), int(result["sets_done"]), int(result["reps_done"]),
         form, json.dumps(result.get("error_counts") or {}), pain_after),
    )
    if pain_after is not None and pain_after >= STOP_PAIN:
        con.execute(
            "INSERT INTO alerts (patient_id, date, tier, reason) VALUES (?,?,?,?)",
            (ck["patient_id"], ck["date"], "urgent",
             f"Pain {pain_after}/10 after {item['exercise_name']}"),
        )
    con.commit()


# ---------- End of week: input for plan adjustment ----------

def weekly_summary(con, patient_id, week_end=None, days=7):
    """Aggregate the last `days` days into one JSON for the plan-adjustment step."""
    end = dt.date.fromisoformat(_today(week_end))
    start = end - dt.timedelta(days=days - 1)
    s, e = start.isoformat(), end.isoformat()

    checkins = [dict(r) for r in con.execute(
        "SELECT date, pain_today, pain_location, action FROM checkins"
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
        "days_reduced": sum(c["action"] == "reduced" for c in checkins),
        "days_rest": sum(c["action"] == "rest" for c in checkins),
        "pain_today_avg": round(mean(c["pain_today"] for c in checkins), 1) if checkins else None,
        "pain_today_by_day": {c["date"]: c["pain_today"] for c in checkins},
        "exercises": exercises,
        "alerts": alerts,
    }


# ---------- Plan adjustment draft (sent to the PT, never straight to the patient) ----------

# Starting thresholds; tune them with the PT ground-truth survey.
PROGRESS_FORM, PROGRESS_PAIN, PROGRESS_SHARE, MIN_DAYS = 0.8, 3, 0.9, 4
EASIER_FORM, EASIER_PAIN, EASIER_TREND = 0.6, 5, -0.15
MAX_REPS = 15
PAUSE_DAYS = 2  # pause an exercise after pain >= 7 on this many days in the week; one day -> lighter


def _ready(ex):
    """This exercise went well enough this week to make it harder."""
    return (ex["days_done"] >= MIN_DAYS and ex["form_avg"] is not None and ex["form_avg"] >= PROGRESS_FORM
            and ex["pain_after_avg"] is not None and ex["pain_after_avg"] <= PROGRESS_PAIN
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
                or (pain_avg is not None and pain_avg >= EASIER_PAIN) or (trend is not None and trend <= EASIER_TREND):
            new_reps = adjusted_reps(reps, 0.7)
            bits = []
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
            if _ready(ex):
                if reps < target[1]:
                    new_reps = min(target[1], reps + 2)
                else:
                    new_sets = min(target[0], sets + 1)
                action = "progress"
                why = (f"Your goal needs {_dose(*target)}. Form {form} and pain {pain_avg}/10 show you're ready "
                       f"for the next step.")
            else:
                action = "hold"
                why = f"Your goal needs {_dose(*target)}; we'll step up once form is 0.8+ and pain stays 3/10 or less."
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
    week_ok = (days >= MIN_DAYS and pain_week is not None and pain_week <= PROGRESS_PAIN
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
    week_line = (f"This week you exercised {days} of 7 days, with average pain {pain_week if pain_week is not None else '–'}/10"
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


def llm_draft(summary, rules, model=None):
    """Optional LLM draft (OpenAI). Returns None if no API key or the call fails."""
    import os
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
        client = OpenAI()
        targets = {EXERCISES[k]: _dose(*v) for k, v in GOALS[rules["goal"]]["targets"].items()}
        resp = client.chat.completions.create(
            model=model or os.environ.get("LLM_MODEL", "gpt-6.2-sol"),
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": LLM_PROMPT.replace("{summary}", json.dumps(summary))
                       .replace("{rules}", json.dumps(rules)).replace("{targets}", json.dumps(targets))}],
        )
        draft = json.loads(resp.choices[0].message.content)
    except Exception:
        return None
    return validate_draft(draft, summary, fallback=rules)


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
    rules = goal_draft(summary)
    draft = (llm_draft(summary, rules) if use_llm else None) or rules
    con.execute("UPDATE adjustment_requests SET status='superseded' WHERE patient_id=? AND status='pending'",
                (patient_id,))
    cur = con.execute(
        "INSERT INTO adjustment_requests (patient_id, week_end, summary_json, draft_json, draft_source, status)"
        " VALUES (?,?,?,?,?, 'pending')",
        (patient_id, summary["week"]["end"], json.dumps(summary), json.dumps(draft), draft["source"]),
    )
    con.commit()
    return {"request_id": cur.lastrowid, "summary": summary, "draft": draft}


def pending_requests(con):
    rows = con.execute(
        "SELECT request_id, patient_id, week_end, draft_source FROM adjustment_requests"
        " WHERE status='pending' ORDER BY request_id").fetchall()
    return [dict(r) for r in rows]


def decide_request(con, request_id, approve, final_items=None, pt_name="PT", pt_note=""):
    """PT approves (optionally edited) or rejects. Approval replaces the patient's plan."""
    req = con.execute("SELECT * FROM adjustment_requests WHERE request_id=?", (request_id,)).fetchone()
    if req is None or req["status"] != "pending":
        raise ValueError("No pending request with that id.")
    draft_items = json.loads(req["draft_json"])["items"]
    final_items = final_items if final_items is not None else draft_items
    edited = int(json.dumps(final_items, sort_keys=True) != json.dumps(draft_items, sort_keys=True))
    now = dt.datetime.now().isoformat(timespec="seconds")
    if approve:
        new_plan = [{"exercise_id": i["exercise_id"], "exercise_name": i["exercise_name"],
                     "sets": i["sets"], "reps": i["reps"]} for i in final_items if int(i["sets"]) > 0]
        week_end = dt.date.fromisoformat(req["week_end"])
        set_plan(con, req["patient_id"], new_plan, approved_by=pt_name,
                 start_date=(week_end + dt.timedelta(days=1)).isoformat())
    con.execute(
        "UPDATE adjustment_requests SET status=?, final_json=?, pt_edited=?, pt_note=?, decided_at=?"
        " WHERE request_id=?",
        ("approved" if approve else "rejected", json.dumps(final_items), edited, pt_note, now, request_id),
    )
    con.commit()
    return {"request_id": request_id, "status": "approved" if approve else "rejected", "pt_edited": bool(edited)}


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
    return {"request_id": r["request_id"], "week_end": r["week_end"], "status": r["status"],
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
