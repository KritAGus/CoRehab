"""CoRehab general-knee recommendation rules for PT review.

Install: copy this file into backend/ and run python3 backend/knee_rules.py --connect
from the project folder. Restart the existing CoRehab backend. The connector checks each affected section before applying the integration.
It requires only the Python standard library.

In the live PT app choose Clinic visit > Exercise recommendation > General knee.
Select the symptom pattern, clearance, loading permission and examined findings.
The PT enters shared sets and reps for new exercises. These values also cap
weekly increases. Use a separate plan for exercises needing
different limits or a modified camera setup. The labels below describe relative
demand of the standard, unweighted app movement. They are design labels for
clinical review, not validated patient-specific intensity or diagnostic scores.
"""
import json

SOURCES = {
    "pfp": "https://bjsm.bmj.com/content/58/24/1486",
    "tendon": "https://pmc.ncbi.nlm.nih.gov/articles/PMC11792096/",
    "oa": "https://www.nice.org.uk/guidance/ng226/chapter/Recommendations",
    "meniscus": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12163282/",
    "triage": "https://www.nhs.uk/symptoms/knee-pain/",
    "calf": "https://www.nhs.uk/conditions/deep-vein-thrombosis-dvt/",
    "dorset": "https://www.mskdorset.nhs.uk/knee-pain/knee-pain-early-management/",
    "aaos": "https://www.orthoinfo.org/bodypart/kneelower-leg/knee-conditioning-program/",
    "hip": "https://pubmed.ncbi.nlm.nih.gov/19574661/",
    "bridge": "https://msk-bexley.nhs.uk/conditions/hip-pain/gluteal-tendinopathy",
    "step": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10315869/",
}

# Symptom patterns guide ranking among examined, cleared movements only.
# Each entry contains the report, an anatomical context, a source and priorities.
PATTERNS = {
    "other": ("Other or unclear", "Use examined movement limitations.", "triage", ()),
    "kneecap": ("Around the kneecap on stairs or squats", "Patellofemoral region. Assess tolerance to loaded bending.", "pfp",
                ("seated_knee_extension", "mini_squat", "side_lying_hip_abduction")),
    "below_patella": ("Focal pain below the kneecap with jumping", "Patellar tendon region. Confirm the tendon-loading stage before selecting exercise.", "tendon",
                      ("seated_knee_extension", "mini_squat")),
    "stiffness": ("Gradual activity-related ache and brief morning stiffness", "Knee joint. In confirmed osteoarthritis, match mobility and strength work to function.", "oa",
                  ("heel_slide", "sitting_knee_bend", "sit_to_stand")),
    "joint_line": ("Inner or outer joint-line pain after a twist", "Meniscal or ligament structures may be involved. Check stability and restrictions.", "meniscus",
                   ("heel_slide", "sitting_knee_bend", "seated_knee_extension")),
    "posterior": ("Back of knee or calf", "Assess the cause before loading. New one-sided calf pain with swelling needs urgent assessment.", "calf", ()),
}

FINDINGS = {
    "flexion": "Limited knee bend; supported sliding is tolerated",
    "quad_control": "Reduced thigh control; can hold knee straight through the leg lift",
    "extension": "Reduced knee-extensor strength; full active seated extension is comfortable",
    "hamstrings": "Reduced knee-flexor strength; supported standing curl is safe",
    "hip_extension": "Reduced hip-extension strength; two-foot bridge is tolerated",
    "hip_abduction": "Reduced hip-abductor strength; straight-leg side-lying lift is tolerated",
    "chair": "Chair rises are difficult; standard arm-free transfer is safe",
    "squat": "Reduced control of loaded bending; shallow supported squat is tolerated",
    "stairs": "Stair ascent is difficult; low step with rail is safe",
}

# finding, muscle or movement target, demand, standard app setup, needs standing
# or foot loading, evidence source. A bridge loads both feet even though it is supine.
CATALOG = {
    "heel_slide": ("flexion", "Knee flexion mobility", "Very light", "Lying heel slide", False, "dorset"),
    "sitting_knee_bend": ("flexion", "Knee flexion mobility", "Very light", "Seated heel slide", False, "dorset"),
    "straight_leg_raise": ("quad_control", "Quadriceps hold the knee straight while hip flexors lift the leg", "Light to moderate", "Supine, no ankle weight", False, "aaos"),
    "seated_knee_extension": ("extension", "Quadriceps and active knee extension", "Light to moderate", "Seated full active extension, no ankle weight", False, "aaos"),
    "standing_knee_bend": ("hamstrings", "Hamstrings and active knee flexion", "Light", "Chair-supported standing curl", True, "aaos"),
    "glute_bridge": ("hip_extension", "Gluteal muscles and hamstrings", "Light to moderate", "Two-foot bridge", True, "bridge"),
    "side_lying_hip_abduction": ("hip_abduction", "Hip abductors, especially gluteus medius", "Low knee load; hip effort varies", "Side-lying lift, no resistance band", False, "hip"),
    "sit_to_stand": ("chair", "Quadriceps and gluteal muscles for chair transfers", "Light to moderate", "Firm chair, arms crossed", True, "dorset"),
    "mini_squat": ("squat", "Quadriceps and hip extensors during weight bearing", "Light to moderate", "Shallow two-leg squat with chair support", True, "dorset"),
    "step_up": ("stairs", "Lead-leg knee and hip extensors for stair ascent", "Light to moderate on a low step; varies with height", "Low step with a rail", True, "step"),
}

GENERAL_URGENT = ("new locking or giving way", "cannot bear weight or straighten after injury", "severe new swelling or deformity")
PATHS = {"unspecified": "Choose a pathway", "general": "General knee (adult, nonoperative)", "replacement": "Knee replacement"}
CLEARANCE = {"unknown": "Assessment pending", "cleared": "PT checked red flags and restrictions; selected standard movements are safe",
             "urgent": "Urgent symptoms present; arrange urgent medical assessment"}
LOAD = {"unknown": "Not assessed", "unloaded": "Unloaded knee movements only", "full": "Weight bearing permitted"}
SCHEMA = """CREATE TABLE IF NOT EXISTS knee_assessments (
patient_id TEXT PRIMARY KEY REFERENCES patients(patient_id), assessment_json TEXT NOT NULL);"""

def form_options():
    return {"pathway": PATHS, "clearance": CLEARANCE, "loading": LOAD,
            "pattern": {key: row[0] for key, row in PATTERNS.items()}, "findings": FINDINGS}


def validate(raw):
    if not isinstance(raw, dict) or raw.get("pathway") not in PATHS:
        raise ValueError("Choose a valid knee care pathway.")
    path = raw["pathway"]
    if path != "general":
        return {"pathway": path}
    clean = {"pathway": path}
    choices = {"pattern": PATTERNS, "clearance": CLEARANCE, "loading": LOAD,
               "bend_position": {"lying", "seated"}}
    defaults = {"pattern": "other", "clearance": "unknown", "loading": "unknown", "bend_position": "lying"}
    for key, allowed in choices.items():
        value = raw.get(key, defaults[key])
        if not isinstance(value, str) or value not in allowed:
            raise ValueError(f"Unknown knee assessment value for {key}.")
        clean[key] = value
    findings = raw.get("findings", [])
    if not isinstance(findings, list) or any(not isinstance(f, str) or f not in FINDINGS for f in findings):
        raise ValueError("Choose findings from the knee assessment form.")
    clean["findings"] = sorted(set(findings))
    for key, limit in (("sets", 5), ("reps", 15)):
        value = raw.get(key)
        if value is not None and (type(value) is not int or not 1 <= value <= limit):
            raise ValueError(f"{key.capitalize()} must be a whole number from 1 to {limit}.")
        clean[key] = value
    return clean


def assessment(con, pid):
    row = con.execute("SELECT assessment_json FROM knee_assessments WHERE patient_id=?", (pid,)).fetchone()
    if row:
        return validate(json.loads(row[0]))
    patient = con.execute("SELECT surgery_date FROM patients WHERE patient_id=?", (pid,)).fetchone()
    # Compatibility with the original knee-replacement project and its demo records.
    return {"pathway": "replacement" if patient and patient[0] else "unspecified"}


def save(con, pid, value):
    previous = assessment(con, pid)
    if previous != value:
        con.execute("UPDATE adjustment_requests SET status='superseded' WHERE patient_id=? AND status='pending'", (pid,))
    if value.get("clearance") == "urgent" and previous.get("clearance") != "urgent":
        import datetime as dt
        con.execute("INSERT INTO alerts (patient_id,date,tier,reason,source) VALUES (?,?, 'urgent',?, 'knee_screen')",
                    (pid, dt.date.today().isoformat(), "PT recorded urgent general-knee symptoms. Arrange urgent assessment."))
    con.execute("INSERT INTO knee_assessments VALUES (?,?) ON CONFLICT(patient_id) DO UPDATE SET assessment_json=excluded.assessment_json",
                (pid, json.dumps(value)))


def review_reason(a):
    if a["pathway"] != "general":
        return "Choose the care pathway in the clinic visit before drafting."
    if a["clearance"] == "urgent":
        return "Urgent symptoms recorded. Arrange urgent medical assessment before exercise."
    if a["clearance"] != "cleared" or a["loading"] == "unknown":
        return "Complete the clinical screen and weight-bearing assessment before drafting."
    if not a["sets"] or not a["reps"]:
        return "Enter the PT-selected sets and reps for the standard exercises."
    if not a["findings"]:
        return "Record at least one examined movement limitation and tolerated exercise setup."
    return ""


def eligible(a):
    if review_reason(a):
        return []
    flexion_move = "heel_slide" if a["bend_position"] == "lying" else "sitting_knee_bend"
    return [eid for eid, (finding, _, _, _, loaded, _) in CATALOG.items()
            if finding in a["findings"] and (not loaded or a["loading"] == "full")
            and (finding != "flexion" or eid == flexion_move)]


def urgent_alert(con, pid):
    return con.execute("SELECT 1 FROM alerts WHERE patient_id=? AND tier='urgent' "
                       "AND COALESCE(status,'open')='open' LIMIT 1", (pid,)).fetchone() is not None


def rank(ids, a, goal, goal_targets, llm):
    preferred = PATTERNS[a["pattern"]][3]
    ordered = sorted(ids, key=lambda eid: (eid not in preferred, eid not in goal_targets))
    if not llm or len(ordered) < 2:
        return ordered, "rules"
    payload = {"reported_pattern": PATTERNS[a["pattern"]][0], "goal": goal,
               "candidates": [{"id": eid, "examined_finding": FINDINGS[CATALOG[eid][0]],
                               "target": CATALOG[eid][1], "relative_demand": CATALOG[eid][2]} for eid in ordered]}
    reply = llm('Rank these PT-cleared exercise candidates by the supplied finding and goal. '
                'Return JSON with only an "order" array containing every candidate ID exactly once. '
                'Clinical restrictions and dosage are already applied by the server.\n' + json.dumps(payload))
    order = reply.get("order") if isinstance(reply, dict) and set(reply) == {"order"} else None
    if (isinstance(order, list) and all(isinstance(i, str) for i in order)
            and len(order) == len(ordered) and set(order) == set(ordered)):
        return order, "llm"
    return ordered, "rules"


