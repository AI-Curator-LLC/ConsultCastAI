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
   (?client=<id>, kept for the tab so it survives signing in). A prospect
   too (the suite's LeadGen Hub, ?prospect=<id>): the picker lists the
   prospect files under Prospects/ after the clients, and a session for one
   goes into its prospect file the same way, so it stays with it when it
   becomes a client (the suite's convertProspect keeps the file whole). A
   prospect made a client meanwhile is written to its client file. Plain
   script, loaded before the page's module; the module calls
   CcaiClientFile.record() once a debrief is shown.
   ===================================================================== */
(function(){
  const PICK_KEY = 'consultcastai_client';   // sessionStorage: the client chosen in this tab
  const S = { folder: 'none', clients: [], prospects: [], pending: null };
  // the picked value: a client file's id, or PROSPECT + a prospect file's id
  const PROSPECT = 'prospect:';
  const isProspectPick = v => String(v || '').startsWith(PROSPECT);

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

  // ?client=<id>&step=... (or ?prospect=<id>) from the suite: remembered for
  // this tab, then taken out of the address bar
  (function fromAddress(){
    const q = new URLSearchParams(location.search);
    if(!q.get('client') && !q.get('prospect')) return;
    choose(q.get('client') || PROSPECT + q.get('prospect'));
    q.delete('client'); q.delete('prospect'); q.delete('step');
    try{ history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q : '') + location.hash); }catch(_){}
  })();

  // ---- the picker on the setup panel -----------------------------------------
  function render(){
    const sel = $('ccaiClientSelect'), btn = $('ccaiFolderBtn'), note = $('ccaiClientNote');
    if(!sel) return;
    const want = chosen();
    sel.replaceChildren(el('option', null, 'No client: practice only'));
    sel.firstChild.value = '';
    const opt = (text, value) => { const o = el('option', null, text); o.value = value; return o; };
    const clients = S.clients.map(c => opt(c.doc.client.name || c.fileName, c.doc.id));
    if(S.prospects.length){
      const cg = el('optgroup'); cg.label = 'Clients'; cg.append(...clients);
      const pg = el('optgroup'); pg.label = 'Prospects';
      pg.append(...S.prospects.map(p => opt(p.doc.client.name || p.fileName, PROSPECT + p.doc.id)));
      if(clients.length) sel.append(cg);
      sel.append(pg);
    }else{
      sel.append(...clients);
    }
    const known = v => [...sel.options].some(o => o.value && o.value === v);
    if(want && !known(want) && S.folder === 'ready'){
      choose('');
    }
    sel.value = known(want) ? want : '';
    // the closed select shows a prospect as one (the options are made anew on each render)
    const picked = sel.selectedOptions[0];
    if(picked && isProspectPick(picked.value)) picked.textContent += ' (prospect)';
    sel.disabled = S.folder !== 'ready';
    btn.hidden = S.folder === 'unsupported';
    btn.textContent = S.folder === 'ready' ? 'Change folder' : S.folder === 'reconnect' ? 'Reconnect folder' : 'Choose clients folder';
    note.textContent = S.folder === 'ready'
      ? (isProspectPick(sel.value) ? 'The debrief and scores go into this prospect’s file, and stay with it when it becomes a client.'
        : sel.value ? 'The debrief and scores go into this client’s file.' : 'The debrief and scores go into your practice file.')
      : S.folder === 'reconnect' ? 'Reconnect your clients folder to keep debriefs in your client files.'
      : S.folder === 'unsupported' ? 'This browser cannot open your clients folder. Use Chrome or Edge to keep debriefs in your client files.'
      : 'Choose the folder where you keep your client files to keep each debrief with its client.';
  }
  async function load(){
    S.folder = ClientFile.hasFolderAccess() ? await ClientFile.restore().catch(() => 'choose') : 'unsupported';
    S.clients = []; S.prospects = [];
    if(S.folder === 'ready'){
      try{ S.clients = (await ClientFile.list()).clients; }
      catch(_){ S.folder = 'reconnect'; }
      if(S.folder === 'ready'){
        try{ S.prospects = (await ClientFile.listProspects()).prospects; }catch(_){ S.prospects = []; }
      }
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
    const id = rec.clientId, prospect = isProspectPick(id);
    const entry = prospect ? await ClientFile.openProspect(id.slice(PROSPECT.length))
      : id ? await ClientFile.open(id) : await ClientFile.openPractice('ccai');
    const had = arr((entry.doc.ccai || {}).sessions).filter(s => s && s.id !== rec.session.id);
    await ClientFile.save(entry, { ccai: { sessions: had.concat([rec.session]) }, lifecycle: { lastPracticeSession: rec.session.createdAt } },
                          'ccai', { keys: ['sessions'] });
    if(!id) return null;
    const name = entry.doc.client.name || (prospect ? 'the prospect' : 'the client');
    return 'Saved to ' + name + (prospect ? '’s prospect file.' : '’s client file.');
  }
  async function flush(){
    const rec = S.pending;
    if(!rec) return;
    try{
      const saved = await write(rec);
      S.pending = null;
      say(saved || 'Saved to your practice file (no client).');
    }catch(e){
      // the prospect became a client meanwhile: its client file (same id) takes the session
      if(e && e.code === 'prospect_converted' && e.clientId){
        rec.clientId = e.clientId;
        choose(e.clientId);
        load();
        return flush();
      }
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
