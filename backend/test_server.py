"""CoRehab API server + the front end. Standard library only.

Pages:  /               front end (frontend/index.html -> patient.html, pt.html)
        /test           the old 3-tab test bench (test_page.html)
API:    see docs/API.md. Demo patients are loaded at start (set DEMO_SEED=0 to skip, DEMO_RESET=1 to reload).

Run:  python test_server.py      then open http://127.0.0.1:8000
      (Colab: see rehab_colab.ipynb)

Endpoint the detection node calls after each exercise:
  POST /api/result
  {"checkin_id": 1, "exercise_id": "mini_squat", "sets_done": 3, "reps_done": 21,
   "form_scores": [0.9, 0.8, ...], "error_counts": {"trunk_lean": 2}, "pain_after": 4}
"""
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import clinic
import demo_seed
import rehab_node as rn

HERE = os.path.dirname(os.path.abspath(__file__))


ENV_INFO = {"file": None, "keys": []}


def load_env(path):
    """Read KEY=value lines from backend/.env (never committed: .env is in .gitignore).
    Copes with Windows Notepad files (BOM, CRLF, UTF-16), quotes, spaces and 'export KEY=...'."""
    if not os.path.isfile(path):
        return
    raw = open(path, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", errors="replace")
    ENV_INFO["file"] = path
    for line in text.splitlines():
        line = line.strip().lstrip("\ufeff")
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'").strip()
        if v and not os.environ.get(k):          # a value in .env wins over an empty variable
            os.environ[k] = v
            ENV_INFO["keys"].append(k)


load_env(os.path.join(HERE, ".env"))
FRONTEND = os.path.abspath(os.path.join(HERE, "..", "frontend"))
DB_PATH = os.environ.get("REHAB_DB", os.path.join(HERE, "rehab_test.db"))  # Colab: set to a Drive path to keep data
con = clinic.connect(DB_PATH)
# One shared SQLite connection, used by one request at a time. Without this, two requests arriving together
# (the apps load several things in parallel) break the connection: "sqlite3.InterfaceError: bad parameter".
DB_LOCK = threading.Lock()
if os.environ.get("DEMO_SEED", "1") != "0":
    demo_seed.seed_demo(con, reset=os.environ.get("DEMO_RESET") == "1")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".png": "image/png",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".json": "application/json"}
PORT = int(os.environ.get("PORT", 8000))
ALLOW_ORIGIN = os.environ.get("ALLOW_ORIGIN", "*")  # CORS, so a separate frontend (Figma Make/React) can call the API


def check_detection(body):
    """Return a list of problems with a detection result; empty list = valid."""
    errs = []
    for key in ("checkin_id", "exercise_id", "sets_done", "reps_done"):
        if key not in body:
            errs.append(f"missing '{key}'")
    for key in ("checkin_id", "sets_done", "reps_done"):
        if key in body and (not isinstance(body[key], int) or body[key] < 0):
            errs.append(f"'{key}' must be a whole number >= 0")
    fs = body.get("form_scores")
    if fs is not None:
        if not isinstance(fs, list) or not all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in fs):
            errs.append("'form_scores' must be a list of numbers from 0 to 1, one per rep")
        elif fs and "reps_done" in body and isinstance(body["reps_done"], int) and len(fs) != body["reps_done"]:
            errs.append(f"'form_scores' has {len(fs)} values but reps_done is {body['reps_done']} (expected one per rep)")
    ec = body.get("error_counts", {})
    if not isinstance(ec, dict) or not all(isinstance(v, int) and v >= 0 for v in ec.values()):
        errs.append("'error_counts' must be an object of error type -> count")
    pa = body.get("pain_after")
    if pa is not None and (not isinstance(pa, int) or not 0 <= pa <= 10):
        errs.append("'pain_after' must be a whole number 0-10")
    fe = body.get("feeling")
    if fe is not None and fe not in rn.FEELINGS:
        errs.append(f"'feeling' must be one of {rn.FEELINGS}")
    return errs


