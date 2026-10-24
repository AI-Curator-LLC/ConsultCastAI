// AI Curator Consulting Suite: js/clientfile.js
// The unified client file (docs/CLIENT_FILE.md, SPEC_Unified_Client_File.md).
// One `<client-slug>.client.json` per client, in one folder the consultant
// chooses. Every app reads and writes the same file: the shared `client`,
// `consultant`, `stage` and `lifecycle` blocks, plus each app's own section
// (`cbai`, `abai`, `ccai`).
//
// THIS FILE IS SHARED. The suite holds the original; ClientBriefAI,
// AdoptBriefAI and ConsultCastAI each carry an identical copy, and a test in
// every repo fails if a copy drifts. Change it here first, then copy it.
//
// Save rules:
//   - An app writes only its own section, never another app's.
//   - The shared blocks are merged field by field, last write wins: a save
//     carries only the fields this app changed since it opened the file.
//   - Anything the app did not touch, including fields it does not know
//     (added by hand or by a newer app), is kept exactly as it is on disk.
//
// The folder lives on the consultant's device. Nothing here talks to a
// server. Browser folder permission belongs to one site, so each app (each
// subdomain) asks for the same folder once; Chrome and Edge then offer
// "Allow on every visit". Browsers without folder access (Firefox, Safari,
// mobile) use one-file mode: open a single file, save downloads it.
//
// Works in the browser (window.ClientFile) and under node for the tests
// (module.exports); the folder functions take any object shaped like a
// FileSystemDirectoryHandle.

