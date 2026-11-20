"""Synthetic patients for testing the data flow and the plan-adjustment step.

There are no real patients yet, so this script makes fake ones. Each fake patient has hidden traits
(how fast pain drops, how good their form is, how often they skip) and a scenario such as "flare" or
"poor_form". The script then plays out their weeks through the SAME functions the app uses:

    daily_checkin -> log_exercise (one fake detection JSON per exercise) -> weekly_summary
    -> create_adjustment_request (rule draft) -> decide_request (a simulated PT approves the draft)

So the database fills up exactly as it would with real use, and every handoff between features gets tested.

Outputs (in --out):
  weekly_summaries.jsonl   one line per patient-week: the exact JSON the plan model receives
  plan_rows.csv            one row per patient-week-exercise: features + the rule draft's action
                           + an empty pt_label column for the PT to fill in (ground truth)
  The database (--db) holds all the raw rows (patients, plans, check-ins, exercise logs, alerts, drafts).

Run:  python simulate.py --patients 50 --weeks 6
The numbers are made up to look plausible. They are for testing and for PT labelling, not evidence.
"""
import argparse
import csv
import datetime as dt
import json
import os
import random

import rehab_node as rn

# How hard each exercise is on the knee (0-1). Harder exercises raise pain more when the dose is too high.
DIFFICULTY = {
    "heel_slide": 0.2, "straight_leg_raise": 0.3, "sitting_knee_bend": 0.2, "standing_knee_bend": 0.4,
    "seated_knee_extension": 0.3, "sit_to_stand": 0.5, "mini_squat": 0.6, "step_up": 0.8,
    "glute_bridge": 0.4, "side_lying_hip_abduction": 0.3,
}
# Form errors the detection model might report for each exercise.
ERRORS = {
    "heel_slide": ["partial_rom", "hip_lift"], "straight_leg_raise": ["knee_bent", "lift_too_high"],
    "sitting_knee_bend": ["partial_rom"], "standing_knee_bend": ["hip_flexion", "trunk_lean"],
    "seated_knee_extension": ["partial_rom", "too_fast"], "sit_to_stand": ["knee_valgus", "trunk_lean", "uses_hands"],
    "mini_squat": ["knee_valgus", "trunk_lean", "knees_past_toes"], "step_up": ["knee_valgus", "hip_drop"],
    "glute_bridge": ["hip_sag", "uneven_hips"], "side_lying_hip_abduction": ["hip_roll", "leg_forward"],
}
SCENARIOS = ["steady", "flare", "poor_form", "low_adherence", "early_goal", "plateau"]
START = dt.date(2026, 9, 7)  # first simulated Monday


def clip(x, lo, hi):
    return max(lo, min(hi, x))


def make_patient(rng, i):
    """Hidden traits. The app never sees these; they only drive the fake data."""
    scenario = SCENARIOS[i % len(SCENARIOS)]
    p = {
        "patient_id": f"sim_{i + 1:03d}",
        "scenario": scenario,
        "weeks_post_op_at_start": rng.randint(1, 6),
        "goal": rng.choice(["home", "walk", "stairs"]),
        "base_pain": rng.uniform(2.5, 5.0),     # pain before exercise in week 1
        "pain_drop": rng.uniform(0.80, 0.92),   # pain multiplied by this each week
        "capacity": rng.uniform(0.25, 0.45),    # how much load the knee tolerates in week 1
        "capacity_gain": rng.uniform(0.05, 0.10),
        "form": rng.uniform(0.72, 0.88),        # form score in week 1
        "form_gain": rng.uniform(0.01, 0.03),
        "adherence": rng.uniform(0.80, 0.97),   # chance of doing the session each day
        "flare_week": None, "flare_ex": None, "bad_form_ex": None,
    }
    if scenario == "flare":            # pain jumps for one or two weeks, caused by one exercise
        p["flare_week"] = rng.randint(2, 4)
    elif scenario == "poor_form":      # form on one exercise gets worse through a week
        p["form"] = rng.uniform(0.62, 0.72)
    elif scenario == "low_adherence":
        p["adherence"] = rng.uniform(0.35, 0.6)
    elif scenario == "early_goal":     # ambitious goal soon after surgery
        p["goal"], p["weeks_post_op_at_start"] = "jog", rng.randint(1, 3)
    elif scenario == "plateau":        # little improvement
        p["capacity_gain"], p["form_gain"], p["pain_drop"] = 0.0, 0.0, 0.99
    return p


