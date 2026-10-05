/* The AI Curator app switcher: one small button in the header that opens a
   menu with the Command Center (the suite home) and the other two apps.
   The same file is used by ClientBriefAI, AdoptBriefAI and ConsultCastAI,
   so it looks and behaves the same in all three. It brings its own styles
   and markup; the page only says where it goes:

     <script src="suite_switcher.js" data-app="clientbriefai"
             data-before="#accountMenu" data-icon="assets/aicurator-spark.png"
             data-lang-key="clientbriefai_ui_lang"></script>

   data-app       which app this is (it is left out of the menu)
   data-before    the header element the button is placed in front of
   data-icon      the AI Curator sparkle
   data-lang-key  where the app keeps its language in localStorage; leave
                  it out for an app with one language, and the browser's
                  language is used

   Every link leads to the named tab of its destination, the same names
   the suite home uses for Launch: if that tab is already open it is
   brought to the front as it is, not reloaded; otherwise a new tab is
   opened with that name. This page is never navigated away from. No
   noopener on these links: the tabs have to stay tied together to be
   found by name. With single sign-on on, the other app opens already
   signed in.

   The list itself is shown in a frame served by the suite
   (menu/apps.html there). A browser lets a page switch to a tab by name
   only when its own address opened that tab or shares its address, so
   from this app's address another app's tab cannot be reached and a
   second copy would open. From the suite's address every tab can. The
   links this file draws are the fallback: they show until the frame says
   it is ready, and stay if it never loads. */