def new_item(eid, a, names):
    finding, target, intensity, setup, _, source = CATALOG[eid]
    return {"exercise_id": eid, "exercise_name": names[eid], "action": "add", "before": None,
            "sets": a["sets"], "reps": a["reps"], "intensity": intensity, "setup": setup,
            "finding": finding, "source_url": SOURCES[source],
            "reason": f"{FINDINGS[finding]}. Target: {target}. Setup: {setup}. Relative demand: {intensity}."}


def draft(con, pid, a, goal, *, week=None, use_llm=True, pain_today=None):
    """Return a normal CoRehab draft. The LLM can only reorder eligible new exercises."""
    import rehab_node as rn

    goal = goal if goal in rn.GOALS else rn.DEFAULT_GOAL
    goals = rn.GOALS[goal]
    result = {"pathway": a["pathway"], "source": "rules", "kind": "first" if week is None else "weekly",
              "goal": goal, "goal_label": goals["label"], "direction": "new" if week is None else "same",
              "items": [], "note_for_pt": "", "explanation": "Plan based on the PT's knee assessment."}
    issue = review_reason(a)
    if a.get("clearance") != "urgent" and (urgent_alert(con, pid) or (pain_today is not None and pain_today >= rn.STOP_PAIN)):
        issue = "Review the urgent alert or high pain before recommending exercise."
    candidates = eligible(a)
    if issue or not candidates:
        result.update(review_required=True, note_for_pt=issue or "No standard exercise matches the findings and loading permission.")
        return result

    pattern = PATTERNS[a["pattern"]]
    result.update(note_for_pt=pattern[1], pattern_source=SOURCES[pattern[2]])
    llm = rn.llm_json if use_llm else None
    if week is None:
        ordered, result["source"] = rank(candidates, a, goals["label"], goals["targets"], llm)
        result["items"] = [new_item(eid, a, rn.EXERCISES) for eid in ordered[:5]]
        return result

    # Keep the existing per-exercise pain/form/feedback reduction and pause rules.
    # Positive changes additionally require screened eligibility and symptom follow-up.
    prior = {row["exercise_id"]: row for row in week["exercises"]}
    legacy = rn.goal_draft(week)["items"]
    morning = week.get("next_morning", [])
    active_days = week["days_checked_in"] - week["days_rest"]
    green = (active_days >= max(1, min(rn.MIN_DAYS, week["planned_days"]))
             and week["pain_today_avg"] is not None and week["pain_today_avg"] <= rn.PROGRESS_PAIN
             and morning and morning[-1] == "baseline" and "worse" not in morning
             and not week["days_rest"] and not week["sessions_stopped_early"]
             and not any(alert["tier"] == "urgent" for alert in week["alerts"])
             and not any(e["bothered_days"] or e["feeling_counts"].get("too_hard") for e in prior.values())
             and not any(e["action"] in ("pause", "easier") for e in legacy)
             and all(eid in candidates for eid in prior))
    rows = result["items"]
    for change in legacy:
        eid = change["exercise_id"]
        if eid not in prior:
            continue
        ex = prior[eid]
        sets, reps = map(int, ex["planned"].split(" x "))
        item = {**change}
        if eid not in candidates:
            item.update(action="pause", sets=0, reps=0, reason="The current visit no longer clears this standard movement.")
        elif item["action"] not in ("pause", "easier"):
            item.update(action="hold", sets=sets, reps=reps, reason="Continue the current dose within the assessed limits.")
            if green and rn._ready(ex, week) and rn._pain_signal(ex, week) is not None:
                if reps < a["reps"]:
                    item.update(action="progress", reps=min(reps + 2, a["reps"]), reason="Weekly response supports increasing reps within the PT limit.")
                elif sets < a["sets"]:
                    item.update(action="progress", sets=sets + 1, reason="Weekly response supports adding one set within the PT limit.")
        if item["sets"] > a["sets"] or item["reps"] > a["reps"]:
            item.update(action="easier", sets=min(item["sets"], a["sets"]), reps=min(item["reps"], a["reps"]),
                        reason="Reduced to the current PT dose limits.")
        if eid in CATALOG:
            item.update(intensity=CATALOG[eid][2], setup=CATALOG[eid][3], source_url=SOURCES[CATALOG[eid][5]])
        rows.append(item)
    missing = [eid for eid in candidates if eid not in prior]
    if green and missing and not any(i["action"] in ("easier", "pause") for i in rows):
        ordered, result["source"] = rank(missing, a, goals["label"], goals["targets"], llm)
        rows.append(new_item(ordered[0], a, rn.EXERCISES))
    lighter = any(i["action"] in ("easier", "pause") for i in rows)
    harder = any(i["action"] in ("progress", "add") for i in rows)
    result["direction"] = "mixed" if lighter and harder else "lighter" if lighter else "harder" if harder else "same"
    result["explanation"] = "Weekly review uses your therapist's findings and the response to your current exercises."
    if rows and all(item["action"] == "pause" for item in rows):
        result.update(review_required=True, note_for_pt="All current exercises need pausing. Review the active plan and prescribe a replacement before resuming.")
    elif not green:
        result["note_for_pt"] += " Increases await a tolerated week and a next-morning response."
    return result


def session_hold(con, pid, a, plan):
    reason = review_reason(a)
    if reason:
        return reason
    if urgent_alert(con, pid):
        return "An urgent alert needs clinical review before exercise."
    allowed = set(eligible(a))
    if any(it["exercise_id"] not in allowed or it["sets"] > a["sets"] or it["reps"] > a["reps"] for it in plan):
        return "The active plan needs PT review against the latest movement or dose restrictions."
    return ""


def check_approval(con, req, a, final_items, active_plan):
    draft_data = json.loads(req["draft_json"])
    summary = json.loads(req["summary_json"])
    if draft_data.get("review_required") or summary.get("knee_assessment") != a or urgent_alert(con, req["patient_id"]):
        raise ValueError("The clinical screen needs review. Generate a fresh draft.")
    if req["kind"] == "first" and active_plan:
        raise ValueError("An active plan now exists. Generate a weekly review.")
    if req["kind"] == "weekly":
        expected = {e["exercise_id"]: e["planned"] for e in summary["exercises"]}
        actual = {i["exercise_id"]: f"{i['sets']} x {i['reps']}" for i in active_plan}
        if expected != actual:
            raise ValueError("The active plan changed. Generate a fresh weekly draft.")
    proposed = {i["exercise_id"]: i for i in draft_data["items"]}
    allowed = set(eligible(a))
    seen = set()
    for it in final_items:
        eid = it.get("exercise_id")
        if eid in seen or eid not in proposed:
            raise ValueError("Only unique exercises from this draft can be approved.")
        seen.add(eid)
        if it.get("exercise_name") != proposed[eid]["exercise_name"]:
            raise ValueError("The exercise name must match its approved identifier.")
        sets, reps = it.get("sets"), it.get("reps")
        if type(sets) is not int or type(reps) is not int:
            raise ValueError("Use whole sets and reps.")
        if sets == 0 and reps == 0:
            continue
        if eid not in allowed or not 1 <= sets <= a["sets"] or not 1 <= reps <= a["reps"]:
            raise ValueError("The movement or dose exceeds the PT's current clearance.")
        original = proposed[eid]
        # A safety reduction or pause cannot be reversed through a stale or edited draft.
        if original["action"] in ("easier", "pause") and (sets > original["sets"] or reps > original["reps"]):
            raise ValueError("Reassess before reversing this reduction or pause.")
        if sets > original["sets"] or reps > original["reps"]:
            raise ValueError("Reassess before increasing beyond this draft.")


def connect_app(check_only=False):
    """Apply verified edits to the live-app entry points. Re-running is safe."""
    import os
    import stat
    import tempfile
    from pathlib import Path

    here = Path(__file__).resolve()
    if here.name != "knee_rules.py" or here.parent.name != "backend":
        raise ValueError("Put knee_rules.py in the extracted CoRehab project's backend folder first.")
    root = here.parent.parent
    changes = []
    for name, edits in CONNECTION_EDITS.items():
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Cannot find the expected project file: {name}")
        original = path.read_bytes()
        content = original.decode("utf-8").replace("\r\n", "\n")
        updated = content
        for before, after in edits:
            if updated.count(after) == 1:
                continue
            if updated.count(before) != 1:
                raise ValueError(f"{name} differs in a section used by the add-on. No files were written. "
                                 "Use the matching CoRehab-main version or merge this section manually.")
            updated = updated.replace(before, after, 1)
        if updated != content:
            if b"\r\n" in original:
                updated = updated.replace("\n", "\r\n")
            changes.append((path, original, updated.encode("utf-8")))
    if not changes:
        print("The knee rules are already connected.")
        return
    print("Files to update: " + ", ".join(str(p.relative_to(root)) for p, _, _ in changes))
    if check_only:
        print("Compatibility verified.")
        return

    def write_atomic(path, data):
        mode = stat.S_IMODE(path.stat().st_mode)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            temporary = Path(tmp.name)
            try:
                tmp.write(data)
                tmp.flush()
                os.fchmod(tmp.fileno(), mode) if hasattr(os, "fchmod") else os.chmod(temporary, mode)
            except BaseException:
                tmp.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    applied = []
    try:
        for path, original, updated in changes:
            if path.read_bytes() != original:
                raise ValueError(f"{path.name} changed during installation. Retry when edits are finished.")
            write_atomic(path, updated)
            applied.append((path, original, updated))
    except BaseException:
        for path, original, updated in reversed(applied):
            if path.read_bytes() == updated:
                write_atomic(path, original)
        raise
    print("Connected. Restart the CoRehab server and refresh the live PT app.")