def starter_plan(weeks_post_op):
    """What a PT might prescribe at the first visit."""
    if weeks_post_op <= 2:
        plan = [("heel_slide", 3, 10), ("straight_leg_raise", 2, 10), ("sit_to_stand", 2, 8)]
    else:
        plan = [("heel_slide", 3, 10), ("sit_to_stand", 3, 10), ("glute_bridge", 2, 10), ("mini_squat", 2, 8)]
    return [{"exercise_id": e, "exercise_name": rn.EXERCISES[e], "sets": s, "reps": r} for e, s, r in plan]


def fake_detection(rng, p, week, day, ex, pain_today):
    """One detection JSON for one exercise, like the pose/form model would send to POST /api/result."""
    eid = ex["exercise_id"]
    load = DIFFICULTY[eid] * ex["sets"] * ex["target_reps"] / 30          # ~0.1 (easy) to ~0.8 (hard)
    stress = load - (p["capacity"] + p["capacity_gain"] * week)            # > 0 means too much for the knee
    flare = p["flare_week"] is not None and week in (p["flare_week"], p["flare_week"] + 1) and eid == p["flare_ex"]

    n = ex["sets"] * ex["target_reps"]
    completion = clip(rng.gauss(0.97, 0.04) - max(0, stress) * 0.6 - (0.15 if flare else 0), 0.4, 1.0)
    reps_done = max(1, round(n * completion))

    form = p["form"] + p["form_gain"] * week - max(0, stress) * 0.4
    if eid == p["bad_form_ex"]:
        form -= 0.03 * day                                                  # gets worse through the week
    scores = [round(clip(form - 0.004 * r + rng.gauss(0, 0.04), 0, 1), 2) for r in range(reps_done)]

    avg = sum(scores) / len(scores)
    errors = {}
    if avg < 0.75:
        for name in rng.sample(ERRORS[eid], k=min(len(ERRORS[eid]), 1 + (avg < 0.6))):
            errors[name] = rng.randint(1, max(1, round((0.8 - avg) * 20)))

    pain_after = pain_today + max(0, stress) * 6 + rng.gauss(0.3, 0.7) + (rng.uniform(3, 5) if flare else 0)
    return {"exercise_id": eid, "sets_done": ex["sets"], "reps_done": reps_done, "form_scores": scores,
            "error_counts": errors, "pain_after": int(clip(round(pain_after), 0, 10))}


def simulate_patient(con, rng, p, weeks, use_llm=False):
    pid = p["patient_id"]
    for table in ("exercise_logs", "checkins", "alerts", "adjustment_requests", "plan_items", "patients"):
        con.execute(f"DELETE FROM {table} WHERE patient_id=?", (pid,))   # re-running gives a clean patient
    surgery = START - dt.timedelta(weeks=p["weeks_post_op_at_start"])
    rn.add_patient(con, pid, f"Sim {p['scenario']}", surgery.isoformat(), rng.choice(["left", "right"]), p["goal"])
    rn.set_plan(con, pid, starter_plan(p["weeks_post_op_at_start"]), approved_by="Sim PT", start_date=START.isoformat())

    out = []
    for week in range(weeks):
        plan_ids = [i["exercise_id"] for i in rn.current_plan(con, pid)]
        if p["scenario"] == "flare" and p["flare_ex"] is None:
            p["flare_ex"] = max(plan_ids, key=DIFFICULTY.get)    # the hardest exercise causes the flare
        if p["scenario"] == "poor_form":
            p["bad_form_ex"] = rng.choice(plan_ids) if week % 2 == 0 else None

        for day in range(7):
            date = START + dt.timedelta(weeks=week, days=day)
            if rng.random() > p["adherence"]:
                continue                                         # skipped day: no check-in
            flare_now = p["flare_week"] is not None and week in (p["flare_week"], p["flare_week"] + 1)
            pain = p["base_pain"] * p["pain_drop"] ** week + rng.gauss(0, 0.8) + (2.5 if flare_now else 0)
            pain = int(clip(round(pain), 0, 10))
            location = "none" if pain <= 3 else rng.choice(["front", "inner", "whole knee"])
            today = rn.daily_checkin(con, pid, pain, date.isoformat(), pain_location=location)
            if today["action"] == "rest":
                continue
            for ex in today["exercises"]:
                if ex["sets"] > 0:
                    rn.log_exercise(con, today["checkin_id"], fake_detection(rng, p, week, day, ex, pain))

        week_end = (START + dt.timedelta(weeks=week, days=6)).isoformat()
        req = rn.create_adjustment_request(con, pid, week_end, use_llm=use_llm)
        # Simulated PT: approves the draft as is, so next week's plan follows it.
        # Real PT decisions replace this once we have labels.
        # If the draft pauses everything, the simulated PT rejects it and keeps the old plan
        # (an empty plan would block the next check-in).
        keeps_some = any(i["sets"] > 0 for i in req["draft"]["items"])
        rn.decide_request(con, req["request_id"], approve=keeps_some, pt_name="Sim PT",
                          pt_note="" if keeps_some else "Draft paused every exercise; kept the current plan.")
        out.append({"scenario": p["scenario"], "week_index": week + 1, "summary": req["summary"], "draft": req["draft"]})
    return out


