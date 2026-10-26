/* =====================================================================
   THE UNIFIED CLIENT FILE in ConsultCastAI (the suite's
   docs/CLIENT_FILE.md). The consultant picks which client a practice
   session is for (or none); when the session ends, its debrief and final
   scores go into that client's file in the clients folder, in the `ccai`
   section:

     ccai.sessions: [{ id, persona, scenario, createdAt, durationSec,
                       scores: { pressure, trust, specificity }, debrief }]

   and lifecycle.lastPracticeSession. A session with no client goes into
   _practice.client.json. The transcript never leaves the server (its own
   retention rules apply there). clientfile.js is the suite's shared
   module, copied unchanged.

   The client comes preselected when the suite opens this app for a client
   (?client=<id>, kept for the tab so it survives signing in). Plain
   script, loaded before the page's module; the module calls
   CcaiClientFile.record() once a debrief is shown.
   ===================================================================== */
(function(){
  const PICK_KEY = 'consultcastai_client';   // sessionStorage: the client chosen in this tab
  const S = { folder: 'none', clients: [], pending: null };

  const $ = id => document.getElementById(id);
  const arr = v => (Array.isArray(v) ? v : []);
  function el(tag, cls, text){
    const n = document.createElement(tag);
    if(cls) n.className = cls;
    if(text != null) n.textContent = text;
    return n;
  }
  function chosen(){ try{ return sessionStorage.getItem(PICK_KEY) || ''; }catch(_){ return ''; } }
  function choose(id){ try{ if(id) sessionStorage.setItem(PICK_KEY, id); else sessionStorage.removeItem(PICK_KEY); }catch(_){} }

  // ?client=<id>&step=... from the suite: remembered for this tab, then
  // taken out of the address bar
  (function fromAddress(){
    const q = new URLSearchParams(location.search);
    if(!q.get('client')) return;
    choose(q.get('client'));
    q.delete('client'); q.delete('step');
    try{ history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q : '') + location.hash); }catch(_){}
  })();

  // ---- the picker on the setup panel -----------------------------------------
  function render(){
    const sel = $('ccaiClientSelect'), btn = $('ccaiFolderBtn'), note = $('ccaiClientNote');
    if(!sel) return;
    const want = chosen();
    sel.replaceChildren(el('option', null, 'No client: practice only'));
    sel.firstChild.value = '';
    S.clients.forEach(c => {
      // the suite's sample client (sample: true in its file): marked in the list
      const o = el('option', null, (c.doc.client.name || c.fileName) + (c.doc.sample === true ? ' (sample)' : ''));
      o.value = c.doc.id;
      sel.append(o);
    });
    if(want && !S.clients.some(c => c.doc.id === want) && S.folder === 'ready'){
      choose('');
    }
    sel.value = S.clients.some(c => c.doc.id === want) ? want : '';
    sel.disabled = S.folder !== 'ready';
    btn.hidden = S.folder === 'unsupported';
    btn.textContent = S.folder === 'ready' ? 'Change folder' : S.folder === 'reconnect' ? 'Reconnect folder' : 'Choose clients folder';
    note.textContent = S.folder === 'ready'
      ? (sel.value ? (S.clients.some(c => c.doc.id === sel.value && c.doc.sample === true)
          ? 'The suite’s sample client: not a real business. The debrief and scores go into its file.'
          : 'The debrief and scores go into this client’s file.') : 'The debrief and scores go into your practice file.')
      : S.folder === 'reconnect' ? 'Reconnect your clients folder to keep debriefs in your client files.'
      : S.folder === 'unsupported' ? 'This browser cannot open your clients folder. Use Chrome or Edge to keep debriefs in your client files.'
      : 'Choose the folder where you keep your client files to keep each debrief with its client.';
  }
  async function load(){
    S.folder = ClientFile.hasFolderAccess() ? await ClientFile.restore().catch(() => 'choose') : 'unsupported';
    S.clients = [];
    if(S.folder === 'ready'){
      try{ S.clients = (await ClientFile.list()).clients; }
      catch(_){ S.folder = 'reconnect'; }
    }
    render();
  }
  async function ensureFolder(pickAnother){
    let st = pickAnother ? await ClientFile.pick() : await ClientFile.restore();
    if(st === 'reconnect') st = await ClientFile.reconnect();
    if(st === 'choose') st = await ClientFile.pick();
    return st === 'ready';
  }
  function mount(){
    const block = $('setupBlock');
    if(!block || $('ccaiClientSelect')) return;
    const field = el('div', 'field ccai-client');
    const label = el('label', 'field-label', 'Client');
    label.htmlFor = 'ccaiClientSelect';
    const row = el('div', 'ccai-client-row');
    const sel = el('select');
    sel.id = 'ccaiClientSelect';
    const btn = el('button', 'btn ghost small', 'Choose clients folder');
    btn.type = 'button';
    btn.id = 'ccaiFolderBtn';
    row.append(sel, btn);
    const note = el('div', 'ccai-client-note');
    note.id = 'ccaiClientNote';
    field.append(label, row, note);
    block.insertBefore(field, block.firstChild);
    sel.addEventListener('change', () => { choose(sel.value); render(); });
    btn.addEventListener('click', async () => {
      try{ await ensureFolder(S.folder === 'ready'); }catch(_){}
      await load();
    });
    load();
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();

  // ---- after the debrief: into the client file ----------------------------------
  function iso(s){
    if(!s) return new Date().toISOString();
    const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');   // the server writes UTC without a zone
    return isNaN(d) ? new Date().toISOString() : d.toISOString();
  }
  function noteBox(){
    let n = $('ccaiFileNote');
    if(!n){
      n = el('div', 'ccai-file-note');
      n.id = 'ccaiFileNote';
      n.setAttribute('role', 'status');
      const anchor = $('debriefText');
      if(anchor) anchor.parentNode.insertBefore(n, anchor.nextSibling);
    }
    return n;
  }
  function say(text, action){
    const n = noteBox();
    n.replaceChildren(el('span', null, text));
    if(action){
      const b = el('button', 'btn ghost small', action.label);
      b.type = 'button';
      b.addEventListener('click', action.run);
      n.append(b);
    }
    n.hidden = false;
  }
  async function write(rec){
    const id = rec.clientId;
    const entry = id ? await ClientFile.open(id) : await ClientFile.openPractice('ccai');
    const had = arr((entry.doc.ccai || {}).sessions).filter(s => s && s.id !== rec.session.id);
    await ClientFile.save(entry, { ccai: { sessions: had.concat([rec.session]) }, lifecycle: { lastPracticeSession: rec.session.createdAt } },
                          'ccai', { keys: ['sessions'] });
    return id ? (entry.doc.client.name || 'the client') : null;
  }
  async function flush(){
    const rec = S.pending;
    if(!rec) return;
    try{
      const name = await write(rec);
      S.pending = null;
      say(name ? 'Saved to ' + name + '’s client file.' : 'Saved to your practice file (no client).');
    }catch(e){
      if(e && e.code === 'not_found'){
        rec.clientId = '';
        choose('');
        return flush();
      }
      S.folder = 'reconnect';
      say('This debrief is not in a client file yet: the clients folder could not be opened.', { label: 'Reconnect folder', run: retry });
    }
  }
  async function retry(){
    try{ if(!await ensureFolder(false)) return; }catch(_){ return; }
    S.folder = 'ready';
    await flush();
    load();
  }

  window.CcaiClientFile = {
    // res: the /sessions/{id}/end response; extra: what the page knows
    // (persona, scenario) when the server did not say
    record(res, extra){
      extra = extra || {};
      if(!res || !res.session_id) return;
      const session = {
        id: res.session_id,
        persona: res.persona_name || extra.persona || '',
        scenario: res.scenario_title || extra.scenario || '',
        createdAt: iso(res.created_at),
        durationSec: Math.max(0, Math.round(Number(res.duration_sec) || 0)),
        scores: { pressure: res.pressure ?? null, trust: res.trust ?? null, specificity: res.specificity ?? null },
        debrief: String(res.debrief || ''),
      };
      S.pending = { clientId: chosen(), session };
      if(!ClientFile.hasFolderAccess()){
        say('This browser cannot open your clients folder, so this debrief stays in Debrief History only.');
        return;
      }
      if(S.folder === 'ready') return flush();
      say('Keep this debrief in your client files?', { label: S.folder === 'reconnect' ? 'Reconnect folder' : 'Choose clients folder', run: retry });
    },
  };
})();
