/* Single sign-on with the AI Curator Consulting Suite: the browser's side.
   The same file is used by all three apps; only SSO_CONFIG differs.
   The server's side is suite_sso.py; the design is docs/SSO_DESIGN.md in
   the aicsuite repo.

   Nothing here does anything unless this app's API says single sign-on is
   on (GET /sso/status). While it is off, or if that question can't be
   answered, the app's own login and signup pages work as they always have.

   SuiteSSO.handOverIfOn(kind)  on the login / signup / forgot-password
                                pages: if it is on, leave for the suite.
   SuiteSSO.start()             sso-start.html: begin a sign-in.
   SuiteSSO.finish()            sso-callback.html: complete it. */
const SSO_CONFIG = {
  app: 'consultcastai',
  prodApi: 'https://consultcastai-api.onrender.com',
  localApi: 'http://localhost:8081',
  baseKey: 'consultcastai_base',       // a developer's saved API address, if any
  tokenKey: 'consultcastai_token',     // where the app keeps its login token
  langKey: null,                       // this app has one language
  home: 'index.html',
  login: 'index.html',                 // the login screen is part of the one page
  // A team invitation this browser was holding when it went off to sign in
  // (index.html stores it): hand it to /sso/exchange, and forget it once
  // the sign-in has succeeded.
  extra: function(){
    let t = null;
    try{ t = localStorage.getItem('consultcastai_invite'); }catch(_){}
    return t ? { invite_token: t } : {};
  },
  done: function(){ try{ localStorage.removeItem('consultcastai_invite'); }catch(_){} },
};

const SuiteSSO = (function(){
  const C = SSO_CONFIG;
  const LOCAL = ['localhost', '127.0.0.1'].includes(location.hostname);
  const PENDING = C.app + '_sso';                 // sessionStorage: this tab's sign-in in progress
  const AFTER = C.app + '_after_login';           // sessionStorage: where to land afterwards (a #fragment)
  const STARTS = C.app + '_sso_starts';           // sessionStorage: when this tab last began a sign-in
  const here = location.pathname.replace(/[^/]*$/, '');

  function apiBase(){
    let saved = null;
    try{ saved = localStorage.getItem(C.baseKey); }catch(_){}
    return saved || (LOCAL ? C.localApi : C.prodApi);
  }

  async function status(){
    const res = await fetch(apiBase() + '/sso/status');
    if(!res.ok) throw new Error('status ' + res.status);
    return res.json();
  }

  function b64url(bytes){
    let s = '';
    bytes.forEach(b => { s += String.fromCharCode(b); });
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function random(n){ return b64url(crypto.getRandomValues(new Uint8Array(n))); }

  function say(text, retry){
    const el = document.getElementById('ssoMsg');
    if(el) el.textContent = text;
    const link = document.getElementById('ssoRetry');
    if(link) link.hidden = !retry;
  }

  /* On the app's own login, signup and forgot-password pages. `kind` is
     'login', 'signup' or 'forgot'. Leaves the page only when single
     sign-on is on; any failure leaves the page exactly as it is. */
  async function handOverIfOn(kind){
    // Keep the page's own form out of sight while the question is asked,
    // so nobody starts typing into a page that is about to leave. It comes
    // back the moment the answer is "off", on any failure, and after 3
    // seconds whatever happens.
    const root = document.documentElement;
    const show = () => { root.style.visibility = ''; };
    root.style.visibility = 'hidden';
    const timer = setTimeout(show, 3000);
    try{
      const s = await status();
      if(!s.enabled){ clearTimeout(timer); show(); return false; }
      if(kind === 'signup'){ location.replace(s.suite + '/signup.html'); return true; }
      if(kind === 'forgot'){ location.replace(s.suite + '/forgot-password.html'); return true; }
      location.replace(here + 'sso-start.html');
      return true;
    }catch(_){ clearTimeout(timer); show(); return false; }
  }

  /* A sign-in that succeeds at the suite but is then refused here would
     send the tab round and round. More than four starts inside a minute
     and it stops and says so instead. */
  function goingInCircles(){
    try{
      const now = Date.now();
      const recent = JSON.parse(sessionStorage.getItem(STARTS) || '[]').filter(t => now - t < 60000);
      recent.push(now);
      sessionStorage.setItem(STARTS, JSON.stringify(recent));
      return recent.length > 4;
    }catch(_){ return false; }
  }

  /* sso-start.html. Makes a random state and a PKCE verifier, keeps both in
     this tab only, and sends the browser to the suite. The suite sends it
     back to sso-callback.html with a one-time code. */
  async function start(){
    try{
      const s = await status();
      if(!s.enabled){ location.replace(here + C.login); return; }
      if(goingInCircles()){
        say('Sign-in is not completing. Please wait a minute and try again. If it keeps happening, contact support.', false);
        return;
      }
      const state = random(24);
      const verifier = random(48);
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
      sessionStorage.setItem(PENDING, JSON.stringify({ state, verifier }));
      const q = new URLSearchParams({ app: C.app, state, code_challenge: b64url(new Uint8Array(digest)) });
      // a developer's machine: tell the (local) suite to come back here
      if(LOCAL) q.set('redirect_uri', location.origin + here + 'sso-callback.html');
      location.replace(s.suite + '/sso/authorize?' + q.toString());
    }catch(_){
      say('We could not reach the sign-in service. Please try again in a moment.', true);
    }
  }

  /* sso-callback.html. Checks the state is the one this tab made, hands
     the code and the verifier to this app's API, and stores the login the
     API returns exactly where the app's own login page would. */
  async function finish(){
    const p = new URLSearchParams(location.search);
    const code = p.get('code'), state = p.get('state');
    // the code is single use; still, take it out of the address bar and history
    try{ history.replaceState(null, '', location.pathname); }catch(_){}
    let pending = null;
    try{ pending = JSON.parse(sessionStorage.getItem(PENDING) || 'null'); sessionStorage.removeItem(PENDING); }catch(_){}
    if(!code || !pending || pending.state !== state){
      say('This sign-in link is not valid any more. Please start again.', true);
      return;
    }
    try{
      const res = await fetch(apiBase() + '/sso/exchange', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.assign({ code, code_verifier: pending.verifier }, extra())),
      });
      const data = await res.json().catch(() => ({}));
      if(!res.ok || !data.api_token) throw new Error('exchange ' + res.status);
      localStorage.setItem(C.tokenKey, data.api_token);
      if(C.langKey && data.language) localStorage.setItem(C.langKey, data.language);
      if(typeof C.done === 'function') C.done();
      let after = '';
      try{ after = sessionStorage.getItem(AFTER) || ''; sessionStorage.removeItem(AFTER); }catch(_){}
      location.replace(here + C.home + (after.charAt(0) === '#' ? after : ''));
    }catch(_){
      say('We could not complete your sign-in. Please try again.', true);
    }
  }

  /* Anything else this app's /sso/exchange accepts: SSO_CONFIG.extra()
     supplies it and SSO_CONFIG.done() runs once the sign-in has succeeded. */
  function extra(){ return (typeof C.extra === 'function') ? C.extra() : {}; }

  /* Called by the app just before it sends a signed-out visitor to its
     login page, so a link like index.html#suite-open survives the trip. */
  function rememberFragment(){
    try{ if(location.hash) sessionStorage.setItem(AFTER, location.hash); }catch(_){}
  }

  return { handOverIfOn, start, finish, rememberFragment, apiBase };
})();