FIELDS = ["patient_id", "scenario", "week_index", "week_end", "week_post_op", "goal", "days_checked_in",
          "pain_today_avg", "days_reduced", "days_rest", "exercise_id", "planned", "days_done", "reps_done_share",
          "form_avg", "form_trend", "pain_after_avg", "pain_after_max", "high_pain_days", "error_total",
          "rule_action", "rule_new_dose", "draft_direction", "draft_added", "note_for_pt", "pt_label", "pt_new_dose", "pt_comment"]


def plan_rows(record):
    """Flatten one week into one row per exercise (the plan decision is made per exercise)."""
    s, d = record["summary"], record["draft"]
    actions = {i["exercise_id"]: i for i in d["items"]}
    rows = []
    for e in s["exercises"]:
        a = actions.get(e["exercise_id"], {})
        rows.append({
            "patient_id": s["patient_id"], "scenario": record["scenario"], "week_index": record["week_index"],
            "week_end": s["week"]["end"], "week_post_op": s["week_post_op"], "goal": s["goal"],
            "days_checked_in": s["days_checked_in"], "pain_today_avg": s["pain_today_avg"],
            "days_reduced": s["days_reduced"], "days_rest": s["days_rest"],
            "exercise_id": e["exercise_id"], "planned": e["planned"], "days_done": e["days_done"],
            "reps_done_share": e["reps_done_share"], "form_avg": e["form_avg"], "form_trend": e["form_trend"],
            "pain_after_avg": e["pain_after_avg"], "pain_after_max": e["pain_after_max"],
            "high_pain_days": e["high_pain_days"], "error_total": sum(e["error_counts"].values()),
            "rule_action": a.get("action", ""), "rule_new_dose": f"{a['sets']} x {a['reps']}" if a else "",
            "draft_direction": d["direction"],
            "draft_added": " ".join(i["exercise_id"] for i in d["items"] if i["action"] == "add"),
            "note_for_pt": d.get("note_for_pt", ""),
            "pt_label": "", "pt_new_dose": "", "pt_comment": "",
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patients", type=int, default=50)
    ap.add_argument("--weeks", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7, help="same seed = same data")
    ap.add_argument("--db", default="sim.db", help="kept separate from the app's rehab_test.db")
    ap.add_argument("--out", default="sim_out")
    ap.add_argument("--llm", action="store_true", help="use the LLM draft (needs OPENAI_API_KEY)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    con = rn.connect(args.db)
    os.makedirs(args.out, exist_ok=True)
    records = []
    for i in range(args.patients):
        records += simulate_patient(con, rng, make_patient(rng, i), args.weeks, use_llm=args.llm)

    with open(os.path.join(args.out, "weekly_summaries.jsonl"), "w") as f:
        for r in records:
            f.write(json.dumps({"scenario": r["scenario"], "week_index": r["week_index"], **r["summary"]}) + "\n")
    rows = [row for r in records for row in plan_rows(r)]
    with open(os.path.join(args.out, "plan_rows.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    count = lambda key, items: {k: sum(1 for x in items if x == k) for k in sorted(set(items))}
    print(f"{args.patients} patients x {args.weeks} weeks -> {len(records)} weekly summaries, {len(rows)} exercise rows")
    print("Draft direction per week:", count(None, [r["draft"]["direction"] for r in records]))
    print("Action per exercise:     ", count(None, [r["rule_action"] for r in rows]))
    print("Database:", os.path.abspath(args.db))
    print("Files:   ", os.path.abspath(args.out))


if __name__ == "__main__":
    main()
