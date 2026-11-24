# Frontend: Homestretch

Plain HTML + JavaScript (no build step). Served by the backend at http://127.0.0.1:8000

- `index.html`: choose an app
- `patient.html`: patient tablet app
- `pt.html`: PT desktop app

Run the backend (`python backend/test_server.py`) and open http://127.0.0.1:8000. `patient-live.js` and `pt-live.js`
connect the pages to the API (see `docs/API.md`); opened as plain files, the pages run as the original prototype.
No API keys or plan rules in the front end.

`detection-live.js` turns on the tablet camera on the setup and exercise screens: it counts reps, checks form and
scores each rep (code in `frontend/detection/`, see `detection/README.md`). On the exercise screen it shows a
live guide to the PT's target, a gauge, each rep's 1-5 score, and speaks the score and fixes when Voice guide is on.
Add `?camera=0` to use the demo timer.
