/* Connects patient.html to the CoRehab API (docs/API.md).
   The prototype keeps its screens and behaviour; this file
   1. loads the patient's real plan, streak and week from the server, and
   2. sends what the patient does (pain, each exercise, end of session) to the server.
   The server is the source of truth: if its answer differs from the screen's own rule, the server wins.
   Opened without the server (e.g. as a file), the prototype runs on its built-in demo data. */
(function () {
  const API = '';                       // same origin as the server
  const PID_KEY = 'corehab_patient_id';
  const FEEL = { 'Easy': 'easy', 'Just right': 'just_right', 'Too hard': 'too_hard' };
  const NAMED = ['Swelling', 'Redness or warmth', 'Fever', 'Calf pain'];
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const DOW = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

  // Exercise details the prototype has no card for (it ships with 4 + sit-to-stand).
  const EXTRA = {
    sitting_knee_bend: { name: 'Sitting knee bends', img: 'img/ex-sitting-knee-bend.jpg', need: ['Sturdy chair'], time: '0:40',
      cue: 'Slide your foot back under the chair, slowly.', fix: 'Keep your heel on the floor as you slide.',
      steps: ['Sit tall in a sturdy chair, feet flat.', 'Slide the foot of your operated leg back under the chair to bend your knee.', 'Hold for 5 seconds, then slide it forward again.'],
      tip: 'Go only as far as feels okay. A little stretch is normal; sharp pain is not.' },
    mini_squat: { name: 'Mini squats', img: 'img/ex-mini-squat.jpg', need: ['Sturdy chair'], time: '0:45',
      cue: 'Bend your knees a little, keep your chest up.', fix: 'Keep your knees over your toes.',
      steps: ['Stand behind a sturdy chair and hold the back with both hands.', 'Bend both knees a little, as if starting to sit.', 'Hold for 2 seconds, then stand up tall.'],
      tip: 'Only a small bend. Keep your weight even on both legs.' },
    step_up: { name: 'Step-ups', img: 'img/ex-step-up.jpg', need: ['Step', 'Rail'], time: '0:50',
      cue: 'Step up with your operated leg, then down.', fix: 'Push through your whole foot.',
      steps: ['Stand facing a low step, holding the rail.', 'Step up with your operated leg, then bring the other foot up.', 'Step down with your good leg first.'],
      tip: 'Use the lowest step and always hold the rail.' },
    glute_bridge: { name: 'Glute bridges', img: 'img/ex-glute-bridge.jpg', need: ['Bed or mat'], time: '0:40',
      cue: 'Squeeze and lift your hips.', fix: 'Keep your knees in line with your hips.',
      steps: ['Lie on your back with both knees bent and feet flat.', 'Squeeze your buttocks and lift your hips off the mat.', 'Hold for 2 seconds, then lower slowly.'],
      tip: 'Lift only as high as feels comfortable. Keep breathing.' },
    side_lying_hip_abduction: { name: 'Side-lying leg lifts', img: 'img/ex-side-lying-hip-abduction.jpg', need: ['Bed or mat'], time: '0:40',
      cue: 'Lift your top leg slowly, toes forward.', fix: 'Keep your hips stacked.',
      steps: ['Lie on your good side with your operated leg on top.', 'Keep the top leg straight and lift it about a foot.', 'Hold for 2 seconds, then lower slowly.'],
      tip: 'Don\'t roll your hips back. Small and slow is best.' },
  };

  const CR = { live: false, pid: null, home: null, ck: null, sent: {} };
  window.CR = CR;

  async function call(path, body) {
    const r = await fetch(API + path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((j.errors || [r.status]).join('; '));
    return j;
  }
  const A = () => window.CRAPP;
  // Show problems on screen instead of hiding them in the console.
  function toast(msg, bad) {
    let t = document.getElementById('cr-toast');
    if (!t) { t = document.createElement('div'); t.id = 'cr-toast'; document.body.appendChild(t);
      t.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:9999;padding:14px 22px;border-radius:14px;font:600 18px system-ui;box-shadow:0 6px 24px rgba(0,0,0,.2);max-width:80vw'; }
    t.textContent = msg; t.style.background = bad ? '#B3261E' : '#167A4C'; t.style.color = '#fff'; t.style.display = 'block';
    clearTimeout(t._h); t._h = setTimeout(() => { t.style.display = 'none'; }, bad ? 6000 : 2500);
  }
  CR.toast = toast;

  function card(item) {
    // Prototype card for this exercise (same images, steps and cues), with the dose from the server.
    const app = A();
    const base = app.PLAN.concat([app.STS]).find(e => e.id === item.exercise_id) || CR.lib[item.exercise_id];
    const extra = EXTRA[item.exercise_id];
    const proto = base || Object.assign({ id: item.exercise_id, pos: '50% 50%', thumb: '50% 55%', lines: [] }, extra || { name: item.exercise_name, img: 'img/mark.png', need: [], steps: [], time: '0:40' });
    return Object.assign({}, proto, { id: item.exercise_id, sets: item.sets, reps: item.reps });
  }

  function dayLabel(iso) {
    const d = new Date(iso + 'T12:00:00');
    return `${DOW[d.getDay()]}, ${MON[d.getMonth()]} ${d.getDate()}`;
  }

  function weekRows(days) {
    return days.map(x => {
      const n = +x.date.slice(8, 10);
      const s = { done: 'done', partial: 'partial', rest: 'rest', today: 'today', open: 'open', missed: 'none', rested_for_pain: 'none' }[x.status] || 'none';
      const row = { d: x.day, n, s };
      if (s === 'done' || s === 'partial') Object.assign(row, { min: Math.max(5, Math.round((x.reps || 0) / 5)), reps: x.reps || 0, pb: x.pain_before, pa: x.pain_after == null ? x.pain_before : x.pain_after });
      return row;
    });
  }

  function applyHome(h, prog) {
    const app = A(), S = app.S;
    CR.home = h;
    if (CR.whoami) CR.whoami();
    // Plan: the server's current plan, in the prototype's card format.
    if (h.plan && h.plan.length) app.PLAN.splice(0, app.PLAN.length, ...h.plan.map(card));
    // What changed (after the PT approved), lined up with the plan order.
    const ch = (h.plan_change && h.plan_change.changes) || [];
    app.CHANGES.splice(0, app.CHANGES.length, ...app.PLAN.map(e => {
      const c = ch.find(x => x.exercise_id === e.id);
      if (!c || c.kind === 'same') return { k: 'same', l: 'Same' };
      if (c.kind === 'new') return { k: 'new', l: 'New', why: c.reason };
      const [bs, br] = (c.before || '0 x 0').split(' x ').map(Number);
      const lab = c.action === 'easier' ? 'Lighter for now' : br !== e.reps ? `${e.reps > br ? e.reps - br + ' more' : br - e.reps + ' fewer'} reps` : `${e.sets > bs ? 'One more set' : 'One less set'}`;
      return { k: 'up', l: lab, was: `${bs} sets of ${br}`, why: c.reason };
    }));
    if (h.rest_days) app.RESTD.splice(0, app.RESTD.length, ...h.rest_days);
    S.streak = h.streak;
    S.planUpdated = h.phase === 'plan_updated';
    S.phase = h.phase === 'intake' || h.phase === 'waiting_for_visit' ? 'waiting' : null;
    if (h.name) { S.info.name = h.name; if (S.ob && S.ob.a) S.ob.a.name = h.name; }
    if (prog) {
      const mon = new Date(prog.this_week[0].date + 'T12:00:00');
      app.WLAB.splice(0, app.WLAB.length, ...[0, 1, 2, 3].map(k => {
        const a = new Date(mon), b = new Date(mon);
        a.setDate(a.getDate() - 7 * k); b.setDate(a.getDate() + 6);
        return `${MON[a.getMonth()]} ${a.getDate()} to ${a.getMonth() === b.getMonth() ? '' : MON[b.getMonth()] + ' '}${b.getDate()}`;
      }));
      // "Your plan for week N" page: this week's dates and the patient's week after surgery.
      const wp = app.WEEKPLAN.app, nm = new Date(mon); nm.setDate(nm.getDate() + 7);
      wp.wk = h.week_post_op || 1; wp.range = app.WLAB[0]; wp.next = `Monday, ${MON[nm.getMonth()]} ${nm.getDate()}`;
      wp.days = prog.this_week.map(x => [x.day, +x.date.slice(8, 10)]);
      app.WEEK.splice(0, app.WEEK.length, ...weekRows(prog.this_week));
      app.LASTWEEK.splice(0, app.LASTWEEK.length, ...weekRows(prog.last_week));
    }
  }

  async function load() {
    let h, prog;
    try {
      [h, prog] = await Promise.all([call(`/api/home?patient_id=${CR.pid}`), call(`/api/progress?patient_id=${CR.pid}`)]);
    } catch (e) {
      if (CR.pid === 'walter') throw e;
      // This browser remembers a patient that is not in the database (e.g. the database was reset): use the demo patient.
      console.warn(`Patient '${CR.pid}' not found, switching to the demo patient`, e);
      localStorage.removeItem(PID_KEY); CR.pid = 'walter'; CR.msgCount = -1;
      [h, prog] = await Promise.all([call(`/api/home?patient_id=walter`), call(`/api/progress?patient_id=walter`)]);
    }
    const wasWaiting = A().S.phase === 'waiting';
    applyHome(h, prog);
    // First plan arrived while waiting: show "Your plan is ready" and the setup steps.
    if (wasWaiting && A().S.phase === null) A().S.phase = 'ready';
    await loadMsgs().catch(() => {});      // this patient's messages (a new account has none)
    if (['tabs', 'cover'].includes(A().S.screen)) A().render();
  }

  // ---------- writes ----------

  function postResult(idx, extra) {
    const app = A(), S = app.S, e = app.PLAN[idx];
    if (!e || !CR.ck) return;
    const key = idx + ':' + (CR.ckDate || '');
    if (CR.sent[key]) return;
    CR.sent[key] = true;
    const r = S.results.find(x => x.id === e.id);
    const reps = extra && extra.reps != null ? extra.reps : r ? r.reps : 0;
    const feel = FEEL[(S.feels || {})[idx] || S.feel] || null;
    const body = { exercise_id: e.id, sets_done: Math.min(app.nsets(e), Math.ceil(reps / Math.max(1, app.target(e))) || 0),
      reps_done: reps, feeling: feel, note: ((S.exNotes || {})[idx] || '').trim() || undefined };
    if (feel === 'too_hard' && S.painAfter != null) body.pain_after = S.painAfter;
    CR.ck.then(id => id && call('/api/result', Object.assign({ checkin_id: id }, body))).catch(err => console.warn('result', err));
  }

  CR.beforeClick = function (b, d) {
    if (!CR.live) return;
    const app = A(), S = app.S;
    if (b.id === 'painNext') {
      const sym = S.symBefore || [];
      const worry = { symptoms: sym.filter(x => NAMED.includes(x)).map(x => x.toLowerCase()), other: sym.includes('Something else') ? (S.symBeforeO || 'Something else') : '' };
      CR.sent = {};
      CR.ck = call('/api/checkin', { patient_id: CR.pid, pain_today: S.painBefore, worry }).then(r => {
        CR.ckDate = r.date;
        // The server decides today's dose. Fix the screen if its rule disagrees.
        const S2 = A().S;
        const mode = r.action === 'rest' ? 'rest' : r.action === 'as_planned' ? 'full' : r.exercises[0] && r.exercises[0].target_reps / r.exercises[0].planned_reps <= 0.55 ? 'l50' : 'l70';
        if (mode === 'rest' && S2.screen !== 'rest') A().go('rest');
        else if (mode !== 'rest' && S2.mode !== mode) { S2.mode = mode; if (S2.screen === 'todayPlan') A().render(); }
        return r.checkin_id;
      }).catch(err => { console.warn('checkin', err); return null; });
      return;
    }
    if (b.id === 'afterNext') { postResult(S.exIdx); return; }
    if (b.id === 'wrapNext') {
      const bothered = (S.bother || []).map(id => ({ exercise_id: id, reason: ((S.whys || {})[id] || 'Something else').toLowerCase(), other: (S.whyO || {})[id] || '' }));
      CR.ck && CR.ck.then(id => id && call('/api/session/end', { checkin_id: id, pain_end: S.endPain, bothered }))
        .then(() => setTimeout(load, 300)).catch(err => console.warn('end', err));
      return;
    }
    if (d.endyes !== undefined || d.sexityes !== undefined) {
      if (d.endyes !== undefined && (S.rep > 0 || S.set > 1)) {
        const e = app.PLAN[S.exIdx];
        postResult(S.exIdx, { reps: (S.set - 1) * app.target(e) + S.rep });
      }
      const why = (S.stopWhy || '').toLowerCase();
      CR.ck && CR.ck.then(id => id && call('/api/session/stop', { checkin_id: id, reason: why, other: S.stopO || '', pain: S.stopPain }))
        .then(() => setTimeout(load, 300)).catch(err => console.warn('stop', err));
      return;
    }
    if (d.obwait !== undefined) {           // questionnaire sent: create the account, then the intake
      const a = S.ob.a || {};
      const answers = Object.assign({}, a, { surgery_date: S.ob.sdate, side: /left/i.test(S.ob.knee || '') ? 'left' : 'right', goal: a.goal || 'walk' });
      call('/api/register', { name: a.name || 'New patient', dob: a.dob, phone: a.phone, surgery_date: S.ob.sdate, side: answers.side })
        .then(r => { CR.pid = r.patient_id; CR.msgCount = -1; localStorage.setItem(PID_KEY, CR.pid);
          toast(`Account created. Your username is "${r.username}". Use it, or your name, to sign in.`); return call('/api/intake', { patient_id: CR.pid, name: a.name, answers }); })
        .then(() => setTimeout(load, 200)).catch(err => console.warn('register', err));
      return;
    }
    if (d.obnext !== undefined && S.ob && S.ob.step === 3) {   // setup checks: consent + safety
      call('/api/consent', { patient_id: CR.pid, items: (S.ob.consent || []).map(String) }).catch(() => {});
      call('/api/setup-check', { patient_id: CR.pid, safe: true, details: '' }).catch(() => {});
      return;
    }
    if (d.signout !== undefined) {                            // Profile > Sign out
      localStorage.removeItem(PID_KEY);
    }
  };

  // ---------- sign in ("I already have an account") ----------
  function showLogin() {
    let o = document.getElementById('cr-login');
    if (o) o.remove();
    o = document.createElement('div');
    o.id = 'cr-login';
    o.style.cssText = 'position:fixed;inset:0;z-index:9997;background:rgba(14,17,22,.45);display:flex;align-items:center;justify-content:center;font-family:inherit';
    o.innerHTML = `<form style="background:#fff;border-radius:24px;padding:36px 40px;width:min(560px,90vw);box-shadow:0 20px 60px rgba(0,0,0,.25)">
      <h2 style="margin:0 0 6px;font-size:32px">Sign in</h2>
      <p style="margin:0 0 22px;font-size:19px;color:#4A515C">Type your name or your username.</p>
      <label for="cr-login-in" style="display:block;font-size:18px;font-weight:600;margin-bottom:8px">Name or username</label>
      <input id="cr-login-in" autocomplete="off" placeholder="e.g. Walter Miller" style="width:100%;box-sizing:border-box;height:64px;padding:0 18px;border-radius:14px;border:1.5px solid #B9C0CA;font:inherit;font-size:22px">
      <p id="cr-login-err" style="min-height:24px;margin:10px 0 0;color:#B3261E;font-size:17px"></p>
      <div style="display:flex;gap:12px;justify-content:flex-end;margin-top:14px">
        <button type="button" id="cr-login-cancel" style="height:60px;padding:0 26px;border-radius:14px;border:1.5px solid #B9C0CA;background:#fff;font:inherit;font-size:20px;font-weight:600">Cancel</button>
        <button type="submit" style="height:60px;padding:0 30px;border-radius:14px;border:0;background:#0E1116;color:#fff;font:inherit;font-size:20px;font-weight:600">Sign in</button></div></form>`;
    document.body.appendChild(o);
    const inp = o.querySelector('#cr-login-in'), err = o.querySelector('#cr-login-err');
    inp.focus();
    o.querySelector('#cr-login-cancel').onclick = () => o.remove();
    o.querySelector('form').onsubmit = async ev => {
      ev.preventDefault();
      err.textContent = '';
      try {
        const r = await call('/api/login', { name: inp.value });
        CR.pid = r.patient_id; CR.msgCount = -1;
        localStorage.setItem(PID_KEY, CR.pid);
        o.remove();
        await load();
        A().toTab('today');
        toast(`Signed in as ${r.name}`);
      } catch (e) { err.textContent = e.message; inp.focus(); }
    };
  }
  CR.showLogin = showLogin;

  // ---------- the signed-in patient's name and picture ----------
  CR.first = () => ((CR.home && CR.home.name) || A().S.info.name || '').split(' ')[0] || 'there';
  CR.greet = () => { const h = new Date().getHours(); return `Good ${h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening'}, ${CR.first()}`; };
  CR.avatar = () => {
    if (CR.pid === 'walter') return '<img src="img/walter.jpg" alt="">';
    const ini = ((CR.home && CR.home.name) || '?').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
    return `<span style="display:flex;align-items:center;justify-content:center;width:100%;height:100%;border-radius:50%;background:#E2D3C2;color:#5B4632;font-weight:700;font-size:clamp(14px,40%,48px);container-type:size"><span style="font-size:min(40cqw,48px)">${ini}</span></span>`;
  };
  CR.ptNote = () => { const c = CR.home && CR.home.plan_change; return (c && (c.pt_note || c.explanation)) || ''; };

  // ---------- small texts on Home ----------
  CR.when = () => {
    const c = CR.home && CR.home.plan_change;
    return c && c.approved_at ? `Approved ${dayLabel(c.approved_at.slice(0, 10))}. It starts with your next session.` : 'It starts with your next session.';
  };
  CR.startsLine = () => 'Your new plan is ready';
  CR.changeLine = () => {
    const c = (CR.home && CR.home.plan_change && CR.home.plan_change.changes) || [];
    const parts = c.filter(x => x.kind !== 'same').map(x => x.kind === 'new' ? `${x.exercise_name} is new` : `${x.exercise_name}: ${(x.after || 'paused').replace(' x ', ' × ')}`);
    return parts.length ? parts.join(', ') + '.' : 'Same exercises as last week.';
  };

  // ---------- messages with the PT ----------
  const KIND_TEXT = { voice: 'Voice message', photo: 'Photo of my knee', session: 'Shared my session' };
  function hm(iso) { const d = new Date(iso); let h = d.getHours(); const m = String(d.getMinutes()).padStart(2, '0');
    return `${h % 12 || 12}:${m} ${h < 12 ? 'am' : 'pm'}`; }
  function toMsgs(list) {
    const out = []; let day = null;
    const today = new Date().toISOString().slice(0, 10);
    list.forEach(m => {
      const d = m.created_at.slice(0, 10);
      if (d !== day) { out.push({ sep: d === today ? 'Today' : dayLabel(d) }); day = d; }
      out.push({ me: m.sender === 'patient', t: m.text, w: hm(m.created_at) });
    });
    return out;
  }
  CR.msgCount = -1;
  async function loadMsgs() {
    const S = A().S;
    const open = S.screen === 'tabs' && S.tab === 'messages';
    const r = await call(`/api/messages?patient_id=${CR.pid}${open ? '&reader=patient' : ''}`);
    const changed = r.messages.length !== CR.msgCount;
    CR.msgCount = r.messages.length;
    const unread = open ? 0 : r.unread;
    if (changed) S.msgs.splice(0, S.msgs.length, ...toMsgs(r.messages));
    if (changed || S.unread !== unread) {
      S.unread = unread;
      if (S.screen === 'tabs' && document.activeElement && document.activeElement.id !== 'draft') A().render();
      else if (S.screen === 'tabs' && changed) A().render();
    }
  }
  CR.sendLast = () => {
    const S = A().S;
    const m = [...S.msgs].reverse().find(x => x.me && !x.sep);
    if (!m) return;
    const kind = m.type && KIND_TEXT[m.type] ? m.type : 'text';
    const text = m.t || KIND_TEXT[kind];
    call('/api/messages', { patient_id: CR.pid, sender: 'patient', text, kind })
      .then(() => { toast('Sent to your PT'); return loadMsgs(); })
      .catch(err => { console.warn('message', err); toast('Message not sent: ' + err.message, true); });
  };

  CR.restart = () => { localStorage.removeItem(PID_KEY); CR.pid = 'walter'; CR.msgCount = -1; load().then(loadMsgs).catch(() => {}); };

  CR.init = async function () {
    CR.lib = {};
    try {
      await call('/api/clinic');
    } catch (e) {
      return;                                  // no server: plain prototype
    }
    CR.live = true;
    document.body.classList.add('live');
    const st = document.createElement('style');
    st.textContent = '.live #simplan,.live #simvisit{display:none!important}';
    document.head.appendChild(st);
    // "I already have an account" opens the sign-in box instead of going straight to the demo patient.
    document.getElementById('device').addEventListener('click', ev => {
      const b = ev.target.closest('button');
      if (b && b.dataset.tab === 'today' && A().S.screen === 'cover') { ev.stopPropagation(); ev.preventDefault(); showLogin(); }
    }, true);
    CR.pid = localStorage.getItem(PID_KEY) || 'walter';
    await load().catch(err => { console.warn('load', err); toast('Could not load from the server: ' + err.message, true); });
    const bar = document.querySelector('.proto, header, .topbar');
    CR.whoami = () => { let w = document.getElementById('cr-who'); if (!w) { w = document.createElement('span'); w.id = 'cr-who';
      w.style.cssText = 'position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:9998;font:600 13px system-ui;background:#E2F3E9;color:#167A4C;padding:4px 10px;border-radius:999px';
      document.body.appendChild(w); } w.textContent = `Connected · patient: ${CR.home ? CR.home.name : CR.pid} (${CR.pid})`; };
    CR.whoami();
    await loadMsgs().catch(() => {});
    // Waiting for the first plan: check every few seconds. Messages: check for the PT's replies.
    setInterval(() => {
      if (!CR.live) return;
      if (A().S.phase === 'waiting') load().catch(() => {});
      else if (A().S.screen === 'tabs') loadMsgs().catch(() => {});
    }, 4000);
  };
})();
