"""Demo data, loaded when the server starts (the database may be empty after a restart on a host).

Patients match the front-end prototype, with dates relative to today:
  walter   Walter Miller   week ~4, weekly draft waiting for the PT     -> ready for review
  frank    Frank Ortiz     week ~7, steady sessions                     -> on track
  dorothy  Dorothy Hayes   no session for 5 days                        -> gone quiet
  george   George Wilson   pain 8 yesterday                             -> urgent
  maria    Maria Lopez     intake done, first visit tomorrow            -> new
  ruth     Ruth Coleman    week ~5, steady                              -> on track
  thomas   Thomas Reed     finished the program                         -> discharged
Sessions use the same functions as the app (check-in -> exercise results -> end of session).
There is no camera data, so form scores are empty; feelings and pain come from the "patient".
"""
import datetime as dt
import random

import clinic
import rehab_node as rn

PT = "Dana Reyes, PT"
IDS = ["walter", "frank", "dorothy", "george", "maria", "ruth", "thomas"]
FIRST_PLAN = [("heel_slide", 3, 10), ("straight_leg_raise", 3, 10), ("seated_knee_extension", 3, 10),
              ("standing_knee_bend", 3, 10)]
WALTER_INTAKE = {
    "name": "Walter Miller", "dob": "1950-03-14", "sex": "Male", "side": "right", "knee": "Right knee",
    "surgery": "Knee replacement surgery", "goal": "walk", "today": 4, "worst": 6, "best": 2,
    "pattern": "Comes and goes", "trend": "Getting better", "where": ["Front of knee", "Around the scar"],
    "feel": ["Achy or dull"], "walking_aid": "Cane", "living": "I live alone, stairs inside my home",
    "hist": ["High blood pressure", "Type 2 diabetes"], "home_equipment": ["Sturdy chair", "Bed or mat"],
}


def _plan(items):
    return [{"exercise_id": e, "exercise_name": rn.EXERCISES[e], "sets": s, "reps": r} for e, s, r in items]


def _play(con, rng, pid, start, end, pain0=4.0, pain_drop=0.15, easy_p=0.35, hard_p=0.05, skip_p=0.08,
          flare_day=None):
    """Play out sessions from start to end (dates), skipping rest days."""
    rest = set(rn.rest_days(con, pid))
    d = start
    while d <= end:
        if rn.WEEKDAYS[d.weekday()] in rest or rng.random() < skip_p:
            d += dt.timedelta(days=1)
            continue
        weeks = (d - start).days / 7
        pain = max(0, min(10, round(pain0 - pain_drop * weeks * 7 / 7 + rng.gauss(0, 0.7))))
        if flare_day and d == flare_day:
            pain = 8
        today = rn.daily_checkin(con, pid, pain, d.isoformat(), pain_location="front" if pain >= 4 else "none")
        if today["action"] == "rest":
            d += dt.timedelta(days=1)
            continue
        for ex in today["exercises"]:
            n = ex["sets"] * ex["target_reps"]
            r = rng.random()
            feeling = "too_hard" if r < hard_p else "easy" if r < hard_p + easy_p else "just_right"
            res = {"exercise_id": ex["exercise_id"], "sets_done": ex["sets"],
                   "reps_done": n if feeling != "too_hard" else max(1, n - rng.randint(2, 6)),
                   "form_scores": [], "feeling": feeling}
            if feeling == "too_hard":
                res["pain_after"] = min(6, pain + rng.randint(1, 2))   # demo: only flare days are urgent
            rn.log_exercise(con, today["checkin_id"], res)
        rn.end_session(con, today["checkin_id"], max(0, min(6, pain + rng.choice([-1, 0, 0, 1]))))
        d += dt.timedelta(days=1)


def _weekly_cycle(con, pid, first_monday, last_sunday, approve=True):
    """At each past week end, draft and (simulated PT) approve, so plan history builds up."""
    end = first_monday + dt.timedelta(days=6)
    while end <= last_sunday:
        req = rn.create_adjustment_request(con, pid, end.isoformat(), use_llm=False)
        if approve and any(i["sets"] > 0 for i in req["draft"]["items"]):
            rn.decide_request(con, req["request_id"], True, pt_name=PT)
            con.execute("UPDATE adjustment_requests SET decided_at=? WHERE request_id=?",   # decided back then, not now
                        (end.isoformat() + "T18:00:00", req["request_id"]))
        end += dt.timedelta(days=7)


