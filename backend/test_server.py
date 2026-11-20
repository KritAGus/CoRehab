"""Knee rehab app skeleton (3 tabs) + the detection -> node test bench. Standard library only.

Tabs: 1 Intake (patient answers, PT reads summary and enters the plan)
      2 Daily exercise (pain check, detection result -> node; camera to be added)
      3 Plan (patient sees plan and changes; PT reviews the weekly draft)

Run:  python test_server.py      then open http://127.0.0.1:8000
      (Colab: see rehab_colab.ipynb)

Endpoint the detection node calls after each exercise:
  POST /api/result
  {"checkin_id": 1, "exercise_id": "mini_squat", "sets_done": 3, "reps_done": 21,
   "form_scores": [0.9, 0.8, ...], "error_counts": {"trunk_lean": 2}, "pain_after": 4}
"""
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import rehab_node as rn

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("REHAB_DB", os.path.join(HERE, "rehab_test.db"))  # Colab: set to a Drive path to keep data
con = rn.connect(DB_PATH)
PORT = int(os.environ.get("PORT", 8000))
ALLOW_ORIGIN = os.environ.get("ALLOW_ORIGIN", "*")  # CORS, so a separate frontend (Figma Make/React) can call the API


def check_detection(body):
    """Return a list of problems with a detection result; empty list = valid."""
    errs = []
    for key in ("checkin_id", "exercise_id", "sets_done", "reps_done", "form_scores"):
        if key not in body:
            errs.append(f"missing '{key}'")
    for key in ("checkin_id", "sets_done", "reps_done"):
        if key in body and (not isinstance(body[key], int) or body[key] < 0):
            errs.append(f"'{key}' must be a whole number >= 0")
    fs = body.get("form_scores")
    if fs is not None:
        if not isinstance(fs, list) or not all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in fs):
            errs.append("'form_scores' must be a list of numbers from 0 to 1, one per rep")
        elif "reps_done" in body and isinstance(body["reps_done"], int) and len(fs) != body["reps_done"]:
            errs.append(f"'form_scores' has {len(fs)} values but reps_done is {body['reps_done']} (expected one per rep)")
    ec = body.get("error_counts", {})
    if not isinstance(ec, dict) or not all(isinstance(v, int) and v >= 0 for v in ec.values()):
        errs.append("'error_counts' must be an object of error type -> count")
    pa = body.get("pain_after")
    if pa is not None and (not isinstance(pa, int) or not 0 <= pa <= 10):
        errs.append("'pain_after' must be a whole number 0-10")
    return errs


