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

   Every link opens in a new tab, like Launch on the suite home, so work
   in progress here is never navigated away from. With single sign-on on,
   the other app opens already signed in. */
(function(){
  const tag = document.currentScript;
  if(!tag) return;
  const HERE = tag.dataset.app || '';
  const BEFORE = tag.dataset.before || '';
  const ICON = tag.dataset.icon || 'assets/aicurator-spark.png';
  const LANG_KEY = tag.dataset.langKey || '';

  const SUITE = 'https://aicsuite.ai-curator.ai/index.html';
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

    function link(url, cls){
      const a = document.createElement('a');
      a.href = url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.setAttribute('role', 'menuitem');
      if(cls) a.className = cls;
      return a;
    }
    const home = link(SUITE, 'aic-home');
    const homeIcon = document.createElement('img');
    homeIcon.src = ICON;
    homeIcon.alt = '';
    const homeText = document.createElement('span');
    home.append(homeIcon, homeText);
    menu.appendChild(home);
    const sep = document.createElement('div');
    sep.className = 'aic-switch-sep';
    menu.appendChild(sep);
    APPS.filter(app => app.key !== HERE).forEach(app => {
      const a = link(app.url);
      const dot = document.createElement('span');
      dot.className = 'aic-switch-dot';
      dot.style.background = app.color;
      dot.style.boxShadow = '0 0 10px ' + app.color;
      a.append(dot, document.createTextNode(app.name));
      menu.appendChild(a);
    });

    // Worded in the app's current language each time it is shown, so a
    // language change in the app needs no wiring here.
    function word(){
      const text = TEXT[lang()];
      homeText.textContent = text.home;
      btn.title = text.label;
      btn.setAttribute('aria-label', text.label);
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