# The following integration edits are generated from the supplied CoRehab-main ZIP.
# Each pair contains the original section and its replacement, including context.
CONNECTION_EDITS = {
    'backend/clinic.py': [
        ('        raise ValueError("Name is required.")\n    pid = _new_id(con, name)\n    rn.add_patient(con, pid, name.strip(), surgery_date or "", side or "", "", dob=dob, phone=phone, status="active")\n    return {"patient_id": pid, "username": pid}\n\n\n',
         '        raise ValueError("Name is required.")\n    pid = _new_id(con, name)\n    rn.add_patient(con, pid, name.strip(), surgery_date or "", side or "", "", dob=dob, phone=phone, status="active")\n    rn.knee_rules.save(con, pid, {"pathway": "unspecified"})\n    con.commit()\n    return {"patient_id": pid, "username": pid}\n\n\n'),
        ('    """PT adds a patient; they get an intake link (status intake_sent)."""\n    pid = _new_id(con, name)\n    rn.add_patient(con, pid, name, surgery_date, side, "", phone=phone, dob=dob, status="intake_sent")\n    con.execute("UPDATE patients SET goal=? WHERE patient_id=?", ("", pid))\n    con.commit()\n    return {"patient_id": pid, "status": "intake_sent", "surgery": surgery}\n',
         '    """PT adds a patient; they get an intake link (status intake_sent)."""\n    pid = _new_id(con, name)\n    rn.add_patient(con, pid, name, surgery_date, side, "", phone=phone, dob=dob, status="intake_sent")\n    path = {"General knee": "general", "Knee replacement": "replacement"}.get(surgery, "unspecified")\n    rn.knee_rules.save(con, pid, rn.knee_rules.validate({"pathway": path}))\n    con.execute("UPDATE patients SET goal=? WHERE patient_id=?", ("", pid))\n    con.commit()\n    return {"patient_id": pid, "status": "intake_sent", "surgery": surgery}\n'),
        ('def save_findings(con, pid, fields, visit_id=None):\n    """Autosave clinic-visit findings as the PT types. Creates today\'s visit record if there is none."""\n    _patient(con, pid)\n    clean = {}\n    for k, v in fields.items():\n        if k not in FINDING_FIELDS:\n',
         'def save_findings(con, pid, fields, visit_id=None):\n    """Autosave clinic-visit findings as the PT types. Creates today\'s visit record if there is none."""\n    _patient(con, pid)\n    fields = dict(fields)\n    assessment = rn.knee_rules.validate(fields.pop("knee_assessment")) if "knee_assessment" in fields else None\n    clean = {}\n    for k, v in fields.items():\n        if k not in FINDING_FIELDS:\n'),
        ('                                    CLINIC["name"], _now())).lastrowid\n        else:\n            visit_id = r["visit_id"]\n    old = con.execute("SELECT findings_json FROM visits WHERE visit_id=?", (visit_id,)).fetchone()\n    merged = {**json.loads((old["findings_json"] if old else None) or "{}"), **clean}\n    con.execute("UPDATE visits SET findings_json=?, status=\'done\', updated_at=? WHERE visit_id=?",\n                (json.dumps(merged), _now(), visit_id))\n    con.commit()\n',
         '                                    CLINIC["name"], _now())).lastrowid\n        else:\n            visit_id = r["visit_id"]\n    if assessment is not None:\n        rn.knee_rules.save(con, pid, assessment)\n    old = con.execute("SELECT findings_json FROM visits WHERE visit_id=?", (visit_id,)).fetchone()\n    merged = {**json.loads((old["findings_json"] if old else None) or "{}"), **clean}\n    if (clean.get("pain_today") is not None and clean["pain_today"] >= rn.STOP_PAIN\n            and rn.knee_rules.assessment(con, pid)["pathway"] == "general" and not rn.knee_rules.urgent_alert(con, pid)):\n        rn.add_alert(con, pid, dt.date.today().isoformat(), "urgent", "High pain reported at the knee assessment.", "knee_screen")\n    con.execute("UPDATE visits SET findings_json=?, status=\'done\', updated_at=? WHERE visit_id=?",\n                (json.dumps(merged), _now(), visit_id))\n    con.commit()\n'),
        ('    return {"patient_id": pid, "name": p["name"], "first_name": (p["name"] or "").split(" ")[0], "phase": phase,\n',
         '    return {"care_pathway": rn.knee_rules.assessment(con, pid)["pathway"],\n            "patient_id": pid, "name": p["name"], "first_name": (p["name"] or "").split(" ")[0], "phase": phase,\n'),
        ('    days_since = (today - dt.date.fromisoformat(last)).days if last else None\n    monday = today - dt.timedelta(days=today.weekday())\n    nv = next_visit(con, pid, today)\n    wk = _week_post_op(p, today)\n\n    if p["status"] == "discharged":\n        group, nxt = "discharged", "Discharged"\n',
         '    days_since = (today - dt.date.fromisoformat(last)).days if last else None\n    monday = today - dt.timedelta(days=today.weekday())\n    nv = next_visit(con, pid, today)\n    pathway = rn.knee_rules.assessment(con, pid)["pathway"]\n    wk = _week_post_op(p, today) if pathway == "replacement" else None\n\n    if p["status"] == "discharged":\n        group, nxt = "discharged", "Discharged"\n'),
        ('        group = "on_track"\n        nxt = f"Visit {_nice(nv[\'date\'], nv[\'time\'])}" if nv else (f"Week {wk + 1} starts Mon" if wk is not None else "")\n    return {"patient_id": pid, "name": p["name"], "age": _age(p["dob"], today),\n            "surgery": f"{(p[\'operated_side\'] or \'\').capitalize()} knee replacement".strip(),\n            "surgery_date": p["surgery_date"], "week_post_op": wk, "group": group, "next": nxt,\n            "last_session": last, "days_since_session": days_since,\n            "pain_after_this_week": _pain_after_week(con, pid, monday),\n',
         '        group = "on_track"\n        nxt = f"Visit {_nice(nv[\'date\'], nv[\'time\'])}" if nv else (f"Week {wk + 1} starts Mon" if wk is not None else "")\n    return {"patient_id": pid, "name": p["name"], "age": _age(p["dob"], today),\n            "surgery": (f"{(p[\'operated_side\'] or \'\').capitalize()} knee replacement".strip() if pathway == "replacement"\n                        else "General knee" if pathway == "general" else "Knee assessment pending"),\n            "surgery_date": p["surgery_date"], "week_post_op": wk, "group": group, "next": nxt,\n            "last_session": last, "days_since_session": days_since,\n            "pain_after_this_week": _pain_after_week(con, pid, monday),\n'),
        ('    history = [dict(r) for r in con.execute(\n        "SELECT start_date, end_date, approved_by, GROUP_CONCAT(exercise_name || \' \' || sets || \'x\' || reps, \', \') AS plan"\n        " FROM plan_items WHERE patient_id=? GROUP BY start_date ORDER BY start_date DESC", (pid,))]\n    return {"patient": {**patient_row(con, p, today), "dob": p["dob"], "phone": p["phone"], "goal": p["goal"],\n                        "rest_days": rn.rest_days(con, pid)},\n            "intake": rn.latest_intake(con, pid), "plan": rn.current_plan(con, pid), "plan_history": history,\n            "sessions": sessions, "pain_by_week": pain_series(con, pid),\n',
         '    history = [dict(r) for r in con.execute(\n        "SELECT start_date, end_date, approved_by, GROUP_CONCAT(exercise_name || \' \' || sets || \'x\' || reps, \', \') AS plan"\n        " FROM plan_items WHERE patient_id=? GROUP BY start_date ORDER BY start_date DESC", (pid,))]\n    return {"knee_assessment": rn.knee_rules.assessment(con, pid), "knee_form": rn.knee_rules.form_options(),\n            "patient": {**patient_row(con, p, today), "dob": p["dob"], "phone": p["phone"], "goal": p["goal"],\n                        "rest_days": rn.rest_days(con, pid)},\n            "intake": rn.latest_intake(con, pid), "plan": rn.current_plan(con, pid), "plan_history": history,\n            "sessions": sessions, "pain_by_week": pain_series(con, pid),\n'),
        ('    intake = rn.latest_intake(con, pid)\n    f = latest_findings(con, pid)\n    findings = f["findings"] if f else {}\n    rules = first_plan_rules(intake, findings, week)\n    draft = (llm_first_plan(intake, findings, week, rules) if use_llm else None) or dict(rules, ai_note=rn.ai_note(use_llm))\n    con.execute("UPDATE adjustment_requests SET status=\'superseded\' WHERE patient_id=? AND status=\'pending\' AND kind=\'first\'", (pid,))\n    summary = {"patient_id": pid, "kind": "first", "week_post_op": week, "findings": findings, "exercises": []}\n    cur = con.execute("INSERT INTO adjustment_requests (patient_id, week_end, summary_json, draft_json, draft_source, status, kind)"\n                      " VALUES (?,?,?,?,?, \'pending\', \'first\')",\n                      (pid, dt.date.today().isoformat(), json.dumps(summary), json.dumps(draft), draft["source"]))\n',
         '    intake = rn.latest_intake(con, pid)\n    f = latest_findings(con, pid)\n    findings = f["findings"] if f else {}\n    assessment = rn.knee_rules.assessment(con, pid)\n    if assessment["pathway"] == "general" and rn.current_plan(con, pid):\n        review = rn.create_adjustment_request(con, pid, use_llm=use_llm)\n        return {**review, "week_post_op": None}\n    if assessment["pathway"] == "replacement":\n        rules = first_plan_rules(intake, findings, week)\n        draft = (llm_first_plan(intake, findings, week, rules) if use_llm else None) or dict(rules, ai_note=rn.ai_note(use_llm))\n    else:\n        week = None\n        draft = rn.knee_rules.draft(con, pid, assessment, p["goal"], use_llm=use_llm, pain_today=findings.get("pain_today"))\n    con.execute("UPDATE adjustment_requests SET status=\'superseded\' WHERE patient_id=? AND status=\'pending\' AND kind=\'first\'", (pid,))\n    summary = {"patient_id": pid, "kind": "first", "week_post_op": week, "findings": findings, "exercises": []}\n    if assessment["pathway"] != "replacement":\n        summary["knee_assessment"] = assessment\n    cur = con.execute("INSERT INTO adjustment_requests (patient_id, week_end, summary_json, draft_json, draft_source, status, kind)"\n                      " VALUES (?,?,?,?,?, \'pending\', \'first\')",\n                      (pid, dt.date.today().isoformat(), json.dumps(summary), json.dumps(draft), draft["source"]))\n'),
    ],
    'backend/rehab_node.py': [
        ('import datetime as dt\nimport json\nimport sqlite3\nfrom statistics import mean\n\nDB_PATH = "rehab.db"\n',
         'import datetime as dt\nimport json\nimport sqlite3\nimport knee_rules\nfrom statistics import mean\n\nDB_PATH = "rehab.db"\n'),
        ('    "patients": [("dob", "TEXT"), ("phone", "TEXT"), ("status", "TEXT DEFAULT \'active\'"), ("rest_days", "TEXT"),\n                 ("pt_private_notes", "TEXT"), ("consent_json", "TEXT"), ("consent_at", "TEXT"),\n                 ("created_at", "TEXT"), ("join_code", "TEXT")],\n    "checkins": [("worry_json", "TEXT"), ("pain_end", "INTEGER"), ("bothered_json", "TEXT"), ("end_note", "TEXT"),\n                 ("stopped_early", "INTEGER DEFAULT 0"), ("stop_reason", "TEXT"), ("stop_pain", "INTEGER"),\n                 ("ended_at", "TEXT")],\n    "exercise_logs": [("feeling", "TEXT"), ("note", "TEXT")],\n',
         '    "patients": [("dob", "TEXT"), ("phone", "TEXT"), ("status", "TEXT DEFAULT \'active\'"), ("rest_days", "TEXT"),\n                 ("pt_private_notes", "TEXT"), ("consent_json", "TEXT"), ("consent_at", "TEXT"),\n                 ("created_at", "TEXT"), ("join_code", "TEXT")],\n    "checkins": [("next_morning", "TEXT"), ("worry_json", "TEXT"), ("pain_end", "INTEGER"), ("bothered_json", "TEXT"), ("end_note", "TEXT"),\n                 ("stopped_early", "INTEGER DEFAULT 0"), ("stop_reason", "TEXT"), ("stop_pain", "INTEGER"),\n                 ("ended_at", "TEXT")],\n    "exercise_logs": [("feeling", "TEXT"), ("note", "TEXT")],\n'),
        ('def connect(path=DB_PATH):\n    con = sqlite3.connect(path, check_same_thread=False)  # Gradio calls from worker threads\n    con.row_factory = sqlite3.Row\n    con.executescript(SCHEMA)\n    for table, cols in NEW_COLUMNS.items():\n        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}\n        for name, kind in cols:\n',
         'def connect(path=DB_PATH):\n    con = sqlite3.connect(path, check_same_thread=False)  # Gradio calls from worker threads\n    con.row_factory = sqlite3.Row\n    con.executescript(SCHEMA + knee_rules.SCHEMA)\n    for table, cols in NEW_COLUMNS.items():\n        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}\n        for name, kind in cols:\n'),
        ('                (patient_id, date, tier, reason, source))\n\n\ndef daily_checkin(con, patient_id, pain_today, date=None, pain_location="none", worry=None):\n    """Ask pain before the session; return today\'s plan with adjusted reps.\n\n    worry: {"symptoms": [...from URGENT_SYMPTOMS], "other": "free text"} from "Is anything worrying you today?"\n',
         '                (patient_id, date, tier, reason, source))\n\n\ndef daily_checkin(con, patient_id, pain_today, date=None, pain_location="none", worry=None, next_morning=None):\n    """Ask pain before the session; return today\'s plan with adjusted reps.\n\n    worry: {"symptoms": [...from URGENT_SYMPTOMS], "other": "free text"} from "Is anything worrying you today?"\n'),
        ('        raise ValueError(f"No plan for {patient_id}. The PT must enter a plan first.")\n    worry = worry or {}\n    symptoms = [str(x).lower() for x in worry.get("symptoms") or []]\n    unknown = [x for x in symptoms if x not in URGENT_SYMPTOMS]\n    if unknown:\n        raise ValueError(f"Unknown symptom(s) {unknown}. Use {URGENT_SYMPTOMS}, or put it in \'other\'.")\n    other = (worry.get("other") or "").strip()\n    action, factor = rep_rule(pain_today)\n    if symptoms:\n        action, factor = "rest", 0.0\n    con.execute(\n        "INSERT INTO checkins (patient_id, date, pain_today, pain_location, action, rep_factor, worry_json)"\n',
         '        raise ValueError(f"No plan for {patient_id}. The PT must enter a plan first.")\n    worry = worry or {}\n    symptoms = [str(x).lower() for x in worry.get("symptoms") or []]\n    assessment = knee_rules.assessment(con, patient_id)\n    named = URGENT_SYMPTOMS + (list(knee_rules.GENERAL_URGENT) if assessment["pathway"] == "general" else [])\n    unknown = [x for x in symptoms if x not in named]\n    if unknown:\n        raise ValueError(f"Unknown symptom(s) {unknown}. Use {named}, or put it in \'other\'.")\n    other = (worry.get("other") or "").strip()\n    action, factor = rep_rule(pain_today)\n    if next_morning not in (None, "unknown", "baseline", "worse"):\n        raise ValueError("Unknown next-morning response.")\n    hold = ""\n    if assessment["pathway"] == "general":\n        hold = knee_rules.session_hold(con, patient_id, assessment, current_plan(con, patient_id))\n        if any(x in knee_rules.GENERAL_URGENT for x in symptoms):\n            hold = "Arrange urgent medical assessment for the new knee symptoms before exercise."\n    if symptoms or hold:\n        action, factor = "rest", 0.0\n    con.execute(\n        "INSERT INTO checkins (patient_id, date, pain_today, pain_location, action, rep_factor, worry_json)"\n'),
        ('        (patient_id, date, pain_today, pain_location, action, factor,\n         json.dumps({"symptoms": symptoms, "other": other}) if (symptoms or other) else None),\n    )\n    if pain_today >= STOP_PAIN:\n        add_alert(con, patient_id, date, "urgent", f"Pain {pain_today}/10 before session; exercise skipped today", "checkin")\n    if symptoms:\n',
         '        (patient_id, date, pain_today, pain_location, action, factor,\n         json.dumps({"symptoms": symptoms, "other": other}) if (symptoms or other) else None),\n    )\n    if next_morning is not None:\n        con.execute("UPDATE checkins SET next_morning=? WHERE patient_id=? AND date=?", (next_morning, patient_id, date))\n    if hold:\n        add_alert(con, patient_id, date, "urgent" if assessment["clearance"] == "urgent" else "review", hold, "knee_screen")\n    if pain_today >= STOP_PAIN:\n        add_alert(con, patient_id, date, "urgent", f"Pain {pain_today}/10 before session; exercise skipped today", "checkin")\n    if symptoms:\n'),
        ('        "rest": "Please rest today. We\'ve told your PT.",\n    }\n    return {"checkin_id": checkin_id, "date": date, "pain_today": pain_today, "urgent": action == "rest",\n            "action": action, "message": messages[action], "exercises": today}\n\n\ndef log_exercise(con, checkin_id, result):\n',
         '        "rest": "Please rest today. We\'ve told your PT.",\n    }\n    return {"checkin_id": checkin_id, "date": date, "pain_today": pain_today, "urgent": action == "rest",\n            "action": action, "message": hold or messages[action], "exercises": today}\n\n\ndef log_exercise(con, checkin_id, result):\n'),
        ('    s, e = start.isoformat(), end.isoformat()\n\n    checkins = [dict(r) for r in con.execute(\n        "SELECT date, pain_today, pain_location, action, pain_end, bothered_json, stopped_early, worry_json FROM checkins"\n        " WHERE patient_id=? AND date BETWEEN ? AND ? ORDER BY date",\n        (patient_id, s, e)).fetchall()]\n    patient = con.execute("SELECT * FROM patients WHERE patient_id=?", (patient_id,)).fetchone()\n',
         '    s, e = start.isoformat(), end.isoformat()\n\n    checkins = [dict(r) for r in con.execute(\n        "SELECT date, pain_today, pain_location, action, pain_end, bothered_json, stopped_early, worry_json, next_morning FROM checkins"\n        " WHERE patient_id=? AND date BETWEEN ? AND ? ORDER BY date",\n        (patient_id, s, e)).fetchall()]\n    patient = con.execute("SELECT * FROM patients WHERE patient_id=?", (patient_id,)).fetchone()\n'),
        ('        "week": {"start": s, "end": e},\n        "days_checked_in": len(checkins),\n        "pain_location_days": locations,\n        "days_reduced": sum(c["action"] == "reduced" for c in checkins),\n        "days_rest": sum(c["action"] == "rest" for c in checkins),\n        "pain_today_avg": round(mean(c["pain_today"] for c in checkins), 1) if checkins else None,\n',
         '        "week": {"start": s, "end": e},\n        "days_checked_in": len(checkins),\n        "pain_location_days": locations,\n        "next_morning": [c["next_morning"] or "unknown" for c in checkins],\n        "days_reduced": sum(c["action"] == "reduced" for c in checkins),\n        "days_rest": sum(c["action"] == "rest" for c in checkins),\n        "pain_today_avg": round(mean(c["pain_today"] for c in checkins), 1) if checkins else None,\n'),
        ('def create_adjustment_request(con, patient_id, week_end=None, use_llm=True):\n    """Build the weekly summary and goal-based draft, store it as a pending request for the PT."""\n    summary = weekly_summary(con, patient_id, week_end)\n    rules = goal_draft(summary)\n    draft = (llm_draft(summary, rules) if use_llm else None) or dict(rules, ai_note=ai_note(use_llm))\n    con.execute("UPDATE adjustment_requests SET status=\'superseded\' WHERE patient_id=? AND status=\'pending\'"\n                " AND COALESCE(kind, \'weekly\')=\'weekly\'", (patient_id,))\n    cur = con.execute(\n',
         'def create_adjustment_request(con, patient_id, week_end=None, use_llm=True):\n    """Build the weekly summary and goal-based draft, store it as a pending request for the PT."""\n    summary = weekly_summary(con, patient_id, week_end)\n    assessment = knee_rules.assessment(con, patient_id)\n    if assessment["pathway"] == "replacement":\n        rules = goal_draft(summary)\n        draft = (llm_draft(summary, rules) if use_llm else None) or dict(rules, ai_note=ai_note(use_llm))\n    else:\n        summary["knee_assessment"] = assessment\n        draft = knee_rules.draft(con, patient_id, assessment, summary["goal"],\n                                 week=summary, use_llm=use_llm)\n    con.execute("UPDATE adjustment_requests SET status=\'superseded\' WHERE patient_id=? AND status=\'pending\'"\n                " AND COALESCE(kind, \'weekly\')=\'weekly\'", (patient_id,))\n    cur = con.execute(\n'),
        ('    if final_items is None and decisions:\n        final_items = apply_decisions(draft_items, current_plan(con, req["patient_id"]), decisions)\n    final_items = final_items if final_items is not None else draft_items\n    if approve and not any(int(i["sets"]) > 0 for i in final_items):\n        raise ValueError("This plan has no exercises left. Keep at least one, or keep the current plan.")\n    edited = int(json.dumps(final_items, sort_keys=True) != json.dumps(draft_items, sort_keys=True))\n',
         '    if final_items is None and decisions:\n        final_items = apply_decisions(draft_items, current_plan(con, req["patient_id"]), decisions)\n    final_items = final_items if final_items is not None else draft_items\n    if approve:\n        assessment = knee_rules.assessment(con, req["patient_id"])\n        expected_path = json.loads(req["draft_json"]).get("pathway", "replacement")\n        if assessment["pathway"] != expected_path:\n            raise ValueError("The care pathway changed. Generate a fresh draft.")\n        if expected_path != "replacement":\n            knee_rules.check_approval(con, req, assessment, final_items, current_plan(con, req["patient_id"]))\n    if approve and not any(int(i["sets"]) > 0 for i in final_items):\n        raise ValueError("This plan has no exercises left. Keep at least one, or keep the current plan.")\n    edited = int(json.dumps(final_items, sort_keys=True) != json.dumps(draft_items, sort_keys=True))\n'),
    ],
    'backend/test_server.py': [
        ('            if path == "/api/checkin":\n                today = rn.daily_checkin(con, body["patient_id"], int(body["pain_today"]),\n                                         body.get("date") or None, body.get("pain_location", "none"),\n                                         body.get("worry"))\n                return self._send(200, {"ok": True, **today})\n            if path == "/api/session/end":\n                return self._send(200, rn.end_session(con, int(body["checkin_id"]), body["pain_end"],\n',
         '            if path == "/api/checkin":\n                today = rn.daily_checkin(con, body["patient_id"], int(body["pain_today"]),\n                                         body.get("date") or None, body.get("pain_location", "none"),\n                                         body.get("worry"), body.get("next_morning"))\n                return self._send(200, {"ok": True, **today})\n            if path == "/api/session/end":\n                return self._send(200, rn.end_session(con, int(body["checkin_id"]), body["pain_end"],\n'),
        ('            if path == "/api/patients/add":\n                return self._send(200, {"ok": True, **clinic.add_patient_by_pt(\n                    con, body["name"], body.get("phone"), body.get("surgery", "Knee replacement"),\n                    body["surgery_date"], body.get("side", "right"), body.get("dob"))})\n            if path == "/api/consent":\n                return self._send(200, clinic.save_consent(con, body["patient_id"], body.get("items", [])))\n            if path == "/api/setup-check":\n',
         '            if path == "/api/patients/add":\n                return self._send(200, {"ok": True, **clinic.add_patient_by_pt(\n                    con, body["name"], body.get("phone"), body.get("surgery", "Knee replacement"),\n                    body.get("surgery_date"), body.get("side", "right"), body.get("dob"))})\n            if path == "/api/consent":\n                return self._send(200, clinic.save_consent(con, body["patient_id"], body.get("items", [])))\n            if path == "/api/setup-check":\n'),
    ],
    'frontend/pt-live.js': [
        ('    const today = new Date();\n    const add = PTL.addOpen ? `<div class="card pad" style="margin-top:14px"><h2>Add a patient</h2><p class="sub">They get a link to fill in the intake before the first visit.</p>\n      <div class="form f3" style="margin-top:12px"><div class="fld"><label for="ap_name">Full name</label><input id="ap_name"></div><div class="fld"><label for="ap_phone">Mobile number</label><input id="ap_phone"></div>\n      <div class="fld"><label for="ap_date">Surgery date</label><input id="ap_date" type="date" value="${new Date(Date.now() - 5 * 864e5).toISOString().slice(0, 10)}"></div>\n      <div class="fld"><label for="ap_side">Knee</label><select id="ap_side"><option value="right">Right knee replacement</option><option value="left">Left knee replacement</option></select></div></div>\n      <div class="foot" style="margin-top:12px"><button class="btn sec" data-lv="addtoggle">Cancel</button><button class="btn" data-lv="addsave">${I().send}Send intake link</button></div></div>` : \'\';\n    return `<div class="mhead"><h1>Patients</h1><span class="sub" style="margin:0">${DOW[today.getDay()]}, ${MON[today.getMonth()]} ${today.getDate()}</span></div>${errBox()}\n      <div class="ptool"><div class="tabs2 ptabs">${tab(\'needs\', \'Needs you\', needs)}${tab(\'all\', \'All\', L.patients.length)}${tab(\'new\', \'New\', c.new || 0)}${tab(\'discharged\', \'Discharged\', c.discharged || 0)}</div>\n',
         '    const today = new Date();\n    const add = PTL.addOpen ? `<div class="card pad" style="margin-top:14px"><h2>Add a patient</h2><p class="sub">They get a link to fill in the intake before the first visit.</p>\n      <div class="form f3" style="margin-top:12px"><div class="fld"><label for="ap_name">Full name</label><input id="ap_name"></div><div class="fld"><label for="ap_phone">Mobile number</label><input id="ap_phone"></div>\n      <div class="fld"><label for="ap_path">Care pathway</label><select id="ap_path"><option value="replacement">Knee replacement</option><option value="general">General knee (nonoperative)</option></select></div>\n      <div class="fld" id="ap_surgical_date"><label for="ap_date">Surgery date</label><input id="ap_date" type="date" value="${new Date(Date.now() - 5 * 864e5).toISOString().slice(0, 10)}"></div>\n      <div class="fld"><label for="ap_side">Knee</label><select id="ap_side"><option value="right">Right knee</option><option value="left">Left knee</option></select></div></div>\n      <div class="foot" style="margin-top:12px"><button class="btn sec" data-lv="addtoggle">Cancel</button><button class="btn" data-lv="addsave">${I().send}Send intake link</button></div></div>` : \'\';\n    return `<div class="mhead"><h1>Patients</h1><span class="sub" style="margin:0">${DOW[today.getDay()]}, ${MON[today.getMonth()]} ${today.getDate()}</span></div>${errBox()}\n      <div class="ptool"><div class="tabs2 ptabs">${tab(\'needs\', \'Needs you\', needs)}${tab(\'all\', \'All\', L.patients.length)}${tab(\'new\', \'New\', c.new || 0)}${tab(\'discharged\', \'Discharged\', c.discharged || 0)}</div>\n'),
        ("    ['Function', [['tug_sec', 'Timed Up and Go', 'sec'], ['chair_stand_30s', '30-second chair stand', 'stands'], ['walking_aid', 'Walking aid', ['None', 'Cane', 'Walker']], ['stairs', 'Stairs', ['independent', 'step to step, rail', 'not yet']]]],\n    ['Knee', [['sw_r', 'Swelling, operated knee', 'cm'], ['sw_l', 'Other knee, to compare', 'cm'], ['incision', 'Incision', ['Healing well', 'Some redness', 'Drainage']]]],\n  ];\n  function visitView() {\n    const P = PTL.cache.profile[PTL.pid];\n    if (!P) return errBox();\n",
         '    [\'Function\', [[\'tug_sec\', \'Timed Up and Go\', \'sec\'], [\'chair_stand_30s\', \'30-second chair stand\', \'stands\'], [\'walking_aid\', \'Walking aid\', [\'None\', \'Cane\', \'Walker\']], [\'stairs\', \'Stairs\', [\'independent\', \'step to step, rail\', \'not yet\']]]],\n    [\'Knee\', [[\'sw_r\', \'Swelling, operated knee\', \'cm\'], [\'sw_l\', \'Other knee, to compare\', \'cm\'], [\'incision\', \'Incision\', [\'Healing well\', \'Some redness\', \'Drainage\']]]],\n  ];\n  function kneeFields(profile) {\n    const form = profile.knee_form, a = profile.knee_assessment;\n    if (!form || !a) return \'\';\n    const select = (key, label, options, fallback) => {\n      const selected = a[key] || fallback;\n      return `<div class="fld"><label for="lv_kg_${key}">${esc(label)}</label><select id="lv_kg_${key}" data-lvf>${Object.entries(options).map(([id, text]) => `<option value="${esc(id)}"${id === selected ? \' selected\' : \'\'}>${esc(text)}</option>`).join(\'\')}</select></div>`;\n    };\n    const cap = (key, max) => `<div class="fld"><label for="lv_kg_${key}">${key === \'sets\' ? \'Sets\' : \'Reps per set\'} for new exercises</label><input id="lv_kg_${key}" type="number" min="1" max="${max}" data-lvf value="${esc(a[key] ?? \'\')}"></div>`;\n    return `<div class="vsec"><p class="eyebrow">Exercise recommendation</p>\n      ${select(\'pathway\', \'Care pathway\', form.pathway, \'unspecified\')}\n      <div id="lv_kg_details"${a.pathway === \'general\' ? \'\' : \' hidden\'}>\n      <div class="form f2">${select(\'pattern\', \'Reported symptom pattern\', form.pattern, \'other\')}\n      ${select(\'clearance\', \'Clinical screen\', form.clearance, \'unknown\')}\n      ${select(\'loading\', \'Loading permission\', form.loading, \'unknown\')}\n      ${select(\'bend_position\', \'Preferred position for knee bending\', {lying:\'Lying\', seated:\'Seated\'}, \'lying\')}</div>\n      <p class="hint">Screen for a newly locked or unstable knee, inability to bear weight, severe swelling, a hot red knee with fever, or new one-sided calf pain with swelling. Arrange urgent medical assessment when indicated.</p>\n      <fieldset><legend>Examined findings and tolerated movements</legend>${Object.entries(form.findings).map(([id, label]) =>\n        `<label style="display:block;margin:8px 0"><input type="checkbox" data-lvf data-kg-finding="${esc(id)}"${(a.findings || []).includes(id) ? \' checked\' : \'\'}> ${esc(label)}</label>`).join(\'\')}</fieldset>\n      <div class="form f2">${cap(\'sets\', 5)}${cap(\'reps\', 15)}</div>\n      <p class="hint">These values also cap weekly increases. Select only the standard unweighted movements the patient can perform with the app’s camera instructions.</p></div></div>`;\n  }\n  function visitView() {\n    const P = PTL.cache.profile[PTL.pid];\n    if (!P) return errBox();\n'),
        ('    const txt = (k, l, v) => `<div class="fld"><label for="lv_${k}">${l}</label><textarea id="lv_${k}" data-lvf="${k}" rows="3">${esc(v)}</textarea></div>`;\n    return head(P.patient, \'list\', \'Step 2 of 3: clinic visit\') + `<div class="vgrid"><div class="card pad">\n      <div class="vcard"><div><h2>Clinic visit</h2><p class="sub" style="margin-top:2px">${fmt(new Date().toISOString())} · Riverside Physical Therapy</p></div><span class="vsave" id="lvsave">${I().check}Saved</span></div>\n      ${VFIELDS.map(([t, fs]) => `<div class="vsec"><p class="eyebrow">${t}</p><div class="form f3">${fs.map(field).join(\'\')}</div></div>`).join(\'\')}\n      <div class="vsec"><p class="eyebrow">Goals and precautions</p><div class="form f2">${txt(\'goals_week6\', \'Goals by week 6 (one per line; the patient sees these)\', (f.goals_week6 || []).join(\'\\n\'))}${txt(\'precautions\', \'Precautions (one per line)\', (f.precautions || []).join(\'\\n\'))}</div></div>\n      <div class="vsec"><div class="form">${txt(\'notes\', \'Short notes (PT only)\', f.notes || \'\')}</div></div></div>\n      <div><div class="card pad"><h2>From the intake</h2><p class="sub" style="white-space:pre-line">${esc(P.intake ? P.intake.summary : \'No intake yet.\')}</p></div>\n      <div class="card pad sec2 fpnext"><span class="spark">${I().spark}</span><div class="fptx"><h2>Next: first plan</h2><p class="sub" style="margin-top:4px">AI drafts it from this visit, the intake and the clinic\'s knee replacement protocol. Each exercise comes with a reason; you accept or reject each one before it\'s sent.</p></div>\n      <div class="fpbtns"><button class="btn" data-lv="firstdraft">${I().spark}Draft plan with AI</button></div></div></div></div>\n      <div class="foot"><button class="btn sec" data-lv="go" data-v="intake">Back</button></div>`;\n  }\n',
         '    const txt = (k, l, v) => `<div class="fld"><label for="lv_${k}">${l}</label><textarea id="lv_${k}" data-lvf="${k}" rows="3">${esc(v)}</textarea></div>`;\n    return head(P.patient, \'list\', \'Step 2 of 3: clinic visit\') + `<div class="vgrid"><div class="card pad">\n      <div class="vcard"><div><h2>Clinic visit</h2><p class="sub" style="margin-top:2px">${fmt(new Date().toISOString())} · Riverside Physical Therapy</p></div><span class="vsave" id="lvsave">${I().check}Saved</span></div>\n      ${kneeFields(P)}\n      ${VFIELDS.map(([t, fs]) => `<div class="vsec"><p class="eyebrow">${t}</p><div class="form f3">${fs.map(field).join(\'\')}</div></div>`).join(\'\')}\n      <div class="vsec"><p class="eyebrow">Goals and precautions</p><div class="form f2">${txt(\'goals_week6\', \'Goals by week 6 (one per line; the patient sees these)\', (f.goals_week6 || []).join(\'\\n\'))}${txt(\'precautions\', \'Precautions (one per line)\', (f.precautions || []).join(\'\\n\'))}</div></div>\n      <div class="vsec"><div class="form">${txt(\'notes\', \'Short notes (PT only)\', f.notes || \'\')}</div></div></div>\n      <div><div class="card pad"><h2>From the intake</h2><p class="sub" style="white-space:pre-line">${esc(P.intake ? P.intake.summary : \'No intake yet.\')}</p></div>\n      <div class="card pad sec2 fpnext"><span class="spark">${I().spark}</span><div class="fptx"><h2>Exercise plan</h2><p class="sub" style="margin-top:4px">The selected pathway and visit findings guide the draft. Each exercise comes with a reason; you accept or reject each one before it\'s sent.</p></div>\n      <div class="fpbtns"><button class="btn" data-lv="firstdraft">${I().spark}Draft plan with AI</button></div></div></div></div>\n      <div class="foot"><button class="btn sec" data-lv="go" data-v="intake">Back</button></div>`;\n  }\n'),
        ('      const e = ex(i.exercise_id), v = PTL.dec[i.exercise_id];\n      const change = i.action === \'hold\' ? \'\' : `${i.before ? `<s>${i.before.replace(\' x \', \' × \')}</s> → ` : \'\'}${i.sets ? dose(i.sets, i.reps) : \'paused\'}`;\n      return `<div class="srow rvrow${i.action !== \'hold\' && v !== \'reject\' ? \' sugd\' : \'\'}"><img src="${e.img}" alt=""><div><b>${esc(e.n)}</b><span class="tag ${tagc[i.action]}">${lab[i.action]}${change ? \': \' + change : \'\'}</span></div>\n        <div class="rvctl"><span class="hint" style="margin:0">${i.action === \'hold\' ? dose(i.sets, i.reps) : \'\'}</span></div>\n        ${i.action !== \'hold\' ? `<div class="fwhy"><span class="fwt">${I().spark}${esc(i.reason)}</span><div class="fab"><button class="facc${v === \'accept\' ? \' on\' : \'\'}" data-lv="dec" data-v="${i.exercise_id}|accept">${I().check}${v === \'accept\' ? \'Accepted\' : \'Accept\'}</button><button class="frej${v === \'reject\' ? \' on\' : \'\'}" data-lv="dec" data-v="${i.exercise_id}|reject">${v === \'reject\' ? \'Rejected\' : \'Reject\'}</button></div></div>` : `<div class="fwhy"><span class="fwt">${esc(i.reason)}</span></div>`}</div>`;\n    }).join(\'\');\n    return { rows, und, acc };\n  }\n\n  function planView() {   // first-plan draft\n    const P = PTL.cache.profile[PTL.pid], req = PTL.cache.request[PTL.pid];\n    if (!P) return errBox();\n    if (!req) return head(P.patient, \'list\') + `<div class="card pad" style="margin-top:20px"><h2>No draft waiting</h2><p class="sub">Fill in the clinic visit, then press "Draft plan with AI".</p><div class="foot"><button class="btn" data-lv="go" data-v="visit">Go to the visit</button></div></div>`;\n    const { rows, und, acc } = suggestions(req);\n    const src = srcLabel(req);\n    return head(P.patient, \'list\', \'Step 3 of 3: first plan\') + `<div class="grid2" style="margin-top:20px"><div class="card pad ai"><div class="aihd"><span class="spark">${I().spark}</span><h2>Week 1 plan</h2><span class="tag new" style="margin-left:auto">${src}</span></div>\n',
         '      const e = ex(i.exercise_id), v = PTL.dec[i.exercise_id];\n      const change = i.action === \'hold\' ? \'\' : `${i.before ? `<s>${i.before.replace(\' x \', \' × \')}</s> → ` : \'\'}${i.sets ? dose(i.sets, i.reps) : \'paused\'}`;\n      return `<div class="srow rvrow${i.action !== \'hold\' && v !== \'reject\' ? \' sugd\' : \'\'}"><img src="${e.img}" alt=""><div><b>${esc(e.n)}</b><span class="tag ${tagc[i.action]}">${lab[i.action]}${change ? \': \' + change : \'\'}</span></div>\n        ${i.intensity ? `<p class="hint">${esc(i.intensity)} · ${esc(i.setup)} · <a href="${esc(i.source_url)}" target="_blank" rel="noopener">Exercise source</a></p>` : \'\'}\n        <div class="rvctl"><span class="hint" style="margin:0">${i.action === \'hold\' ? dose(i.sets, i.reps) : \'\'}</span></div>\n        ${i.action !== \'hold\' ? `<div class="fwhy"><span class="fwt">${I().spark}${esc(i.reason)}</span><div class="fab"><button class="facc${v === \'accept\' ? \' on\' : \'\'}" data-lv="dec" data-v="${i.exercise_id}|accept">${I().check}${v === \'accept\' ? \'Accepted\' : \'Accept\'}</button><button class="frej${v === \'reject\' ? \' on\' : \'\'}" data-lv="dec" data-v="${i.exercise_id}|reject">${v === \'reject\' ? \'Rejected\' : \'Reject\'}</button></div></div>` : `<div class="fwhy"><span class="fwt">${esc(i.reason)}</span></div>`}</div>`;\n    }).join(\'\');\n    return { rows, und, acc };\n  }\n\n  function assessmentNeeded(P, req) {\n    return head(P.patient, \'list\') + `<div class="card pad sec2"><h2>Clinical review needed</h2>\n      <p>${esc(req.draft.note_for_pt)}</p><button class="btn" data-lv="go" data-v="visit">Open clinic visit</button></div>`;\n  }\n\n  function planView() {   // first-plan draft\n    const P = PTL.cache.profile[PTL.pid], req = PTL.cache.request[PTL.pid];\n    if (!P) return errBox();\n    if (!req) return head(P.patient, \'list\') + `<div class="card pad" style="margin-top:20px"><h2>No draft waiting</h2><p class="sub">Fill in the clinic visit, then press "Draft plan with AI".</p><div class="foot"><button class="btn" data-lv="go" data-v="visit">Go to the visit</button></div></div>`;\n    if (req.draft.review_required) return assessmentNeeded(P, req);\n    const { rows, und, acc } = suggestions(req);\n    const src = srcLabel(req);\n    return head(P.patient, \'list\', \'Step 3 of 3: first plan\') + `<div class="grid2" style="margin-top:20px"><div class="card pad ai"><div class="aihd"><span class="spark">${I().spark}</span><h2>Week 1 plan</h2><span class="tag new" style="margin-left:auto">${src}</span></div>\n'),
        ('    if (!P) return errBox();\n    if (!req) return head(P.patient, \'list\') + `<div class="card pad" style="margin-top:20px"><h2>No weekly draft waiting</h2><p class="sub">A draft is made at the start of each week from last week\'s sessions.</p><div class="foot"><button class="btn" data-lv="redraft">Draft from the latest sessions</button></div></div>`;\n    if (req.kind === \'first\') { A().S.view = \'plan\'; return planView(); }\n    const s = req.summary;\n    const days = []; for (let d = new Date(s.week.start + \'T12:00:00\'); d <= new Date(s.week.end + \'T12:00:00\'); d.setDate(d.getDate() + 1)) days.push(d.toISOString().slice(0, 10));\n    const byDate = Object.fromEntries(P.sessions.map(x => [x.date, x]));\n',
         '    if (!P) return errBox();\n    if (!req) return head(P.patient, \'list\') + `<div class="card pad" style="margin-top:20px"><h2>No weekly draft waiting</h2><p class="sub">A draft is made at the start of each week from last week\'s sessions.</p><div class="foot"><button class="btn" data-lv="redraft">Draft from the latest sessions</button></div></div>`;\n    if (req.kind === \'first\') { A().S.view = \'plan\'; return planView(); }\n    if (req.draft.review_required) return assessmentNeeded(P, req);\n    const s = req.summary;\n    const days = []; for (let d = new Date(s.week.start + \'T12:00:00\'); d <= new Date(s.week.end + \'T12:00:00\'); d.setDate(d.getDate() + 1)) days.push(d.toISOString().slice(0, 10));\n    const byDate = Object.fromEntries(P.sessions.map(x => [x.date, x]));\n'),
        ("    const num = k => v(k) === '' ? null : Number(v(k));\n    const lines = k => v(k) ? v(k).split('\\n').map(x => x.trim()).filter(Boolean) : null;\n    const sw = (v('sw_r') && v('sw_l')) ? { right: Number(v('sw_r')), left: Number(v('sw_l')) } : null;\n    return { bend_deg: num('bend_deg'), short_of_straight_deg: num('short_of_straight_deg'), rom_mode: v('rom_mode') || null,\n      quad_lag: v('quad_lag') === '' ? null : v('quad_lag') === 'yes', quad_grade: v('quad_grade') || null, pain_today: num('pain_today'),\n      tug_sec: num('tug_sec'), chair_stand_30s: num('chair_stand_30s'), walking_aid: v('walking_aid') || null, stairs: v('stairs') || null,\n      swelling_cm: sw, incision: v('incision') || null, goals_week6: lines('goals_week6'), precautions: lines('precautions'), notes: v('notes') || null };\n  }\n  async function saveFindings() {\n    const s = document.getElementById('lvsave');\n    try { await call('/api/visit/findings', { patient_id: PTL.pid, fields: collectFindings() }); if (s) s.innerHTML = I().check + 'Saved'; }\n    catch (e) { if (s) s.textContent = 'Not saved: ' + e.message; }\n  }\n\n  function busy(el) { el.disabled = true; el.textContent = 'Drafting… (AI can take ~10 s)'; }\n",
         "    const num = k => v(k) === '' ? null : Number(v(k));\n    const lines = k => v(k) ? v(k).split('\\n').map(x => x.trim()).filter(Boolean) : null;\n    const sw = (v('sw_r') && v('sw_l')) ? { right: Number(v('sw_r')), left: Number(v('sw_l')) } : null;\n    const knee = {pathway: v('kg_pathway')};\n    if (knee.pathway === 'general') {\n      for (const key of ['pattern', 'clearance', 'loading', 'bend_position']) knee[key] = v('kg_' + key);\n      knee.findings = Array.from(document.querySelectorAll('[data-kg-finding]:checked'), box => box.dataset.kgFinding);\n      knee.sets = num('kg_sets');\n      knee.reps = num('kg_reps');\n    }\n    return { ...(knee.pathway ? {knee_assessment:knee} : {}), bend_deg: num('bend_deg'), short_of_straight_deg: num('short_of_straight_deg'), rom_mode: v('rom_mode') || null,\n      quad_lag: v('quad_lag') === '' ? null : v('quad_lag') === 'yes', quad_grade: v('quad_grade') || null, pain_today: num('pain_today'),\n      tug_sec: num('tug_sec'), chair_stand_30s: num('chair_stand_30s'), walking_aid: v('walking_aid') || null, stairs: v('stairs') || null,\n      swelling_cm: sw, incision: v('incision') || null, goals_week6: lines('goals_week6'), precautions: lines('precautions'), notes: v('notes') || null };\n  }\n  let saveQueue = Promise.resolve();\n  function saveFindings() {\n    if (!document.getElementById('lvsave')) return Promise.resolve(false);\n    const pid = PTL.pid, fields = collectFindings();\n    saveQueue = saveQueue.catch(() => {}).then(() => sendFindings(pid, fields));\n    return saveQueue;\n  }\n  async function sendFindings(pid, fields) {\n    const s = document.getElementById('lvsave');\n    try { await call('/api/visit/findings', { patient_id: pid, fields }); if (s) s.innerHTML = I().check + 'Saved'; return true; }\n    catch (e) { if (s) s.textContent = 'Not saved: ' + e.message; return false; }\n  }\n\n  function busy(el) { el.disabled = true; el.textContent = 'Drafting… (AI can take ~10 s)'; }\n"),
        ("        case 'addsave': {\n          const name = document.getElementById('ap_name').value.trim();\n          if (!name) { document.getElementById('ap_name').focus(); return; }\n          await call('/api/patients/add', { name, phone: document.getElementById('ap_phone').value, surgery_date: document.getElementById('ap_date').value,\n            side: document.getElementById('ap_side').value, surgery: 'Knee replacement' });\n          PTL.addOpen = false; return open('list');\n        }\n        case 'dec': { const [id, v] = d.v.split('|'); PTL.dec[id] = PTL.dec[id] === v ? undefined : v; PTL.note = (document.getElementById('lvnote') || {}).value ?? PTL.note; S._keep = true; return A().render(); }\n        case 'firstdraft': { busy(el); clearTimeout(saveT); if (document.getElementById('lvsave')) await saveFindings();\n          await call('/api/first-plan/draft', { patient_id: PTL.pid }); return open('plan'); }\n        case 'redraft': { busy(el); const r = PTL.cache.request[PTL.pid];\n          await call('/api/adjustment/draft', { patient_id: PTL.pid, week_end: r ? r.week_end : undefined }); return open('review'); }\n        case 'send': case 'keep': {\n",
         "        case 'addsave': {\n          const name = document.getElementById('ap_name').value.trim();\n          if (!name) { document.getElementById('ap_name').focus(); return; }\n          const general = document.getElementById('ap_path').value === 'general';\n          await call('/api/patients/add', { name, phone: document.getElementById('ap_phone').value, surgery_date: general ? '' : document.getElementById('ap_date').value,\n            side: document.getElementById('ap_side').value, surgery: general ? 'General knee' : 'Knee replacement' });\n          PTL.addOpen = false; return open('list');\n        }\n        case 'dec': { const [id, v] = d.v.split('|'); PTL.dec[id] = PTL.dec[id] === v ? undefined : v; PTL.note = (document.getElementById('lvnote') || {}).value ?? PTL.note; S._keep = true; return A().render(); }\n        case 'firstdraft': { busy(el); clearTimeout(saveT); if (document.getElementById('lvsave') && !(await saveFindings())) { el.disabled = false; el.textContent = 'Draft plan with AI'; return; }\n          const result = await call('/api/first-plan/draft', { patient_id: PTL.pid });\n          return open(result.draft.kind === 'weekly' ? 'review' : 'plan'); }\n        case 'redraft': { busy(el); const r = PTL.cache.request[PTL.pid];\n          await call('/api/adjustment/draft', { patient_id: PTL.pid, week_end: r ? r.week_end : undefined }); return open('review'); }\n        case 'send': case 'keep': {\n"),
        ("    dev.addEventListener('change', ev => { if (ev.target.dataset.lvf !== undefined) { clearTimeout(saveT); saveFindings(); } }, true);\n",
         "    dev.addEventListener('change', ev => {\n      if (ev.target.id === 'ap_path') document.getElementById('ap_surgical_date').hidden = ev.target.value === 'general';\n      if (ev.target.id === 'lv_kg_pathway') document.getElementById('lv_kg_details').hidden = ev.target.value !== 'general';\n      if (ev.target.dataset.lvf !== undefined) { clearTimeout(saveT); saveFindings(); } }, true);\n"),
    ],
    'frontend/patient-live.js': [
        ('  };\n\n  const CR = { live: false, pid: null, home: null, ck: null, sent: {} };\n  window.CR = CR;\n\n  async function call(path, body) {\n',
         "  };\n\n  const CR = { live: false, pid: null, home: null, ck: null, sent: {} };\n  CR.generalUrgent = ['New locking or giving way', 'Cannot bear weight or straighten after injury', 'Severe new swelling or deformity'];\n  window.CR = CR;\n\n  async function call(path, body) {\n"),
        ("    CR.ck.then(id => id && call('/api/result', Object.assign({ checkin_id: id }, body))).catch(err => console.warn('result', err));\n  }\n\n  CR.beforeClick = function (b, d) {\n    if (!CR.live) return;\n    const app = A(), S = app.S;\n    if (b.id === 'painNext') {\n      const sym = S.symBefore || [];\n      const worry = { symptoms: sym.filter(x => NAMED.includes(x)).map(x => x.toLowerCase()), other: sym.includes('Something else') ? (S.symBeforeO || 'Something else') : '' };\n      CR.sent = {};\n      CR.ck = call('/api/checkin', { patient_id: CR.pid, pain_today: S.painBefore, worry }).then(r => {\n        CR.ckDate = r.date;\n        // The server decides today's dose. Fix the screen if its rule disagrees.\n        const S2 = A().S;\n        const mode = r.action === 'rest' ? 'rest' : r.action === 'as_planned' ? 'full' : r.exercises[0] && r.exercises[0].target_reps / r.exercises[0].planned_reps <= 0.55 ? 'l50' : 'l70';\n        if (mode === 'rest' && S2.screen !== 'rest') A().go('rest');\n        else if (mode !== 'rest' && S2.mode !== mode) { S2.mode = mode; if (S2.screen === 'todayPlan') A().render(); }\n        return r.checkin_id;\n      }).catch(err => { console.warn('checkin', err); return null; });\n      return;\n    }\n    if (b.id === 'afterNext') { postResult(S.exIdx); return; }\n    if (b.id === 'wrapNext') {\n",
         "    CR.ck.then(id => id && call('/api/result', Object.assign({ checkin_id: id }, body))).catch(err => console.warn('result', err));\n  }\n\n  document.addEventListener('change', ev => {\n    if (ev.target.id === 'kneeMorning') A().S.kneeMorning = ev.target.value;\n  });\n  CR.beforeClick = function (b, d) {\n    if (!CR.live) return;\n    const app = A(), S = app.S;\n    if (b.id === 'painNext') {\n      const sym = S.symBefore || [];\n      const worry = { symptoms: sym.filter(x => NAMED.includes(x) || CR.generalUrgent.includes(x)).map(x => x.toLowerCase()), other: sym.includes('Something else') ? (S.symBeforeO || 'Something else') : '' };\n      CR.sent = {};\n      const general = CR.home && CR.home.care_pathway === 'general';\n      if (general) b.disabled = true;\n      CR.ck = call('/api/checkin', { patient_id: CR.pid, pain_today: S.painBefore, worry,\n        next_morning: general ? (S.kneeMorning || 'unknown') : undefined }).then(r => {\n        CR.ckDate = r.date;\n        // The server decides today's dose. Fix the screen if its rule disagrees.\n        const S2 = A().S;\n        const mode = r.action === 'rest' ? 'rest' : r.action === 'as_planned' ? 'full' : r.exercises[0] && r.exercises[0].target_reps / r.exercises[0].planned_reps <= 0.55 ? 'l50' : 'l70';\n        if (general) {\n          if (S2.screen !== 'painBefore') return r.checkin_id;\n          S2.mode = mode; S2.kneeHold = r.message; S2.exIdx = 0; S2.results = [];\n          A().go(mode === 'rest' ? 'rest' : 'todayPlan');\n        } else if (mode === 'rest' && S2.screen !== 'rest') A().go('rest');\n        else if (mode !== 'rest' && S2.mode !== mode) { S2.mode = mode; if (S2.screen === 'todayPlan') A().render(); }\n        return r.checkin_id;\n      }).catch(err => { if (general) { b.disabled = false; CR.toast('Could not confirm today’s plan. Please try again.', true); } console.warn('checkin', err); return null; });\n      return general;\n    }\n    if (b.id === 'afterNext') { postResult(S.exIdx); return; }\n    if (b.id === 'wrapNext') {\n"),
    ],
    'frontend/patient.html': [
        ('const SYMIC={\'Swelling\':\'<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="3"/>\',\'Redness or warmth\':\'<path d="M12 3c3 4 5 6.5 5 9.5a5 5 0 0 1-10 0C7 9.5 9 7 12 3z"/>\',\'Fever\':\'<path d="M10 14V5a2 2 0 0 1 4 0v9a4 4 0 1 1-4 0z"/><path d="M12 9v7"/>\',\'Calf pain\':\'<path d="M9 3v8c0 3 2 5 2 8v2h4"/><path d="M14 4l2 2M16 9l2 1"/>\',\'Something else\':\'<path d="M4 20h4L19 9l-4-4L4 16z"/>\'};\nfunction symChoices(k){const sel=S[k],none=sel.length===0&&S[k+\'T\'],yes=S[k+\'Y\']||sel.length>0;\n let h=\'<div class="yn2"><button class="ynb yes" data-symyes="\'+k+\'" aria-pressed="\'+(yes&&!none)+\'">\'+ic(\'<circle cx="12" cy="12" r="9"/><path d="M8.5 16.5c1-1.2 2.2-1.8 3.5-1.8s2.5.6 3.5 1.8"/><path d="M9 10v.5M15 10v.5" stroke-width="2.8"/><path d="M7.5 8.7l2.5-1.2M16.5 8.7L14 7.5"/>\',40,2)+\'Yes, something\\\'s worrying</button><button class="ynb no" data-symno="\'+k+\'" aria-pressed="\'+none+\'">\'+ic(\'<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5c1 1.4 2.2 2 3.5 2s2.5-.6 3.5-2"/><path d="M9 9.5v.5M15 9.5v.5" stroke-width="2.8"/>\',40,2)+\'No, I feel okay</button></div>\';\n if(yes&&!none){h+=\'<p class="q" style="font-size:22px;margin:24px 0 12px">What is it? Pick all that apply.</p><div class="symgrid">\';SYMPTOMS.concat([\'Something else\']).forEach(s=>{h+=\'<button class="symc" data-sym="\'+k+\'" data-v="\'+s+\'" aria-pressed="\'+sel.includes(s)+\'"><span class="symi">\'+ic(SYMIC[s],34,2.2)+\'</span>\'+s+\'</button>\';});h+=\'</div>\';\n  if(sel.includes(\'Something else\'))h+=fup(\'<label class="q" for="\'+k+\'O" style="font-size:22px">Tell us what it is</label><input id="\'+k+\'O" data-symo="\'+k+\'" value="\'+esc(S[k+\'O\']||\'\')+\'" placeholder="For example: my knee feels stiff" style="width:100%;box-sizing:border-box;height:64px;padding:0 18px;border-radius:var(--r-md);border:1px solid var(--border-strong);font:inherit;font-size:22px;background:var(--raised)">\');}\n return h;}\nV.painBefore=()=>{const ok=S.painBefore!==null&&S.symBeforeT;return `<div class="scr"><div class="top">${backBtn(\'tab:today\')}<span style="flex:1"></span><button class="exitb" data-sexit aria-label="End session">End</button></div>\n <h1 style="margin:8px 0 0">Before you start</h1><div style="margin-top:24px"><p class="q">How does your knee feel right now?</p>${painGrid(\'painBefore\')}</div>\n <div style="margin-top:40px"><p class="q">Is anything worrying you today?</p>${symChoices(\'symBefore\')}</div>\n <div class="foot">${sos(true)}<button class="main" id="painNext" ${ok?\'\':\'disabled\'}>Continue</button></div></div>`;};\nV.rest=()=>`<div class="scr"><div class="top">${backBtn(\'painBefore\')}</div><div style="margin-top:20px"><h1>Let\'s rest today</h1><p class="sub">Your knee needs a break. We\'ve let your PT know, and they may call you.</p></div>\n <div class="ban urg" style="margin-top:40px"><div class="ic">${I.bang}</div><div><b>Call 911</b> if you have chest pain, trouble breathing, or your leg is suddenly very swollen.</div></div>\n <div class="ban ok" style="margin-top:16px"><div class="ic">${I.check}</div><div><b>Today won\'t break your streak.</b> Rest days count when your knee needs them.</div></div>\n <div class="foot"><span></span><button class="main" data-tab="today">Back to home</button></div></div>`;\n',
         'const SYMIC={\'Swelling\':\'<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="3"/>\',\'Redness or warmth\':\'<path d="M12 3c3 4 5 6.5 5 9.5a5 5 0 0 1-10 0C7 9.5 9 7 12 3z"/>\',\'Fever\':\'<path d="M10 14V5a2 2 0 0 1 4 0v9a4 4 0 1 1-4 0z"/><path d="M12 9v7"/>\',\'Calf pain\':\'<path d="M9 3v8c0 3 2 5 2 8v2h4"/><path d="M14 4l2 2M16 9l2 1"/>\',\'Something else\':\'<path d="M4 20h4L19 9l-4-4L4 16z"/>\'};\nfunction symChoices(k){const sel=S[k],none=sel.length===0&&S[k+\'T\'],yes=S[k+\'Y\']||sel.length>0;\n let h=\'<div class="yn2"><button class="ynb yes" data-symyes="\'+k+\'" aria-pressed="\'+(yes&&!none)+\'">\'+ic(\'<circle cx="12" cy="12" r="9"/><path d="M8.5 16.5c1-1.2 2.2-1.8 3.5-1.8s2.5.6 3.5 1.8"/><path d="M9 10v.5M15 10v.5" stroke-width="2.8"/><path d="M7.5 8.7l2.5-1.2M16.5 8.7L14 7.5"/>\',40,2)+\'Yes, something\\\'s worrying</button><button class="ynb no" data-symno="\'+k+\'" aria-pressed="\'+none+\'">\'+ic(\'<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5c1 1.4 2.2 2 3.5 2s2.5-.6 3.5-2"/><path d="M9 9.5v.5M15 9.5v.5" stroke-width="2.8"/>\',40,2)+\'No, I feel okay</button></div>\';\n if(yes&&!none){h+=\'<p class="q" style="font-size:22px;margin:24px 0 12px">What is it? Pick all that apply.</p><div class="symgrid">\';SYMPTOMS.concat(k === \'symBefore\' && window.CR && CR.home && CR.home.care_pathway === \'general\' ? CR.generalUrgent : [], [\'Something else\']).forEach(s=>{h+=\'<button class="symc" data-sym="\'+k+\'" data-v="\'+s+\'" aria-pressed="\'+sel.includes(s)+\'"><span class="symi">\'+ic(SYMIC[s] || SYMIC[\'Something else\'],34,2.2)+\'</span>\'+s+\'</button>\';});h+=\'</div>\';\n  if(sel.includes(\'Something else\'))h+=fup(\'<label class="q" for="\'+k+\'O" style="font-size:22px">Tell us what it is</label><input id="\'+k+\'O" data-symo="\'+k+\'" value="\'+esc(S[k+\'O\']||\'\')+\'" placeholder="For example: my knee feels stiff" style="width:100%;box-sizing:border-box;height:64px;padding:0 18px;border-radius:var(--r-md);border:1px solid var(--border-strong);font:inherit;font-size:22px;background:var(--raised)">\');}\n return h;}\nV.painBefore=()=>{const ok=S.painBefore!==null&&S.symBeforeT;return `<div class="scr"><div class="top">${backBtn(\'tab:today\')}<span style="flex:1"></span><button class="exitb" data-sexit aria-label="End session">End</button></div>\n <h1 style="margin:8px 0 0">Before you start</h1><div style="margin-top:24px"><p class="q">How does your knee feel right now?</p>${painGrid(\'painBefore\')}</div>\n ${window.CR && CR.home && CR.home.care_pathway === \'general\' ? `<div style="margin-top:20px"><label class="q" for="kneeMorning">How did your knee feel the morning after your last exercise session?</label><select id="kneeMorning" style="display:block;font:inherit;padding:8px"><option value="unknown">First session or unsure</option><option value="baseline"${S.kneeMorning === \'baseline\' ? \' selected\' : \'\'}>Usual level or better</option><option value="worse"${S.kneeMorning === \'worse\' ? \' selected\' : \'\'}>Worse than usual</option></select></div>` : \'\'}\n <div style="margin-top:40px"><p class="q">Is anything worrying you today?</p>${symChoices(\'symBefore\')}</div>\n <div class="foot">${sos(true)}<button class="main" id="painNext" ${ok?\'\':\'disabled\'}>Continue</button></div></div>`;};\nV.rest=()=>`<div class="scr"><div class="top">${backBtn(\'painBefore\')}</div><div style="margin-top:20px"><h1>Let\'s rest today</h1><p class="sub">${esc(S.kneeHold || "Your knee needs a break. We\'ve let your PT know, and they may call you.")}</p></div>\n <div class="ban urg" style="margin-top:40px"><div class="ic">${I.bang}</div><div><b>Call 911</b> if you have chest pain, trouble breathing, or your leg is suddenly very swollen.</div></div>\n <div class="ban ok" style="margin-top:16px"><div class="ic">${I.check}</div><div><b>Today won\'t break your streak.</b> Rest days count when your knee needs them.</div></div>\n <div class="foot"><span></span><button class="main" data-tab="today">Back to home</button></div></div>`;\n'),
        (" if(scr==='painBefore'){S.painBefore=null;S.symBefore=[];S.symBeforeT=false;}\n",
         " if(scr==='painBefore'){S.kneeMorning='unknown';S.kneeHold='';S.painBefore=null;S.symBefore=[];S.symBeforeT=false;}\n"),
        ("\ndev.addEventListener('click',ev=>{\n const b=ev.target.closest('button');if(!b)return;const d=b.dataset;\n if(window.CR)CR.beforeClick(b,d); /* CoRehab: send to the server (patient-live.js) */\n if(d.tab){toTab(d.tab);return;}\n if(d.obstart!==undefined){S.ob=obFresh();S.obpain=null;go('onboard');return;}\n if(d.obscan!==undefined){S.ob.scanning=true;render();later(()=>{S.ob.scanning=false;S.ob.joined=true;render();},1600);return;}\n",
         "\ndev.addEventListener('click',ev=>{\n const b=ev.target.closest('button');if(!b)return;const d=b.dataset;\n if(window.CR && CR.beforeClick(b,d) === true)return; /* CoRehab: send to the server (patient-live.js) */\n if(d.tab){toTab(d.tab);return;}\n if(d.obstart!==undefined){S.ob=obFresh();S.obpain=null;go('onboard');return;}\n if(d.obscan!==undefined){S.ob.scanning=true;render();later(()=>{S.ob.scanning=false;S.ob.joined=true;render();},1600);return;}\n"),
    ],
}

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Connect the general-knee rules to CoRehab.")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--connect", action="store_true", help="Apply the integration to this CoRehab copy")
    actions.add_argument("--check", action="store_true", help="Check compatibility without changing the app")
    args = parser.parse_args()
    try:
        connect_app(check_only=args.check)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
