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
  // where each token comes from: OneDrive with any Microsoft account
  // (onedrive_files.py), Google Drive with the Google connection
  const SOURCE = { onedrive: 'onedrive', gdrive: 'google' };
  const SCOPE = { onedrive: 'Files.ReadWrite', gdrive: 'https://www.googleapis.com/auth/drive' };

  // ES / FR / PT not yet reviewed by a native speaker (same caveat as the rest).
  const TEXT = {
    en: { where: 'Where is your clients folder?', whereP: 'Pick it once. The suite and all three apps then open it on their own when you sign in, in any browser.',
          od: 'Choose folder in OneDrive', gd: 'Choose folder in Google Drive', local: 'Use a folder on this computer',
          localP: 'Only this browser on this computer, and it may ask again after you close it.',
          connect: 'Connect {name} first', allow: 'Allow {name} files first', odSub: 'With your Microsoft account, personal or work', gdSub: 'With your Google connection',
          privacy: 'Your files go between {name} and this browser only. The suite keeps which folder you chose, never what is in it.',
          browseH: 'Choose your clients folder in {name}', top: 'My files', use: 'Use this folder', cancel: 'Cancel', back: 'Back',
          loading: 'Loading...', empty: 'No folders here.', files: '{n} files here', failed: 'Could not open this folder. Try again.',
          chosen: 'Clients folder: {path}', forget: 'Stop using this folder', inCloud: '{name}: {folder}',
          whyNetwork: "This browser couldn't reach {name}: the internet connection dropped, or something on this network or a browser extension blocked it. Check your connection, then click Try again.",
          whyNotConnected: "{name} isn't connected to the suite any more. Click Reconnect {name}, then choose the folder.",
          whyReconnect: "{name} asked for a new sign-in (a changed password or removed access). Click Reconnect {name}, then choose the folder.",
          whyNoFiles: "The connection to {name} doesn't allow files yet. Click Reconnect {name} and allow files.",
          whyNoDrive: "The connected Microsoft account has no OneDrive. Some Microsoft 365 plans, such as email-only plans, don't include it; personal Microsoft accounts do. Click Reconnect OneDrive and pick an account that has OneDrive, or use a folder on this computer.",
          whyDenied: "{name} refused access. The folder may belong to another account, or your organization doesn't let apps open files. Reconnect with the account that owns the folder, or ask your IT administrator.",
          whyGone: "This folder is no longer in {name}: it was moved, renamed or deleted. Click Back and choose another folder.",
          whyBusy: "{name} is busy or not answering right now. Wait a minute, then click Try again.",
          whyOther: "{name} answered with an error the suite doesn't recognise. Click Try again. If it keeps happening, send AI Curator support the reference shown here.",
          whySave: "The suite couldn't keep this folder as your clients folder. Click Use this folder again. If it keeps happening, send AI Curator support the reference shown here.",
          ref: "Reference: {ref}",
          retry: "Try again",
          reconnect: "Reconnect {name}",
          pNetwork: "Your clients folder in {name} can't be reached right now: the internet connection dropped, or something blocked it. It is still saved on your account.",
          pBusy: "{name} is not answering right now, so your clients folder ({folder}) can't be opened yet. It is still saved on your account.",
          pSuite: "The suite couldn't check your clients folder just now. Nothing was changed: your folder is still saved on your account.",
          pNotConnected: "Your clients folder ({folder}) is in {name}, and {name} needs to be connected again before it can be opened. The folder is still saved on your account.",
          pNoFiles: "Your clients folder ({folder}) is in {name}, but the connection doesn't allow files yet. Reconnect and allow files.",
          pAccount: "Your clients folder ({folder}) is in {name}, but the Microsoft account connected now can't open it. Reconnect {name} with the account that holds the folder.",
          pGone: "Your clients folder ({folder}) can't be found in {name}. It was moved, renamed or deleted, or {name} is connected with a different account.",
          pOther: "{name} answered with an error, so your clients folder ({folder}) can't be opened yet. It is still saved on your account.",
          chooseAgain: "Choose the folder again",
          checking: "Checking...",
          pWith: "It was chosen with {account}.",
          useLocal: "Open the copy on this computer for now",
          localNote: "{name} can't be opened right now, so you are working in the copy of your clients folder on this computer. Your {name} folder is still saved on your account.",
          tryCloud: "Try {name} again" },
    es: { where: '¿Dónde está su carpeta de clientes?', whereP: 'Elíjala una vez. La suite y las tres aplicaciones la abrirán solas al iniciar sesión, en cualquier navegador.',
          od: 'Elegir carpeta en OneDrive', gd: 'Elegir carpeta en Google Drive', local: 'Usar una carpeta de este equipo',
          localP: 'Solo en este navegador de este equipo, y puede volver a pedirla al cerrarlo.',
          connect: 'Conecte {name} primero', allow: 'Permita los archivos de {name} primero', odSub: 'Con su cuenta Microsoft, personal o de trabajo', gdSub: 'Con su conexión de Google',
          privacy: 'Sus archivos van solo entre {name} y este navegador. La suite guarda qué carpeta eligió, nunca su contenido.',
          browseH: 'Elija su carpeta de clientes en {name}', top: 'Mis archivos', use: 'Usar esta carpeta', cancel: 'Cancelar', back: 'Atrás',
          loading: 'Cargando...', empty: 'No hay carpetas aquí.', files: '{n} archivos aquí', failed: 'No se pudo abrir esta carpeta. Inténtelo de nuevo.',
          chosen: 'Carpeta de clientes: {path}', forget: 'Dejar de usar esta carpeta', inCloud: '{name}: {folder}',
          whyNetwork: "Este navegador no pudo comunicarse con {name}: se cortó la conexión a internet, o algo en esta red o una extensión del navegador lo bloqueó. Revise su conexión y haga clic en Intentar de nuevo.",
          whyNotConnected: "{name} ya no está conectado a la suite. Haga clic en Volver a conectar {name} y luego elija la carpeta.",
          whyReconnect: "{name} pidió un nuevo inicio de sesión (una contraseña cambiada o un acceso retirado). Haga clic en Volver a conectar {name} y luego elija la carpeta.",
          whyNoFiles: "La conexión con {name} aún no permite archivos. Haga clic en Volver a conectar {name} y permita los archivos.",
          whyNoDrive: "La cuenta Microsoft conectada no tiene OneDrive. Algunos planes de Microsoft 365, como los de solo correo, no lo incluyen; las cuentas Microsoft personales sí. Haga clic en Volver a conectar OneDrive y elija una cuenta con OneDrive, o use una carpeta de este equipo.",
          whyDenied: "{name} denegó el acceso. Puede que la carpeta pertenezca a otra cuenta, o que su organización no permita que las aplicaciones abran archivos. Vuelva a conectarlo con la cuenta propietaria de la carpeta, o consulte a su administrador de TI.",
          whyGone: "Esta carpeta ya no está en {name}: se movió, se cambió de nombre o se eliminó. Haga clic en Atrás y elija otra carpeta.",
          whyBusy: "{name} está ocupado o no responde en este momento. Espere un minuto y haga clic en Intentar de nuevo.",
          whyOther: "{name} respondió con un error que la suite no reconoce. Haga clic en Intentar de nuevo. Si sigue ocurriendo, envíe al soporte de AI Curator la referencia que se muestra aquí.",
          whySave: "La suite no pudo guardar esta carpeta como su carpeta de clientes. Vuelva a hacer clic en Usar esta carpeta. Si sigue ocurriendo, envíe al soporte de AI Curator la referencia que se muestra aquí.",
          ref: "Referencia: {ref}",
          retry: "Intentar de nuevo",
          reconnect: "Volver a conectar {name}",
          pNetwork: "No se puede acceder a su carpeta de clientes en {name} en este momento: se cortó la conexión a internet o algo la bloqueó. Sigue guardada en su cuenta.",
          pBusy: "{name} no responde en este momento, así que su carpeta de clientes ({folder}) aún no se puede abrir. Sigue guardada en su cuenta.",
          pSuite: "La suite no pudo comprobar su carpeta de clientes ahora. No se cambió nada: su carpeta sigue guardada en su cuenta.",
          pNotConnected: "Su carpeta de clientes ({folder}) está en {name}, y hay que volver a conectar {name} para poder abrirla. La carpeta sigue guardada en su cuenta.",
          pNoFiles: "Su carpeta de clientes ({folder}) está en {name}, pero la conexión aún no permite archivos. Vuelva a conectarlo y permita los archivos.",
          pAccount: "Su carpeta de clientes ({folder}) está en {name}, pero la cuenta Microsoft conectada ahora no puede abrirla. Vuelva a conectar {name} con la cuenta que tiene la carpeta.",
          pGone: "No se encuentra su carpeta de clientes ({folder}) en {name}. Se movió, se cambió de nombre o se eliminó, o {name} está conectado con otra cuenta.",
          pOther: "{name} respondió con un error, así que su carpeta de clientes ({folder}) aún no se puede abrir. Sigue guardada en su cuenta.",
          chooseAgain: "Elegir la carpeta de nuevo",
          checking: "Comprobando...",
          pWith: "Se eligió con {account}.",
          useLocal: "Abrir la copia de este equipo por ahora",
          localNote: "{name} no se puede abrir en este momento, así que está trabajando en la copia de su carpeta de clientes de este equipo. Su carpeta de {name} sigue guardada en su cuenta.",
          tryCloud: "Probar {name} de nuevo" },
    fr: { where: 'Où est votre dossier clients ?', whereP: 'Choisissez-le une fois. La suite et les trois applications l\'ouvriront seules à la connexion, dans n\'importe quel navigateur.',
          od: 'Choisir un dossier dans OneDrive', gd: 'Choisir un dossier dans Google Drive', local: 'Utiliser un dossier de cet ordinateur',
          localP: 'Seulement dans ce navigateur sur cet ordinateur, et il peut le redemander après la fermeture.',
          connect: 'Connectez d\'abord {name}', allow: 'Autorisez d\'abord les fichiers {name}', odSub: 'Avec votre compte Microsoft, personnel ou professionnel', gdSub: 'Avec votre connexion Google',
          privacy: 'Vos fichiers circulent uniquement entre {name} et ce navigateur. La suite garde le dossier choisi, jamais son contenu.',
          browseH: 'Choisissez votre dossier clients dans {name}', top: 'Mes fichiers', use: 'Utiliser ce dossier', cancel: 'Annuler', back: 'Retour',
          loading: 'Chargement...', empty: 'Aucun dossier ici.', files: '{n} fichiers ici', failed: 'Impossible d\'ouvrir ce dossier. Réessayez.',
          chosen: 'Dossier clients : {path}', forget: 'Ne plus utiliser ce dossier', inCloud: '{name} : {folder}',
          whyNetwork: "Ce navigateur n'a pas pu joindre {name} : la connexion internet a été coupée, ou quelque chose sur ce réseau ou une extension du navigateur l'a bloqué. Vérifiez votre connexion, puis cliquez sur Réessayer.",
          whyNotConnected: "{name} n'est plus connecté à la suite. Cliquez sur Reconnecter {name}, puis choisissez le dossier.",
          whyReconnect: "{name} demande une nouvelle connexion (mot de passe changé ou accès retiré). Cliquez sur Reconnecter {name}, puis choisissez le dossier.",
          whyNoFiles: "La connexion à {name} n'autorise pas encore les fichiers. Cliquez sur Reconnecter {name} et autorisez les fichiers.",
          whyNoDrive: "Le compte Microsoft connecté n'a pas de OneDrive. Certains forfaits Microsoft 365, comme les forfaits messagerie seule, ne l'incluent pas ; les comptes Microsoft personnels si. Cliquez sur Reconnecter OneDrive et choisissez un compte qui a OneDrive, ou utilisez un dossier de cet ordinateur.",
          whyDenied: "{name} a refusé l'accès. Le dossier appartient peut-être à un autre compte, ou votre organisation n'autorise pas les applications à ouvrir des fichiers. Reconnectez-vous avec le compte propriétaire du dossier, ou demandez à votre administrateur informatique.",
          whyGone: "Ce dossier n'est plus dans {name} : il a été déplacé, renommé ou supprimé. Cliquez sur Retour et choisissez un autre dossier.",
          whyBusy: "{name} est occupé ou ne répond pas pour le moment. Attendez une minute, puis cliquez sur Réessayer.",
          whyOther: "{name} a répondu par une erreur que la suite ne reconnaît pas. Cliquez sur Réessayer. Si cela continue, envoyez au support AI Curator la référence affichée ici.",
          whySave: "La suite n'a pas pu garder ce dossier comme dossier clients. Cliquez à nouveau sur Utiliser ce dossier. Si cela continue, envoyez au support AI Curator la référence affichée ici.",
          ref: "Référence : {ref}",
          retry: "Réessayer",
          reconnect: "Reconnecter {name}",
          pNetwork: "Votre dossier clients dans {name} est inaccessible pour le moment : la connexion internet a été coupée, ou quelque chose l'a bloquée. Il est toujours enregistré sur votre compte.",
          pBusy: "{name} ne répond pas pour le moment, votre dossier clients ({folder}) ne peut donc pas encore être ouvert. Il est toujours enregistré sur votre compte.",
          pSuite: "La suite n'a pas pu vérifier votre dossier clients à l'instant. Rien n'a été modifié : votre dossier est toujours enregistré sur votre compte.",
          pNotConnected: "Votre dossier clients ({folder}) est dans {name}, et {name} doit être reconnecté avant de pouvoir l'ouvrir. Le dossier est toujours enregistré sur votre compte.",
          pNoFiles: "Votre dossier clients ({folder}) est dans {name}, mais la connexion n'autorise pas encore les fichiers. Reconnectez-vous et autorisez les fichiers.",
          pAccount: "Votre dossier clients ({folder}) est dans {name}, mais le compte Microsoft connecté actuellement ne peut pas l'ouvrir. Reconnectez {name} avec le compte qui contient le dossier.",
          pGone: "Votre dossier clients ({folder}) est introuvable dans {name}. Il a été déplacé, renommé ou supprimé, ou {name} est connecté avec un autre compte.",
          pOther: "{name} a répondu par une erreur, votre dossier clients ({folder}) ne peut donc pas encore être ouvert. Il est toujours enregistré sur votre compte.",
          chooseAgain: "Choisir à nouveau le dossier",
          checking: "Vérification...",
          pWith: "Il a été choisi avec {account}.",
          useLocal: "Ouvrir la copie de cet ordinateur pour l'instant",
          localNote: "{name} ne peut pas être ouvert pour le moment, vous travaillez donc dans la copie de votre dossier clients sur cet ordinateur. Votre dossier {name} est toujours enregistré sur votre compte.",
          tryCloud: "Réessayer {name}" },
    pt: { where: 'Onde está sua pasta de clientes?', whereP: 'Escolha uma vez. A suíte e os três aplicativos a abrem sozinhos quando você entra, em qualquer navegador.',
          od: 'Escolher pasta no OneDrive', gd: 'Escolher pasta no Google Drive', local: 'Usar uma pasta deste computador',
          localP: 'Só neste navegador deste computador, e ele pode pedir de novo depois que você fechar.',
          connect: 'Conecte o {name} primeiro', allow: 'Permita os arquivos do {name} primeiro', odSub: 'Com sua conta Microsoft, pessoal ou de trabalho', gdSub: 'Com sua conexão Google',
          privacy: 'Seus arquivos vão apenas entre o {name} e este navegador. A suíte guarda qual pasta você escolheu, nunca o conteúdo.',
          browseH: 'Escolha sua pasta de clientes no {name}', top: 'Meus arquivos', use: 'Usar esta pasta', cancel: 'Cancelar', back: 'Voltar',
          loading: 'Carregando...', empty: 'Nenhuma pasta aqui.', files: '{n} arquivos aqui', failed: 'Não foi possível abrir esta pasta. Tente de novo.',
          chosen: 'Pasta de clientes: {path}', forget: 'Parar de usar esta pasta', inCloud: '{name}: {folder}',
          whyNetwork: "Este navegador não conseguiu falar com o {name}: a conexão com a internet caiu, ou algo nesta rede ou uma extensão do navegador bloqueou. Verifique sua conexão e clique em Tentar de novo.",
          whyNotConnected: "O {name} não está mais conectado à suíte. Clique em Reconectar {name} e depois escolha a pasta.",
          whyReconnect: "O {name} pediu um novo login (senha alterada ou acesso removido). Clique em Reconectar {name} e depois escolha a pasta.",
          whyNoFiles: "A conexão com o {name} ainda não permite arquivos. Clique em Reconectar {name} e permita os arquivos.",
          whyNoDrive: "A conta Microsoft conectada não tem OneDrive. Alguns planos do Microsoft 365, como os só de email, não incluem; as contas Microsoft pessoais incluem. Clique em Reconectar OneDrive e escolha uma conta que tenha OneDrive, ou use uma pasta deste computador.",
          whyDenied: "O {name} recusou o acesso. A pasta pode pertencer a outra conta, ou sua organização não permite que aplicativos abram arquivos. Reconecte com a conta dona da pasta, ou fale com o administrador de TI.",
          whyGone: "Esta pasta não está mais no {name}: foi movida, renomeada ou excluída. Clique em Voltar e escolha outra pasta.",
          whyBusy: "O {name} está ocupado ou não responde agora. Espere um minuto e clique em Tentar de novo.",
          whyOther: "O {name} respondeu com um erro que a suíte não reconhece. Clique em Tentar de novo. Se continuar acontecendo, envie ao suporte da AI Curator a referência mostrada aqui.",
          whySave: "A suíte não conseguiu guardar esta pasta como sua pasta de clientes. Clique em Usar esta pasta de novo. Se continuar acontecendo, envie ao suporte da AI Curator a referência mostrada aqui.",
          ref: "Referência: {ref}",
          retry: "Tentar de novo",
          reconnect: "Reconectar {name}",
          pNetwork: "Sua pasta de clientes no {name} não pode ser acessada agora: a conexão com a internet caiu, ou algo a bloqueou. Ela continua salva na sua conta.",
          pBusy: "O {name} não está respondendo agora, então sua pasta de clientes ({folder}) ainda não pode ser aberta. Ela continua salva na sua conta.",
          pSuite: "A suíte não conseguiu verificar sua pasta de clientes agora. Nada foi alterado: sua pasta continua salva na sua conta.",
          pNotConnected: "Sua pasta de clientes ({folder}) está no {name}, e o {name} precisa ser conectado de novo para abri-la. A pasta continua salva na sua conta.",
          pNoFiles: "Sua pasta de clientes ({folder}) está no {name}, mas a conexão ainda não permite arquivos. Reconecte e permita os arquivos.",
          pAccount: "Sua pasta de clientes ({folder}) está no {name}, mas a conta Microsoft conectada agora não consegue abri-la. Reconecte o {name} com a conta que tem a pasta.",
          pGone: "Sua pasta de clientes ({folder}) não foi encontrada no {name}. Ela foi movida, renomeada ou excluída, ou o {name} está conectado com outra conta.",
          pOther: "O {name} respondeu com um erro, então sua pasta de clientes ({folder}) ainda não pode ser aberta. Ela continua salva na sua conta.",
          chooseAgain: "Escolher a pasta de novo",
          checking: "Verificando...",
          pWith: "Ela foi escolhida com {account}.",
          useLocal: "Abrir a cópia deste computador por enquanto",
          localNote: "O {name} não pode ser aberto agora, então você está trabalhando na cópia da sua pasta de clientes neste computador. Sua pasta do {name} continua salva na sua conta.",
          tryCloud: "Tentar o {name} de novo" },
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
  const tokens = {}, asking = {};
  let choosing = false;      // the folder browser is open: any connected account may be browsed
  // A token for this provider, or a CloudError: 'not_connected' or 'no_files'.
  // For the kept folder the suite only gives the token of the Microsoft account
  // the folder belongs to (onedrive_files.py): never another account's.
  // Every call on the page waits for the same answer: one request, not one each.
  function token(provider) {
    const slot = provider + (choosing ? ':browse' : '');
    const kept = tokens[slot];
    if (kept && kept.until > Date.now()) return Promise.resolve(kept.access);
    if (!asking[slot]) asking[slot] = tokenNow(provider, slot).finally(() => { delete asking[slot]; });
    return asking[slot];
  }
  async function tokenNow(provider, slot) {
    let got;
    // the suite's own reason rides along (microsoft_reconnect, onedrive_not_connected, microsoft_unavailable)
    try { got = await suite('/integrations/' + SOURCE[provider] + '/token' + (slot.endsWith(':browse') && provider === 'onedrive' ? '?purpose=browse' : '')); } catch (e) {
      // the suite itself didn't answer (offline, a restart): never "not connected"
      if (!(e instanceof CloudError)) throw new CloudError('network', e.message);
      if (/^suite_(429|5\d\d)$/.test(e.code)) throw Object.assign(new CloudError('http_503', e.message), { status: 503, detail: e.code });
      throw Object.assign(new CloudError('not_connected', e.message), { detail: e.code });
    }
    if (!got || !got.access_token) throw new CloudError('not_connected', 'no token');
    if (Array.isArray(got.scopes) && !got.scopes.some(s => s === SCOPE[provider] || s.endsWith('/' + SCOPE[provider]))) throw new CloudError('no_files', 'files not allowed');
    const until = got.expires_at ? Date.parse(got.expires_at) - 60000 : Date.now() + 5 * 60000;
    tokens[slot] = { access: got.access_token, until };
    return got.access_token;
  }
  // Microsoft's or Google's own words for a refusal: { code, hint } (the code is
  // shown as the reference; the message is only looked at, never shown or kept)
  async function refusalOf(r) {
    try {
      const b = await r.clone().json(), e = (b && b.error) || {};
      const inner = (e.innerError && e.innerError.code) || (Array.isArray(e.errors) && e.errors[0] && e.errors[0].reason) || '';
      const code = String(typeof e === 'string' ? e : (e.code || e.status || '')).replace(/[^A-Za-z0-9_.:-]/g, '').slice(0, 60);
      return { graph: [code, String(inner).replace(/[^A-Za-z0-9_.:-]/g, '').slice(0, 60)].filter(Boolean).join(' '), hint: String(e.message || '') };
    } catch (x) { return { graph: '', hint: '' }; }
  }
  // A call to Graph or Drive with the token; one retry with a fresh token.
  async function call(provider, url, opts, again) {
    const access = await token(provider);
    let r;
    try {
      r = await fetch(url, Object.assign({}, opts, { headers: Object.assign({ Authorization: 'Bearer ' + access }, (opts && opts.headers) || {}) }));
    } catch (e) { throw new CloudError('network', e.message); }      // offline, blocked by the network or an extension
    if (r.status === 401 && !again) { delete tokens[provider]; delete tokens[provider + ':browse']; return call(provider, url, opts, true); }
    if (r.status === 404) throw Object.assign(notFound(url), { status: 404 }, await refusalOf(r));
    if (!r.ok) throw Object.assign(new CloudError('http_' + r.status, url.split('?')[0] + ' ' + r.status), { status: r.status }, await refusalOf(r));
    return r;
  }

  // Why a folder could not be opened, in plain words, with what to do next and
  // the provider's own reference. top: the very top of the drive (a 404 there
  // means the account has no OneDrive at all).
  function why(provider, e, top) {
    const code = String((e && e.code) || ''), st = (e && e.status) || 0, hint = String((e && e.hint) || '').toLowerCase();
    const ref = [st ? 'HTTP ' + st : '', e && e.graph ? e.graph : '', e && e.detail ? e.detail : ''].filter(Boolean).join(' ')
      || (code && code !== 'network' ? code : '');
    let key = 'whyOther', fix = '';
    if (code === 'network') key = 'whyNetwork';
    else if (code === 'no_files') { key = 'whyNoFiles'; fix = 'reconnect'; }
    else if (code === 'not_connected') {
      key = e.detail === 'microsoft_unavailable' ? 'whyBusy' : e.detail === 'microsoft_reconnect' ? 'whyReconnect' : 'whyNotConnected';
      fix = key === 'whyBusy' ? '' : 'reconnect';
    } else if (provider === 'onedrive' && (/spo license|mysite|not provisioned|onedrive.*(not|isn).*(set up|available)/.test(hint) || (top && st === 404))) {
      key = 'whyNoDrive'; fix = 'reconnect';
    } else if (st === 401) { key = 'whyReconnect'; fix = 'reconnect'; }
    else if (st === 403) { key = 'whyDenied'; fix = 'reconnect'; }
    else if (st === 404) key = 'whyGone';
    else if (st === 429 || st >= 500) key = 'whyBusy';
    return { key, ref, fix, text: say(key, { name: NAMES[provider] }) };
  }
  // The same cause in the Microsoft sync log on Integrations: kind and codes only, never a name
  function logWhy(provider, where, w) {
    if (provider !== 'onedrive') return;
    const detail = (where + ' ' + w.key + ' ' + w.ref).replace(/[^A-Za-z0-9 .,:_-]/g, '').slice(0, 200);
    try { fetch(SUITE + '/api/integrations/microsoft/log', { method: 'POST', credentials: 'include', body: JSON.stringify({ kind: 'files', detail }) }).catch(() => {}); } catch (x) { /* best effort */ }
    if (typeof console !== 'undefined') console.warn('[clients folder]', where, w.key, w.ref);
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
    // the folder itself, by its ids: can this connection open it?
    async probe(folder) { await json('onedrive', `${GRAPH}/drives/${folder.drive_id}/items/${folder.item_id}?$select=id`); },
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
    async probe(folder) {
      const f = await json('gdrive', `${DRIVE}/files/${folder.item_id}?fields=id,trashed&supportsAllDrives=true`);
      if (f && f.trashed) throw Object.assign(notFound(folder.item_id), { status: 404 });
    },
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

  // ---- fewer calls ---------------------------------------------------------------------
  // OneDrive and Google Drive answer slowly when asked many things at once, so a
  // page asks for a folder's listing once and finds each file's id in it, instead
  // of asking for every file by name. A listing is trusted for a short while and
  // only for names it has: a name it doesn't have is always asked for.
  const LIST_FRESH = 45000;
  const listings = new Map();      // provider:drive:folder id -> { at, byName: Promise<Map> }
  const listKey = (provider, dir) => provider + ':' + (dir.driveId || '') + ':' + dir.id;
  function listed(provider, dir, fresh) {
    const key = listKey(provider, dir), hit = listings.get(key);
    if (hit && !fresh && Date.now() - hit.at < LIST_FRESH) return hit.byName;
    const items = API[provider].list(dir);
    const entry = { at: Date.now(), items, byName: items.then(list => new Map(list.map(n => [n.name, n]))) };
    listings.set(key, entry);
    entry.byName.catch(() => { if (listings.get(key) === entry) listings.delete(key); });
    return entry.byName;
  }
  async function find(provider, dir, name) {
    let known = null;
    try { known = (await listed(provider, dir)).get(name); } catch (e) { /* asked for by name below */ }
    if (known) return known;
    const n = await API[provider].child(dir, name);          // not in the listing: asked for, so a new file is never missed
    const hit = listings.get(listKey(provider, dir));
    if (hit) hit.byName.then(m => m.set(n.name, n), () => {});
    return n;
  }
  function remember(provider, dir, node, gone) {
    const hit = listings.get(listKey(provider, dir));
    if (hit) hit.byName.then(m => { if (gone) m.delete(node.name); else m.set(node.name, node); }, () => {});
  }

  // ---- folder and file handles, as clientfile.js expects them ---------------------------
  class CloudFile {
    constructor(provider, node) { this.kind = 'file'; this.name = node.name; this.provider = provider; this.node = node; }
    async getFile() {
      let blob;
      try { blob = await API[this.provider].read(this.node); } catch (e) { if (e && e.name === 'NotFoundError') listings.clear(); throw e; }
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
      const byName = await listed(this.provider, this.node, true);       // a listing asked for is always a new one
      for (const n of byName.values()) yield n.dir ? new CloudDir(this.provider, n) : new CloudFile(this.provider, n);
    }
    async *entries() { for await (const h of this.values()) yield [h.name, h]; }
    async getFileHandle(name, opts) {
      try {
        const n = await find(this.provider, this.node, name);
        if (n.dir) { const e = new Error('is a folder'); e.name = 'TypeMismatchError'; throw e; }
        return new CloudFile(this.provider, n);
      } catch (e) {
        if (e.name !== 'NotFoundError' || !(opts && opts.create)) throw e;
        const made = await API[this.provider].create(this.node, name);
        remember(this.provider, this.node, made);
        return new CloudFile(this.provider, made);
      }
    }
    async getDirectoryHandle(name, opts) {
      try {
        const n = await find(this.provider, this.node, name);
        if (!n.dir) { const e = new Error('is a file'); e.name = 'TypeMismatchError'; throw e; }
        return new CloudDir(this.provider, n);
      } catch (e) {
        if (e.name !== 'NotFoundError' || !(opts && opts.create)) throw e;
        const made = await API[this.provider].mkdir(this.node, name);
        remember(this.provider, this.node, made);
        return new CloudDir(this.provider, made);
      }
    }
    async removeEntry(name) {
      const n = await find(this.provider, this.node, name);
      await API[this.provider].remove(n);
      remember(this.provider, this.node, n, true);
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
  let saved;     // undefined: not asked yet (or the suite didn't answer); null: none
  let providers = {};
  let unknown = false;     // the account couldn't be asked: never the same as "no folder"
  const sleep = ms => new Promise(done => setTimeout(done, ms));
  const WAITS = [0, 600, 1800];     // a slow or busy answer is asked for again before anything is decided
  // a failure worth asking again: offline for a moment, busy, a server restarting
  const passing = e => { const c = String((e && e.code) || ''), st = (e && e.status) || 0; return c === 'network' || st === 429 || st >= 500 || (e && e.detail === 'microsoft_unavailable'); };
  async function current(fresh) {
    if (saved === undefined || fresh) {
      unknown = true;
      for (const wait of WAITS) {
        if (wait) await sleep(wait);
        try {
          const got = await suite('/cloud-folder/current');
          saved = got.folder || null;
          providers = got.providers || {};
          unknown = false;
          break;
        } catch (e) {
          if (e instanceof CloudError && !/^suite_(429|5\d\d)$/.test(e.code)) { saved = null; unknown = false; break; }   // signed out: the page goes to sign-in
          // in an app, a suite that can't be reached means no suite account here (or it is blocked):
          // the app carries on with a folder on this computer, as it always has
          if (SUITE && !(e instanceof CloudError)) { saved = null; unknown = false; break; }
        }
      }
      if (unknown && saved === undefined) return null;
    }
    return saved;
  }
  const folderName = folder => (folder && folder.path && folder.path.length ? folder.path[folder.path.length - 1] : '') || (folder ? NAMES[folder.provider] : '');
  // Why the account's folder can't be opened now: { provider, folder, key, ref, fix }.
  // fix: 'retry', 'reconnect' or 'choose' (the one button).
  function problemOf(folder, e) {
    const provider = folder ? folder.provider : '';
    if (!folder) return { provider: '', folder: '', key: 'pSuite', ref: '', fix: 'retry' };
    const w = why(provider, e, false);
    const st = (e && e.status) || 0;
    if (e && e.detail === 'onedrive_other_account') return { provider, folder: folderName(folder), account: folder.account || '', key: 'pAccount', ref: w.ref, fix: 'reconnect' };
    const key = { whyNetwork: 'pNetwork', whyBusy: 'pBusy', whyNotConnected: 'pNotConnected', whyReconnect: 'pNotConnected', whyNoFiles: 'pNoFiles',
                  whyNoDrive: 'pAccount', whyDenied: 'pAccount', whyGone: 'pGone' }[w.key] || (st === 400 ? 'pAccount' : 'pOther');
    const fix = key === 'pGone' ? 'choose' : key === 'pNotConnected' || key === 'pNoFiles' || key === 'pAccount' ? 'reconnect' : 'retry';
    return { provider, folder: folderName(folder), account: folder.account || '', key, ref: w.ref, fix };
  }
  // The account's folder, checked: { state: 'ready', handle } when this connection can open it,
  // { state: 'none' } when the account has none, { state: 'problem', problem } when it has one that
  // can't be opened now. A problem is never "none": the folder stays kept, and the page says why.
  let opening = null;
  function open(fresh) {
    if (!opening || fresh) opening = openNow(!!fresh);
    return opening;
  }
  async function openNow(fresh) {
    const folder = await current(fresh);
    if (unknown) return { state: 'problem', problem: problemOf(null) };
    if (!folder) return { state: 'none' };
    const fail = e => {
      const problem = problemOf(folder, e);
      logWhy(folder.provider, 'open', { key: problem.key, ref: problem.ref });
      return { state: 'problem', problem };
    };
    const st = providers[folder.provider];
    if (st !== 'ready') return fail(Object.assign(new CloudError(st === 'no_files' ? 'no_files' : 'not_connected', 'not ready'), st === 'other_account' ? { detail: 'onedrive_other_account' } : {}));
    let err = null;
    for (const wait of WAITS) {
      if (wait) await sleep(wait);
      try { await API[folder.provider].probe(folder); return { state: 'ready', handle: handleFor(folder) }; }
      catch (e) { err = e; if (!passing(e)) break; delete tokens[folder.provider]; }
    }
    return fail(err);
  }
  // The folder's handle, or null (kept for the pages that only ask "is there one I can use")
  async function restore() {
    const got = await open();
    return got.state === 'ready' ? got.handle : null;
  }
  // From the page that chose "a folder on this computer": the only thing that takes the folder off the account.
  async function forget() {
    saved = null; opening = null;
    try { await suite('/cloud-folder/clear'); } catch (e) { /* kept on the account; the next choice replaces it */ }
  }

  // ---- the bar: the account's folder can't be opened now, why, and one button -----------------
  // The same on every page. Try again checks once more and, when the folder opens, loads the page
  // again; Reconnect goes to Microsoft or Google and comes back to the Clients page with the kept
  // folder; Choose the folder again opens that provider's folder browser.
  // opts.local: this browser may still open the same folder on this computer; the bar then offers
  // "Open the copy on this computer for now" (said, never done silently; the kept folder stays kept).
  function showProblem(problem, opts) {
    hideProblem();
    if (!problem) return null;
    const useLocal = opts && typeof opts.local === 'function' ? opts.local : null;
    if (!styled) { const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = SUITE + '/css/cloudfolder.css'; document.head.appendChild(link); styled = true; }
    const name = NAMES[problem.provider] || 'OneDrive';
    const bar = el('div', 'aiccs-cf-bar'); bar.id = 'aiccs-cf-bar'; bar.setAttribute('role', 'alert'); bar.dataset.key = problem.key;
    const tx = el('div', 'aiccs-cf-bartext');
    tx.append(el('b', null, say(problem.key, { name, folder: problem.folder || name })
      + (problem.account && (problem.key === 'pAccount' || problem.key === 'pNotConnected') ? ' ' + say('pWith', { account: problem.account }) : '')));
    if (problem.ref) tx.append(el('small', null, say('ref', { ref: problem.ref })));
    bar.append(tx);
    const again = el('button', 'aiccs-cf-btn' + (problem.fix === 'retry' ? ' primary' : ''), say('retry')); again.type = 'button'; again.id = 'aiccs-cf-bar-retry';
    again.onclick = async () => {
      again.disabled = true; again.textContent = say('checking');
      const got = await open(true).catch(() => null);
      if (got && got.state === 'ready') { location.reload(); return; }
      showProblem(got && got.problem ? got.problem : problem, opts);
    };
    if (problem.fix === 'reconnect') {
      const go = el('a', 'aiccs-cf-btn primary', say('reconnect', { name })); go.id = 'aiccs-cf-bar-go'; go.href = connectUrl(problem.provider || 'onedrive');
      bar.append(go);
    } else if (problem.fix === 'choose') {
      const pick = el('button', 'aiccs-cf-btn primary', say('chooseAgain')); pick.type = 'button'; pick.id = 'aiccs-cf-bar-choose';
      pick.onclick = async () => { const h = await start(problem.provider).catch(() => null); if (h) location.reload(); };
      const go = el('a', 'aiccs-cf-btn', say('reconnect', { name })); go.id = 'aiccs-cf-bar-go'; go.href = connectUrl(problem.provider || 'onedrive');
      bar.append(pick, go);
    }
    if (problem.fix === 'retry') bar.append(again);
    if (useLocal) {
      const lo = el('button', 'aiccs-cf-btn', say('useLocal')); lo.type = 'button'; lo.id = 'aiccs-cf-bar-local';
      lo.onclick = async () => { lo.disabled = true; if (await useLocal()) location.reload(); else lo.disabled = false; };
      bar.append(lo);
    }
    const host = document.querySelector('main') || document.body;
    host.insertBefore(bar, host.firstChild);
    return bar;
  }
  // Working in the copy on this computer because the kept folder can't be opened: said on every
  // page, with the way back. Try again checks the kept folder and, when it opens, uses it again.
  function showLocalNote(problem, back) {
    hideProblem();
    if (!styled) { const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = SUITE + '/css/cloudfolder.css'; document.head.appendChild(link); styled = true; }
    const name = NAMES[problem && problem.provider] || 'OneDrive';
    const bar = el('div', 'aiccs-cf-bar aiccs-cf-barlocal'); bar.id = 'aiccs-cf-bar'; bar.setAttribute('role', 'status'); bar.dataset.key = 'localNote';
    const tx = el('div', 'aiccs-cf-bartext'); tx.append(el('b', null, say('localNote', { name }))); bar.append(tx);
    const again = el('button', 'aiccs-cf-btn', say('tryCloud', { name })); again.type = 'button'; again.id = 'aiccs-cf-bar-cloud';
    again.onclick = async () => {
      again.disabled = true; again.textContent = say('checking');
      const got = await open(true).catch(() => null);
      if (got && got.state === 'ready') { if (back) await back(); location.reload(); return; }
      again.disabled = false; again.textContent = say('tryCloud', { name });
    };
    if (problem && problem.fix === 'reconnect') { const go = el('a', 'aiccs-cf-btn', say('reconnect', { name })); go.id = 'aiccs-cf-bar-go'; go.href = connectUrl(problem.provider || 'onedrive'); bar.append(go); }
    bar.append(again);
    const host = document.querySelector('main') || document.body;
    host.insertBefore(bar, host.firstChild);
    return bar;
  }
  function hideProblem() { const old = typeof document !== 'undefined' && document.getElementById('aiccs-cf-bar'); if (old) old.remove(); }

  // ---- the dialogs (their own look, so the apps need no styles) ---------------------------
  const FILE_ICON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3v5h5"/></svg>';
  const FOLDER_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M3 6.5A2.5 2.5 0 0 1 5.5 4h4l2 2.2h7A2.5 2.5 0 0 1 21 8.7v8.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/></svg>';
  // The three places' icons: plain line drawings in the suite's own style, never a
  // company's logo (a cloud, a storage drive, a folder); our own constants.
  const SVG = d => '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
  const PLACE_ICON = {
    onedrive: SVG('<path d="M7 18.5h10.5a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7.6 9.1 4.75 4.75 0 0 0 7 18.5z"/>'),
    gdrive: SVG('<rect x="3.5" y="13" width="17" height="6" rx="2"/><path d="M5.2 13 8 6.2A2 2 0 0 1 9.85 5h4.3a2 2 0 0 1 1.85 1.2L18.8 13"/><path d="M16.5 16h.01M13.5 16h.01"/>'),
    local: SVG('<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h3.8l2 2.2h7.2a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>'),
  };
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
    return SUITE + (provider === 'onedrive' ? '/api/integrations/onedrive/connect' : '/api/integrations/google/connect?drive=1');
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
      const opt = (cls, icon, title, sub, onclick) => {
        const b = el('button', 'aiccs-cf-opt ' + cls); b.type = 'button';
        const i = el('i'); i.setAttribute('aria-hidden', 'true');
        i.innerHTML = PLACE_ICON[icon];      // our own constant icon
        const tx = el('span'); tx.append(el('b', null, title), el('small', null, sub));
        b.append(i, tx); b.onclick = onclick; return b;
      };
      [['onedrive', od, 'aiccs-cf-od', 'od', 'odSub'], ['gdrive', gd, 'aiccs-cf-gd', 'gd', 'gdSub']].forEach(([p, st, cls, key, sub]) => {
        if (only && only !== p) return;
        const ready = st === 'ready';
        const subText = ready ? say(sub) : say(st === 'no_files' ? 'allow' : 'connect', { name: NAMES[p] });
        f.append(opt(cls, p, say(key), subText, async () => {
          if (!ready) { location.href = connectUrl(p); return; }   // back to the chooser after Microsoft or Google
          away = true; d.close();
          const picked = await browse(p);
          away = false;
          if (picked) done(picked); else d.showModal();
        }));
      });
      if (localPick) {
        f.append(opt('aiccs-cf-lo', 'local', say('local'), say('localP'), async () => {
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
      choosing = true;         // a folder is being chosen: whichever account is connected may be browsed
      const done = v => { if (!settled) { settled = true; choosing = false; listings.clear(); resolve(v); if (d.open) d.close(); d.remove(); } };
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
      // what failed, what to do, the reference, and one button (Try again, or Reconnect)
      function failed(e, top, again) {
        const w = why(provider, e, top);
        logWhy(provider, top ? 'top' : 'list', w);
        const li = el('li', 'aiccs-cf-msg aiccs-cf-why');
        li.setAttribute('role', 'alert');
        li.append(el('span', null, w.text));
        if (w.ref) li.append(el('small', null, say('ref', { ref: w.ref })));
        let b;
        if (w.fix === 'reconnect') { b = el('a', 'aiccs-cf-btn primary', say('reconnect', { name: NAMES[provider] })); b.href = connectUrl(provider); }
        else if (w.key !== 'whyGone') { b = el('button', 'aiccs-cf-btn', say('retry')); b.type = 'button'; b.onclick = again; }
        if (b) li.append(b);
        list.replaceChildren(li);
      }
      async function open() {
        drawCrumbs();
        use.disabled = trail.length < 2;    // not the very top of the drive
        list.replaceChildren(el('li', 'aiccs-cf-msg', say('loading')));
        note.textContent = '';
        let items;
        try { items = await API[provider].list(trail[trail.length - 1].node); } catch (e) { failed(e, false, open); return; }
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
        // kept on the account first, then read back: only then is it done
        try {
          await suite('/cloud-folder', folder);
          const got = await suite('/cloud-folder/current');
          if (!got.folder || got.folder.item_id !== folder.item_id || got.folder.provider !== provider) throw new CloudError('not_kept', 'not kept');
          saved = got.folder; providers = got.providers || providers; unknown = false;
          choosing = false; delete tokens[provider];      // from here on: the kept folder's own account only
        } catch (e) {
          use.disabled = false;
          note.textContent = say('whySave') + ' ' + say('ref', { ref: (e && e.code) || 'unknown' });
          return;
        }
        opening = Promise.resolve({ state: 'ready', handle: handleFor(saved) });
        hideProblem();
        done(handleFor(saved));
      };
      const begin = async () => {
        list.replaceChildren(el('li', 'aiccs-cf-msg', say('loading')));
        try { const top = await API[provider].top(); trail.push({ node: top, name: say('top') }); open(); }
        catch (e) { failed(e, true, begin); }
      };
      begin();
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

  return { restore, open, problemOf, showProblem, showLocalNote, hideProblem, folderName, choose, browse, start, forget, current, returning, names: NAMES, label, say, CloudError,
           _api: API, _handleFor: handleFor, _why: why };
})();
if (typeof module !== 'undefined') module.exports = CloudFolder;