(function(){
  const tag = document.currentScript;
  if(!tag) return;
  const HERE = tag.dataset.app || '';
  const BEFORE = tag.dataset.before || '';
  const ICON = tag.dataset.icon || 'assets/aicurator-spark.png';
  const LANG_KEY = tag.dataset.langKey || '';

  const SUITE = 'https://aicsuite.ai-curator.ai/index.html';
  const SUITE_ORIGIN = new URL(SUITE).origin;
  const SUITE_MENU = SUITE_ORIGIN + '/menu/apps.html';
  const SUITE_TAB = 'aiccs-command-center';
  const appTab = key => 'aiccs-' + key;
  // Product names are never translated. The dot colors are the suite's.
  const APPS = [
    { key: 'consultcastai', name: 'ConsultCastAI', url: 'https://consultcastai.ai-curator.ai/', color: '#19d7ff' },
    { key: 'clientbriefai', name: 'ClientBriefAI', url: 'https://clientbriefai.ai-curator.ai/index.html', color: '#815bff' },
    { key: 'adoptbriefai', name: 'AdoptBriefAI', url: 'https://adoptbriefai.ai-curator.ai/index.html', color: '#d94bff' },
  ];
  // ES / FR / PT not yet reviewed by a native speaker (same caveat as the apps).
  const TEXT = {
    en: { home: 'Command Center', label: 'AI Curator apps' },
    es: { home: 'Centro de mando', label: 'Aplicaciones de AI Curator' },
    fr: { home: 'Centre de pilotage', label: 'Applications AI Curator' },
    pt: { home: 'Central de comando', label: 'Aplicativos AI Curator' },
  };

  function lang(){
    let chosen = '';
    try{ if(LANG_KEY) chosen = localStorage.getItem(LANG_KEY) || ''; }catch(_){}
    if(!chosen) chosen = String(navigator.language || 'en').toLowerCase().split('-')[0];
    return TEXT[chosen] ? chosen : 'en';
  }

  const CSS = `
.aic-switch{position:relative;flex:none;display:inline-flex;align-items:center;font-family:Inter,-apple-system,"Segoe UI",Arial,sans-serif;}
.aic-switch-btn{width:32px;height:32px;border-radius:10px;padding:0;cursor:pointer;display:flex;align-items:center;justify-content:center;
  background:#0b0e22;border:1px solid rgba(25,215,255,.45);box-shadow:0 0 12px rgba(25,215,255,.18);transition:border-color .15s,box-shadow .15s;}
.aic-switch-btn:hover,.aic-switch-btn[aria-expanded="true"]{border-color:#19d7ff;box-shadow:0 0 18px rgba(25,215,255,.4);}
.aic-switch-btn:focus-visible{outline:2px solid #19d7ff;outline-offset:2px;}
.aic-switch-btn img{width:22px;height:22px;display:block;}
@media (pointer:coarse){.aic-switch-btn{width:40px;height:40px;}.aic-switch-btn img{width:26px;height:26px;}}
.aic-switch-menu{position:absolute;right:0;top:calc(100% + 8px);z-index:9999;min-width:210px;padding:6px;text-align:left;
  background:#10142b;border:1px solid #34366c;border-radius:12px;box-shadow:0 16px 35px rgba(0,0,0,.6);}
.aic-switch-menu[hidden]{display:none;}
.aic-switch-menu a{display:flex;align-items:center;gap:10px;padding:10px 11px;border-radius:8px;text-decoration:none;
  color:#dce1f6;font-size:14px;font-weight:600;line-height:1.2;white-space:nowrap;}
.aic-switch-menu a:hover,.aic-switch-menu a:focus-visible{background:#1a2148;color:#fff;outline:none;}
.aic-switch-menu a.aic-home{font-weight:800;color:#fff;}
.aic-switch-menu a.aic-home img{width:18px;height:18px;flex:none;}
.aic-switch-sep{height:1px;margin:5px 6px;background:#252b54;}
.aic-switch-dot{width:9px;height:9px;border-radius:50%;flex:none;margin:0 5px 0 4px;}
.aic-switch-frame{display:block;width:100%;height:132px;border:0;border-radius:8px;background:#10142b;}
.aic-switch-frame[hidden],.aic-switch-own[hidden]{display:none;}
`;

  function build(){
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    const wrap = document.createElement('div');
    wrap.className = 'aic-switch';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'aic-switch-btn';
    btn.setAttribute('aria-haspopup', 'true');
    btn.setAttribute('aria-expanded', 'false');
    const spark = document.createElement('img');
    spark.src = ICON;
    spark.alt = '';
    btn.appendChild(spark);

    const menu = document.createElement('div');
    menu.className = 'aic-switch-menu';
    menu.setAttribute('role', 'menu');
    menu.hidden = true;

    function link(url, tab, cls){
      const a = document.createElement('a');
      a.href = url;
      a.target = tab;
      a.setAttribute('role', 'menuitem');
      if(cls) a.className = cls;
      a.addEventListener('click', (e) => {
        if(e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        show(url, tab);
      });
      return a;
    }
    // The tab with this name, brought to the front as it is. If there is
    // none the browser hands back a new blank one, and that is sent to url.
    function show(url, tab){
      let win = window.open('', tab);
      if(!win) return;
      let fresh = false;
      try{ fresh = win.location.href === 'about:blank'; }catch(_){ /* the page already open there */ }
      if(fresh) win = window.open(url, tab) || win;
      win.focus();
    }
    // This file's own links, the fallback for the suite's frame.
    const own = document.createElement('div');
    own.className = 'aic-switch-own';
    menu.appendChild(own);
    const home = link(SUITE, SUITE_TAB, 'aic-home');
    const homeIcon = document.createElement('img');
    homeIcon.src = ICON;
    homeIcon.alt = '';
    const homeText = document.createElement('span');
    home.append(homeIcon, homeText);
    own.appendChild(home);
    const sep = document.createElement('div');
    sep.className = 'aic-switch-sep';
    own.appendChild(sep);
    APPS.filter(app => app.key !== HERE).forEach(app => {
      const a = link(app.url, appTab(app.key));
      const dot = document.createElement('span');
      dot.className = 'aic-switch-dot';
      dot.style.background = app.color;
      dot.style.boxShadow = '0 0 10px ' + app.color;
      a.append(dot, document.createTextNode(app.name));
      own.appendChild(a);
    });

    // The same list, served by the suite. It takes over once it says it is
    // ready; only the suite's page inside this frame is listened to.
    const frame = document.createElement('iframe');
    frame.className = 'aic-switch-frame';
    frame.hidden = true;
    frame.src = SUITE_MENU + '?app=' + encodeURIComponent(HERE) + '&lang=' + lang();
    menu.appendChild(frame);
    let framed = false;
    window.addEventListener('message', (e) => {
      if(e.origin !== SUITE_ORIGIN || e.source !== frame.contentWindow || !e.data) return;
      if(e.data.type === 'aiccs:menu-ready'){
        frame.hidden = false;
        own.hidden = true;
        framed = true;
      }else if(e.data.type === 'aiccs:menu-size'){
        // It measures itself once the menu is open; a closed menu has no size.
        const height = Number(e.data.height);
        if(height > 0 && height < 600) frame.style.height = height + 'px';
      }else if(e.data.type === 'aiccs:menu-close'){
        close();
        if(e.data.focus) btn.focus();
      }
    });

    // Worded in the app's current language each time it is shown, so a
    // language change in the app needs no wiring here.
    function word(){
      const text = TEXT[lang()];
      homeText.textContent = text.home;
      btn.title = text.label;
      btn.setAttribute('aria-label', text.label);
      frame.title = text.label;
      if(framed) frame.contentWindow.postMessage({ type: 'aiccs:menu-lang', lang: lang() }, SUITE_ORIGIN);
    }
    function close(){
      menu.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
    }
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if(!menu.hidden){ close(); return; }
      word();
      menu.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
    });
    menu.addEventListener('click', close);
    document.addEventListener('click', (e) => { if(!wrap.contains(e.target)) close(); });
    document.addEventListener('keydown', (e) => { if(e.key === 'Escape' && !menu.hidden){ close(); btn.focus(); } });

    word();
    wrap.append(btn, menu);
    const anchor = BEFORE ? document.querySelector(BEFORE) : null;
    if(anchor && anchor.parentNode) anchor.parentNode.insertBefore(wrap, anchor);
  }

  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
