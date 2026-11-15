/* Cascade Guard website chat widget.
   <script src="https://YOUR-SERVER/widget.js" data-key="pk_..." async></script>
   Optional: data-title="Ask us" data-color="#23527c" data-greeting="Hi! Ask me about ..." data-contact="Call 98xxxxxx" */
(function () {
  'use strict';
  var me = document.currentScript || document.querySelector('script[data-key][src*="widget.js"]');
  if (!me || window.__cgWidget) return;
  window.__cgWidget = true;
  var key = me.getAttribute('data-key') || '';
  var base = new URL(me.src, location.href).origin;
  var title = me.getAttribute('data-title') || 'Ask us';
  var color = /^#[0-9a-fA-F]{3,8}$/.test(me.getAttribute('data-color') || '') ? me.getAttribute('data-color') : '#23527c';
  var greeting = me.getAttribute('data-greeting') || 'Hi! Ask a question and I will answer from our own information.';
  var contact = me.getAttribute('data-contact') || '';
  var history = [], busy = false;

  var host = document.createElement('div');
  host.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:2147483000';
  var root = host.attachShadow({ mode: 'open' });
  var style = document.createElement('style');
  style.textContent =
    ':host{all:initial}*{box-sizing:border-box;font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif}' +
    '.b{width:56px;height:56px;border-radius:50%;border:0;background:' + color + ';color:#fff;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.25);display:flex;align-items:center;justify-content:center}' +
    '.b:focus-visible,.s:focus-visible,textarea:focus-visible,.x:focus-visible{outline:2px solid #fff;outline-offset:2px;box-shadow:0 0 0 4px ' + color + '}' +
    '.p{position:absolute;right:0;bottom:68px;width:min(370px,calc(100vw - 36px));height:min(520px,calc(100vh - 110px));background:#fff;color:#1d2528;border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.28);display:none;flex-direction:column;overflow:hidden}' +
    '.p.o{display:flex}.h{background:' + color + ';color:#fff;padding:12px 14px;font-weight:650;font-size:15px;display:flex;justify-content:space-between;align-items:center}' +
    '.x{background:none;border:0;color:#fff;font-size:20px;cursor:pointer;line-height:1;padding:2px 6px;border-radius:6px}' +
    '.l{flex:1;overflow:auto;padding:12px;display:flex;flex-direction:column;gap:10px;background:#f4f6f5}' +
    '.m{max-width:88%;padding:8px 11px;border-radius:12px;font-size:14px;line-height:1.45;white-space:pre-wrap;word-break:break-word}' +
    '.u{align-self:flex-end;background:' + color + ';color:#fff;border-bottom-right-radius:4px}' +
    '.a{align-self:flex-start;background:#fff;border:1px solid #dde4e4;border-bottom-left-radius:4px}' +
    '.t{display:block;margin-top:6px;font-size:11.5px;color:#5d6b6f}.w{color:#8a5a00}' +
    'form{display:flex;gap:6px;padding:10px;border-top:1px solid #e3e9e9;background:#fff}' +
    'textarea{flex:1;resize:none;border:1px solid #cfd8d8;border-radius:10px;padding:8px 10px;font-size:14px;height:40px;color:#1d2528;background:#fff}' +
    '.s{border:0;background:' + color + ';color:#fff;border-radius:10px;padding:0 14px;font-weight:600;cursor:pointer;font-size:14px}.s:disabled{opacity:.6}' +
    '.f{font-size:10.5px;color:#7a8789;text-align:center;padding:0 0 7px;background:#fff}.f a{color:inherit}';
  root.appendChild(style);

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
  var panel = el('div', 'p'); panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', title);
  var head = el('div', 'h'); head.appendChild(el('span', '', title));
  var close = el('button', 'x', '×'); close.type = 'button'; close.setAttribute('aria-label', 'Close chat'); head.appendChild(close);
  var log = el('div', 'l'); log.setAttribute('aria-live', 'polite');
  var form = el('form'); var input = el('textarea'); input.placeholder = 'Type your question…'; input.maxLength = 1000; input.setAttribute('aria-label', 'Your question');
  var send = el('button', 's', 'Send'); send.type = 'submit'; form.appendChild(input); form.appendChild(send);
  var foot = el('div', 'f'); var link = el('a', '', 'Answers checked by Cascade Guard'); link.href = base + '/'; link.target = '_blank'; link.rel = 'noopener'; foot.appendChild(link);
  panel.appendChild(head); panel.appendChild(log); panel.appendChild(form); panel.appendChild(foot);
  var bubble = el('button', 'b'); bubble.type = 'button'; bubble.setAttribute('aria-label', 'Open chat: ' + title);
  bubble.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>';
  root.appendChild(panel); root.appendChild(bubble);

  function add(cls, text, note, warn) {
    var m = el('div', 'm ' + cls, text);
    if (note) { var t = el('span', 't' + (warn ? ' w' : ''), note); m.appendChild(t); }
    log.appendChild(m); log.scrollTop = log.scrollHeight; return m;
  }
  function toggle(open) {
    panel.classList.toggle('o', open);
    if (open) { if (!log.childNodes.length) add('a', greeting); input.focus(); }
  }
  bubble.addEventListener('click', function () { toggle(!panel.classList.contains('o')); });
  close.addEventListener('click', function () { toggle(false); bubble.focus(); });
  root.addEventListener('keydown', function (e) { if (e.key === 'Escape') { toggle(false); bubble.focus(); } });
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit', { cancelable: true })); } });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var q = input.value.trim(); if (!q || busy) return;
    busy = true; send.disabled = true; input.value = '';
    add('u', q);
    var wait = add('a', '…');
    fetch(base + '/widget/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: key, question: q, history: history.slice(-8) }) })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        wait.remove();
        if (!res.ok) { add('a', (res.j && typeof res.j.detail === 'string') ? res.j.detail : 'Sorry, something went wrong. Please try again.'); return; }
        var j = res.j, text = String(j.answer || '').replace(/\s*\[\d+(?:\s*[,–-]\s*\d+)*\]/g, '').replace(/^>\s?/gm, '');
        var note = '', warn = false;
        if (j.verdict === 'not_found') { text = "Sorry, I couldn't find that in our information." + (contact ? ' ' + contact : ''); }
        else {
          var docs = []; (j.sources || []).slice(0, 2).forEach(function (s) { if (docs.indexOf(s.doc) < 0) docs.push(s.doc); });
          if (j.verdict === 'unconfirmed') { note = 'Please confirm this with us before relying on it.'; warn = true; }
          else note = '✓ Checked against: ' + docs.join(', ');
        }
        add('a', text, note, warn);
        history.push({ role: 'user', content: q }, { role: 'assistant', content: text });
      })
      .catch(function () { wait.remove(); add('a', 'Sorry, the chat could not be reached. Please try again.'); })
      .then(function () { busy = false; send.disabled = false; input.focus(); });
  });
  (document.body || document.documentElement).appendChild(host);
})();
