// AI Curator Consulting Suite: js/cloudfolder.js
// The clients folder in OneDrive or Google Drive. The same file in the suite
// and in all three apps (copied unchanged, like js/clientfile.js).
//
// The consultant picks a folder once; the account keeps which folder it is
// (cloud_folder.py: the provider, the folder's ids, its path for display,
// never anything inside it). From then on every page opens it on its own,
// in any browser, on any device: clientfile.js asks restore() first.
//
// The files go between OneDrive or Google Drive and this browser only. The
// page reads and writes them itself, with Microsoft Graph or the Drive API
// and a short-lived token from the suite's Microsoft 365 or Google connection
// (microsoft365.py, google_workspace.py). Nothing about the clients passes
// through the suite's server.
//
// The folder is handed to clientfile.js as an object that behaves like the
// browser's own folder handle (values, getFileHandle, getDirectoryHandle,
// removeEntry, getFile, createWritable), so nothing else changes.
//
// In an app the script says where the suite is:
//   <script src="cloudfolder.js" data-suite="(the suite's address)"></script>

const CloudFolder = (() => {
  const tag = typeof document !== 'undefined' ? document.currentScript : null;
  const SUITE = (tag && tag.dataset.suite) || '';          // '' in the suite itself
  const GRAPH = 'https://graph.microsoft.com/v1.0';
  const DRIVE = 'https://www.googleapis.com/drive/v3';
  const UPLOAD = 'https://www.googleapis.com/upload/drive/v3';
  const FOLDER_MIME = 'application/vnd.google-apps.folder';
  const SIMPLE_MAX = 4 * 1024 * 1024;                        // Graph's one-request upload limit
  const NAMES = { onedrive: 'OneDrive', gdrive: 'Google Drive' };
  const SOURCE = { onedrive: 'microsoft', gdrive: 'google' };
  const SCOPE = { onedrive: 'Files.ReadWrite', gdrive: 'https://www.googleapis.com/auth/drive' };

  // ES / FR / PT not yet reviewed by a native speaker (same caveat as the rest).
  const TEXT = {
    en: { where: 'Where is your clients folder?', whereP: 'Pick it once. The suite and all three apps then open it on their own when you sign in, in any browser.',
          od: 'Choose folder in OneDrive', gd: 'Choose folder in Google Drive', local: 'Use a folder on this computer',
          localP: 'Only this browser on this computer, and it may ask again after you close it.',
          connect: 'Connect {name} first', allow: 'Allow {name} files first', odSub: 'With your Microsoft 365 connection', gdSub: 'With your Google connection',
          privacy: 'Your files go between {name} and this browser only. The suite keeps which folder you chose, never what is in it.',
          browseH: 'Choose your clients folder in {name}', top: 'My files', use: 'Use this folder', cancel: 'Cancel', back: 'Back',
          loading: 'Loading...', empty: 'No folders here.', files: '{n} files here', failed: 'Could not open this folder. Try again.',
          chosen: 'Clients folder: {path}', forget: 'Stop using this folder', inCloud: '{name}: {folder}' },
    es: { where: '¿Dónde está su carpeta de clientes?', whereP: 'Elíjala una vez. La suite y las tres aplicaciones la abrirán solas al iniciar sesión, en cualquier navegador.',
          od: 'Elegir carpeta en OneDrive', gd: 'Elegir carpeta en Google Drive', local: 'Usar una carpeta de este equipo',
          localP: 'Solo en este navegador de este equipo, y puede volver a pedirla al cerrarlo.',
          connect: 'Conecte {name} primero', allow: 'Permita los archivos de {name} primero', odSub: 'Con su conexión de Microsoft 365', gdSub: 'Con su conexión de Google',
          privacy: 'Sus archivos van solo entre {name} y este navegador. La suite guarda qué carpeta eligió, nunca su contenido.',
          browseH: 'Elija su carpeta de clientes en {name}', top: 'Mis archivos', use: 'Usar esta carpeta', cancel: 'Cancelar', back: 'Atrás',
          loading: 'Cargando...', empty: 'No hay carpetas aquí.', files: '{n} archivos aquí', failed: 'No se pudo abrir esta carpeta. Inténtelo de nuevo.',
          chosen: 'Carpeta de clientes: {path}', forget: 'Dejar de usar esta carpeta', inCloud: '{name}: {folder}' },
    fr: { where: 'Où est votre dossier clients ?', whereP: 'Choisissez-le une fois. La suite et les trois applications l\'ouvriront seules à la connexion, dans n\'importe quel navigateur.',
          od: 'Choisir un dossier dans OneDrive', gd: 'Choisir un dossier dans Google Drive', local: 'Utiliser un dossier de cet ordinateur',
          localP: 'Seulement dans ce navigateur sur cet ordinateur, et il peut le redemander après la fermeture.',
          connect: 'Connectez d\'abord {name}', allow: 'Autorisez d\'abord les fichiers {name}', odSub: 'Avec votre connexion Microsoft 365', gdSub: 'Avec votre connexion Google',
          privacy: 'Vos fichiers circulent uniquement entre {name} et ce navigateur. La suite garde le dossier choisi, jamais son contenu.',
          browseH: 'Choisissez votre dossier clients dans {name}', top: 'Mes fichiers', use: 'Utiliser ce dossier', cancel: 'Annuler', back: 'Retour',
          loading: 'Chargement...', empty: 'Aucun dossier ici.', files: '{n} fichiers ici', failed: 'Impossible d\'ouvrir ce dossier. Réessayez.',
          chosen: 'Dossier clients : {path}', forget: 'Ne plus utiliser ce dossier', inCloud: '{name} : {folder}' },
    pt: { where: 'Onde está sua pasta de clientes?', whereP: 'Escolha uma vez. A suíte e os três aplicativos a abrem sozinhos quando você entra, em qualquer navegador.',
          od: 'Escolher pasta no OneDrive', gd: 'Escolher pasta no Google Drive', local: 'Usar uma pasta deste computador',
          localP: 'Só neste navegador deste computador, e ele pode pedir de novo depois que você fechar.',
          connect: 'Conecte o {name} primeiro', allow: 'Permita os arquivos do {name} primeiro', odSub: 'Com sua conexão Microsoft 365', gdSub: 'Com sua conexão Google',
          privacy: 'Seus arquivos vão apenas entre o {name} e este navegador. A suíte guarda qual pasta você escolheu, nunca o conteúdo.',
          browseH: 'Escolha sua pasta de clientes no {name}', top: 'Meus arquivos', use: 'Usar esta pasta', cancel: 'Cancelar', back: 'Voltar',
          loading: 'Carregando...', empty: 'Nenhuma pasta aqui.', files: '{n} arquivos aqui', failed: 'Não foi possível abrir esta pasta. Tente de novo.',
          chosen: 'Pasta de clientes: {path}', forget: 'Parar de usar esta pasta', inCloud: '{name}: {folder}' },
  };
  function say(key, vars) {
    const l = String((typeof document !== 'undefined' && document.documentElement.lang) || 'en').slice(0, 2);
    let s = (TEXT[l] || TEXT.en)[key] || TEXT.en[key];
    Object.entries(vars || {}).forEach(([k, v]) => { s = s.split('{' + k + '}').join(v); });
    return s;
  }

  class CloudError extends Error {
    constructor(code, message) { super(message); this.code = code; this.name = 'CloudFolderError'; }
  }
  const notFound = name => { const e = new Error(name + ' not found'); e.name = 'NotFoundError'; return e; };

  // ---- the suite: the account's folder, and the tokens ------------------------------
  async function suite(path, body) {
    const opts = { method: 'POST', credentials: 'include' };
    if (body !== undefined) { opts.headers = { 'Content-Type': 'application/json' }; opts.body = JSON.stringify(body); }
    const r = await fetch(SUITE + '/api' + path, opts);
    let data = null;
    try { data = await r.json(); } catch (e) { /* no answer */ }
    if (!r.ok) throw new CloudError((data && data.detail && data.detail.code) || (data && data.code) || 'suite_' + r.status, 'suite ' + r.status);
    return data;
  }
  const tokens = {};
  // A token for this provider, or a CloudError: 'not_connected' or 'no_files'.
  async function token(provider) {
    const kept = tokens[provider];
    if (kept && kept.until > Date.now()) return kept.access;
    let got;
    try { got = await suite('/integrations/' + SOURCE[provider] + '/token'); } catch (e) { throw new CloudError('not_connected', e.message); }
    if (!got || !got.access_token) throw new CloudError('not_connected', 'no token');
    if (Array.isArray(got.scopes) && !got.scopes.some(s => s === SCOPE[provider] || s.endsWith('/' + SCOPE[provider]))) throw new CloudError('no_files', 'files not allowed');
    const until = got.expires_at ? Date.parse(got.expires_at) - 60000 : Date.now() + 5 * 60000;
    tokens[provider] = { access: got.access_token, until };
    return got.access_token;
  }
  // A call to Graph or Drive with the token; one retry with a fresh token.
  async function call(provider, url, opts, again) {
    const access = await token(provider);
    const r = await fetch(url, Object.assign({}, opts, { headers: Object.assign({ Authorization: 'Bearer ' + access }, (opts && opts.headers) || {}) }));
    if (r.status === 401 && !again) { delete tokens[provider]; return call(provider, url, opts, true); }
    if (r.status === 404) throw notFound(url);
    if (!r.ok) throw new CloudError('http_' + r.status, url.split('?')[0] + ' ' + r.status);
    return r;
  }
  const json = async (provider, url, opts) => (await call(provider, url, opts)).json();

  // ---- the two providers: list, read, write, make, remove -----------------------------
  // A node: { id, name, dir, modified, size } plus driveId for OneDrive.
  const OneDrive = {
    node(item, driveId) {
      return { id: item.id, name: item.name, dir: !!item.folder, modified: Date.parse(item.lastModifiedDateTime) || Date.now(),
               size: item.size || 0, driveId: (item.parentReference && item.parentReference.driveId) || driveId, count: item.folder ? item.folder.childCount : 0 };
    },
    async top() {
      const item = await json('onedrive', GRAPH + '/me/drive/root?$select=id,name,folder,parentReference,lastModifiedDateTime');
      return this.node(item);
    },
    async list(dir) {
      const out = [];
      let url = `${GRAPH}/drives/${dir.driveId}/items/${dir.id}/children?$select=id,name,folder,file,size,lastModifiedDateTime,parentReference&$top=200`;
      while (url) {
        const page = await json('onedrive', url);
        (page.value || []).forEach(i => out.push(this.node(i, dir.driveId)));
        url = page['@odata.nextLink'] || '';
      }
      return out;
    },
    async child(dir, name) {
      const item = await json('onedrive', `${GRAPH}/drives/${dir.driveId}/items/${dir.id}:/${encodeURIComponent(name)}?$select=id,name,folder,file,size,lastModifiedDateTime,parentReference`);
      return this.node(item, dir.driveId);
    },
    async read(file) {
      return (await call('onedrive', `${GRAPH}/drives/${file.driveId}/items/${file.id}/content`)).blob();
    },
    async create(dir, name) {
      const item = await json('onedrive', `${GRAPH}/drives/${dir.driveId}/items/${dir.id}:/${encodeURIComponent(name)}:/content?@microsoft.graph.conflictBehavior=fail`,
        { method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: '' });
      return this.node(item, dir.driveId);
    },
    async write(file, blob) {
      if (blob.size <= SIMPLE_MAX) {
        await call('onedrive', `${GRAPH}/drives/${file.driveId}/items/${file.id}/content`, { method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: blob });
        return;
      }
      const session = await json('onedrive', `${GRAPH}/drives/${file.driveId}/items/${file.id}/createUploadSession`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ item: { '@microsoft.graph.conflictBehavior': 'replace' } }) });
      const CHUNK = 320 * 1024 * 10;
      for (let at = 0; at < blob.size; at += CHUNK) {
        const end = Math.min(at + CHUNK, blob.size);
        // the upload address carries its own authorization: no token goes there
        const r = await fetch(session.uploadUrl, { method: 'PUT', headers: { 'Content-Range': `bytes ${at}-${end - 1}/${blob.size}` }, body: blob.slice(at, end) });
        if (!r.ok) throw new CloudError('upload_' + r.status, 'upload ' + r.status);
      }
    },
    async mkdir(dir, name) {
      const item = await json('onedrive', `${GRAPH}/drives/${dir.driveId}/items/${dir.id}/children`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, folder: {}, '@microsoft.graph.conflictBehavior': 'fail' }) });
      return this.node(item, dir.driveId);
    },
    async remove(node) {
      // to OneDrive's recycle bin, as deleting a file in the folder would
      await call('onedrive', `${GRAPH}/drives/${node.driveId}/items/${node.id}`, { method: 'DELETE' });
    },
  };
  const GDrive = {
    node(f) {
      return { id: f.id, name: f.name, dir: f.mimeType === FOLDER_MIME, modified: Date.parse(f.modifiedTime) || Date.now(), size: Number(f.size) || 0 };
    },
    async top() { return this.node(await json('gdrive', DRIVE + '/files/root?fields=id,name,mimeType,modifiedTime')); },
    async list(dir) {
      const out = [];
      let pageToken = '';
      do {
        const q = encodeURIComponent(`'${dir.id}' in parents and trashed = false`);
        const page = await json('gdrive', `${DRIVE}/files?q=${q}&fields=nextPageToken,files(id,name,mimeType,modifiedTime,size)&pageSize=1000&orderBy=folder,name`
          + '&supportsAllDrives=true&includeItemsFromAllDrives=true' + (pageToken ? '&pageToken=' + encodeURIComponent(pageToken) : ''));
        (page.files || []).forEach(f => out.push(this.node(f)));
        pageToken = page.nextPageToken || '';
      } while (pageToken);
      return out;
    },
    async child(dir, name) {
      const found = (await this.list(dir)).find(n => n.name === name);
      if (!found) throw notFound(name);
      return found;
    },
    async read(file) { return (await call('gdrive', `${DRIVE}/files/${file.id}?alt=media&supportsAllDrives=true`)).blob(); },
    async create(dir, name) {
      const boundary = 'aiccs' + Math.random().toString(36).slice(2);
      const body = `--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${JSON.stringify({ name, parents: [dir.id] })}\r\n`
        + `--${boundary}\r\nContent-Type: application/json\r\n\r\n\r\n--${boundary}--`;
      const f = await json('gdrive', `${UPLOAD}/files?uploadType=multipart&supportsAllDrives=true&fields=id,name,mimeType,modifiedTime,size`,
        { method: 'POST', headers: { 'Content-Type': 'multipart/related; boundary=' + boundary }, body });
      return this.node(f);
    },
    async write(file, blob) {
      await call('gdrive', `${UPLOAD}/files/${file.id}?uploadType=media&supportsAllDrives=true`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: blob });
    },
    async mkdir(dir, name) {
      const f = await json('gdrive', `${DRIVE}/files?supportsAllDrives=true&fields=id,name,mimeType,modifiedTime`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, mimeType: FOLDER_MIME, parents: [dir.id] }) });
      return this.node(f);
    },
    async remove(node) {
      // to Drive's bin, never deleted for good from here
      await call('gdrive', `${DRIVE}/files/${node.id}?supportsAllDrives=true`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ trashed: true }) });
    },
  };
  const API = { onedrive: OneDrive, gdrive: GDrive };

  // ---- folder and file handles, as clientfile.js expects them ---------------------------
  class CloudFile {
    constructor(provider, node) { this.kind = 'file'; this.name = node.name; this.provider = provider; this.node = node; }
    async getFile() {
      const blob = await API[this.provider].read(this.node);
      return new File([blob], this.name, { type: blob.type || 'application/json', lastModified: this.node.modified });
    }
    async createWritable() {
      const parts = [];
      const self = this;
      return {
        async write(data) {
          if (data && typeof data === 'object' && !(data instanceof Blob) && !(data instanceof ArrayBuffer) && !ArrayBuffer.isView(data) && 'data' in data) data = data.data;
          parts.push(data);
        },
        async close() {
          const blob = new Blob(parts, { type: 'application/json' });
          await API[self.provider].write(self.node, blob);
          self.node.modified = Date.now();
        },
        async abort() { parts.length = 0; },
      };
    }
    async queryPermission() { return 'granted'; }
    async requestPermission() { return 'granted'; }
  }
  class CloudDir {
    constructor(provider, node, label) { this.kind = 'directory'; this.name = node.name; this.provider = provider; this.node = node; this.label = label || ''; this.cloud = true; }
    async *values() {
      for (const n of await API[this.provider].list(this.node)) yield n.dir ? new CloudDir(this.provider, n) : new CloudFile(this.provider, n);
    }
    async *entries() { for await (const h of this.values()) yield [h.name, h]; }
    async getFileHandle(name, opts) {
      try {
        const n = await API[this.provider].child(this.node, name);
        if (n.dir) { const e = new Error('is a folder'); e.name = 'TypeMismatchError'; throw e; }
        return new CloudFile(this.provider, n);
      } catch (e) {
        if (e.name !== 'NotFoundError' || !(opts && opts.create)) throw e;
        return new CloudFile(this.provider, await API[this.provider].create(this.node, name));
      }
    }
    async getDirectoryHandle(name, opts) {
      try {
        const n = await API[this.provider].child(this.node, name);
        if (!n.dir) { const e = new Error('is a file'); e.name = 'TypeMismatchError'; throw e; }
        return new CloudDir(this.provider, n);
      } catch (e) {
        if (e.name !== 'NotFoundError' || !(opts && opts.create)) throw e;
        return new CloudDir(this.provider, await API[this.provider].mkdir(this.node, name));
      }
    }
    async removeEntry(name) {
      await API[this.provider].remove(await API[this.provider].child(this.node, name));
    }
    async queryPermission() { return 'granted'; }
    async requestPermission() { return 'granted'; }
  }
  const label = folder => say('inCloud', { name: NAMES[folder.provider], folder: folder.path.length ? folder.path[folder.path.length - 1] : NAMES[folder.provider] });
  function handleFor(folder) {
    const name = folder.path.length ? folder.path[folder.path.length - 1] : NAMES[folder.provider];
    return new CloudDir(folder.provider, { id: folder.item_id, driveId: folder.drive_id, name, dir: true }, label(folder));
  }

  // ---- on page load: the account's folder, if there is one and it can be reached ---------
  let saved;     // undefined: not asked yet; null: none
  let providers = {};
  async function current() {
    if (saved === undefined) {
      try {
        const got = await suite('/cloud-folder/current');
        saved = got.folder || null;
        providers = got.providers || {};
      } catch (e) { saved = null; }
    }
    return saved;
  }
  async function restore() {
    const folder = await current();
    if (!folder || providers[folder.provider] !== 'ready') return null;   // not connected now: the local folder, if any
    try { await token(folder.provider); } catch (e) { return null; }
    return handleFor(folder);
  }
  async function forget() {
    saved = null;
    try { await suite('/cloud-folder/clear'); } catch (e) { /* kept on the account; the next choice replaces it */ }
  }

  // ---- the dialogs (their own look, so the apps need no styles) ---------------------------
  const FILE_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3v5h5"/></svg>';
  const FOLDER_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M3 6.5A2.5 2.5 0 0 1 5.5 4h4l2 2.2h7A2.5 2.5 0 0 1 21 8.7v8.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/></svg>';
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  // The look comes from the suite's css/cloudfolder.css (a stylesheet, which
  // the suite's strict pages allow where an inline style is refused).
  let styled = false;
  // A fresh dialog each time (the chooser stays underneath the folder browser).
  function dialog(id) {
    if (!styled) {
      const link = document.createElement('link');
      link.rel = 'stylesheet'; link.href = SUITE + '/css/cloudfolder.css';
      document.head.appendChild(link); styled = true;
    }
    const old = document.getElementById(id);
    if (old) old.remove();
    const d = el('dialog', 'aiccs-cf'); d.id = id; d.setAttribute('aria-labelledby', id + '-h');
    document.body.appendChild(d);
    return d;
  }
  function connectUrl(provider) {
    return SUITE + (provider === 'onedrive' ? '/api/integrations/microsoft/connect?folder=1' : '/api/integrations/google/connect?drive=1');
  }
  // What each provider can do now: 'ready', 'no_files' or 'not_connected'
  // (the suite says, from the connections it holds; no token is asked for).
  async function state(provider) {
    let got = null;
    try { got = await suite('/cloud-folder/current'); } catch (e) { /* not known */ }
    return (got && got.providers && got.providers[provider]) || 'not_connected';
  }

  // "Where is your clients folder?": resolves to a folder handle, or null when cancelled.
  // localPick: the page's own "folder on this computer" picker (null where the browser has none).
  async function choose(localPick, only) {
    const [od, gd] = await Promise.all([state('onedrive'), state('gdrive')]);
    return new Promise(resolve => {
      const d = dialog('aiccs-cf');
      let settled = false, away = false;      // away: closed for a moment while the browser or picker is open
      const done = v => { if (!settled) { settled = true; resolve(v); if (d.open) d.close(); d.remove(); } };
      d.addEventListener('close', () => { if (!away) done(null); });
      const f = el('form'); f.method = 'dialog';
      const h = el('h2', null, say('where')); h.id = 'aiccs-cf-h';
      f.append(h, el('p', null, say('whereP')));
      const opt = (cls, mark, title, sub, onclick) => {
        const b = el('button', 'aiccs-cf-opt ' + cls); b.type = 'button';
        const i = el('i', null, mark); i.setAttribute('aria-hidden', 'true');
        const tx = el('span'); tx.append(el('b', null, title), el('small', null, sub));
        b.append(i, tx); b.onclick = onclick; return b;
      };
      [['onedrive', od, 'aiccs-cf-od', 'OD', 'od', 'odSub'], ['gdrive', gd, 'aiccs-cf-gd', 'GD', 'gd', 'gdSub']].forEach(([p, st, cls, mark, key, sub]) => {
        if (only && only !== p) return;
        const ready = st === 'ready';
        const subText = ready ? say(sub) : say(st === 'no_files' ? 'allow' : 'connect', { name: NAMES[p] });
        f.append(opt(cls, mark, say(key), subText, async () => {
          if (!ready) { location.href = connectUrl(p); return; }   // back to the chooser after Microsoft or Google
          away = true; d.close();
          const picked = await browse(p);
          away = false;
          if (picked) done(picked); else d.showModal();
        }));
      });
      if (localPick) {
        f.append(opt('aiccs-cf-lo', '', say('local'), say('localP'), async () => {
          away = true; d.close();
          const h2 = await localPick();
          if (h2) await forget();
          done(h2 || null);
        }));
      }
      f.append(el('p', 'aiccs-cf-note', say('privacy', { name: 'OneDrive / Google Drive' })));
      const row = el('div', 'aiccs-cf-row');
      const cancel = el('button', 'aiccs-cf-btn', say('cancel')); cancel.type = 'button'; cancel.onclick = () => d.close();
      row.append(cancel); f.append(row);
      d.append(f);
      if (!d.open) d.showModal();
    });
  }

  // The folder browser: folders only, from the top of the drive; "Use this folder" keeps it.
  function browse(provider) {
    return new Promise(resolve => {
      const d = dialog('aiccs-cf-browse');
      const trail = [];        // [{ node, name }]
      let settled = false;
      const done = v => { if (!settled) { settled = true; resolve(v); if (d.open) d.close(); d.remove(); } };
      d.addEventListener('close', () => done(null));
      const f = el('form'); f.method = 'dialog';
      const h = el('h2', null, say('browseH', { name: NAMES[provider] })); h.id = 'aiccs-cf-browse-h';
      const crumbs = el('nav', 'aiccs-cf-crumbs'); crumbs.setAttribute('aria-label', NAMES[provider]);
      const list = el('ul', 'aiccs-cf-list');
      const note = el('span', 'aiccs-cf-grow');
      const use = el('button', 'aiccs-cf-btn primary', say('use')); use.type = 'button'; use.disabled = true;
      const cancel = el('button', 'aiccs-cf-btn', say('cancel')); cancel.type = 'button'; cancel.onclick = () => done(null);
      const row = el('div', 'aiccs-cf-row'); row.append(note, cancel, use);
      f.append(h, el('p', null, say('privacy', { name: NAMES[provider] })), crumbs, list, row);
      d.append(f);
      d.showModal();

      function drawCrumbs() {
        crumbs.replaceChildren();
        trail.forEach((step, i) => {
          if (i) crumbs.append(el('span', null, '›'));
          if (i === trail.length - 1) { crumbs.append(el('b', null, step.name)); return; }
          const b = el('button', null, step.name); b.type = 'button';
          b.onclick = () => { trail.length = i + 1; open(); };
          crumbs.append(b);
        });
      }
      async function open() {
        drawCrumbs();
        use.disabled = trail.length < 2;    // not the very top of the drive
        list.replaceChildren(el('li', 'aiccs-cf-msg', say('loading')));
        note.textContent = '';
        let items;
        try { items = await API[provider].list(trail[trail.length - 1].node); } catch (e) { list.replaceChildren(el('li', 'aiccs-cf-msg', say('failed'))); return; }
        const dirs = items.filter(n => n.dir).sort((a, b) => a.name.localeCompare(b.name));
        const files = items.length - dirs.length;
        note.textContent = files ? say('files', { n: files }) : '';
        list.replaceChildren();
        if (!dirs.length && !files) list.append(el('li', 'aiccs-cf-msg', say('empty')));
        dirs.forEach(n => {
          const li = el('li');
          const b = el('button'); b.type = 'button';
          b.innerHTML = FOLDER_ICON;     // our own constant icon
          b.append(el('span', null, n.name));
          if (n.count) b.append(el('small', null, String(n.count)));
          b.onclick = () => { trail.push({ node: n, name: n.name }); open(); };
          li.append(b); list.append(li);
        });
        // the files, greyed: to see it is the right folder (never opened from here)
        items.filter(n => !n.dir).sort((a, b) => a.name.localeCompare(b.name)).slice(0, 50).forEach(n => {
          const li = el('li', 'aiccs-cf-file');
          li.innerHTML = FILE_ICON;     // our own constant icon
          li.append(el('span', null, n.name));
          list.append(li);
        });
        const first = list.querySelector('button'); if (first) first.focus();
      }
      use.onclick = async () => {
        const here = trail[trail.length - 1];
        const folder = { provider, item_id: here.node.id, drive_id: here.node.driveId || '', path: trail.slice(1).map(s => s.name) };
        use.disabled = true;
        try { saved = (await suite('/cloud-folder', folder)).folder; } catch (e) { use.disabled = false; note.textContent = say('failed'); return; }
        done(handleFor(saved));
      };
      (async () => {
        try { const top = await API[provider].top(); trail.push({ node: top, name: say('top') }); open(); }
        catch (e) { list.replaceChildren(el('li', 'aiccs-cf-msg', say('failed'))); }
      })();
    });
  }

  // One provider's folder browser straight away (the Account page's buttons);
  // first to Microsoft or Google when files aren't allowed yet.
  // stay: never sent to Microsoft or Google (just back from there).
  async function start(provider, stay) {
    if (await state(provider) !== 'ready') { if (!stay) location.href = connectUrl(provider); return null; }
    return browse(provider);
  }

  // After Microsoft or Google sent the consultant back to the chooser (?cloud=onedrive|gdrive).
  // Only when it worked: a refusal or a cancel is never sent round again.
  function returning() {
    try {
      const q = new URLSearchParams(location.search), p = q.get('cloud');
      return p in NAMES && (q.get('ms') || q.get('google')) === 'connected' ? p : '';
    } catch (e) { return ''; }
  }

  return { restore, choose, browse, start, forget, current, returning, names: NAMES, label, say, CloudError, _api: API, _handleFor: handleFor };
})();
if (typeof module !== 'undefined') module.exports = CloudFolder;