def rows(sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):  # CORS preflight
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def log_message(self, *args):  # keep the console quiet
        pass

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "test_page.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        pid = q.get("patient_id", "")
        if url.path == "/api/exercises":
            return self._send(200, rn.EXERCISES)
        if url.path == "/api/goals":
            return self._send(200, [{"key": k, "label": g["label"]} for k, g in rn.GOALS.items()])
        if url.path == "/api/intake":
            return self._send(200, {"intake": rn.latest_intake(con, pid)})
        if url.path == "/api/plan":
            p = con.execute("SELECT * FROM patients WHERE patient_id=?", (pid,)).fetchone()
            return self._send(200, {"patient": dict(p) if p else None, "plan": rn.current_plan(con, pid)})
        if url.path == "/api/adjustment":
            return self._send(200, {"pending": rn.latest_request(con, pid, "pending"),
                                    "plan": rn.current_plan(con, pid)})
        if url.path == "/api/myplan":
            p = con.execute("SELECT * FROM patients WHERE patient_id=?", (pid,)).fetchone()
            return self._send(200, {"patient": dict(p) if p else None, "plan": rn.current_plan(con, pid),
                                    "change": rn.plan_change_for_patient(con, pid)})
        if url.path == "/api/db":
            pid = q.get("patient_id", "")
            return self._send(200, {
                "logs": rows("SELECT log_id, date, exercise_id, planned_sets, planned_reps, target_reps, sets_done,"
                             " reps_done, form_score, errors_json, pain_after FROM exercise_logs"
                             " WHERE patient_id=? ORDER BY log_id DESC LIMIT 50", (pid,)),
                "alerts": rows("SELECT date, tier, reason FROM alerts WHERE patient_id=? ORDER BY alert_id DESC LIMIT 20",
                               (pid,)),
            })
        self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = self._body()
        except json.JSONDecodeError as e:
            return self._send(400, {"ok": False, "errors": [f"invalid JSON: {e}"]})
        path = urlparse(self.path).path
        try:
            if path == "/api/test-patient":
                pid = body.get("patient_id") or "u_test"
                rn.add_patient(con, pid, "Test patient", "2026-10-28", "left", "Climb stairs at home")
                rn.set_plan(con, pid, rn.DEMO_PLAN, approved_by="Test PT")
                return self._send(200, {"ok": True, "plan": rn.current_plan(con, pid)})
            if path == "/api/intake":
                summary = rn.save_intake(con, body["patient_id"], body.get("name", ""), body["answers"])
                return self._send(200, {"ok": True, "summary": summary})
            if path == "/api/plan":
                items = [{"exercise_id": i["exercise_id"], "exercise_name": rn.EXERCISES[i["exercise_id"]],
                          "sets": int(i["sets"]), "reps": int(i["reps"])} for i in body["items"]]
                if not items:
                    raise ValueError("Add at least one exercise.")
                if con.execute("SELECT 1 FROM patients WHERE patient_id=?", (body["patient_id"],)).fetchone() is None:
                    raise ValueError("No patient with that ID. The patient fills in the intake first.")
                rn.set_plan(con, body["patient_id"], items, approved_by=body.get("pt_name") or "PT")
                return self._send(200, {"ok": True, "plan": rn.current_plan(con, body["patient_id"])})
            if path == "/api/goal":
                pid = body["patient_id"]
                rn.set_goal(con, pid, body.get("goal", ""))
                # A new goal means a new draft for the PT, if there are sessions to base it on.
                last = con.execute("SELECT MAX(date) FROM checkins WHERE patient_id=?", (pid,)).fetchone()[0]
                if last and rn.current_plan(con, pid):
                    out = rn.create_adjustment_request(con, pid, last)
                    return self._send(200, {"ok": True, "drafted": True, "request_id": out["request_id"]})
                return self._send(200, {"ok": True, "drafted": False})
            if path == "/api/demo-week":
                pid = body.get("patient_id") or "u_demo"
                rn.load_demo_week(con, pid)
                return self._send(200, {"ok": True, "patient_id": pid})
            if path == "/api/adjustment/draft":
                week_end = body.get("week_end") or (con.execute(  # default: last day the patient checked in
                    "SELECT MAX(date) FROM checkins WHERE patient_id=?", (body["patient_id"],)).fetchone()[0])
                out = rn.create_adjustment_request(con, body["patient_id"], week_end)
                return self._send(200, {"ok": True, "request_id": out["request_id"]})
            if path == "/api/adjustment/decide":
                res = rn.decide_request(con, int(body["request_id"]), bool(body["approve"]), body.get("items"),
                                        body.get("pt_name") or "PT", body.get("note", ""))
                return self._send(200, {"ok": True, **res})
            if path == "/api/checkin":
                today = rn.daily_checkin(con, body["patient_id"], int(body["pain_today"]),
                                         body.get("date") or None, body.get("pain_location", "none"))
                return self._send(200, {"ok": True, **today})
            if path == "/api/result":
                errs = check_detection(body)
                if errs:
                    return self._send(422, {"ok": False, "errors": errs})
                rn.log_exercise(con, body["checkin_id"], body)
                saved = rows("SELECT * FROM exercise_logs ORDER BY log_id DESC LIMIT 1")[0]
                alert = body.get("pain_after") is not None and body["pain_after"] >= rn.STOP_PAIN
                return self._send(200, {"ok": True, "saved": saved, "alert_sent": alert})
        except (KeyError, ValueError) as e:
            return self._send(400, {"ok": False, "errors": [str(e)]})
        self._send(404, {"error": "not found"})


def start(host="127.0.0.1", port=PORT):
    """Start the server in a background thread (used by the Colab notebook). Returns the server."""
    import threading
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Test bench running at {url}  (Ctrl+C to stop)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    server.serve_forever()
