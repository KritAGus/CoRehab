/* Connects pt.html to the CoRehab API (docs/API.md).
   Replaces the prototype's scripted screens with screens built from server data, using the same styles:
   patient list, intake, clinic visit (autosaved findings), first-plan draft, weekly review, patient profile.
   Messages and Schedule are live too (messages table; visits + calls).
   Opened without the server, the prototype runs on its built-in demo data. */
(function () {
  const PTL = { live: false, cache: { profile: {}, request: {} }, dec: {}, filter: 'all', addOpen: false, note: '' };
  window.PTL = PTL;
  const A = () => window.PTAPP;
  const PT_NAME = 'Dana Reyes, PT';
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  // Backend exercise ID -> prototype library ID (images and names live in the prototype).
  const LIBID = { heel_slide: 'heel_slide', straight_leg_raise: 'slr', sitting_knee_bend: 'sitbend', seated_knee_extension: 'ext',
    standing_knee_bend: 'stbend', sit_to_stand: 'sts', mini_squat: 'squat', step_up: 'step', glute_bridge: 'bridge',
    side_lying_hip_abduction: 'abd' };
  const GROUPS = { urgent: ['Urgent', 'urg'], ready_for_review: ['Ready for review', 'new'], gone_quiet: ['Gone quiet', 'rev'],
    done_today: ['Done today', 'ok'], new: ['New', 'new'], on_track: ['On track', 'ok'], discharged: ['Discharged', 'mut'] };
  const NEEDS = ['urgent', 'ready_for_review', 'gone_quiet'];
  const FEEL = { easy: 'Easy', just_right: 'Just right', too_hard: 'Too hard' };

  async function call(path, body) {
    const r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((j.errors || [r.status]).join('; '));
    return j;
  }
  const esc = t => String(t == null ? '' : t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const ex = id => { const e = A().lib(LIBID[id] || id); return e || { n: id, img: 'img/mark.png' }; };
  const fmt = iso => { if (!iso) return '–'; const d = new Date(iso.slice(0, 10) + 'T12:00:00'); return `${DOW[d.getDay()]}, ${MON[d.getMonth()]} ${d.getDate()}`; };
  const dose = (s, r) => `${s} × ${r}`;
  const ini = n => (n || '?').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
  const avatar = (p, sz) => p.patient_id === 'walter' ? `<img src="img/walter.jpg" alt=""${sz ? ` style="width:${sz}px;height:${sz}px"` : ''}>` : `<span class="ini"${sz ? ` style="width:${sz}px;height:${sz}px;flex-basis:${sz}px;font-size:${Math.round(sz / 2.9)}px"` : ''}>${ini(p.name)}</span>`;
  const I = () => A().I;

  // ---------- navigation ----------
  async function open(view, pid) {
    const S = A().S;
    if (pid) PTL.pid = pid;
    try {
      if (view === 'list' || view === 'msg') PTL.cache.list = await call('/api/patients');
      if (view === 'msg') await loadMsgs(pid || PTL.thread, true);
      if (view === 'sched') await loadSched(true);
      if (['intake', 'visit', 'prof', 'review', 'plan'].includes(view)) PTL.cache.profile[PTL.pid] = await call(`/api/profile?patient_id=${PTL.pid}`);
      if (['review', 'plan'].includes(view)) {
        PTL.cache.request[PTL.pid] = (await call(`/api/request?patient_id=${PTL.pid}&status=pending`)).request;
        PTL.dec = {};
        PTL.note = '';
      }
    } catch (e) { PTL.err = e.message; }
    S.view = view;
    A().render();
  }
  PTL.open = open;

  // ---------- views ----------
  function head(p, back, step) {
    const facts = [p.age ? `<span class="tag mut">${p.age}</span>` : '', `<span class="tag mut">${esc(p.surgery)}${p.surgery_date ? ', ' + fmt(p.surgery_date).slice(5) : ''}</span>`,
      p.week_post_op != null ? `<span class="tag mut">Week ${p.week_post_op} after surgery</span>` : '', `<span class="tag mut">${p.data_days_30} data days in 30</span>`].join('');
    return `<button class="backl" data-lv="go" data-v="${back || 'list'}">${I().back}${back === 'prof' ? esc(p.name) : 'Patients'}</button>
      <div class="phd">${avatar(p, 72)}<div><h1>${esc(p.name)}</h1><div class="facts">${facts}</div></div>${step ? `<span class="tag new" style="margin-left:auto">${step}</span>` : ''}
      <span style="margin-left:${step ? '12px' : 'auto'}"></span><button class="btn sec" data-lv="go" data-v="prof">Profile</button></div>`;
  }
  const errBox = () => PTL.err ? `<div class="card pad" style="border:2px solid var(--urgent,#c33)"><b>Something went wrong:</b> ${esc(PTL.err)}</div>` : '';

  function listView() {
    const L = PTL.cache.list || { patients: [], counts: {} };
    const f = PTL.filter;
    const rows = L.patients.filter(p => f === 'all' || (f === 'needs' ? NEEDS.includes(p.group) : p.group === f));
    const action = p => ({ urgent: ['prof', 'Call'], ready_for_review: [p.next.startsWith('First') ? 'plan' : 'review', 'Review'],
      gone_quiet: ['prof', 'Check in'], new: [p.next.startsWith('Intake ready') ? 'intake' : 'prof', p.next.startsWith('Intake ready') ? 'Start visit' : 'Open'] }[p.group] || ['prof', 'Open']);
    const pain = p => p.pain_after_this_week == null && p.pain_after_last_week == null ? '<span class="hint" style="margin:0">None yet</span>'
      : `<span class="pain"><b>${p.pain_after_this_week ?? '–'}</b>${p.pain_after_last_week != null ? `<em class="fl">was ${p.pain_after_last_week}</em>` : ''}</span>`;
    let body = '', last = null;
    rows.forEach(p => {
      if (p.group !== last) { body += `<tr class="grp"><td colspan="6">${GROUPS[p.group][0]}<span>${rows.filter(x => x.group === p.group).length}</span></td></tr>`; last = p.group; }
      const [v, lab] = action(p);
      body += `<tr class="go" data-lv="open" data-pid="${p.patient_id}" data-v="${v}" tabindex="0"><td><div class="who">${avatar(p)}<div><b>${esc(p.name)}</b><span>${p.age ?? ''}${p.age ? ' · ' : ''}${esc(p.surgery.replace(' replacement', ''))}${p.week_post_op ? `, week ${p.week_post_op}` : ''}</span></div></div></td>
        <td><span class="tag ${GROUPS[p.group][1]}">${GROUPS[p.group][0]}</span></td><td>${pain(p)}</td><td>${p.last_session ? fmt(p.last_session) : '–'}</td><td>${esc(p.next)}</td>
        <td class="ract"><button class="btn${['urgent', 'ready_for_review'].includes(p.group) ? '' : ' sec'}" data-lv="open" data-pid="${p.patient_id}" data-v="${v}">${lab}</button></td></tr>`;
    });
    const c = L.counts || {};
    const tab = (k, l, n) => `<button aria-pressed="${f === k}" data-lv="filter" data-v="${k}">${l}<span>${n}</span></button>`;
    const needs = NEEDS.reduce((a, g) => a + (c[g] || 0), 0);
    const today = new Date();
    const add = PTL.addOpen ? `<div class="card pad" style="margin-top:14px"><h2>Add a patient</h2><p class="sub">They get a link to fill in the intake before the first visit.</p>
      <div class="form f3" style="margin-top:12px"><div class="fld"><label for="ap_name">Full name</label><input id="ap_name"></div><div class="fld"><label for="ap_phone">Mobile number</label><input id="ap_phone"></div>
      <div class="fld"><label for="ap_date">Surgery date</label><input id="ap_date" type="date" value="${new Date(Date.now() - 5 * 864e5).toISOString().slice(0, 10)}"></div>
      <div class="fld"><label for="ap_side">Knee</label><select id="ap_side"><option value="right">Right knee replacement</option><option value="left">Left knee replacement</option></select></div></div>
      <div class="foot" style="margin-top:12px"><button class="btn sec" data-lv="addtoggle">Cancel</button><button class="btn" data-lv="addsave">${I().send}Send intake link</button></div></div>` : '';
    return `<div class="mhead"><h1>Patients</h1><span class="sub" style="margin:0">${DOW[today.getDay()]}, ${MON[today.getMonth()]} ${today.getDate()}</span></div>${errBox()}
      <div class="ptool"><div class="tabs2 ptabs">${tab('needs', 'Needs you', needs)}${tab('all', 'All', L.patients.length)}${tab('new', 'New', c.new || 0)}${tab('discharged', 'Discharged', c.discharged || 0)}</div>
      <span style="flex:1"></span><button class="btn" data-lv="addtoggle">${I().plus}Add patient</button></div>${add}
      <div class="card ptable pt2 pfill"><div class="pscroll"><table><thead><tr><th>Patient</th><th>Status</th><th>Pain after<br><small>this week</small></th><th>Last session</th><th>Next</th><th></th></tr></thead><tbody>${body || '<tr><td colspan="6" class="empty">No patients here.</td></tr>'}</tbody></table></div></div>`;
  }

  function intakeView() {
    const P = PTL.cache.profile[PTL.pid];
    if (!P) return errBox();
    const it = P.intake;
    const a = (it && it.answers) || {};
    const rows = Object.entries(a).filter(([k, v]) => v !== '' && v != null && typeof v !== 'object' || Array.isArray(v)).slice(0, 40)
      .map(([k, v]) => `<dt>${esc(k.replace(/_/g, ' '))}</dt><dd>${esc(Array.isArray(v) ? v.join(', ') : v)}</dd>`).join('');
    return head(P.patient, 'list', 'Step 1 of 3: intake') + `<div class="grid2" style="margin-top:20px"><div class="card pad ai"><div class="aihd"><span class="spark">${I().spark}</span><h2>Intake summary</h2></div>
      <p class="aisum" style="white-space:pre-line">${esc(it ? it.summary : 'No intake yet. The patient has a link to fill it in.')}</p><p class="ainote">Check the full answers before you act on it.</p></div>
      <div class="card ans pad"><h2>Answers</h2><dl class="ldl">${rows || '<dd>None yet</dd>'}</dl></div></div>
      <div class="foot"><button class="btn" data-lv="go" data-v="visit">Next: clinic visit ${I().next}</button></div>`;
  }

  const VFIELDS = [
    ['Movement', [['bend_deg', 'Bend (flexion)', '°'], ['short_of_straight_deg', 'Short of straight', '°'], ['rom_mode', 'Measured', ['active', 'passive']]]],
    ['Strength', [['quad_lag', 'Quad lag', ['yes', 'no']], ['quad_grade', 'Quad strength', ['1', '2', '3', '3+', '4', '5']], ['pain_today', 'Pain today', '/10']]],
    ['Function', [['tug_sec', 'Timed Up and Go', 'sec'], ['chair_stand_30s', '30-second chair stand', 'stands'], ['walking_aid', 'Walking aid', ['None', 'Cane', 'Walker']], ['stairs', 'Stairs', ['independent', 'step to step, rail', 'not yet']]]],
    ['Knee', [['sw_r', 'Swelling, operated knee', 'cm'], ['sw_l', 'Other knee, to compare', 'cm'], ['incision', 'Incision', ['Healing well', 'Some redness', 'Drainage']]]],
  ];
  function visitView() {
    const P = PTL.cache.profile[PTL.pid];
    if (!P) return errBox();
    const f = (P.findings && P.findings.findings) || {};
    const val = k => k === 'quad_lag' ? (f.quad_lag == null ? '' : f.quad_lag ? 'yes' : 'no') : k === 'sw_r' ? ((f.swelling_cm || {}).right ?? '') : k === 'sw_l' ? ((f.swelling_cm || {}).left ?? '') : (f[k] ?? '');
    const field = ([k, l, u]) => Array.isArray(u)
      ? `<div class="fld"><label for="lv_${k}">${l}</label><select id="lv_${k}" data-lvf="${k}"><option value=""></option>${u.map(o => `<option${String(val(k)) === o ? ' selected' : ''}>${o}</option>`).join('')}</select></div>`
      : `<div class="fld"><label for="lv_${k}">${l}</label><div class="unit"><input id="lv_${k}" data-lvf="${k}" value="${esc(val(k))}" inputmode="decimal"><span>${u}</span></div></div>`;
    const txt = (k, l, v) => `<div class="fld"><label for="lv_${k}">${l}</label><textarea id="lv_${k}" data-lvf="${k}" rows="3">${esc(v)}</textarea></div>`;
    return head(P.patient, 'list', 'Step 2 of 3: clinic visit') + `<div class="vgrid"><div class="card pad">
      <div class="vcard"><div><h2>Clinic visit</h2><p class="sub" style="margin-top:2px">${fmt(new Date().toISOString())} · Riverside Physical Therapy</p></div><span class="vsave" id="lvsave">${I().check}Saved</span></div>
      ${VFIELDS.map(([t, fs]) => `<div class="vsec"><p class="eyebrow">${t}</p><div class="form f3">${fs.map(field).join('')}</div></div>`).join('')}
      <div class="vsec"><p class="eyebrow">Goals and precautions</p><div class="form f2">${txt('goals_week6', 'Goals by week 6 (one per line; the patient sees these)', (f.goals_week6 || []).join('\n'))}${txt('precautions', 'Precautions (one per line)', (f.precautions || []).join('\n'))}</div></div>
      <div class="vsec"><div class="form">${txt('notes', 'Short notes (PT only)', f.notes || '')}</div></div></div>
      <div><div class="card pad"><h2>From the intake</h2><p class="sub" style="white-space:pre-line">${esc(P.intake ? P.intake.summary : 'No intake yet.')}</p></div>
      <div class="card pad sec2 fpnext"><span class="spark">${I().spark}</span><div class="fptx"><h2>Next: first plan</h2><p class="sub" style="margin-top:4px">AI drafts it from this visit, the intake and the clinic's knee replacement protocol. Each exercise comes with a reason; you accept or reject each one before it's sent.</p></div>
      <div class="fpbtns"><button class="btn" data-lv="firstdraft">${I().spark}Draft plan with AI</button></div></div></div></div>
      <div class="foot"><button class="btn sec" data-lv="go" data-v="intake">Back</button></div>`;
  }

  // Where a draft came from: the AI, or the rules (and why the AI was not used).
  function srcLabel(req) {
    return req.draft_source === 'llm' ? 'AI draft' : `Rules draft${req.draft.ai_note ? ' · ' + req.draft.ai_note : ''}`;
  }
  function suggestions(req) {
    const items = req.draft.items;
    const und = items.filter(i => i.action !== 'hold' && !PTL.dec[i.exercise_id]).length;
    const acc = items.filter(i => i.action !== 'hold' && PTL.dec[i.exercise_id] === 'accept').length;
    const lab = { add: 'New', progress: 'Step up', easier: 'Lighter', pause: 'Pause', hold: 'Same' };
    const tagc = { add: 'new', progress: 'new', easier: 'rev', pause: 'urg', hold: 'mut' };
    const rows = items.map(i => {
      const e = ex(i.exercise_id), v = PTL.dec[i.exercise_id];
      const change = i.action === 'hold' ? '' : `${i.before ? `<s>${i.before.replace(' x ', ' × ')}</s> → ` : ''}${i.sets ? dose(i.sets, i.reps) : 'paused'}`;
      return `<div class="srow rvrow${i.action !== 'hold' && v !== 'reject' ? ' sugd' : ''}"><img src="${e.img}" alt=""><div><b>${esc(e.n)}</b><span class="tag ${tagc[i.action]}">${lab[i.action]}${change ? ': ' + change : ''}</span></div>
        <div class="rvctl"><span class="hint" style="margin:0">${i.action === 'hold' ? dose(i.sets, i.reps) : ''}</span></div>
        ${i.action !== 'hold' ? `<div class="fwhy"><span class="fwt">${I().spark}${esc(i.reason)}</span><div class="fab"><button class="facc${v === 'accept' ? ' on' : ''}" data-lv="dec" data-v="${i.exercise_id}|accept">${I().check}${v === 'accept' ? 'Accepted' : 'Accept'}</button><button class="frej${v === 'reject' ? ' on' : ''}" data-lv="dec" data-v="${i.exercise_id}|reject">${v === 'reject' ? 'Rejected' : 'Reject'}</button></div></div>` : `<div class="fwhy"><span class="fwt">${esc(i.reason)}</span></div>`}</div>`;
    }).join('');
    return { rows, und, acc };
  }

  function planView() {   // first-plan draft
    const P = PTL.cache.profile[PTL.pid], req = PTL.cache.request[PTL.pid];
    if (!P) return errBox();
    if (!req) return head(P.patient, 'list') + `<div class="card pad" style="margin-top:20px"><h2>No draft waiting</h2><p class="sub">Fill in the clinic visit, then press "Draft plan with AI".</p><div class="foot"><button class="btn" data-lv="go" data-v="visit">Go to the visit</button></div></div>`;
    const { rows, und, acc } = suggestions(req);
    const src = srcLabel(req);
    return head(P.patient, 'list', 'Step 3 of 3: first plan') + `<div class="grid2" style="margin-top:20px"><div class="card pad ai"><div class="aihd"><span class="spark">${I().spark}</span><h2>Week 1 plan</h2><span class="tag new" style="margin-left:auto">${src}</span></div>
      ${req.draft.note_for_pt ? `<p class="hint">${esc(req.draft.note_for_pt)}</p>` : ''}${rows}
      <div class="dec2"><button class="linkb" data-lv="firstdraft" id="lv-redraft">${I().spark}Redraft with AI</button></div></div>
      <div><div class="card pad"><h2>Note to ${esc(P.patient.name.split(' ')[0])}</h2><textarea id="lvnote" class="rvnote" rows="4" aria-label="Note">${esc(PTL.note || 'Here is your first week. Go slow, stop if the pain goes past 7, and message me anytime.')}</textarea>
      <div class="dec">${und ? `<button class="btn" disabled>${I().send}Review ${und} more to send</button>` : acc ? `<button class="btn" data-lv="send">${I().send}Send plan with ${acc} exercise${acc > 1 ? 's' : ''}</button>` : `<button class="btn" disabled>Accept at least one exercise</button>`}</div></div>
      <div class="card pad sec2"><h2>Visit findings used</h2><ul class="ailist">${Object.entries(req.summary.findings || {}).filter(([k, v]) => v != null && v !== '' && !['notes', 'done_checklist'].includes(k)).map(([k, v]) => `<li>${esc(k.replace(/_/g, ' '))}: ${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</li>`).join('') || '<li>None</li>'}</ul></div></div></div>
      <div class="foot"><button class="btn sec" data-lv="go" data-v="visit">Back to the visit</button></div>`;
  }

  function reviewView() {   // weekly review
    const P = PTL.cache.profile[PTL.pid], req = PTL.cache.request[PTL.pid];
    if (!P) return errBox();
    if (!req) return head(P.patient, 'list') + `<div class="card pad" style="margin-top:20px"><h2>No weekly draft waiting</h2><p class="sub">A draft is made at the start of each week from last week's sessions.</p><div class="foot"><button class="btn" data-lv="redraft">Draft from the latest sessions</button></div></div>`;
    if (req.kind === 'first') { A().S.view = 'plan'; return planView(); }
    const s = req.summary;
    const days = []; for (let d = new Date(s.week.start + 'T12:00:00'); d <= new Date(s.week.end + 'T12:00:00'); d.setDate(d.getDate() + 1)) days.push(d.toISOString().slice(0, 10));
    const byDate = Object.fromEntries(P.sessions.map(x => [x.date, x]));
    const rest = P.patient.rest_days || [];
    const bar = iso => { const x = byDate[iso], dn = DOW[new Date(iso + 'T12:00:00').getDay()];
      const cls = x ? (x.action === 'rest' ? 'rest' : x.stopped_early ? 'part' : '') : rest.includes(dn) ? 'rest' : 'rest';
      return `<div class="wkd ${cls}"><span>${dn}</span><em>${x && x.action !== 'rest' ? I().check : ''}</em><i>${x ? (x.action === 'rest' ? 'Rested (pain)' : 'Pain ' + (x.pain_end ?? x.pain_before)) : rest.includes(dn) ? 'Rest' : 'Missed'}</i></div>`; };
    const exRows = s.exercises.map(e => { const fc = e.feeling_counts || {};
      const felt = Object.entries(fc).filter(([, n]) => n).map(([k, n]) => `${FEEL[k]} ×${n}`).join(', ') || '–';
      return `<tr><td><div class="who"><img src="${ex(e.exercise_id).img}" alt="" style="border-radius:8px;width:52px;height:36px;flex:0 0 52px"><b>${esc(ex(e.exercise_id).n)}</b></div></td><td>${e.days_done} of ${s.planned_days}</td><td>${felt}</td><td>${e.form_avg != null ? e.form_avg : '<span class="hint" style="margin:0">No camera yet</span>'}</td></tr>`; }).join('');
    const notes = P.sessions.filter(x => x.date >= s.week.start && x.date <= s.week.end).flatMap(x => [
      x.stopped_early ? `<li><b>${fmt(x.date)}, ended early:</b> ${esc(x.stop_reason || '')}</li>` : '',
      ...(x.bothered || []).map(b => `<li><b>${fmt(x.date)}:</b> ${esc(ex(b.exercise_id).n)} bothered: ${esc(b.reason)}${b.other ? ' (' + esc(b.other) + ')' : ''}</li>`),
      x.worry ? `<li><b>${fmt(x.date)}, worried:</b> ${esc([...(x.worry.symptoms || []), x.worry.other].filter(Boolean).join(', '))}</li>` : '',
      ...x.exercises.filter(e => e.note).map(e => `<li><b>${fmt(x.date)} note:</b> "${esc(e.note)}"</li>`),
      x.note ? `<li><b>${fmt(x.date)}, after the session:</b> "${esc(x.note)}"</li>` : '']).filter(Boolean);
    const { rows, und, acc } = suggestions(req);
    return head(P.patient, 'list', `Week of ${fmt(s.week.start).slice(5)}`) + `<div class="grid2" style="margin-top:20px"><div>
      <div class="card pad"><h2>The week</h2><div class="painrow"><div class="pbox"><b>${s.days_checked_in - s.days_rest}</b><small> of ${s.planned_days}</small><span>sessions</span></div>
      <div class="pbox"><b>${s.pain_end_avg ?? '–'}</b><small>/10</small><span>avg pain after</span></div><div class="pbox"><b>${s.pain_today_avg ?? '–'}</b><small>/10</small><span>avg pain before</span></div></div>
      <p class="eyebrow" style="margin-top:18px">Day by day</p><div class="wkstrip">${days.map(bar).join('')}</div></div>
      <div class="card pad sec2"><h2>By exercise</h2><table class="extab"><thead><tr><th>Exercise</th><th>Done</th><th>Felt</th><th>Form (camera)</th></tr></thead><tbody>${exRows}</tbody></table></div>
      <div class="card pad sec2"><h2>From ${esc(P.patient.name.split(' ')[0])}</h2><ul class="ailist">${notes.join('') || '<li>No notes or worries this week</li>'}</ul></div></div>
      <div><div class="card pad ai"><div class="aihd"><span class="spark">${I().spark}</span><h2>Next week's plan</h2><span class="tag new" style="margin-left:auto">${req.draft.items.filter(i => i.action !== 'hold').length} suggestions · ${srcLabel(req)}</span></div>
      <p class="sub">${esc(req.draft.explanation)}</p>${req.draft.note_for_pt ? `<p class="hint">${esc(req.draft.note_for_pt)}</p>` : ''}${rows}
      <div class="dec2"><button class="linkb" data-lv="redraft" id="lv-redraft">${I().spark}Redraft with AI</button></div></div>
      <div class="card pad sec2"><h2>Note to ${esc(P.patient.name.split(' ')[0])}</h2><textarea id="lvnote" class="rvnote" rows="4">${esc(PTL.note)}</textarea>
      <div class="dec">${und ? `<button class="btn" disabled>${I().send}Review ${und} more to send</button>` : acc ? `<button class="btn" data-lv="send">${I().send}Send plan with ${acc} change${acc > 1 ? 's' : ''}</button>` : `<button class="btn" data-lv="keep">${I().check}Keep current plan</button>`}</div>
      <div class="dec2"><button class="linkb" data-lv="go" data-v="prof">${I().cal}Schedule a visit or call</button>${acc ? '<button class="linkb" data-lv="keep">Keep current plan</button>' : ''}</div></div></div></div>`;
  }

  function profView() {
    const P = PTL.cache.profile[PTL.pid];
    if (!P) return errBox();
    const p = P.patient;
    const sess = P.sessions.slice(0, 14).map(x => `<tr><td>${fmt(x.date)}</td><td>${x.action === 'rest' ? '<span class="tag urg">Rested</span>' : x.stopped_early ? '<span class="tag rev">Ended early</span>' : '<span class="tag ok">Done</span>'}</td>
      <td>${x.pain_before} → ${x.pain_end ?? '–'}</td><td>${x.exercises.map(e => `${esc(ex(e.exercise_id).n)}${e.feeling ? ' (' + FEEL[e.feeling] + ')' : ''}`).join(', ') || '–'}${x.note ? `<div class="hint" style="margin:4px 0 0">"${esc(x.note)}"</div>` : ''}</td></tr>`).join('');
    const pain = P.pain_by_week.map(w => `<div class="pbox"><b>${w.pain_after}</b><small>/10</small><span>wk of ${fmt(w.week_start).slice(5)}</span></div>`).join('');
    const alerts = P.alerts.filter(a => a.status !== 'closed').map(a => `<li><span class="tag ${a.tier === 'urgent' ? 'urg' : 'rev'}">${a.tier}</span> ${fmt(a.date)}: ${esc(a.reason)}</li>`).join('');
    const visits = P.visits.map(v => `<li>${fmt(v.date)} ${esc(v.time || '')} · ${esc(v.kind.replace('_', ' '))} · ${esc(v.status)}${v.reason ? ' · ' + esc(v.reason) : ''}</li>`).join('');
    const calls = P.calls.map(c => `<li>${fmt(c.date)} · ${c.minutes} min · ${esc(c.agreed)}</li>`).join('');
    const hist = P.plan_history.slice(0, 6).map(h => `<li><b>${fmt(h.start_date)}</b>${h.end_date ? '' : ' (current)'}: ${esc(h.plan)}</li>`).join('');
    const disc = p.group === 'discharged';
    return head(p, 'list') + `<div class="grid2" style="margin-top:20px"><div>
      ${alerts ? `<div class="card pad" style="border:2px solid var(--urgent,#c33)"><h2>Open alerts</h2><ul class="ailist">${alerts}</ul><p class="hint">Logging a call closes them.</p></div>` : ''}
      <div class="card pad${alerts ? ' sec2' : ''}"><h2>Sessions</h2><table class="extab"><thead><tr><th>Day</th><th></th><th>Pain before → after</th><th>Exercises (felt)</th></tr></thead><tbody>${sess || '<tr><td colspan="4">No sessions yet</td></tr>'}</tbody></table></div>
      <div class="card pad sec2"><h2>Pain after, by week</h2><div class="painrow" style="flex-wrap:wrap">${pain || '<p class="sub">No data yet</p>'}</div></div>
      <div class="card pad sec2"><h2>Plan history</h2><ul class="ailist">${hist || '<li>No plan yet</li>'}</ul></div></div>
      <div><div class="card pad"><h2>Log a call</h2><div class="form f2"><div class="fld"><label for="lv_cmin">Minutes</label><input id="lv_cmin" value="8" inputmode="numeric"></div><div class="fld"><label for="lv_cout">What you agreed</label><input id="lv_cout" placeholder="e.g. Rest today, visit Monday"></div></div>
      <div class="dec"><button class="btn" data-lv="call">${I().phone}Log call</button></div><ul class="ailist">${calls}</ul></div>
      <div class="card pad sec2"><h2>Schedule a visit</h2><div class="form f3"><div class="fld"><label for="lv_vd">Date</label><input id="lv_vd" type="date"></div><div class="fld"><label for="lv_vt">Time</label><input id="lv_vt" type="time" value="10:30"></div><div class="fld"><label for="lv_vr">Reason (patient sees it)</label><input id="lv_vr" placeholder="Check the knee in person"></div></div>
      <div class="dec"><button class="btn sec" data-lv="visit">${I().cal}Schedule</button></div><ul class="ailist">${visits}</ul></div>
      <div class="card pad sec2"><h2>Private notes</h2><textarea id="lv_pn" rows="4" data-lvpn placeholder="Only you see these.">${esc(P.pt_private_notes)}</textarea><span class="hint" id="lv_pns"></span></div>
      <div class="card pad sec2"><h2>Actions</h2><div class="dec" style="gap:10px;flex-wrap:wrap">${P.intake ? '<button class="btn sec" data-lv="go" data-v="intake">Intake</button>' : ''}<button class="btn sec" data-lv="go" data-v="visit">Clinic visit</button>${P.plan.length ? '<button class="btn sec" data-lv="go" data-v="review">Weekly review</button>' : ''}${disc ? '' : '<button class="btn sec" data-lv="discharge">Discharge</button>'}</div></div></div></div>`;
  }

  function doneView() {
    const d = PTL.done || {};
    return `<div class="card sent"><div class="ck">${I().check}</div><h1>${esc(d.title || 'Done')}</h1><p class="sub" style="font-size:18px">${esc(d.sub || '')}</p><ul>${(d.items || []).map(x => `<li>${I().check}${esc(x)}</li>`).join('')}</ul>
      <div class="foot sentb" style="justify-content:center;margin-top:28px"><button class="btn sec" data-lv="go" data-v="prof">Open profile</button><button class="btn" data-lv="go" data-v="list">Back to patients</button></div></div>`;
  }

  // ---------- messages ----------
  const hm = iso => { const d = new Date(iso); const h = d.getHours(); return `${h % 12 || 12}:${String(d.getMinutes()).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`; };
  async function loadMsgs(pid, markRead) {
    const th = (await call('/api/threads')).threads;
    PTL.cache.threads = th;
    if (!pid && !PTL.thread) pid = th.length ? th[0].patient_id : 'walter';
    if (pid) PTL.thread = pid;
    const r = await call(`/api/messages?patient_id=${PTL.thread}${markRead ? '&reader=pt' : ''}`);
    const changed = JSON.stringify(r.messages.map(m => m.msg_id)) !== JSON.stringify((PTL.cache.msgs || []).map(m => m.msg_id));
    PTL.cache.msgs = r.messages;
    if (markRead) PTL.cache.threads = (await call('/api/threads')).threads;
    syncSideCount();
    return changed;
  }
  function syncSideCount() {
    // The prototype's side menu counts unread threads from S.threads; keep it in step with the server.
    const S = A().S, th = PTL.cache.threads || [];
    S.threads.forEach(t => { t.unread = false; });
    th.filter(t => t.unread > 0).forEach(t => {
      let x = S.threads.find(y => y.id === t.patient_id);
      if (!x) { x = { id: t.patient_id, who: t.name, ini: ini(t.name), sub: '', msgs: [] }; S.threads.push(x); }
      x.unread = true;
    });
  }
  function msgView() {
    const L = PTL.cache.list || { patients: [] };
    const th = PTL.cache.threads || [];
    const pid = PTL.thread;
    // Conversations first (newest), then other patients you can start a conversation with.
    const people = th.map(t => ({ ...L.patients.find(p => p.patient_id === t.patient_id) || { patient_id: t.patient_id, name: t.name, surgery: '' }, t }))
      .concat(L.patients.filter(p => !th.some(t => t.patient_id === p.patient_id) && p.group !== 'discharged').map(p => ({ ...p, t: null })));
    const cur = people.find(p => p.patient_id === pid) || people[0] || {};
    const first = (cur.name || '').split(' ')[0];
    const lastTxt = t => t ? (t.last.sender === 'pt' ? 'You: ' : '') + t.last.text : 'No messages yet';
    let day = null;
    const bubbles = (PTL.cache.msgs || []).map(m => {
      const d = m.created_at.slice(0, 10);
      const sep = d !== day ? `<div class="dsep"><span>${d === new Date().toISOString().slice(0, 10) ? 'Today' : fmt(d)}</span></div>` : '';
      day = d;
      const seen = m.sender === 'pt' ? (m.read_at ? ' · Seen' : ' · Delivered') : '';
      return sep + `<div class="bub ${m.sender === 'pt' ? 'mine' : 'them'}">${m.kind !== 'text' ? `<span class="btag">${esc(m.kind)}</span>` : ''}<p>${esc(m.text)}</p><span class="bt">${hm(m.created_at)}${seen}</span></div>`;
    }).join('') || `<p class="sub" style="padding:24px">No messages with ${esc(first)} yet. Write the first one below.</p>`;
    const qr = ['Thanks for letting me know.', 'Let\'s talk by phone.', 'Please rest today and ice the knee.'];
    return `<div class="mhead"><h1>Messages</h1></div>${errBox()}
      <div class="msgwrap card" style="grid-template-columns:260px minmax(0,1fr) 290px"><div class="tlist"><div class="band tsrow"><b style="padding:0 6px">Conversations</b></div>
      ${people.map(p => `<button class="trow${p.patient_id === cur.patient_id ? ' on' : ''}" data-lv="thread" data-pid="${p.patient_id}">${avatar(p)}<div><b>${esc(p.name)}${p.group === 'urgent' ? ' <span class="tag urg">Alert</span>' : ''}</b><span>${esc(lastTxt(p.t))}</span></div>${p.t && p.t.unread ? '<i class="udot" aria-label="Unread"></i>' : ''}</button>`).join('')}</div>
      <div class="tview"><div class="thd band">${avatar(cur)}<div><b>${esc(cur.name || '')}</b><span>${esc(cur.surgery || '')}${cur.week_post_op != null ? ', week ' + cur.week_post_op : ''}</span></div>
      <button class="btn sec" style="margin-left:auto;height:38px" data-lv="open" data-pid="${cur.patient_id}" data-v="prof">${I().phone}Profile</button></div>
      <div class="tmsgs" id="tmsgs">${bubbles}</div>
      ${cur.group === 'urgent' ? '<div class="tnote urg">This patient has an urgent alert. A call is faster than a reply.</div>' : ''}
      <div class="qrs">${qr.map(q => `<button data-lv="mqr" data-v="${esc(q)}">${esc(q)}</button>`).join('')}</div>
      <div class="tcomp"><input id="lvmdraft" value="${esc(PTL.mdraft || '')}" placeholder="Write to ${esc(first)}" aria-label="Message"><button class="btn" data-lv="msgsend">${I().send}Send</button></div></div>
      <aside class="tctx"><div class="band cband"><b>Patient details</b></div><div class="cbody"><div class="cprof">${avatar(cur)}<b>${esc(cur.name || '')}</b><span>${cur.age ? cur.age + ' · ' : ''}${esc(cur.surgery || '')}</span></div>
      <div class="ctxst"><div><b>${cur.pain_after_this_week ?? '–'}</b><span>pain after, this week</span></div><div><b>${cur.data_days_30 ?? '–'}</b><span>data days in 30</span></div></div>
      <dl class="cdl"><dt>Status</dt><dd>${cur.group ? `<span class="tag ${GROUPS[cur.group][1]}">${GROUPS[cur.group][0]}</span>` : '–'}</dd><dt>Last session</dt><dd>${cur.last_session ? fmt(cur.last_session) : '–'}</dd><dt>Next</dt><dd>${esc(cur.next || '–')}</dd></dl></div></aside></div>`;
  }
  async function sendPtMsg(text) {
    text = (text || '').trim();
    if (!text) return;
    await call('/api/messages', { patient_id: PTL.thread, sender: 'pt', text });
    PTL.mdraft = '';
    await loadMsgs(PTL.thread, true);
    A().render();
    scrollMsgs();
  }
  function scrollMsgs() {
    // Scroll only the message list. (scrollIntoView would also scroll the frame and hide the reply box.)
    const t = document.getElementById('tmsgs');
    if (t) t.scrollTop = t.scrollHeight;
    document.querySelectorAll('.msgwrap, .tview').forEach(el => { el.scrollTop = 0; });
  }


  // ---------- Schedule (visits from the database, calls to make, call history) ----------
  const DWK = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'];
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const monday = () => { const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return d; };
  function schedRange() {
    const S = A().S;
    if (S.calV === 'month') {
      const m0 = new Date(); m0.setDate(1); m0.setMonth(m0.getMonth() + (S.calOff || 0)); m0.setHours(12, 0, 0, 0);
      const start = new Date(m0); start.setDate(1 - ((m0.getDay() + 6) % 7));
      const end = new Date(start); end.setDate(start.getDate() + 41);
      return { m0, start, end };
    }
    const start = monday(); start.setDate(start.getDate() + 7 * (S.calOff || 0));
    const end = new Date(start); end.setDate(start.getDate() + 6);
    return { start, end };
  }
  async function loadSched(force) {
    const r = schedRange(), key = iso(r.start) + '|' + iso(r.end);
    if (!force && PTL.cache.sched && PTL.cache.sched.key === key) return false;
    const before = JSON.stringify(PTL.cache.sched || {});
    PTL.cache.sched = { key, ...(await call(`/api/schedule?start=${iso(r.start)}&end=${iso(r.end)}`)) };
    return JSON.stringify(PTL.cache.sched) !== before;
  }
  const hh = t => { const [h, m] = (t || '09:00').split(':').map(Number); return h + (m || 0) / 60; };
  const fmtH = h => { const H = Math.floor(h), m = Math.round((h - H) * 60); return ((H % 12) || 12) + ':' + String(m).padStart(2, '0') + (H < 12 ? ' am' : ' pm'); };
  const short = n => { const w = (n || '').split(' '); return w[0] + (w[1] ? ' ' + w[1][0] + '.' : ''); };
  function schedView() {
    const S = A().S, D = PTL.cache.sched, r = schedRange(), H0 = 8, H1 = 17;
    if (!D || D.key !== iso(r.start) + '|' + iso(r.end)) loadSched().then(ch => ch && A().S.view === 'sched' && A().render()).catch(e => { PTL.err = e.message; A().render(); });
    const evs = ((D && D.key === iso(r.start) + '|' + iso(r.end)) ? D.events : []).map(e => ({ ...e, st: Math.min(Math.max(hh(e.time), H0), H1 - 0.75) }));
    const today = iso(new Date());
    const dayDate = i => { const d = new Date(r.start); d.setDate(d.getDate() + i); return d; };
    const cols = S.calV === 'day' ? [S.calDay || 0] : [0, 1, 2, 3, 4];
    const pct = h => ((h - H0) / (H1 - H0) * 100).toFixed(3) + '%';
    const sel = S.evSel != null ? evs[S.evSel] : null;
    const range = S.calV === 'month' ? `${MON[r.m0.getMonth()]} ${r.m0.getFullYear()}`
      : S.calV === 'day' ? (d => `${['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'][S.calDay || 0]}, ${MON[d.getMonth()]} ${d.getDate()}`)(dayDate(S.calDay || 0))
      : (a => `${MON[a.getMonth()]} ${a.getDate()} to ${dayDate(4).getMonth() !== a.getMonth() ? MON[dayDate(4).getMonth()] + ' ' : ''}${dayDate(4).getDate()}`)(dayDate(0));
    const bar = `<div class="calbar"><div class="calnav"><button class="btn sec" data-calnav="0">Today</button><button class="cnb" data-calnav="-1" aria-label="Previous">${I().back}</button><button class="cnb" data-calnav="1" aria-label="Next">${I().next}</button><b>${range}</b></div><div class="seg">${['day', 'week', 'month'].map(v => `<button data-calv="${v}" class="${(S.calV || 'week') === v ? 'on' : ''}">${v[0].toUpperCase() + v.slice(1)}</button>`).join('')}</div></div>`;
    let cal;
    if (S.calV === 'month') {
      const cells = Array.from({ length: 42 }, (_, i) => dayDate(i));
      cal = `<div class="mgrid">${['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(h => `<b>${h}</b>`).join('')}${cells.map(x => {
        const k = iso(x), list = evs.filter(e => e.date === k);
        const we = x.getDay() === 0 || x.getDay() === 6, dn = Math.round((x - monday()) / 864e5), wk = Math.floor(dn / 7), di = ((dn % 7) + 7) % 7;
        return `<button class="mcell${x.getMonth() !== r.m0.getMonth() ? ' off' : ''}${we ? ' we' : ''}" ${we ? 'disabled' : `data-calpick="${di}" data-calwk="${wk}"`}><span class="mnum${k === today ? ' tdy' : ''}">${x.getDate()}</span>${list.map(e => `<i class="mev ${e.k}">${esc(short(e.name))}</i>`).join('')}</button>`; }).join('')}</div>`;
    } else {
      cal = `<div class="calhd" style="grid-template-columns:56px repeat(${cols.length},1fr)"><span></span>${cols.map(c => `<b>${DWK[c]} ${dayDate(c).getDate()}</b>`).join('')}</div>
      <div class="calbody" style="grid-template-columns:56px repeat(${cols.length},minmax(0,1fr))"><div class="hrs">${Array.from({ length: H1 - H0 }, (_, i) => `<span style="top:${pct(H0 + i)}">${fmtH(H0 + i).replace(':00', '')}</span>`).join('')}</div>
      ${cols.map((di, ci) => `<div class="dcol" style="grid-column:${ci + 2}">${evs.map((e, i) => e.date !== iso(dayDate(di)) ? '' : `<button class="cev ${e.k}${S.evSel === i ? ' on' : ''}" data-ev="${i}" style="top:${pct(e.st)};height:calc(${(Math.max(e.minutes, 40) / 60 / (H1 - H0) * 100).toFixed(3)}% - 4px)${e.status === 'done' ? ';opacity:.55' : ''}"><b>${esc(short(e.name))}</b><span>${fmtH(hh(e.time))}</span></button>`).join('')}</div>`).join('')}
      ${Array.from({ length: H1 - H0 }, (_, i) => `<i class="hl" style="top:${pct(H0 + i)}"></i>`).join('')}</div>`;
    }
    const detail = sel ? `<div class="evd"><div><p class="eyebrow">${fmt(sel.date)} · ${fmtH(hh(sel.time))} to ${fmtH(hh(sel.time) + sel.minutes / 60)} · ${sel.type}${sel.status === 'done' ? ' · done' : ''}</p><h2 style="margin-top:4px">${esc(sel.name)}</h2><p class="sub" style="margin-top:2px">${esc(sel.reason || (sel.kind === 'first' ? 'First visit' : 'Clinic visit'))}</p><p class="ctxl" style="margin-top:8px"><b>Before the visit:</b> ${esc(sel.prep)}</p></div><div class="evdb"><button class="xbtn" data-ev="${S.evSel}" aria-label="Close">×</button><button class="btn sec" data-lv="open" data-v="prof" data-pid="${sel.patient_id}">Open patient</button></div></div>` : '';
    const calls = D ? D.calls_to_make : [], hist = D ? D.call_history : [];
    const crow = c => `<div class="crow"><div class="cmain"><b>${esc(c.name)}</b><span>${esc(c.why)}</span></div><span class="cdue${c.k === 'urg' ? ' now' : ''}">${esc(c.due)}</span>
      <div class="cact"><span class="cph">${I().phone}${esc(c.phone || 'No phone')}</span>${PTL.logFor === c.patient_id ? '' : `<button class="btn sec" data-lv="schlog" data-pid="${c.patient_id}">Log call</button>`}</div>
      ${PTL.logFor === c.patient_id ? `<div style="grid-column:1/-1;display:flex;gap:8px;margin-top:8px;flex-wrap:wrap"><input id="lv_scmin" type="number" min="1" value="10" aria-label="Minutes" style="width:80px"><input id="lv_scout" placeholder="What you agreed" style="flex:1;min-width:160px"><button class="btn" data-lv="schsave" data-pid="${c.patient_id}">Save</button><button class="btn sec" data-lv="schlog" data-pid="">Cancel</button></div>` : ''}</div>`;
    return `${errBox()}<div class="mhead"><h1>Schedule</h1></div>
      <div class="schgrid"><div class="card wcal">${bar}${cal}${detail}
      <div class="calkey"><span><i class="clin"></i>In clinic</span><span><i class="new"></i>New patient</span><span><i class="call"></i>Phone</span><span><i class="urg"></i>After an alert</span></div></div>
      <div class="schr"><div class="card pad"><div class="selhd"><h2>Calls to make</h2></div><div class="clist">${calls.map(crow).join('') || '<p class="sub">No calls due.</p>'}</div></div>
      <div class="card pad hcard"><div class="selhd"><h2>Call history</h2></div><div class="hlist">${hist.map(h => `<div class="hrow"><span class="hchk">${I().check}</span><div><div class="hrt"><b>${esc(h.name)}</b><span>${fmt(h.date)}</span></div><p>${esc(h.agreed || '')}${h.minutes ? ` (${h.minutes} min)` : ''}</p></div></div>`).join('') || '<p class="sub">No calls logged yet.</p>'}</div></div></div></div>`;
  }
  PTL.views = { sched: schedView, msg: msgView, list: listView, wlist: listView, intake: intakeView, visit: visitView, plan: planView, review: reviewView, prof: profView, lvdone: doneView, sent: doneView, wsent: doneView, kept: doneView };

  // ---------- actions ----------
  let saveT = null;
  function collectFindings() {
    const v = k => { const el = document.getElementById('lv_' + k); return el ? el.value.trim() : ''; };
    const num = k => v(k) === '' ? null : Number(v(k));
    const lines = k => v(k) ? v(k).split('\n').map(x => x.trim()).filter(Boolean) : null;
    const sw = (v('sw_r') && v('sw_l')) ? { right: Number(v('sw_r')), left: Number(v('sw_l')) } : null;
    return { bend_deg: num('bend_deg'), short_of_straight_deg: num('short_of_straight_deg'), rom_mode: v('rom_mode') || null,
      quad_lag: v('quad_lag') === '' ? null : v('quad_lag') === 'yes', quad_grade: v('quad_grade') || null, pain_today: num('pain_today'),
      tug_sec: num('tug_sec'), chair_stand_30s: num('chair_stand_30s'), walking_aid: v('walking_aid') || null, stairs: v('stairs') || null,
      swelling_cm: sw, incision: v('incision') || null, goals_week6: lines('goals_week6'), precautions: lines('precautions'), notes: v('notes') || null };
  }
  async function saveFindings() {
    const s = document.getElementById('lvsave');
    try { await call('/api/visit/findings', { patient_id: PTL.pid, fields: collectFindings() }); if (s) s.innerHTML = I().check + 'Saved'; }
    catch (e) { if (s) s.textContent = 'Not saved: ' + e.message; }
  }

  function busy(el) { el.disabled = true; el.textContent = 'Drafting… (AI can take ~10 s)'; }

  async function act(el) {
    const d = el.dataset, S = A().S;
    PTL.err = null;
    try {
      switch (d.lv) {
        case 'go': return open(d.v, PTL.pid);
        case 'open': return open(d.v, d.pid);
        case 'filter': PTL.filter = d.v; return A().render();
        case 'thread': PTL.mdraft = ''; await loadMsgs(d.pid, true); A().render(); return scrollMsgs();
        case 'msgsend': return sendPtMsg((document.getElementById('lvmdraft') || {}).value);
        case 'mqr': PTL.mdraft = d.v; A().render(); { const x = document.getElementById('lvmdraft'); if (x) { x.focus(); x.setSelectionRange(x.value.length, x.value.length); } } return;
        case 'schlog': PTL.logFor = d.pid || null; S._keep = true; A().render(); { const x = document.getElementById('lv_scout'); if (x) x.focus(); } return;
        case 'schsave': {
          await call('/api/calls', { patient_id: d.pid, minutes: Number(document.getElementById('lv_scmin').value || 0), agreed: document.getElementById('lv_scout').value });
          PTL.logFor = null; await loadSched(true); S._keep = true; return A().render();
        }
        case 'addtoggle': PTL.addOpen = !PTL.addOpen; return A().render();
        case 'addsave': {
          const name = document.getElementById('ap_name').value.trim();
          if (!name) { document.getElementById('ap_name').focus(); return; }
          await call('/api/patients/add', { name, phone: document.getElementById('ap_phone').value, surgery_date: document.getElementById('ap_date').value,
            side: document.getElementById('ap_side').value, surgery: 'Knee replacement' });
          PTL.addOpen = false; return open('list');
        }
        case 'dec': { const [id, v] = d.v.split('|'); PTL.dec[id] = PTL.dec[id] === v ? undefined : v; PTL.note = (document.getElementById('lvnote') || {}).value ?? PTL.note; S._keep = true; return A().render(); }
        case 'firstdraft': { busy(el); clearTimeout(saveT); if (document.getElementById('lvsave')) await saveFindings();
          await call('/api/first-plan/draft', { patient_id: PTL.pid }); return open('plan'); }
        case 'redraft': { busy(el); const r = PTL.cache.request[PTL.pid];
          await call('/api/adjustment/draft', { patient_id: PTL.pid, week_end: r ? r.week_end : undefined }); return open('review'); }
        case 'send': case 'keep': {
          const req = PTL.cache.request[PTL.pid], P = PTL.cache.profile[PTL.pid];
          const decisions = {}; req.draft.items.forEach(i => { if (i.action !== 'hold') decisions[i.exercise_id] = PTL.dec[i.exercise_id] || 'reject'; });
          const note = (document.getElementById('lvnote') || {}).value || '';
          const res = await call('/api/adjustment/decide', { request_id: req.request_id, approve: d.lv === 'send', decisions, note, pt_name: PT_NAME });
          const first = req.kind === 'first';
          PTL.done = d.lv === 'keep' ? { title: 'Current plan kept', sub: `${P.patient.name}'s plan continues next week.`, items: ['You can still message or schedule a visit'] }
            : { title: first ? 'First plan sent' : 'Updated plan sent', sub: `${P.patient.name} sees it in the app now.`,
                items: [`${res.plan.length} exercises: ${res.plan.map(p => `${ex(p.exercise_id).n} ${dose(p.sets, p.reps)}`).join(', ')}`, `Signed by you: ${PT_NAME}`, 'Your review time is logged for remote monitoring'] };
          S.view = 'lvdone'; return A().render();
        }
        case 'call': {
          await call('/api/calls', { patient_id: PTL.pid, minutes: Number(document.getElementById('lv_cmin').value || 0), agreed: document.getElementById('lv_cout').value });
          return open('prof');
        }
        case 'visit': {
          const date = document.getElementById('lv_vd').value; if (!date) { document.getElementById('lv_vd').focus(); return; }
          await call('/api/visits/schedule', { patient_id: PTL.pid, date, time: document.getElementById('lv_vt').value, reason: document.getElementById('lv_vr').value });
          return open('prof');
        }
        case 'discharge': {
          await call('/api/discharge', { patient_id: PTL.pid, note: 'Great work. Keep up your home exercises.', community_program: true, checklist: ['Home program reviewed'] });
          return open('prof');
        }
      }
    } catch (e) { PTL.err = e.message; A().render(); }
  }

  PTL.init = async function () {
    try { await call('/api/clinic'); } catch (e) { return; }      // no server: plain prototype
    PTL.live = true;
    const dev = document.getElementById('device');
    dev.addEventListener('click', ev => {
      const el = ev.target.closest('[data-lv]');
      if (!el) return;
      ev.stopPropagation(); ev.preventDefault();
      act(el);
    }, true);
    dev.addEventListener('keydown', ev => { if (ev.target.id === 'lvmdraft' && ev.key === 'Enter') { ev.preventDefault(); ev.stopPropagation(); sendPtMsg(ev.target.value); } }, true);
    dev.addEventListener('input', ev => {
      const t = ev.target;
      if (t.id === 'lvmdraft') { PTL.mdraft = t.value; ev.stopPropagation(); return; }
      if (t.dataset.lvf !== undefined) { const s = document.getElementById('lvsave'); if (s) s.textContent = 'Saving…'; clearTimeout(saveT); saveT = setTimeout(saveFindings, 600); }
      if (t.dataset.lvpn !== undefined) { clearTimeout(saveT); saveT = setTimeout(() => call('/api/pt-notes', { patient_id: PTL.pid, text: t.value }).then(() => { const s = document.getElementById('lv_pns'); if (s) s.textContent = 'Saved'; }), 600); }
    }, true);
    dev.addEventListener('change', ev => { if (ev.target.dataset.lvf !== undefined) { clearTimeout(saveT); saveFindings(); } }, true);
    // Side nav "Patients" goes to the live list.
    dev.addEventListener('click', ev => {
      const b = ev.target.closest('[data-go="list"],[data-go="wlist"],[data-go="msg"],[data-go="sched"]');
      if (b) { ev.stopPropagation(); const v = b.dataset.go === 'wlist' ? 'list' : b.dataset.go; open(v).then(() => { if (v === 'msg') scrollMsgs(); }); }
    }, true);
    // Check for new patient messages every few seconds (keeps what you are typing).
    setInterval(async () => {
      try {
        const S = A().S;
        if (S.view === 'msg') {
          const focused = document.activeElement && document.activeElement.id === 'lvmdraft';
          const pos = focused ? document.activeElement.selectionStart : null;
          if (await loadMsgs(PTL.thread, true)) {
            A().render(); scrollMsgs();
            if (focused) { const x = document.getElementById('lvmdraft'); if (x) { x.focus(); x.setSelectionRange(pos, pos); } }
          }
        } else {
          if (S.view === 'sched' && !PTL.logFor && await loadSched(true)) { S._keep = true; A().render(); }
          const before = JSON.stringify(PTL.cache.threads || []);
          PTL.cache.threads = (await call('/api/threads')).threads; syncSideCount();
          if (JSON.stringify(PTL.cache.threads) !== before && S.view === 'list') A().render();
        }
      } catch (e) { /* server busy or stopped */ }
    }, 4000);
    const st = document.createElement('style');
    st.textContent = '.msgwrap{grid-template-rows:minmax(0,1fr);height:min(720px,calc(100vh - 230px));min-height:420px!important;flex:0 0 auto!important}.msgwrap>.tview,.msgwrap>.tlist,.msgwrap>.tctx{min-height:0}.tview{overflow:hidden}.tview>.tmsgs{min-height:0}.tview>.thd,.tview>.qrs,.tview>.tcomp,.tview>.tnote{flex:0 0 auto}.ldl{display:grid;grid-template-columns:auto 1fr;gap:6px 16px;margin:12px 0 0}.ldl dt{color:var(--ink-muted,#666);text-transform:capitalize}.ldl dd{margin:0}';
    document.head.appendChild(st);
    PTL.cache.threads = (await call('/api/threads').catch(() => ({ threads: [] }))).threads;
    syncSideCount();
    open('list');
  };
})();