def _patient(con, pid, name, dob, surgery_days_ago, today, side="right", goal="walk", **kw):
    rn.add_patient(con, pid, name, (today - dt.timedelta(days=surgery_days_ago)).isoformat(), side, goal,
                   dob=dob, phone=kw.get("phone"), status=kw.get("status", "active"),
                   rest_days=kw.get("rest_days", ["Tue", "Sun"]), join_code=kw.get("join_code"))


def seed_demo(con, today=None, reset=False):
    today = today or dt.date.today()
    if not reset and con.execute("SELECT 1 FROM patients WHERE patient_id='walter'").fetchone():
        return False
    for pid in IDS:
        for t in ("exercise_logs", "checkins", "alerts", "adjustment_requests", "plan_items", "intakes", "visits",
                  "calls", "discharges", "messages", "patients"):
            con.execute(f"DELETE FROM {t} WHERE patient_id=?", (pid,))
    con.commit()
    rng = random.Random(42)
    monday = today - dt.timedelta(days=today.weekday())
    yesterday = today - dt.timedelta(days=1)

    # Walter: started 3 weeks ago; last week's draft waits for the PT.
    _patient(con, "walter", "Walter Miller", "1950-03-14", 34, today, phone="(412) 555-0187", join_code="RIV284")
    rn.save_intake(con, "walter", "Walter Miller", WALTER_INTAKE)
    start = monday - dt.timedelta(weeks=3)
    v = clinic.schedule_visit(con, "walter", (start - dt.timedelta(days=4)).isoformat(), "10:30", kind="first",
                              reason="First visit")["visit_id"]
    clinic.save_findings(con, "walter", {"bend_deg": 85, "short_of_straight_deg": 6, "rom_mode": "active", "quad_lag": True,
                                          "quad_grade": "3+/5", "pain_today": 4, "tug_sec": 15.2, "chair_stand_30s": 7,
                                          "walking_aid": "Cane", "stairs": "One step at a time with rail",
                                          "swelling_cm": {"left": 38.0, "right": 41.5}, "incision": "Healing well",
                                          "goals_week6": ["Walk 30 minutes with no cane", "Bend to 110°"],
                                          "precautions": ["No kneeling yet"]}, visit_id=v)
    rn.set_plan(con, "walter", _plan(FIRST_PLAN), approved_by=PT, start_date=start.isoformat())
    _play(con, rng, "walter", start, monday - dt.timedelta(days=8), pain0=4.5, easy_p=0.4, hard_p=0.0)
    _weekly_cycle(con, "walter", start, monday - dt.timedelta(days=8))
    _play(con, rng, "walter", monday - dt.timedelta(days=7), yesterday, pain0=2.2, easy_p=0.55, hard_p=0.0, skip_p=0)
    rn.create_adjustment_request(con, "walter", (monday - dt.timedelta(days=1)).isoformat(), use_llm=False)
    clinic.schedule_visit(con, "walter", (monday + dt.timedelta(days=10)).isoformat(), "10:30", reason="Week 6 check")

    # Frank: on track, later in the program.
    _patient(con, "frank", "Frank Ortiz", "1958-06-02", 51, today, phone="(412) 555-0174", goal="stairs")
    rn.save_intake(con, "frank", "Frank Ortiz", {"goal": "stairs", "side": "right"})
    s = monday - dt.timedelta(weeks=6)
    rn.set_plan(con, "frank", _plan([("heel_slide", 3, 10), ("sit_to_stand", 3, 10), ("glute_bridge", 2, 10)]),
                approved_by=PT, start_date=s.isoformat())
    _play(con, rng, "frank", s, monday - dt.timedelta(days=1), pain0=4, easy_p=0.3)
    _weekly_cycle(con, "frank", s, monday - dt.timedelta(days=1))
    _play(con, rng, "frank", monday, yesterday, pain0=2.5, easy_p=0.3, skip_p=0)

    # Ruth: on track.
    _patient(con, "ruth", "Ruth Coleman", "1955-09-21", 37, today, side="left", phone="(412) 555-0131")
    rn.save_intake(con, "ruth", "Ruth Coleman", {"goal": "walk", "side": "left"})
    s = monday - dt.timedelta(weeks=4)
    rn.set_plan(con, "ruth", _plan(FIRST_PLAN), approved_by=PT, start_date=s.isoformat())
    _play(con, rng, "ruth", s, monday - dt.timedelta(days=1), pain0=4)
    _weekly_cycle(con, "ruth", s, monday - dt.timedelta(days=1))
    _play(con, rng, "ruth", monday, yesterday, pain0=3, skip_p=0)

    # Dorothy: gone quiet (last session 5 days ago).
    _patient(con, "dorothy", "Dorothy Hayes", "1945-02-11", 25, today, side="left", phone="(412) 555-0148", goal="home")
    rn.save_intake(con, "dorothy", "Dorothy Hayes", {"goal": "home", "side": "left"})
    s = today - dt.timedelta(days=18)
    rn.set_plan(con, "dorothy", _plan(FIRST_PLAN[:3]), approved_by=PT, start_date=s.isoformat())
    _play(con, rng, "dorothy", s, today - dt.timedelta(days=5), pain0=5.5, pain_drop=0.05, skip_p=0)

    # George: urgent, pain 8 on his last exercise day (rest days are skipped).
    flare = yesterday
    while rn.WEEKDAYS[flare.weekday()] in ("Tue", "Sun"):
        flare -= dt.timedelta(days=1)
    _patient(con, "george", "George Wilson", "1953-12-30", 12, today, phone="(412) 555-0162")
    rn.save_intake(con, "george", "George Wilson", {"goal": "home", "side": "right"})
    s = today - dt.timedelta(days=6)
    rn.set_plan(con, "george", _plan(FIRST_PLAN[:3]), approved_by=PT, start_date=s.isoformat())
    _play(con, rng, "george", s, flare, pain0=5, skip_p=0, flare_day=flare)

    # Maria: new, intake ready, first visit tomorrow.
    _patient(con, "maria", "Maria Lopez", "1959-04-08", 9, today, side="left", phone="(412) 555-0199", goal="home")
    rn.save_intake(con, "maria", "Maria Lopez", {"goal": "home", "side": "left", "today": 5, "worst": 7,
                                                 "walking_aid": "Walker"})
    clinic.schedule_visit(con, "maria", (today + dt.timedelta(days=1)).isoformat(), "09:00", kind="first", reason="First visit")

    # Thomas: discharged.
    _patient(con, "thomas", "Thomas Reed", "1950-07-17", 60, today, phone="(412) 555-0110")
    rn.save_intake(con, "thomas", "Thomas Reed", {"goal": "walk", "side": "right"})
    s = today - dt.timedelta(days=52)
    clinic.save_findings(con, "thomas", {"bend_deg": 80, "tug_sec": 16, "chair_stand_30s": 6})
    rn.set_plan(con, "thomas", _plan(FIRST_PLAN), approved_by=PT, start_date=s.isoformat())
    _play(con, rng, "thomas", s, today - dt.timedelta(days=10), pain0=4, easy_p=0.4)
    clinic.discharge(con, "thomas", note="Great work, Thomas. Keep walking every day.",
                     final_measures={"bend_deg": 118, "tug_sec": 9.5, "chair_stand_30s": 13},
                     checklist=["Home program reviewed", "Summary sent to the doctor"], community_program=True)
    # Conversations from the prototype, dated relative to today. Older messages are already read.
    def msg(pid, days_ago, hm, sender, text, read=True):
        at = f"{(today - dt.timedelta(days=days_ago)).isoformat()}T{hm}:00"
        clinic.send_message(con, pid, sender, text, at=at)
        if read:
            con.execute("UPDATE messages SET read_at=? WHERE patient_id=? AND created_at=?", (at, pid, at))
    msg("walter", 7, "08:55", "pt", "Morning, Walter! Your plan for this week is ready. Start with the heel slides and go slow.")
    msg("walter", 7, "09:40", "patient", "Thank you, Dana. Will do.")
    msg("walter", 5, "10:13", "patient", "The knee bends were harder today. Is it okay to hold the chair with both hands?")
    msg("walter", 5, "13:30", "pt", "Yes, both hands is perfect. Safety first.")
    msg("walter", 4, "16:12", "pt", "Great work on your heel slides this week, Walter. Take the knee bends slowly and hold the chair.", read=False)
    msg("frank", 2, "09:20", "patient", "Should I add more walking on top of the exercises?", read=False)
    msg("ruth", 3, "14:30", "patient", "Can I do my exercises in the evening instead?")
    msg("ruth", 3, "15:05", "pt", "Yes, any time of day works. Just keep the same days.")
    # Older alerts were handled at the time; only recent ones stay open.
    con.execute(f"UPDATE alerts SET status='closed' WHERE date < ? AND patient_id IN ({','.join('?' * len(IDS))})",
                (flare.isoformat(), *IDS))
    con.commit()
    return True