def rows(sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Cache-Control", "no-store")   # always the latest page and scripts
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

    def log_message(self, fmt, *args):  # quiet, except failed API calls
        if len(args) >= 2 and str(args[1])[:1] in "45" and "/api/" in str(args[0]):
            print(f"[API error] {args[0]} -> {args[1]}")

    def do_GET(self):
        with DB_LOCK:
            return self._do_get()

    def do_POST(self):
        with DB_LOCK:
            return self._do_post()

    def _do_get(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/test":
            with open(os.path.join(HERE, "test_page.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if not url.path.startswith("/api/"):
            return self._static(url.path)
        pid = q.get("patient_id", "")
        try:
            out = self._get_api(url.path, pid, q)
        except ValueError as e:
            return self._send(404, {"ok": False, "errors": [str(e)]})
        if out is not None:
            return self._send(200, out)
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
        if url.path == "/api/db":  # test bench
            pid = q.get("patient_id", "")
            return self._send(200, {
                "logs": rows("SELECT log_id, date, exercise_id, planned_sets, planned_reps, target_reps, sets_done,"
                             " reps_done, form_score, errors_json, pain_after FROM exercise_logs"
                             " WHERE patient_id=? ORDER BY log_id DESC LIMIT 50", (pid,)),
                "alerts": rows("SELECT date, tier, reason FROM alerts WHERE patient_id=? ORDER BY alert_id DESC LIMIT 20",
                               (pid,)),
            })
        self._send(404, {"error": "not found"})

    def _static(self, path):
        """Serve the front end. Only files inside frontend/ are reachable."""
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.abspath(os.path.join(FRONTEND, rel))
        if not full.startswith(FRONTEND + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        with open(full, "rb") as f:
            return self._send(200, f.read(), TYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream"))

    def _get_api(self, path, pid, q):
        """New read routes for the patient and PT apps. Returns None if the path is not one of them."""
        if path == "/api/weekly-summary":
            return rn.weekly_summary(con, pid, q.get("week_end"))
        if path == "/api/home":
            return clinic.home(con, pid)
        if path == "/api/progress":
            return clinic.progress(con, pid)
        if path == "/api/patients":
            return clinic.patient_list(con)
        if path == "/api/profile":
            return clinic.profile(con, pid)
        if path == "/api/visit/findings":
            return {"findings": clinic.latest_findings(con, pid)}
        if path == "/api/discharge":
            return {"discharge": clinic.discharge_summary(con, pid)}
        if path == "/api/request":   # latest plan draft of any kind (first or weekly), for the PT review screen
            return {"request": rn.latest_request(con, pid, q.get("status")), "plan": rn.current_plan(con, pid)}
        if path == "/api/messages":
            return {"messages": clinic.messages(con, pid, q.get("reader")), "unread": clinic.unread_for_patient(con, pid)}
        if path == "/api/threads":
            return {"threads": clinic.threads(con)}
        if path == "/api/llm-status":
            return rn.llm_status(test_call=True)
        if path == "/api/clinic":
            return clinic.CLINIC
        return None

    def _do_post(self):
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
                out = rn.create_adjustment_request(con, body["patient_id"], week_end, use_llm=body.get("use_llm", True))
                return self._send(200, {"ok": True, "request_id": out["request_id"]})
            if path == "/api/adjustment/decide":
                res = rn.decide_request(con, int(body["request_id"]), bool(body["approve"]), body.get("items"),
                                        body.get("pt_name") or "PT", body.get("note", ""), body.get("decisions"))
                return self._send(200, {"ok": True, **res})
            if path == "/api/checkin":
                today = rn.daily_checkin(con, body["patient_id"], int(body["pain_today"]),
                                         body.get("date") or None, body.get("pain_location", "none"),
                                         body.get("worry"))
                return self._send(200, {"ok": True, **today})
            if path == "/api/session/end":
                return self._send(200, rn.end_session(con, int(body["checkin_id"]), body["pain_end"],
                                                      body.get("bothered"), body.get("note", "")))
            if path == "/api/session/stop":
                return self._send(200, rn.stop_session(con, int(body["checkin_id"]), body.get("reason", ""),
                                                       body.get("other", ""), body.get("pain")))
            if path == "/api/login":
                return self._send(200, {"ok": True, **clinic.login(con, body.get("name", ""))})
            if path == "/api/register":
                return self._send(200, {"ok": True, **clinic.register(con, body.get("name", ""), body.get("dob"),
                                                                      body.get("phone"), body.get("surgery_date"),
                                                                      body.get("side"))})
            if path == "/api/messages":
                return self._send(200, {"ok": True, **clinic.send_message(con, body["patient_id"], body.get("sender", "patient"),
                                                                          body.get("text", ""), body.get("kind", "text"))})
            if path == "/api/join":
                return self._send(200, {"ok": True, **clinic.join(con, body.get("code", ""))})
            if path == "/api/patients/add":
                return self._send(200, {"ok": True, **clinic.add_patient_by_pt(
                    con, body["name"], body.get("phone"), body.get("surgery", "Knee replacement"),
                    body["surgery_date"], body.get("side", "right"), body.get("dob"))})
            if path == "/api/consent":
                return self._send(200, clinic.save_consent(con, body["patient_id"], body.get("items", [])))
            if path == "/api/setup-check":
                return self._send(200, clinic.setup_check(con, body["patient_id"], bool(body["safe"]), body.get("details", "")))
            if path == "/api/rest-days":
                rn.set_rest_days(con, body["patient_id"], body["days"])
                return self._send(200, {"ok": True, "rest_days": rn.rest_days(con, body["patient_id"])})
            if path == "/api/pt-notes":
                return self._send(200, clinic.set_private_notes(con, body["patient_id"], body.get("text", "")))
            if path == "/api/visits/schedule":
                return self._send(200, {"ok": True, **clinic.schedule_visit(
                    con, body["patient_id"], body["date"], body.get("time", ""), body.get("place"), body.get("reason", ""),
                    body.get("kind", "follow_up"))})
            if path == "/api/visit/findings":
                return self._send(200, {"ok": True, **clinic.save_findings(con, body["patient_id"], body.get("fields", {}),
                                                                           body.get("visit_id"))})
            if path == "/api/first-plan/draft":
                return self._send(200, {"ok": True, **clinic.create_first_plan_request(con, body["patient_id"])})
            if path == "/api/calls":
                return self._send(200, {"ok": True, **clinic.log_call(con, body["patient_id"], body.get("minutes", 0),
                                                                      body.get("agreed", ""), body.get("date"))})
            if path == "/api/discharge":
                return self._send(200, {"ok": True, "discharge": clinic.discharge(
                    con, body["patient_id"], body.get("note", ""), body.get("checklist"),
                    body.get("community_program", False), body.get("final_measures"))})
            if path == "/api/result":
                errs = check_detection(body)
                if errs:
                    return self._send(422, {"ok": False, "errors": errs})
                rn.log_exercise(con, body["checkin_id"], body)
                saved = rows("SELECT * FROM exercise_logs ORDER BY log_id DESC LIMIT 1")[0]
                alert = body.get("pain_after") is not None and body["pain_after"] >= rn.STOP_PAIN
                return self._send(200, {"ok": True, "saved": saved, "alert_sent": alert})
        except KeyError as e:
            print(f"[API error] {path}: missing field {e}")
            return self._send(400, {"ok": False, "errors": [f"missing field {e}"]})
        except (ValueError, TypeError) as e:
            print(f"[API error] {path}: {e}")
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
    print(f"CoRehab running at {url}   patient app: {url}/patient.html   PT app: {url}/pt.html   test bench: {url}/test")
    st = rn.llm_status(test_call=bool(os.environ.get("OPENAI_API_KEY")))
    if st["connected"]:
        print(f"LLM: connected ({st['model']}, key {st['key_hint']}). AI drafts are on.")
    else:
        env = ENV_INFO["file"] or os.path.join(HERE, ".env")
        found = "found" if ENV_INFO["file"] else "NOT found"
        print(f"LLM: not connected -> drafts use the rules. {st['error']}")
        print(f"     .env {found} at {env}; keys read from it: {', '.join(ENV_INFO['keys']) or 'none'}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    server.serve_forever()
