"""Clinic-app features on top of rehab_node (see docs: "Home PT: Frontend and Backend Alignment").

Patient app: home status, consent, setup safety check, progress / streak / medals, discharge summary.
PT app: patient list with status groups, add patient, profile, private notes, visits (scheduled + clinic
findings), first-plan draft (rules, LLM optional), discharge, call log and RTM data days.

Everything goes through the same SQLite database as rehab_node.
"""
import datetime as dt
import json
import os
from statistics import mean

import rehab_node as rn

SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (
    visit_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL,
    kind          TEXT NOT NULL,      -- first / follow_up / discharge
    status        TEXT NOT NULL,      -- scheduled / done
    date          TEXT,
    time          TEXT,
    place         TEXT,
    reason        TEXT,               -- what the patient sees
    findings_json TEXT,               -- clinic measurements, autosaved while the PT types
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS calls (
    call_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL,
    date          TEXT NOT NULL,
    minutes       INTEGER,
    agreed        TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    msg_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id    TEXT NOT NULL,
    sender        TEXT NOT NULL,      -- patient / pt
    kind          TEXT NOT NULL DEFAULT 'text',   -- text / voice / photo / session
    text          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    read_at       TEXT                -- when the other side opened it
);
CREATE TABLE IF NOT EXISTS discharges (
    patient_id    TEXT PRIMARY KEY,
    date          TEXT NOT NULL,
    summary_json  TEXT NOT NULL,      -- start vs final measures, sessions, checklist, community program
    note          TEXT                -- closing note to the patient
);
"""

CLINIC = {"code": "RIV284", "name": "Riverside Physical Therapy", "address": "400 Riverside Drive, Pittsburgh",
          "pt_name": "Dana Reyes, PT"}
GONE_QUIET_DAYS = 4
MEDALS = [("bronze", "One full week", 7), ("silver", "Two strong weeks", 14), ("gold", "Six-week finish", 42)]


def connect(path):
    con = rn.connect(path)
    con.executescript(SCHEMA)
    return con


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def _patient(con, pid):
    p = con.execute("SELECT * FROM patients WHERE patient_id=?", (pid,)).fetchone()
    if p is None:
        raise ValueError(f"No patient '{pid}'.")
    return p


def _age(dob, today):
    try:
        d = dt.date.fromisoformat(dob)
    except (TypeError, ValueError):
        return None
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def _week_post_op(p, today):
    try:
        return max(0, (today - dt.date.fromisoformat(p["surgery_date"])).days // 7)
    except (TypeError, ValueError):
        return None


# ---------- joining, consent, safety ----------

def join(con, code):
    """Demo login: the clinic join code links to the demo patient (any code goes to the one demo clinic)."""
    p = con.execute("SELECT patient_id FROM patients WHERE join_code=?", (code.strip().upper(),)).fetchone()
    pid = p["patient_id"] if p else "walter"
    return {"patient_id": pid, "clinic": CLINIC}


def _new_id(con, name):
    base = "".join(ch for ch in name.lower() if ch.isalnum())[:12] or "patient"
    pid, n = base, 1
    while con.execute("SELECT 1 FROM patients WHERE patient_id=?", (pid,)).fetchone():
        n += 1
        pid = f"{base}{n}"
    return pid


def login(con, text):
    """Demo sign-in: username (patient_id) or full name, case doesn't matter. First name works if only one matches."""
    t = " ".join((text or "").split()).lower()
    if not t:
        raise ValueError("Type your name or username.")
    rows = con.execute("SELECT patient_id, name FROM patients WHERE COALESCE(status,'active')!='intake_sent'").fetchall()
    hit = [r for r in rows if r["patient_id"].lower() == t] or [r for r in rows if (r["name"] or "").lower() == t] \
        or [r for r in rows if (r["name"] or "").lower().split(" ")[0] == t]
    if not hit:
        raise ValueError(f"No account found for '{text.strip()}'. Check the spelling, or create a new account.")
    if len(hit) > 1:
        raise ValueError("More than one account has that name. Sign in with your username: "
                         + ", ".join(r["patient_id"] for r in hit))
    return {"patient_id": hit[0]["patient_id"], "name": hit[0]["name"]}


def register(con, name, dob=None, phone=None, surgery_date=None, side=None):
    """Patient creates an account in the app (after joining the clinic). Intake comes next."""
    if not (name or "").strip():
        raise ValueError("Name is required.")
    pid = _new_id(con, name)
    rn.add_patient(con, pid, name.strip(), surgery_date or "", side or "", "", dob=dob, phone=phone, status="active")
    rn.knee_rules.save(con, pid, {"pathway": "unspecified"})
    con.commit()
    return {"patient_id": pid, "username": pid}


def add_patient_by_pt(con, name, phone, surgery, surgery_date, side="right", dob=None):
    """PT adds a patient; they get an intake link (status intake_sent)."""
    pid = _new_id(con, name)
    rn.add_patient(con, pid, name, surgery_date, side, "", phone=phone, dob=dob, status="intake_sent")
    path = {"General knee": "general", "Knee replacement": "replacement"}.get(surgery, "unspecified")
    rn.knee_rules.save(con, pid, rn.knee_rules.validate({"pathway": path}))
    con.execute("UPDATE patients SET goal=? WHERE patient_id=?", ("", pid))
    con.commit()
    return {"patient_id": pid, "status": "intake_sent", "surgery": surgery}


def save_consent(con, pid, items):
    _patient(con, pid)
    con.execute("UPDATE patients SET consent_json=?, consent_at=? WHERE patient_id=?", (json.dumps(items), _now(), pid))
    con.commit()
    return {"ok": True, "consent_at": _now()}


def setup_check(con, pid, safe, details=""):
    """Safety check at setup. 'Not safe' (e.g. no clear space, no sturdy chair) -> urgent alert for the PT."""
    _patient(con, pid)
    if not safe:
        rn.add_alert(con, pid, dt.date.today().isoformat(), "urgent",
                     f"Setup safety check: not safe{': ' + details if details else ''}", "setup")
        con.commit()
    return {"ok": True, "urgent": not safe}


# ---------- visits, notes, calls, discharge ----------

def schedule_visit(con, pid, date, time, place=None, reason="", kind="follow_up"):
    _patient(con, pid)
    dt.date.fromisoformat(date)
    cur = con.execute("INSERT INTO visits (patient_id, kind, status, date, time, place, reason, updated_at)"
                      " VALUES (?,?, 'scheduled', ?,?,?,?,?)",
                      (pid, kind, date, time, place or CLINIC["name"], reason, _now()))
    con.commit()
    return {"visit_id": cur.lastrowid}


def next_visit(con, pid, today=None):
    today = (today or dt.date.today()).isoformat()
    r = con.execute("SELECT * FROM visits WHERE patient_id=? AND status='scheduled' AND date>=? ORDER BY date, time LIMIT 1",
                    (pid, today)).fetchone()
    return dict(r) if r else None


FINDING_FIELDS = {
    # movement
    "bend_deg": float, "short_of_straight_deg": float, "rom_mode": str,          # active / passive
    # strength
    "quad_lag": bool, "quad_grade": str, "pain_today": int,
    # function
    "tug_sec": float, "chair_stand_30s": int, "walking_aid": str, "stairs": str,
    # knee
    "swelling_cm": dict, "incision": str,                                         # {"left": 38.5, "right": 41}
    # shown to the patient
    "goals_week6": list, "precautions": list,
    # PT only
    "notes": str, "done_checklist": list,
}


def save_findings(con, pid, fields, visit_id=None):
    """Autosave clinic-visit findings as the PT types. Creates today's visit record if there is none."""
    _patient(con, pid)
    fields = dict(fields)
    assessment = rn.knee_rules.validate(fields.pop("knee_assessment")) if "knee_assessment" in fields else None
    clean = {}
    for k, v in fields.items():
        if k not in FINDING_FIELDS:
            raise ValueError(f"Unknown field '{k}'. Allowed: {', '.join(FINDING_FIELDS)}")
        if v is None or v == "":
            clean[k] = None
            continue
        typ = FINDING_FIELDS[k]
        clean[k] = (v in (True, "yes", "Yes", "true", 1)) if typ is bool else typ(v)
    if visit_id is None:
        r = con.execute("SELECT visit_id FROM visits WHERE patient_id=? AND date=? ORDER BY visit_id DESC LIMIT 1",
                        (pid, dt.date.today().isoformat())).fetchone()
        if r is None:
            kind = "first" if not rn.current_plan(con, pid) else "follow_up"
            visit_id = con.execute("INSERT INTO visits (patient_id, kind, status, date, time, place, updated_at)"
                                   " VALUES (?,?, 'done', ?, ?, ?, ?)",
                                   (pid, kind, dt.date.today().isoformat(), dt.datetime.now().strftime("%H:%M"),
                                    CLINIC["name"], _now())).lastrowid
        else:
            visit_id = r["visit_id"]
    if assessment is not None:
        rn.knee_rules.save(con, pid, assessment)
    old = con.execute("SELECT findings_json FROM visits WHERE visit_id=?", (visit_id,)).fetchone()
    merged = {**json.loads((old["findings_json"] if old else None) or "{}"), **clean}
    if (clean.get("pain_today") is not None and clean["pain_today"] >= rn.STOP_PAIN
            and rn.knee_rules.assessment(con, pid)["pathway"] == "general" and not rn.knee_rules.urgent_alert(con, pid)):
        rn.add_alert(con, pid, dt.date.today().isoformat(), "urgent", "High pain reported at the knee assessment.", "knee_screen")
    con.execute("UPDATE visits SET findings_json=?, status='done', updated_at=? WHERE visit_id=?",
                (json.dumps(merged), _now(), visit_id))
    con.commit()
    return {"visit_id": visit_id, "findings": merged}


def latest_findings(con, pid):
    r = con.execute("SELECT * FROM visits WHERE patient_id=? AND findings_json IS NOT NULL ORDER BY date DESC, visit_id DESC LIMIT 1",
                    (pid,)).fetchone()
    return {"visit_id": r["visit_id"], "date": r["date"], "findings": json.loads(r["findings_json"])} if r else None


def set_private_notes(con, pid, text):
    _patient(con, pid)
    con.execute("UPDATE patients SET pt_private_notes=? WHERE patient_id=?", (text, pid))
    con.commit()
    return {"ok": True}


def log_call(con, pid, minutes, agreed="", date=None):
    """Logging a call closes the patient's open alerts and counts as the month's live contact (RTM)."""
    _patient(con, pid)
    date = date or dt.date.today().isoformat()
    cur = con.execute("INSERT INTO calls (patient_id, date, minutes, agreed) VALUES (?,?,?,?)",
                      (pid, date, int(minutes), agreed))
    con.execute("UPDATE alerts SET status='closed' WHERE patient_id=? AND COALESCE(status,'open')='open'", (pid,))
    con.commit()
    return {"call_id": cur.lastrowid}


def schedule(con, start, end, today=None):
    """PT Schedule screen: visits between start and end (inclusive), calls to make, and call history."""
    today = today or dt.date.today()
    events = []
    for r in con.execute("SELECT v.visit_id, v.patient_id, v.kind, v.status, v.date, v.time, v.place, v.reason, p.name, p.phone"
                         " FROM visits v JOIN patients p USING(patient_id) WHERE v.date BETWEEN ? AND ? AND v.time IS NOT NULL"
                         " ORDER BY v.date, v.time", (start, end)):
        e = dict(r)
        alert = con.execute("SELECT reason FROM alerts WHERE patient_id=? AND COALESCE(status,'open')='open' ORDER BY date DESC LIMIT 1",
                            (e["patient_id"],)).fetchone()
        e["type"] = "Phone" if e["kind"] == "phone" else "In clinic"
        e["k"] = "new" if e["kind"] == "first" else "call" if e["kind"] == "phone" else "urg" if alert else "clin"
        e["minutes"] = 60 if e["kind"] == "first" else 45
        last = con.execute("SELECT date, pain_today, pain_end FROM checkins WHERE patient_id=? AND action!='rest' ORDER BY date DESC LIMIT 1",
                           (e["patient_id"],)).fetchone()
        prep = []
        if alert:
            prep.append(f"Open alert: {alert['reason']}.")
        if last:
            ld = dt.date.fromisoformat(last["date"])
            prep.append(f"Last session {ld.strftime('%a')}, {ld.strftime('%b')} {ld.day}: pain {last['pain_today']} before, {last['pain_end'] if last['pain_end'] is not None else '–'} after.")
        if e["kind"] == "first":
            prep.append("Intake ready." if rn.latest_intake(con, e["patient_id"]) else "No intake yet.")
        e["prep"] = " ".join(prep) or "No sessions yet."
        events.append(e)
    month_start = today.replace(day=1).isoformat()
    month_end = ((today.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1))
    to_call = []
    for p in con.execute("SELECT patient_id, name, phone, COALESCE(status,'active') AS status FROM patients ORDER BY name"):
        pid = p["patient_id"]
        alerts = con.execute("SELECT tier, reason FROM alerts WHERE patient_id=? AND COALESCE(status,'open')='open' ORDER BY tier='urgent' DESC, date DESC",
                             (pid,)).fetchall()
        if alerts:
            to_call.append({"patient_id": pid, "name": p["name"], "phone": p["phone"], "why": f"Alert follow-up: {alerts[0]['reason']}",
                            "due": "Today", "k": "urg" if alerts[0]["tier"] == "urgent" else "rev"})
        elif p["status"] == "active" and rn.current_plan(con, pid) and not con.execute(
                "SELECT 1 FROM calls WHERE patient_id=? AND date>=?", (pid, month_start)).fetchone():
            to_call.append({"patient_id": pid, "name": p["name"], "phone": p["phone"], "why": "Monthly call (remote monitoring)",
                            "due": f"By {month_end.strftime('%b')} {month_end.day}", "k": "mut"})
    to_call.sort(key=lambda c: {"urg": 0, "rev": 1}.get(c["k"], 2))
    history = [dict(r) for r in con.execute("SELECT c.call_id, c.patient_id, c.date, c.minutes, c.agreed, p.name FROM calls c"
                                            " JOIN patients p USING(patient_id) ORDER BY c.date DESC, c.call_id DESC LIMIT 30")]
    return {"start": start, "end": end, "events": events, "calls_to_make": to_call, "call_history": history}


def data_days(con, pid, today=None, days=30):
    """RTM: days with data in the last 30 days (a check-in counts as a data day)."""
    today = today or dt.date.today()
    start = (today - dt.timedelta(days=days - 1)).isoformat()
    return con.execute("SELECT COUNT(DISTINCT date) FROM checkins WHERE patient_id=? AND date BETWEEN ? AND ?",
                       (pid, start, today.isoformat())).fetchone()[0]


def discharge(con, pid, note="", checklist=None, community_program=False, final_measures=None):
    p = _patient(con, pid)
    first = con.execute("SELECT findings_json FROM visits WHERE patient_id=? AND findings_json IS NOT NULL ORDER BY date, visit_id LIMIT 1",
                        (pid,)).fetchone()
    last = latest_findings(con, pid)
    sessions = con.execute("SELECT COUNT(*) FROM checkins WHERE patient_id=? AND action!='rest'", (pid,)).fetchone()[0]
    summary = {"start": json.loads(first["findings_json"]) if first else {},
               "final": final_measures or (last["findings"] if last else {}),
               "goals_week6": (last["findings"].get("goals_week6") if last else None) or [],
               "sessions_done": sessions, "checklist": checklist or [], "community_program": bool(community_program),
               "weeks": _week_post_op(p, dt.date.today())}
    con.execute("INSERT INTO discharges (patient_id, date, summary_json, note) VALUES (?,?,?,?)"
                " ON CONFLICT(patient_id) DO UPDATE SET date=excluded.date, summary_json=excluded.summary_json, note=excluded.note",
                (pid, dt.date.today().isoformat(), json.dumps(summary), note))
    con.execute("UPDATE patients SET status='discharged' WHERE patient_id=?", (pid,))
    con.commit()
    return discharge_summary(con, pid)


def discharge_summary(con, pid):
    r = con.execute("SELECT * FROM discharges WHERE patient_id=?", (pid,)).fetchone()
    return {"date": r["date"], "note": r["note"], **json.loads(r["summary_json"])} if r else None


# ---------- messages between the patient and the PT ----------

def send_message(con, pid, sender, text, kind="text", at=None):
    _patient(con, pid)
    if sender not in ("patient", "pt"):
        raise ValueError("sender must be 'patient' or 'pt'")
    text = (text or "").strip()
    if not text:
        raise ValueError("Message is empty.")
    if kind not in ("text", "voice", "photo", "session"):
        raise ValueError("kind must be text, voice, photo or session")
    cur = con.execute("INSERT INTO messages (patient_id, sender, kind, text, created_at) VALUES (?,?,?,?,?)",
                      (pid, sender, kind, text[:2000], at or _now()))
    con.commit()
    return {"msg_id": cur.lastrowid}


def messages(con, pid, reader=None):
    """The conversation, oldest first. reader ('patient' or 'pt') marks the other side's messages as read."""
    _patient(con, pid)
    if reader in ("patient", "pt"):
        con.execute("UPDATE messages SET read_at=? WHERE patient_id=? AND sender!=? AND read_at IS NULL", (_now(), pid, reader))
        con.commit()
    return [dict(r) for r in con.execute("SELECT * FROM messages WHERE patient_id=? ORDER BY created_at, msg_id", (pid,))]


def threads(con):
    """One row per patient who has messages, newest first, with unread counts for the PT."""
    rows = con.execute("SELECT m.patient_id, p.name, MAX(m.created_at) AS last_at,"
                       " SUM(CASE WHEN m.sender='patient' AND m.read_at IS NULL THEN 1 ELSE 0 END) AS unread"
                       " FROM messages m JOIN patients p USING(patient_id) GROUP BY m.patient_id ORDER BY last_at DESC").fetchall()
    out = []
    for r in rows:
        last = con.execute("SELECT sender, kind, text FROM messages WHERE patient_id=? ORDER BY created_at DESC, msg_id DESC LIMIT 1",
                           (r["patient_id"],)).fetchone()
        out.append({**dict(r), "last": dict(last)})
    return out


def unread_for_patient(con, pid):
    return con.execute("SELECT COUNT(*) FROM messages WHERE patient_id=? AND sender='pt' AND read_at IS NULL", (pid,)).fetchone()[0]


# ---------- patient app: sessions, streak, progress, home ----------

def _sessions(con, pid):
    return [dict(r) for r in con.execute("SELECT * FROM checkins WHERE patient_id=? ORDER BY date", (pid,))]


def streak(con, pid, today=None):
    """Days in a row with a session. Rest days never break the streak; today doesn't break it until it's over.
    Stopping early still counts."""
    today = today or dt.date.today()
    rest = set(rn.rest_days(con, pid))
    done = {s["date"] for s in _sessions(con, pid) if s["action"] != "rest"}
    first_plan = con.execute("SELECT MIN(start_date) FROM plan_items WHERE patient_id=?", (pid,)).fetchone()[0]
    if not first_plan:
        return 0
    start = dt.date.fromisoformat(first_plan)
    n, d = 0, today
    while d >= start:
        iso, wd = d.isoformat(), rn.WEEKDAYS[d.weekday()]
        if iso in done:
            n += 1
        elif wd in rest or d == today:
            pass
        else:
            break
        d -= dt.timedelta(days=1)
    return n


def medals(n):
    return [{"key": k, "label": l, "days": d, "earned": n >= d, "to_go": max(0, d - n)} for k, l, d in MEDALS]


def week_days(con, pid, today=None, offset=0):
    """Mon-Sun of this week (offset -1 = last week) with a status per day, for the plan and progress screens."""
    today = today or dt.date.today()
    monday = today - dt.timedelta(days=today.weekday()) + dt.timedelta(weeks=offset)
    rest = set(rn.rest_days(con, pid))
    by_date = {s["date"]: s for s in _sessions(con, pid)}
    out = []
    for i in range(7):
        d = monday + dt.timedelta(days=i)
        s = by_date.get(d.isoformat())
        wd = rn.WEEKDAYS[i]
        if s and s["action"] != "rest":
            reps = con.execute("SELECT COALESCE(SUM(reps_done),0) FROM exercise_logs WHERE checkin_id=?", (s["checkin_id"],)).fetchone()[0]
            status = "partial" if s["stopped_early"] else "done"
            out.append({"day": wd, "date": d.isoformat(), "status": status, "reps": reps,
                        "pain_before": s["pain_today"], "pain_after": s["pain_end"]})
            continue
        if wd in rest:
            status = "rest"
        elif d == today:
            status = "today"
        elif d > today:
            status = "open"
        else:
            status = "missed" if not (s and s["action"] == "rest") else "rested_for_pain"
        out.append({"day": wd, "date": d.isoformat(), "status": status})
    return out


def pain_series(con, pid):
    """Weekly average pain after sessions (end-of-session pain; exercise pain if none), oldest first."""
    rows = _sessions(con, pid)
    weeks = {}
    for s in rows:
        d = dt.date.fromisoformat(s["date"])
        key = (d - dt.timedelta(days=d.weekday())).isoformat()
        val = s["pain_end"]
        if val is None:
            v = con.execute("SELECT AVG(pain_after) FROM exercise_logs WHERE checkin_id=? AND pain_after IS NOT NULL",
                            (s["checkin_id"],)).fetchone()[0]
            val = v
        if val is not None:
            weeks.setdefault(key, []).append(val)
    return [{"week_start": k, "pain_after": round(mean(v), 1)} for k, v in sorted(weeks.items())]


def progress(con, pid, today=None):
    n = streak(con, pid, today)
    return {"streak": n, "medals": medals(n), "this_week": week_days(con, pid, today),
            "last_week": week_days(con, pid, today, -1), "pain_by_week": pain_series(con, pid),
            "sessions_done": sum(1 for s in _sessions(con, pid) if s["action"] != "rest")}


def home(con, pid, today=None):
    """What the patient's Home screen shows, as one phase plus details."""
    today = today or dt.date.today()
    p = _patient(con, pid)
    plan = rn.current_plan(con, pid)
    intake = rn.latest_intake(con, pid)
    change = rn.plan_change_for_patient(con, pid)
    if p["status"] == "discharged":
        phase = "discharged"
    elif not intake:
        phase = "intake"
    elif not plan:
        phase = "waiting_for_visit"
    elif change and change["approved_at"] and change["approved_at"][:10] >= (today - dt.timedelta(days=7)).isoformat():
        phase = "plan_updated"
    else:
        phase = "active"
    done_today = con.execute("SELECT 1 FROM checkins WHERE patient_id=? AND date=? AND action!='rest'",
                             (pid, today.isoformat())).fetchone() is not None
    rest_today = rn.WEEKDAYS[today.weekday()] in rn.rest_days(con, pid)
    lf = latest_findings(con, pid)
    f = (lf or {}).get("findings") or {}
    visits = [dict(r) for r in con.execute(
        "SELECT visit_id, kind, date, time, place, reason FROM visits WHERE patient_id=? AND status='scheduled' AND date>=?"
        " ORDER BY date, time", (pid, today.isoformat()))]
    first = con.execute("SELECT visit_id, kind, status, date, time, place, reason FROM visits WHERE patient_id=? AND kind='first'"
                        " ORDER BY date DESC LIMIT 1", (pid,)).fetchone()
    return {"care_pathway": rn.knee_rules.assessment(con, pid)["pathway"],
            "patient_id": pid, "name": p["name"], "first_name": (p["name"] or "").split(" ")[0], "phase": phase,
            "visits": visits, "first_visit": dict(first) if first else None,
            "goals": f.get("goals_week6") or [], "precautions": f.get("precautions") or [],
            "intake_date": intake["created_at"][:10] if intake and intake.get("created_at") else None,
            "clinic": CLINIC, "next_visit": next_visit(con, pid, today), "plan": plan, "plan_change": change,
            "rest_days": rn.rest_days(con, pid), "rest_today": rest_today, "done_today": done_today,
            "streak": streak(con, pid, today), "week_post_op": _week_post_op(p, today),
            "unread_messages": unread_for_patient(con, pid),
            "discharge": discharge_summary(con, pid) if phase == "discharged" else None}


# ---------- PT app: patient list and profile ----------

def _pain_after_week(con, pid, monday):
    end = monday + dt.timedelta(days=6)
    rows = con.execute("SELECT checkin_id, pain_end FROM checkins WHERE patient_id=? AND date BETWEEN ? AND ? AND action!='rest'",
                       (pid, monday.isoformat(), end.isoformat())).fetchall()
    vals = []
    for r in rows:
        if r["pain_end"] is not None:
            vals.append(r["pain_end"])
        else:
            v = con.execute("SELECT AVG(pain_after) FROM exercise_logs WHERE checkin_id=? AND pain_after IS NOT NULL",
                            (r["checkin_id"],)).fetchone()[0]
            if v is not None:
                vals.append(v)
    return round(mean(vals), 1) if vals else None


def _nice(date, time=None):
    d = dt.date.fromisoformat(date)
    t = ""
    if time:
        h, m = (int(x) for x in time.split(":")[:2])
        t = f", {h % 12 or 12}:{m:02d} {'am' if h < 12 else 'pm'}"
    return f"{d.strftime('%a')}, {d.strftime('%b')} {d.day}{t}"


def patient_row(con, p, today):
    pid = p["patient_id"]
    plan = rn.current_plan(con, pid)
    intake = rn.latest_intake(con, pid)
    last = con.execute("SELECT MAX(date) FROM checkins WHERE patient_id=? AND action!='rest'", (pid,)).fetchone()[0]
    urgent = con.execute("SELECT reason FROM alerts WHERE patient_id=? AND tier='urgent' AND COALESCE(status,'open')='open'"
                         " ORDER BY alert_id DESC LIMIT 1", (pid,)).fetchone()
    pending = con.execute("SELECT request_id, kind FROM adjustment_requests WHERE patient_id=? AND status='pending'"
                          " ORDER BY request_id DESC LIMIT 1", (pid,)).fetchone()
    decided_today = con.execute("SELECT 1 FROM adjustment_requests WHERE patient_id=? AND status IN ('approved','rejected')"
                                " AND substr(decided_at,1,10)=?", (pid, today.isoformat())).fetchone()
    days_since = (today - dt.date.fromisoformat(last)).days if last else None
    monday = today - dt.timedelta(days=today.weekday())
    nv = next_visit(con, pid, today)
    pathway = rn.knee_rules.assessment(con, pid)["pathway"]
    wk = _week_post_op(p, today) if pathway == "replacement" else None

    if p["status"] == "discharged":
        group, nxt = "discharged", "Discharged"
    elif urgent:
        group, nxt = "urgent", urgent["reason"]
    elif pending:
        group, nxt = "ready_for_review", "First plan draft ready" if pending["kind"] == "first" else "Weekly plan draft ready"
    elif decided_today:
        group, nxt = "done_today", "Plan sent today"
    elif not plan:
        group = "new"
        nxt = ("Intake link sent" if not intake else "Intake ready")
        if nv:
            nxt += f"; visit {_nice(nv['date'], nv['time'])}"
    elif days_since is None or days_since >= GONE_QUIET_DAYS:
        group, nxt = "gone_quiet", f"No session for {days_since} days" if days_since is not None else "No sessions yet"
    else:
        group = "on_track"
        nxt = f"Visit {_nice(nv['date'], nv['time'])}" if nv else (f"Week {wk + 1} starts Mon" if wk is not None else "")
    return {"patient_id": pid, "name": p["name"], "age": _age(p["dob"], today),
            "surgery": (f"{(p['operated_side'] or '').capitalize()} knee replacement".strip() if pathway == "replacement"
                        else "General knee" if pathway == "general" else "Knee assessment pending"),
            "surgery_date": p["surgery_date"], "week_post_op": wk, "group": group, "next": nxt,
            "last_session": last, "days_since_session": days_since,
            "pain_after_this_week": _pain_after_week(con, pid, monday),
            "pain_after_last_week": _pain_after_week(con, pid, monday - dt.timedelta(days=7)),
            "data_days_30": data_days(con, pid, today), "pending_request_id": pending["request_id"] if pending else None,
            "unread_messages": con.execute("SELECT COUNT(*) FROM messages WHERE patient_id=? AND sender='patient' AND read_at IS NULL",
                                           (pid,)).fetchone()[0]}


GROUP_ORDER = ["urgent", "ready_for_review", "gone_quiet", "done_today", "new", "on_track", "discharged"]


def auto_weekly_drafts(con, today=None, use_llm=True):
    """Each new week, draft next week's plan for every active patient who exercised last week (once per week)."""
    today = today or dt.date.today()
    last_sunday = (today - dt.timedelta(days=today.weekday() + 1)).isoformat()
    start = (dt.date.fromisoformat(last_sunday) - dt.timedelta(days=6)).isoformat()
    made = []
    for p in con.execute("SELECT patient_id FROM patients WHERE COALESCE(status,'active')='active'").fetchall():
        pid = p["patient_id"]
        if not rn.current_plan(con, pid):
            continue
        if con.execute("SELECT 1 FROM adjustment_requests WHERE patient_id=? AND COALESCE(kind,'weekly')='weekly'"
                       " AND week_end>=?", (pid, last_sunday)).fetchone():
            continue
        if not con.execute("SELECT 1 FROM checkins WHERE patient_id=? AND date BETWEEN ? AND ?",
                           (pid, start, last_sunday)).fetchone():
            continue
        if con.execute("SELECT 1 FROM adjustment_requests WHERE patient_id=? AND status='pending'", (pid,)).fetchone():
            continue
        last = con.execute("SELECT MAX(date) FROM checkins WHERE patient_id=? AND action!='rest'", (pid,)).fetchone()[0]
        if last and (today - dt.date.fromisoformat(last)).days >= GONE_QUIET_DAYS:
            continue   # gone quiet: the PT calls first, no new plan
        rn.create_adjustment_request(con, pid, last_sunday, use_llm=use_llm)
        made.append(pid)
    return made


def patient_list(con, today=None):
    today = today or dt.date.today()
    auto_weekly_drafts(con, today)
    rows = [patient_row(con, p, today) for p in con.execute("SELECT * FROM patients ORDER BY name")]
    rows.sort(key=lambda r: (GROUP_ORDER.index(r["group"]), r["name"] or ""))
    counts = {g: sum(r["group"] == g for r in rows) for g in GROUP_ORDER}
    return {"patients": rows, "counts": counts,
            "rtm": {"review_minutes_month": 42, "calls_this_month": 3}}  # fixed numbers for the demo (see spec)


def profile(con, pid, today=None):
    today = today or dt.date.today()
    p = _patient(con, pid)
    sessions = []
    for s in reversed(_sessions(con, pid)):
        logs = [dict(r) for r in con.execute("SELECT exercise_id, sets_done, reps_done, feeling, pain_after, form_score, note"
                                             " FROM exercise_logs WHERE checkin_id=?", (s["checkin_id"],))]
        sessions.append({"date": s["date"], "action": s["action"], "pain_before": s["pain_today"], "pain_end": s["pain_end"],
                         "worry": json.loads(s["worry_json"]) if s["worry_json"] else None,
                         "bothered": json.loads(s["bothered_json"] or "[]"), "note": s["end_note"],
                         "stopped_early": bool(s["stopped_early"]), "stop_reason": s["stop_reason"], "exercises": logs})
    history = [dict(r) for r in con.execute(
        "SELECT start_date, end_date, approved_by, GROUP_CONCAT(exercise_name || ' ' || sets || 'x' || reps, ', ') AS plan"
        " FROM plan_items WHERE patient_id=? GROUP BY start_date ORDER BY start_date DESC", (pid,))]
    return {"knee_assessment": rn.knee_rules.assessment(con, pid), "knee_form": rn.knee_rules.form_options(),
            "patient": {**patient_row(con, p, today), "dob": p["dob"], "phone": p["phone"], "goal": p["goal"],
                        "rest_days": rn.rest_days(con, pid)},
            "intake": rn.latest_intake(con, pid), "plan": rn.current_plan(con, pid), "plan_history": history,
            "sessions": sessions, "pain_by_week": pain_series(con, pid),
            "visits": [dict(r) for r in con.execute("SELECT visit_id, kind, status, date, time, place, reason FROM visits"
                                                     " WHERE patient_id=? ORDER BY date DESC", (pid,))],
            "findings": latest_findings(con, pid),
            "calls": [dict(r) for r in con.execute("SELECT * FROM calls WHERE patient_id=? ORDER BY date DESC", (pid,))],
            "alerts": [dict(r) for r in con.execute("SELECT * FROM alerts WHERE patient_id=? ORDER BY alert_id DESC LIMIT 20", (pid,))],
            "pt_private_notes": p["pt_private_notes"] or "", "request": rn.latest_request(con, pid)}


# ---------- first plan draft (AI step 2) ----------

# Clinic knee-replacement protocol: which exercises are allowed from which week, and the starting dose.
PROTOCOL = {
    "heel_slide":               {"from_week": 0, "start": (3, 10)},
    "straight_leg_raise":       {"from_week": 0, "start": (3, 10)},
    "seated_knee_extension":    {"from_week": 0, "start": (3, 10)},
    "sitting_knee_bend":        {"from_week": 0, "start": (3, 10)},
    "glute_bridge":             {"from_week": 1, "start": (2, 10)},
    "sit_to_stand":             {"from_week": 2, "start": (3, 8)},
    "standing_knee_bend":       {"from_week": 2, "start": (3, 10)},
    "side_lying_hip_abduction": {"from_week": 3, "start": (2, 10)},
    "mini_squat":               {"from_week": 4, "start": (2, 8)},
    "step_up":                  {"from_week": 6, "start": (2, 8)},
}
MAX_FIRST_PLAN = 5


def allowed_exercises(week):
    return {k: v for k, v in PROTOCOL.items() if (week or 0) >= v["from_week"]}


def first_plan_rules(intake, findings, week):
    """Rule-based first plan: each exercise is chosen because of a visit finding, within the protocol."""
    f = findings or {}
    allowed = allowed_exercises(week)
    picks = []

    def add(eid, reason, dose=None):
        if eid in allowed and eid not in [p[0] for p in picks]:
            picks.append((eid, dose or allowed[eid]["start"], reason))

    bend, short = f.get("bend_deg"), f.get("short_of_straight_deg")
    if bend is not None and bend < 110:
        add("heel_slide", f"Bend is {bend:.0f}°; heel slides build bend toward 110°.")
        if bend < 90:
            add("sitting_knee_bend", f"Bend is under 90° ({bend:.0f}°), so a second bending exercise.")
    if f.get("quad_lag"):
        add("straight_leg_raise", "Quad lag at the visit: straight leg raises rebuild quad control.")
    if short is not None and short > 3:
        add("seated_knee_extension", f"Knee is {short:.0f}° short of straight; extensions work on straightening.")
    tug, cs = f.get("tug_sec"), f.get("chair_stand_30s")
    if (tug is not None and tug > 12) or (cs is not None and cs < 10):
        why = " and ".join(x for x in [f"Timed Up and Go {tug:.0f} s" if tug is not None and tug > 12 else "",
                                         f"{cs} chair stands in 30 s" if cs is not None and cs < 10 else ""] if x)
        add("sit_to_stand", f"{why}: sit-to-stand builds the strength for getting up.")
    if str(f.get("stairs", "")).lower() not in ("", "independent", "no trouble"):
        add("glute_bridge", "Stairs are hard: glute bridges build hip strength for stepping up.")
    for eid, why in [("heel_slide", "Core exercise of the knee replacement protocol."),
                     ("straight_leg_raise", "Core exercise of the knee replacement protocol."),
                     ("glute_bridge", "Builds hip strength for walking."),
                     ("sit_to_stand", "Practises getting up from a chair.")]:
        if len(picks) >= 4:
            break
        add(eid, why)

    pain = f.get("pain_today")
    swelling = f.get("swelling_cm") or {}
    swollen = len(swelling) == 2 and abs(float(swelling.get("left", 0)) - float(swelling.get("right", 0))) >= 2
    items = []
    for eid, (s, r), why in picks[:MAX_FIRST_PLAN]:
        if pain is not None and pain >= 6:
            r, why = min(r, 8), why + f" Fewer reps to start because pain was {pain}/10."
        if swollen and eid in ("sit_to_stand", "mini_squat", "step_up"):
            s, why = min(s, 2), why + " Lighter while the knee is still swollen."
        items.append({"exercise_id": eid, "exercise_name": rn.EXERCISES[eid], "action": "add", "before": None,
                      "sets": s, "reps": r, "reason": why})
    goal = (intake or {}).get("answers", {}).get("goal") if intake else None
    return {"source": "rules", "kind": "first", "goal": goal if goal in rn.GOALS else rn.DEFAULT_GOAL,
            "goal_label": rn.GOALS.get(goal, rn.GOALS[rn.DEFAULT_GOAL])["label"], "direction": "new",
            "explanation": "Your first home plan, based on your visit today.",
            "items": items, "note_for_pt": "" if items else "No findings yet: fill in the visit to get a draft."}


FIRST_PLAN_PROMPT = """You draft a knee-replacement patient's FIRST home-exercise plan for a physical therapist (PT).
The PT accepts, edits or rejects each exercise; you only propose.
Use ONLY exercises allowed by the clinic protocol below for this week after surgery, at or below the starting dose
(sets x reps). Choose 3 to 5 exercises. Each needs a one-line reason tied to a visit finding
(for example: quad lag keeps straight leg raises). Be cautious with pain 6+ or swelling.
Return JSON: {"items":[{"exercise_id","sets","reps","reason"}],"note_for_pt":"..."}

Week after surgery: {week}
Protocol (allowed now): {protocol}
Visit findings: {findings}
Intake summary: {intake}
Rule-based draft (a starting point): {rules}
"""


def llm_first_plan(intake, findings, week, rules, model=None):
    allowed = {k: f"{v['start'][0]} x {v['start'][1]}" for k, v in allowed_exercises(week).items()}
    prompt = (FIRST_PLAN_PROMPT.replace("{week}", str(week)).replace("{protocol}", json.dumps(allowed))
              .replace("{findings}", json.dumps(findings)).replace("{intake}", (intake or {}).get("summary", ""))
              .replace("{rules}", json.dumps(rules["items"])))
    draft = rn.llm_json(prompt, model)
    if draft is None:
        return None
    out = validate_first_plan(draft, week, rules)
    if out is rules:
        rn.LLM_STATE["last_error"] = "the AI draft did not pass the code check"
        print("[LLM] first-plan draft did not pass the code check -> using the rules draft")
        return None
    return out


def validate_first_plan(draft, week, fallback):
    """Code check: protocol exercises only, dose at or below the protocol start, 1-5 exercises, a reason each."""
    allowed = allowed_exercises(week)
    items = draft.get("items") if isinstance(draft, dict) else None
    if not items or not 1 <= len(items) <= MAX_FIRST_PLAN:
        return fallback
    out = []
    for it in items:
        eid = it.get("exercise_id")
        if eid not in allowed or not it.get("reason"):
            return fallback
        s, r = int(it.get("sets", 0)), int(it.get("reps", 0))
        ms, mr = allowed[eid]["start"]
        if not (1 <= s <= ms and 1 <= r <= mr):
            return fallback
        out.append({"exercise_id": eid, "exercise_name": rn.EXERCISES[eid], "action": "add", "before": None,
                    "sets": s, "reps": r, "reason": str(it["reason"])[:200]})
    return {**fallback, "source": "llm", "items": out, "note_for_pt": draft.get("note_for_pt", "")}


def create_first_plan_request(con, pid, use_llm=True):
    p = _patient(con, pid)
    week = _week_post_op(p, dt.date.today()) or 0
    intake = rn.latest_intake(con, pid)
    f = latest_findings(con, pid)
    findings = f["findings"] if f else {}
    assessment = rn.knee_rules.assessment(con, pid)
    if assessment["pathway"] == "general" and rn.current_plan(con, pid):
        review = rn.create_adjustment_request(con, pid, use_llm=use_llm)
        return {**review, "week_post_op": None}
    if assessment["pathway"] == "replacement":
        rules = first_plan_rules(intake, findings, week)
        draft = (llm_first_plan(intake, findings, week, rules) if use_llm else None) or dict(rules, ai_note=rn.ai_note(use_llm))
    else:
        week = None
        draft = rn.knee_rules.draft(con, pid, assessment, p["goal"], use_llm=use_llm, pain_today=findings.get("pain_today"))
    con.execute("UPDATE adjustment_requests SET status='superseded' WHERE patient_id=? AND status='pending' AND kind='first'", (pid,))
    summary = {"patient_id": pid, "kind": "first", "week_post_op": week, "findings": findings, "exercises": []}
    if assessment["pathway"] != "replacement":
        summary["knee_assessment"] = assessment
    cur = con.execute("INSERT INTO adjustment_requests (patient_id, week_end, summary_json, draft_json, draft_source, status, kind)"
                      " VALUES (?,?,?,?,?, 'pending', 'first')",
                      (pid, dt.date.today().isoformat(), json.dumps(summary), json.dumps(draft), draft["source"]))
    con.commit()
    return {"request_id": cur.lastrowid, "draft": draft, "week_post_op": week}
