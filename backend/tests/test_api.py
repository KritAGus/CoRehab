"""End-to-end API tests: start the real server on a temp database and call it over HTTP.

Run from backend/:  python -m pytest -q tests
"""
import datetime as dt
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


@pytest.fixture(scope="module")
def api():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    os.environ.update({"REHAB_DB": db, "DEMO_SEED": "1", "DEMO_RESET": "1"})
    import test_server
    server = test_server.start("127.0.0.1", 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers["Content-Type"] == "application/json" else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    yield call
    server.shutdown()


def test_static_and_safety(api):
    assert api("/")[0] == 200
    assert api("/patient.html")[0] == 200 and api("/pt.html")[0] == 200
    assert api("/img/walter.jpg")[0] == 200
    assert api("/../backend/rehab_node.py")[0] == 404
    assert api("/test")[0] == 200


def test_patient_list_groups(api):
    code, out = api("/api/patients")
    assert code == 200
    groups = {p["patient_id"]: p["group"] for p in out["patients"]}
    assert groups == {"george": "urgent", "walter": "ready_for_review", "dorothy": "gone_quiet", "maria": "new",
                      "frank": "on_track", "ruth": "on_track", "thomas": "discharged"}
    w = next(p for p in out["patients"] if p["patient_id"] == "walter")
    assert w["data_days_30"] > 0 and (w["pain_after_this_week"] or w["pain_after_last_week"]) is not None


def test_session_flow(api):
    _, j = api("/api/join", {"code": "riv284"})
    pid = j["patient_id"]
    assert pid == "walter"
    _, h = api(f"/api/home?patient_id={pid}")
    assert h["phase"] in ("active", "plan_updated") and h["plan"]
    code, ck = api("/api/checkin", {"patient_id": pid, "pain_today": 4, "worry": {"symptoms": [], "other": "a bit stiff"},
                                    "date": "2030-01-07"})
    assert code == 200 and ck["action"] == "reduced" and ck["exercises"][0]["target_reps"] < ck["exercises"][0]["planned_reps"]
    ex = ck["exercises"][0]
    assert api("/api/result", {"checkin_id": ck["checkin_id"], "exercise_id": ex["exercise_id"], "sets_done": 3,
                               "reps_done": 21, "feeling": "great"})[0] == 422
    code, r = api("/api/result", {"checkin_id": ck["checkin_id"], "exercise_id": ex["exercise_id"], "sets_done": 3,
                                  "reps_done": 21, "feeling": "too_hard", "pain_after": 7, "note": "sharp"})
    assert code == 200 and r["alert_sent"]
    code, e = api("/api/session/end", {"checkin_id": ck["checkin_id"], "pain_end": 5,
                                       "bothered": [{"exercise_id": ex["exercise_id"], "reason": "Pain"}], "note": "ok"})
    assert code == 200 and not e["urgent"]
    _, prof = api(f"/api/profile?patient_id={pid}")
    s = prof["sessions"][0]
    assert s["date"] == "2030-01-07" and s["bothered"][0]["reason"] == "pain" and s["exercises"][0]["feeling"] == "too_hard"
    assert any("Worried about" in a["reason"] for a in prof["alerts"])


def test_red_flag_symptom_means_rest_and_urgent(api):
    code, ck = api("/api/checkin", {"patient_id": "frank", "pain_today": 2, "worry": {"symptoms": ["calf pain"]},
                                    "date": "2030-01-08"})
    assert code == 200 and ck["action"] == "rest" and ck["urgent"]
    assert api("/api/checkin", {"patient_id": "frank", "pain_today": 2, "worry": {"symptoms": ["headache"]}})[0] == 400


def test_stop_early(api):
    _, ck = api("/api/checkin", {"patient_id": "ruth", "pain_today": 3, "date": "2030-01-09"})
    code, out = api("/api/session/stop", {"checkin_id": ck["checkin_id"], "reason": "Pain", "pain": 8})
    assert code == 200 and out["urgent"]


def test_first_plan_flow(api):
    _, added = api("/api/patients/add", {"name": "Ann Test", "phone": "1", "surgery_date": (dt.date.today() - dt.timedelta(days=10)).isoformat()})
    pid = added["patient_id"]
    assert added["status"] == "intake_sent"
    api("/api/intake", {"patient_id": pid, "name": "Ann Test", "answers": {"goal": "home", "side": "right"}})
    code, f = api("/api/visit/findings", {"patient_id": pid, "fields": {"bend_deg": 80, "quad_lag": "yes"}})
    assert code == 200
    code, f = api("/api/visit/findings", {"patient_id": pid, "fields": {"tug_sec": 15, "short_of_straight_deg": 6}})
    assert f["findings"]["bend_deg"] == 80 and f["findings"]["tug_sec"] == 15          # autosave merges
    assert api("/api/visit/findings", {"patient_id": pid, "fields": {"nope": 1}})[0] == 400
    _, d = api("/api/first-plan/draft", {"patient_id": pid})
    ids = [i["exercise_id"] for i in d["draft"]["items"]]
    assert {"heel_slide", "straight_leg_raise", "seated_knee_extension"} <= set(ids)
    assert all(i["reason"] for i in d["draft"]["items"])
    assert "step_up" not in ids                                                         # not allowed in week 1
    _, lst = api("/api/patients")
    assert next(p for p in lst["patients"] if p["patient_id"] == pid)["group"] == "ready_for_review"
    code, res = api("/api/adjustment/decide", {"request_id": d["request_id"], "approve": True,
                                               "decisions": {ids[0]: "reject"}, "pt_name": "Dana Reyes, PT"})
    assert code == 200 and ids[0] not in [p["exercise_id"] for p in res["plan"]] and len(res["plan"]) == len(ids) - 1
    _, h = api(f"/api/home?patient_id={pid}")
    assert h["phase"] == "plan_updated" and all(c["kind"] == "new" for c in h["plan_change"]["changes"])


def test_weekly_review_per_suggestion(api):
    _, r = api("/api/request?patient_id=walter&status=pending")
    req = r["request"]
    added = [i["exercise_id"] for i in req["draft"]["items"] if i["action"] == "add"]
    assert added
    code, res = api("/api/adjustment/decide", {"request_id": req["request_id"], "approve": True,
                                               "decisions": {added[0]: "reject"}})
    assert code == 200 and added[0] not in [p["exercise_id"] for p in res["plan"]]


def test_cannot_approve_empty_plan(api):
    _, d = api("/api/first-plan/draft", {"patient_id": "maria"})
    rejects = {i["exercise_id"]: "reject" for i in d["draft"]["items"]}
    code, out = api("/api/adjustment/decide", {"request_id": d["request_id"], "approve": True, "decisions": rejects})
    assert code == 400 and "no exercises" in out["errors"][0]


def test_progress_visit_notes_calls_discharge(api):
    _, p = api("/api/progress?patient_id=walter")
    assert p["streak"] > 0 and p["medals"][0]["key"] == "bronze" and len(p["this_week"]) == 7
    _, v = api("/api/visits/schedule", {"patient_id": "dorothy", "date": "2030-02-01", "time": "09:30", "reason": "Check bend"})
    _, h = api("/api/home?patient_id=dorothy")
    assert h["next_visit"]["visit_id"] == v["visit_id"]
    api("/api/pt-notes", {"patient_id": "dorothy", "text": "Lives alone; call first."})
    _, prof = api("/api/profile?patient_id=dorothy")
    assert prof["pt_private_notes"].startswith("Lives alone")
    api("/api/calls", {"patient_id": "george", "minutes": 12, "agreed": "Ice, rest, visit Thursday"})
    _, lst = api("/api/patients")
    assert next(x for x in lst["patients"] if x["patient_id"] == "george")["group"] != "urgent"   # call closes alerts
    _, dis = api("/api/discharge", {"patient_id": "frank", "note": "Well done", "community_program": True})
    assert dis["discharge"]["community_program"] and api("/api/home?patient_id=frank")[1]["phase"] == "discharged"


def test_consent_setup_rest_days(api):
    assert api("/api/consent", {"patient_id": "maria", "items": ["camera", "data", "billing"]})[0] == 200
    _, s = api("/api/setup-check", {"patient_id": "maria", "safe": False, "details": "no sturdy chair"})
    assert s["urgent"]
    assert api("/api/rest-days", {"patient_id": "maria", "days": ["Wed", "Sun"]})[1]["rest_days"] == ["Wed", "Sun"]
    assert api("/api/rest-days", {"patient_id": "maria", "days": ["Funday"]})[0] == 400
    _, ws = api("/api/weekly-summary?patient_id=walter")
    assert ws["planned_days"] == 5 and "feeling_counts" in ws["exercises"][0]


def test_register_then_intake_shows_as_new(api):
    _, r = api("/api/register", {"name": "Nina Park", "dob": "1952-01-02", "phone": "5"})
    pid = r["patient_id"]
    assert api(f"/api/home?patient_id={pid}")[1]["phase"] == "intake"
    api("/api/intake", {"patient_id": pid, "name": "Nina Park", "answers": {"goal": "walk", "side": "left",
                                                                         "surgery_date": "2026-11-10"}})
    assert api(f"/api/home?patient_id={pid}")[1]["phase"] == "waiting_for_visit"
    row = next(p for p in api("/api/patients")[1]["patients"] if p["patient_id"] == pid)
    assert row["group"] == "new" and row["next"].startswith("Intake ready")


def test_parallel_requests(api):
    """The apps load several things at once; the shared database connection must cope."""
    import concurrent.futures as cf
    paths = ["/api/home?patient_id=walter", "/api/progress?patient_id=walter", "/api/patients",
             "/api/profile?patient_id=ruth"] * 25
    with cf.ThreadPoolExecutor(16) as ex:
        codes = list(ex.map(lambda p: api(p)[0], paths))
    assert codes.count(200) == len(paths)


def test_messages_both_ways(api):
    _, before = api("/api/messages?patient_id=walter")
    n = len(before["messages"])
    assert before["unread"] >= 1                                     # Dana's last message is unread for Walter
    assert api("/api/messages", {"patient_id": "walter", "sender": "patient", "text": "Is warmth normal?"})[0] == 200
    _, th = api("/api/threads")
    w = next(t for t in th["threads"] if t["patient_id"] == "walter")
    assert w["unread"] == 1 and w["last"]["text"] == "Is warmth normal?"
    _, pt_view = api("/api/messages?patient_id=walter&reader=pt")    # PT opens it -> read
    assert len(pt_view["messages"]) == n + 1
    assert next(t for t in api("/api/threads")[1]["threads"] if t["patient_id"] == "walter")["unread"] == 0
    api("/api/messages", {"patient_id": "walter", "sender": "pt", "text": "Yes, a little warmth is normal."})
    _, pv = api("/api/messages?patient_id=walter&reader=patient")
    assert pv["messages"][-1]["sender"] == "pt" and api("/api/messages?patient_id=walter")[1]["unread"] == 0
    assert api("/api/messages", {"patient_id": "walter", "sender": "patient", "text": "  "})[0] == 400


def test_login_by_name_or_username(api):
    assert api("/api/login", {"name": "walter"})[1]["patient_id"] == "walter"
    assert api("/api/login", {"name": "  ruth   coleman "})[1]["patient_id"] == "ruth"
    assert api("/api/login", {"name": "Frank"})[1]["patient_id"] == "frank"           # unique first name
    assert api("/api/login", {"name": "Nobody Here"})[0] == 400
    _, r = api("/api/register", {"name": "Walter Miller"})                             # a second Walter Miller
    assert r["username"] == "waltermiller"
    code, out = api("/api/login", {"name": "Walter Miller"})
    assert code == 400 and "waltermiller" in out["errors"][0]                           # ambiguous -> use username
    assert api("/api/login", {"name": "waltermiller"})[1]["patient_id"] == "waltermiller"


def test_rules_draft_says_why(api):
    _, d = api("/api/first-plan/draft", {"patient_id": "maria"})
    assert d["draft"]["source"] == "rules" and d["draft"]["ai_note"]                    # no key in tests


def test_home_shows_visits_goals_and_end_note(api):
    _, v = api("/api/visits/schedule", {"patient_id": "ruth", "date": "2030-03-04", "time": "14:00", "reason": "Check swelling"})
    _, h = api("/api/home?patient_id=ruth")
    assert any(x["visit_id"] == v["visit_id"] and x["time"] == "14:00" for x in h["visits"])
    _, w = api("/api/home?patient_id=walter")
    assert w["goals"] and w["precautions"] and "visits" in w
    _, prof = api("/api/profile?patient_id=walter")
    assert prof["sessions"][0]["note"] == "ok"          # end-of-session note from test_session_flow reaches the PT


def test_unsafe_setup_makes_urgent_alert(api):
    _, r = api("/api/register", {"name": "Omar Lee", "dob": "1950-05-05", "phone": "7"})
    _, s = api("/api/setup-check", {"patient_id": r["patient_id"], "safe": False, "details": "Not true: floor is clear"})
    assert s["urgent"]
    _, prof = api(f"/api/profile?patient_id={r['patient_id']}")
    assert any(a["tier"] == "urgent" and "floor is clear" in a["reason"] for a in prof["alerts"])
    _, ok = api("/api/setup-check", {"patient_id": "walter", "safe": True})
    assert not ok["urgent"]


def test_schedule_shows_visits_and_calls(api):
    _, v = api("/api/visits/schedule", {"patient_id": "dorothy", "date": "2030-04-03", "time": "11:15", "reason": "Week 6 check"})
    _, s = api("/api/schedule?start=2030-04-01&end=2030-04-07")
    e = next(x for x in s["events"] if x["visit_id"] == v["visit_id"])
    assert e["name"] == "Dorothy Hayes" and e["time"] == "11:15" and e["type"] == "In clinic"
    _, s = api("/api/schedule")
    assert s["calls_to_make"] and s["calls_to_make"][0]["due"] in ("Today",) or all(c["k"] != "urg" for c in s["calls_to_make"])
    pid = s["calls_to_make"][0]["patient_id"]
    api("/api/calls", {"patient_id": pid, "minutes": 9, "agreed": "All good"})
    _, s2 = api("/api/schedule")
    assert pid not in [c["patient_id"] for c in s2["calls_to_make"]] and s2["call_history"][0]["agreed"] == "All good"