const ClientFile = (function () {
  const FORMAT = 'aicsuite.client';
  const VERSION = 1;
  const EXT = '.client.json';
  const ARCHIVE_DIR = 'archive';
  const PRACTICE_FILE = '_practice' + EXT;
  const APPS = ['cbai', 'abai', 'ccai', 'suite'];
  const SECTION_APPS = ['cbai', 'abai', 'ccai'];
  const STAGES = ['diagnose', 'scope', 'deliver', 'adopt', 'measure', 'closed'];
  const LIFECYCLE_FLAGS = ['readinessDone', 'scopeDone', 'aiaDone', 'policyPackDone', 'businessCaseDone', 'adoptionPlanDone'];
  const LIFECYCLE_DATES = ['lastCheckIn', 'lastImpactReport', 'lastPracticeSession'];
  // the shared blocks an app may write, and the nested objects inside them
  // that are merged one level deeper (a change to the logo must not undo a
  // change to the contact line made by another app)
  const SHARED = ['client', 'consultant', 'lifecycle'];
  const NESTED = { consultant: ['branding'] };
  const MAX_FILE_BYTES = 16 * 1024 * 1024;
  const IDB = { name: 'aicsuite-clientfile', store: 'handles', key: 'clientsDir' };

  class ClientFileError extends Error {
    constructor(code, message) { super(message); this.code = code; this.name = 'ClientFileError'; }
  }

  // ---- pure helpers ------------------------------------------------------

  const clone = v => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
  const same = (a, b) => JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b);
  const isObj = v => v !== null && typeof v === 'object' && !Array.isArray(v);

  function newId() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    // older browsers: RFC 4122 version 4 from getRandomValues
    const b = new Uint8Array(16);
    crypto.getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    const h = [...b].map(x => x.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }

  // Accents dropped, "&" read as "and", anything else not a letter or digit
  // becomes one hyphen. "Harbor & Vine Kitchen" -> "harbor-and-vine-kitchen".
  function slugify(name) {
    const s = String(name || '').normalize('NFKD').replace(/[̀-ͯ]/g, '')
      .toLowerCase().replace(/&/g, ' and ').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60).replace(/-+$/, '');
    return s || 'client';
  }

  // The name used to match one client across files (the migration, and the
  // apps' old name-based links): case, accents, punctuation, "&"/"and" and a
  // trailing company suffix do not matter.
  function nameKey(name) {
    return String(name || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()
      .replace(/&/g, ' and ').replace(/[^a-z0-9]+/g, ' ').trim()
      .replace(/\s+(inc|llc|ltd|limited|co|corp|corporation|company|pllc|plc)$/, '').replace(/\s+/g, ' ');
  }

  function blankClient() {
    return { name: '', industry: '', headcount: '', revenueBand: '', region: '', reportLanguage: 'en',
             contacts: [], currentSoftware: '', notes: '' };
  }
  function blankLifecycle() {
    const l = {};
    LIFECYCLE_FLAGS.forEach(k => { l[k] = false; });
    LIFECYCLE_DATES.forEach(k => { l[k] = null; });
    return l;
  }

  // A new file: the shared blocks only. App sections are added by the apps
  // the first time they save; a missing section reads as empty.
  function blank(client, app) {
    return {
      format: FORMAT, version: VERSION, id: newId(),
      updatedAt: new Date().toISOString(), updatedBy: APPS.includes(app) ? app : 'suite',
      client: Object.assign(blankClient(), isObj(client) ? clone(client) : {}),
      consultant: { notes: '', branding: { logoRef: '', name: '', contactLine: '' } },
      stage: 'diagnose',
      lifecycle: blankLifecycle(),
    };
  }

  // Reads a parsed file: refuses anything that is not a client file or comes
  // from a newer format, fills the shared blocks' missing fields, and keeps
  // every field it does not know.
  function normalize(doc) {
    if (!isObj(doc) || doc.format !== FORMAT) throw new ClientFileError('not_client_file', 'This is not a client file.');
    if (typeof doc.version !== 'number' || doc.version > VERSION) {
      throw new ClientFileError('newer_version', 'This client file was saved by a newer version. Update the app to open it.');
    }
    if (typeof doc.id !== 'string' || !doc.id) throw new ClientFileError('no_id', 'This client file has no id.');
    const out = clone(doc);
    out.client = Object.assign(blankClient(), isObj(doc.client) ? clone(doc.client) : {});
    if (!Array.isArray(out.client.contacts)) out.client.contacts = [];
    const c = isObj(doc.consultant) ? clone(doc.consultant) : {};
    out.consultant = Object.assign({ notes: '' }, c,
      { branding: Object.assign({ logoRef: '', name: '', contactLine: '' }, isObj(c.branding) ? c.branding : {}) });
    out.lifecycle = Object.assign(blankLifecycle(), isObj(doc.lifecycle) ? clone(doc.lifecycle) : {});
    if (!STAGES.includes(out.stage)) out.stage = 'diagnose';
    return out;
  }

  function parse(text) {
    let doc;
    try { doc = JSON.parse(text); } catch (e) { throw new ClientFileError('unreadable', 'This file could not be read as JSON.'); }
    return normalize(doc);
  }

  // The file as written: two-space JSON with a final newline, so a hand edit
  // or a diff stays readable.
  function serialize(doc) { return JSON.stringify(doc, null, 2) + '\n'; }

  // A shared block, field by field: start from what is on disk and apply the
  // fields this app changed since it opened the file (base -> mine). A field
  // missing from the app's copy is "not touched", never "deleted": an app
  // leaves out what it does not know, and that must survive. To clear a
  // shared field an app writes an empty value ('', [], null).
  function mergeFields(disk, base, mine, nested) {
    const out = isObj(disk) ? clone(disk) : {};
    const b = isObj(base) ? base : {}, m = isObj(mine) ? mine : {};
    for (const k of Object.keys(m)) {
      if (nested && nested.includes(k) && isObj(m[k])) {
        out[k] = mergeFields(out[k], b[k], m[k], null);
        continue;
      }
      if (!same(b[k], m[k])) out[k] = clone(m[k]);
    }
    return out;
  }

  // The app's own section. Only this app writes it, so the keys it manages
  // (`keys`) are written exactly as the app has them, removals included: a
  // cleared Business Case must not come back. Any other key in the section
  // (added by hand, or by a newer version of the app) is kept. Without a key
  // list nothing is removed, only written.
  function mergeSection(disk, mine, keys) {
    const out = isObj(disk) ? clone(disk) : {};
    const m = isObj(mine) ? mine : {};
    if (Array.isArray(keys)) keys.forEach(k => { if (!(k in m)) delete out[k]; });
    for (const k of Object.keys(m)) out[k] = clone(m[k]);
    return out;
  }

  // The calendar block (docs/SPEC_Meetings_Calendar.md section 6): the
  // per-client switch and, per dated item, the id of the event put on the
  // consultant's calendar for it (js/calendar.js). Every writer may change
  // it, so it merges three ways: `enabled` and each event entry are taken
  // from this writer's copy only where it changed them since it opened the
  // file (an entry it removed is removed), else kept from disk. Another
  // writer's new event ids are never lost.
  function mergeCalendar(disk, base, mine) {
    const d = isObj(disk) ? disk : {}, b = isObj(base) ? base : {}, m = isObj(mine) ? mine : {};
    const out = clone(d);
    if ('enabled' in m && !same(b.enabled, m.enabled)) out.enabled = m.enabled;
    const de = isObj(d.events) ? d.events : {}, be = isObj(b.events) ? b.events : {}, me = isObj(m.events) ? m.events : {};
    const events = clone(de);
    for (const k of new Set([...Object.keys(be), ...Object.keys(me)])) {
      if (same(be[k], me[k])) continue;
      if (me[k] === undefined) delete events[k]; else events[k] = clone(me[k]);
    }
    out.events = events;
    return out;
  }

  // The meetings list (docs/SPEC_Meetings_Calendar.md section 5.3): written
  // by the suite (a meeting and its transcript, summary and follow-ups) and
  // by the apps (the suggestions BriefPilot and AdoptPilot make from it, and
  // what the consultant decides). Merged by meeting id, then each follow-up
  // and suggestion by its id: what this writer changed since it opened the
  // file is taken, anything else stays as on disk, and a decision on disk
  // (accepted, dismissed) is never undone by a writer still showing it
  // pending. A meeting or an item this writer removed is removed.
  const DECIDED = ['accepted', 'dismissed'];
  const byId = list => new Map((Array.isArray(list) ? list : []).filter(x => isObj(x) && x.id).map(x => [String(x.id), x]));
  function mergeById(disk, base, mine, mergeOne) {
    const d = byId(disk), b = byId(base), m = byId(mine);
    const out = new Map(Array.from(d, ([k, v]) => [k, clone(v)]));
    for (const id of new Set([...b.keys(), ...m.keys()])) {
      const bi = b.get(id), mi = m.get(id), di = out.get(id);
      if (same(bi, mi)) continue;
      if (mi === undefined) { out.delete(id); continue; }
      out.set(id, di === undefined ? clone(mi) : mergeOne(di, bi, mi));
    }
    return Array.from(out.values());
  }
  function mergeItem(di, bi, mi) {
    if (DECIDED.includes(di.status) && !DECIDED.includes(mi.status)) return di;
    const out = clone(di);
    for (const k of Object.keys(mi)) if (!same(bi && bi[k], mi[k])) out[k] = clone(mi[k]);
    return out;
  }
  function mergeMeeting(dm, bm, mm) {
    const out = clone(dm);
    for (const k of Object.keys(mm)) {
      if (k === 'followUps' || k === 'suggestions') out[k] = mergeById(dm[k], bm && bm[k], mm[k], mergeItem);
      else if (k === 'analyzed') out[k] = Object.assign({}, isObj(dm[k]) ? dm[k] : {}, isObj(mm[k]) ? mm[k] : {});
      else if (!same(bm && bm[k], mm[k])) out[k] = clone(mm[k]);
    }
    return out;
  }
  function mergeMeetings(disk, base, mine) {
    return mergeById(disk, base, mine, mergeMeeting)
      .sort((x, y) => String(y.start || '').localeCompare(String(x.start || '')));
  }

  // What one app's save does to the file on disk. `base` is the file as this
  // app opened it (or last saved it); `mine` is the app's current copy;
  // `opts.keys` lists the keys the app manages in its own section. The shared
  // blocks merge field by field, the app's section as above; another app's
  // section, the id and anything else stay as they are on disk.
  function merge(disk, base, mine, app, opts) {
    if (!APPS.includes(app)) throw new ClientFileError('bad_app', 'Unknown app: ' + app);
    if (!isObj(mine)) throw new ClientFileError('bad_copy', 'Nothing to save.');
    if (!isObj(disk)) return Object.assign(clone(mine), { updatedAt: new Date().toISOString(), updatedBy: app });
    if (base && base.id && disk.id !== base.id) {
      throw new ClientFileError('replaced', 'This client file was replaced by another client. Open it again.');
    }
    const out = clone(disk);
    for (const k of SHARED) {
      if (isObj(mine[k])) out[k] = mergeFields(disk[k], base && base[k], mine[k], NESTED[k]);
    }
    if (SECTION_APPS.includes(app) && (isObj(mine[app]) || app in disk)) {
      out[app] = mergeSection(disk[app], mine[app], opts && opts.keys);
    }
    if (!same(base && base.stage, mine.stage) && STAGES.includes(mine.stage)) out.stage = mine.stage;
    if (isObj(mine.calendar)) out.calendar = mergeCalendar(disk.calendar, base && base.calendar, mine.calendar);
    if (Array.isArray(mine.meetings)) out.meetings = mergeMeetings(disk.meetings, base && base.meetings, mine.meetings);
    out.updatedAt = new Date().toISOString();
    out.updatedBy = app;
    return out;
  }

  // ---- the folder ----------------------------------------------------------

  let DIR = null;               // the clients folder (FileSystemDirectoryHandle)
  const hasFolderAccess = () => typeof window !== 'undefined' && typeof window.showDirectoryPicker === 'function';

  function idb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(IDB.name, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(IDB.store);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  async function idbGet() {
    try {
      const db = await idb();
      return await new Promise(resolve => {
        const r = db.transaction(IDB.store, 'readonly').objectStore(IDB.store).get(IDB.key);
        r.onsuccess = () => resolve(r.result || null);
        r.onerror = () => resolve(null);
      });
    } catch (e) { return null; }
  }
  async function idbPut(handle) {
    try {
      const db = await idb();
      await new Promise(resolve => {
        const r = db.transaction(IDB.store, 'readwrite').objectStore(IDB.store).put(handle, IDB.key);
        r.onsuccess = r.onerror = () => resolve();
      });
    } catch (e) { /* not remembered; the next visit asks again */ }
  }

  async function permission(handle, ask) {
    if (!handle || typeof handle.queryPermission !== 'function') return handle ? 'granted' : 'none';
    const opts = { mode: 'readwrite' };
    let p = await handle.queryPermission(opts);
    if (p !== 'granted' && ask && typeof handle.requestPermission === 'function') p = await handle.requestPermission(opts);
    return p;
  }

  // On page load: the remembered folder, if this browser still lets us use it.
  // Returns 'ready', 'reconnect' (remembered, needs one click: the browser's
  // permission prompt must come from a user action), 'choose' (none
  // remembered) or 'unsupported' (one-file mode).
  async function restore() {
    if (DIR) return 'ready';
    if (!hasFolderAccess()) return 'unsupported';
    const h = await idbGet();
    if (!h) return 'choose';
    const p = await permission(h, false);
    if (p === 'granted') { DIR = h; return 'ready'; }
    return 'reconnect';
  }
  // From a click: ask again for the remembered folder.
  async function reconnect() {
    const h = await idbGet();
    if (!h) return 'choose';
    const p = await permission(h, true);
    if (p === 'granted') { DIR = h; return 'ready'; }
    return 'reconnect';
  }
  // From a click: "Choose clients folder". The same picker id in every app
  // starts the picker where the consultant chose last time.
  async function pick() {
    if (!hasFolderAccess()) return 'unsupported';
    let h;
    try {
      h = await window.showDirectoryPicker({ id: 'aicsuite-clients', mode: 'readwrite' });
    } catch (e) {
      if (e && e.name === 'AbortError') return DIR ? 'ready' : 'choose';
      throw e;
    }
    DIR = h;
    await idbPut(h);
    return 'ready';
  }
  function useFolder(handle) { DIR = handle || null; }
  function folderName() { return DIR ? DIR.name : ''; }
  function requireDir() {
    if (!DIR) throw new ClientFileError('no_folder', 'Choose your clients folder again.');
    return DIR;
  }

  async function readHandle(fileHandle) {
    const f = await fileHandle.getFile();
    if (f.size > MAX_FILE_BYTES) throw new ClientFileError('too_large', 'This client file is too large to open.');
    return parse(await f.text());
  }

  async function exists(dir, name) {
    try { await dir.getFileHandle(name); return true; } catch (e) { return false; }
  }
  async function freeName(dir, base) {
    let name = base + EXT, n = 2;
    while (await exists(dir, name)) name = `${base}-${n++}${EXT}`;
    return name;
  }
  async function writeHandle(fileHandle, doc) {
    const w = await fileHandle.createWritable();
    await w.write(serialize(doc));
    await w.close();
  }

  // ---- the folder's layout -------------------------------------------------
  // A client file sits directly in the clients folder or in a subfolder up to
  // MAX_DEPTH levels down: one folder per client, with that client's file and
  // its exported reports. Hidden folders, the archive and tool folders are
  // not entered, and only the files asked for are ever read.
  const MAX_DEPTH = 3;
  const SKIP_DIRS = [ARCHIVE_DIR, 'node_modules', '__pycache__'];
  function skipDir(name) { return name.startsWith('.') || SKIP_DIRS.includes(name.toLowerCase()); }

  // Every file under the clients folder (or `root`) whose name passes `want`:
  // [{ name, path, handle, dir, folder }]. `path` is relative to the clients
  // folder ("Acme/acme.client.json"), `folder` is its folder's path ("" at
  // the top), `dir` that folder's handle.
  async function walk(want, root) {
    const out = [];
    async function visit(dir, folder, depth) {
      for await (const entry of dir.values()) {
        if (entry.kind === 'directory') {
          if (depth < MAX_DEPTH && !skipDir(entry.name)) await visit(entry, folder ? folder + '/' + entry.name : entry.name, depth + 1);
        } else if (want(entry.name)) {
          out.push({ name: entry.name, path: folder ? folder + '/' + entry.name : entry.name, handle: entry, dir, folder });
        }
      }
    }
    await visit(root || requireDir(), '', 0);
    return out;
  }

  // Every client file in the folder and its subfolders (not the archive),
  // newest first, with the files that could not be read listed apart so
  // nothing is hidden. Each entry: { fileName (its path), handle, doc, dir,
  // folder }. ConsultCastAI's practice file (sessions with no client), at the
  // top of the folder, is not a client: it comes back on its own as
  // `practice` (or null).
  async function list() {
    const clients = [], unreadable = [];
    let practice = null;
    for (const f of await walk(n => n.toLowerCase().endsWith(EXT))) {
      try {
        const doc = await readHandle(f.handle);
        const entry = { fileName: f.path, handle: f.handle, doc, dir: f.dir, folder: f.folder };
        if (!f.folder && f.name.toLowerCase() === PRACTICE_FILE) { practice = entry; continue; }
        clients.push(entry);
      } catch (e) {
        unreadable.push({ fileName: f.path, reason: e.code || 'unreadable' });
      }
    }
    clients.sort((a, b) => String(b.doc.updatedAt || '').localeCompare(String(a.doc.updatedAt || '')));
    return { clients, unreadable, practice };
  }

  // The practice file, created the first time: where ConsultCastAI keeps the
  // sessions practised with no client. An entry like open()'s.
  async function openPractice(app) {
    const dir = requireDir();
    let handle;
    try {
      handle = await dir.getFileHandle(PRACTICE_FILE);
    } catch (e) {
      handle = await dir.getFileHandle(PRACTICE_FILE, { create: true });
      await writeHandle(handle, blank({ name: 'Practice sessions' }, app || 'ccai'));
    }
    const doc = await readHandle(handle);
    return { fileName: PRACTICE_FILE, handle, doc, base: clone(doc), dir, folder: '' };
  }

  // One client, by id (the `?client=<id>` the suite opens an app with). The
  // entry keeps the file as opened (`base`) for the merge at save time.
  async function open(id) {
    const { clients } = await list();
    const hit = clients.find(c => c.doc.id === id);
    if (!hit) throw new ClientFileError('not_found', 'That client is not in your clients folder.');
    return { fileName: hit.fileName, handle: hit.handle, doc: hit.doc, base: clone(hit.doc), dir: hit.dir, folder: hit.folder };
  }

  // The folder a new client gets: its name as typed, minus what a folder
  // name cannot hold.
  function folderNameFor(name) {
    const s = String(name || '').replace(/[<>:"/\\|?*\u0000-\u001f]/g, ' ').replace(/\s+/g, ' ').trim()
      .replace(/^\.+/, '').slice(0, 80).replace(/[. ]+$/, '');
    return s || 'Client';
  }

  // A new client file holding the shared blocks only. `client` is the
  // client block's fields (at least the name). It goes into a folder named
  // after the client (made, or the one already there), or into `opts.dir`
  // (with `opts.folder`, its path) when given: the migration writes next to
  // the old files.
  async function create(client, app, opts) {
    const root = requireDir();
    const doc = blank(client, app);
    let dir = opts && opts.dir, folder = opts && opts.dir ? String(opts.folder || '') : '';
    if (!dir) {
      folder = folderNameFor(doc.client.name);
      dir = await root.getDirectoryHandle(folder, { create: true });
    }
    const fileName = await freeName(dir, slugify(doc.client.name));
    const handle = await dir.getFileHandle(fileName, { create: true });
    await writeHandle(handle, doc);
    return { fileName: folder ? folder + '/' + fileName : fileName, handle, doc, base: clone(doc), dir, folder };
  }

  // A silent save from `app`: re-read the file, merge this app's changes into
  // it, write it back. `opts.keys`: the keys the app manages in its own
  // section (see mergeSection). Returns the saved document; the entry's base
  // moves on so the next save only carries later changes.
  async function save(entry, mine, app, opts) {
    if (!entry || !entry.handle) throw new ClientFileError('no_file', 'This client has no file to save to.');
    let disk = null;
    try { disk = await readHandle(entry.handle); } catch (e) {
      if (e && e.code && e.code !== 'unreadable') throw e;
      // the file vanished or was emptied: write this app's copy whole
    }
    const doc = merge(disk, entry.base, mine, app, opts);
    await writeHandle(entry.handle, doc);
    entry.doc = doc;
    entry.base = clone(doc);
    return doc;
  }

  // Archive: into `archive/` at the top of the clients folder. A client
  // with a folder of its own (no other client file in it or under it) moves
  // with the whole folder, its exported reports included, keeping its path
  // under archive/; a client file on its own moves alone. There is no
  // delete; restoring is moving it back by hand. `target` is an entry from
  // list() or its fileName (path). Returns where it went.
  async function archive(target) {
    const root = requireDir();
    const path = typeof target === 'string' ? target : target.fileName;
    const all = await walk(n => n.toLowerCase().endsWith(EXT));
    const me = all.find(f => f.path === path);
    if (!me) throw new ClientFileError('not_found', 'That client is not in your clients folder.');
    const arch = await root.getDirectoryHandle(ARCHIVE_DIR, { create: true });
    const own = me.folder && !all.some(f => f !== me && (f.folder === me.folder || f.folder.startsWith(me.folder + '/')));
    if (own) {
      const parts = me.folder.split('/');
      const leaf = parts.pop();
      let into = arch, parent = root;
      for (const p of parts) {
        into = await into.getDirectoryHandle(p, { create: true });
        parent = await parent.getDirectoryHandle(p);
      }
      let name = leaf, n = 2;
      while (await dirExists(into, name)) name = `${leaf}-${n++}`;
      await copyDir(me.dir, await into.getDirectoryHandle(name, { create: true }));
      await parent.removeEntry(leaf, { recursive: true });
      return [ARCHIVE_DIR, ...parts, name].join('/');
    }
    const text = await (await me.handle.getFile()).text();
    const stem = me.name.slice(0, -EXT.length);
    const fileName = await freeName(arch, stem);
    const dst = await arch.getFileHandle(fileName, { create: true });
    const w = await dst.createWritable();
    await w.write(text);
    await w.close();
    await me.dir.removeEntry(me.name);
    return ARCHIVE_DIR + '/' + fileName;
  }
  async function dirExists(dir, name) {
    try { await dir.getDirectoryHandle(name); return true; } catch (e) { return false; }
  }
  // Every file and folder of `src` into `dst`, byte for byte.
  async function copyDir(src, dst) {
    for await (const entry of src.values()) {
      if (entry.kind === 'directory') {
        await copyDir(entry, await dst.getDirectoryHandle(entry.name, { create: true }));
      } else {
        const f = await entry.getFile();
        const data = typeof f.arrayBuffer === 'function' ? await f.arrayBuffer() : await f.text();
        const w = await (await dst.getFileHandle(entry.name, { create: true })).createWritable();
        await w.write(data);
        await w.close();
      }
    }
  }

  // ---- one-file mode (no folder access) ----------------------------------

  // Pick one client file. Resolves to an entry without a handle, or null.
  function openOneFile() {
    return new Promise((resolve, reject) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = '.json,application/json';
      input.onchange = async () => {
        const f = input.files && input.files[0];
        if (!f) { resolve(null); return; }
        try {
          const doc = parse(await f.text());
          resolve({ fileName: f.name, handle: null, doc, base: clone(doc), oneFile: true });
        } catch (e) { reject(e); }
      };
      input.click();
    });
  }
  // Save in one-file mode: the merge runs against the file as opened, and the
  // result downloads under the same name.
  function downloadOneFile(entry, mine, app, opts) {
    const doc = merge(entry.base, entry.base, mine, app, opts);
    const url = URL.createObjectURL(new Blob([serialize(doc)], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url; a.download = entry.fileName || slugify(doc.client.name) + EXT;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    entry.doc = doc; entry.base = clone(doc);
    return doc;
  }

  return {
    FORMAT, VERSION, EXT, ARCHIVE_DIR, PRACTICE_FILE, APPS, SECTION_APPS, STAGES, LIFECYCLE_FLAGS, LIFECYCLE_DATES,
    ClientFileError, newId, slugify, nameKey, blank, normalize, parse, serialize, merge, mergeFields, mergeSection, mergeCalendar, mergeMeetings,
    hasFolderAccess, restore, reconnect, pick, useFolder, folderName, walk, folderNameFor, list, open, openPractice, create, save, archive,
    openOneFile, downloadOneFile,
  };
})();
if (typeof module !== 'undefined') module.exports = ClientFile;
