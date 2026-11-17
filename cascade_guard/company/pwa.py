"""Install the dashboard as an app: web manifest, service worker, offline page, icons.

On Android (Chrome) and iPhone (Safari: Share > Add to Home Screen) the dashboard installs like an app. The same
pages run inside the Android and iOS apps in mobile/, which add "CascadeGuardApp" to their user agent; /pwa.js then
marks the page (html.in-app) so the app hides prices and payment links, as the app stores require.
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

from cascade_guard.company.app import STATIC

router = APIRouter(include_in_schema=False)
ICONS = STATIC / "icons"
VERSION = "1"   # bump to make installed apps fetch the offline page and icons again
THEME = "#23527c"
PAPER = "#f2f5f1"


def _name() -> str:
    return os.getenv("COMPANY_NAME") or os.getenv("BUSINESS_NAME") or "Cascade Guard"


@router.get("/manifest.webmanifest")
def manifest():
    name = _name()
    icon = [{"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/icons/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]
    body = {
        "id": "/app",
        "name": name,
        "short_name": name if len(name) <= 14 else "Assistant",
        "description": "Your website chat, conversations and leads, with answers checked against your documents.",
        "start_url": "/app?source=app",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": PAPER,
        "theme_color": THEME,
        "icons": icon,
        "categories": ["business", "productivity"],
        "shortcuts": [
            {"name": "Conversations", "url": "/app#conversations", "icons": icon[:1]},
            {"name": "Leads", "url": "/app#leads", "icons": icon[:1]},
            {"name": "Test chat", "url": "/app#try", "icons": icon[:1]},
        ],
    }
    return Response(json.dumps(body), media_type="application/manifest+json",
                    headers={"Cache-Control": "public, max-age=3600"})


# Network first for pages; the offline page only when there is no connection. Nothing private is cached:
# API answers, documents and conversations always come from the server.
SW = """/* Cascade Guard service worker */
const CACHE = 'cg-shell-%(v)s';
const SHELL = ['/offline', '/icons/icon-192.png', '/icons/apple-touch-icon.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const r = e.request;
  if(r.method !== 'GET') return;
  const url = new URL(r.url);
  if(url.origin !== self.location.origin) return;
  if(r.mode === 'navigate'){
    e.respondWith(fetch(r).catch(() => caches.match('/offline')));
  } else if(url.pathname.startsWith('/icons/')){
    e.respondWith(caches.match(r).then(hit => hit || fetch(r)));
  }
});
"""


@router.get("/sw.js")
def service_worker():
    return Response(SW % {"v": VERSION}, media_type="text/javascript; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


# Loaded by the pages (<script src="/pwa.js" defer>): registers the service worker and marks pages opened in the app.
PWA_JS = """(function(){
  var inApp = /CascadeGuardApp/.test(navigator.userAgent);
  var standalone = (window.matchMedia && matchMedia('(display-mode: standalone)').matches) || navigator.standalone === true;
  var root = document.documentElement;
  if(inApp) root.classList.add('in-app');
  if(inApp || standalone) root.classList.add('installed');
  var s = document.createElement('style');
  s.textContent = '.in-app .web-only,.in-app #pricing,.installed .get-app{display:none!important}';
  document.head.appendChild(s);
  if(inApp && location.pathname === '/login'){
    // the phone app's own start screen, where a company can enter its own server address
    var home = /Android/.test(navigator.userAgent) ? 'https://localhost/' : 'capacitor://localhost/';
    document.addEventListener('DOMContentLoaded', function(){
      var a = document.createElement('a');
      a.href = home + 'index.html?change=1';
      a.textContent = 'Use a different server';
      a.style.cssText = 'display:block;text-align:center;margin:18px auto 28px;font-size:14px';
      document.body.appendChild(a);
    });
  }
  if('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost')){
    try{ navigator.serviceWorker.register('/sw.js', {scope: '/'}).catch(function(){}); }catch(e){}
  }
})();
"""


@router.get("/pwa.js")
def pwa_js():
    return Response(PWA_JS, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})


OFFLINE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="%(theme)s"><title>No connection</title>
<style>
:root{--paper:%(paper)s;--ink:#1d2528;--muted:#56646a;--blue:%(theme)s}
@media (prefers-color-scheme:dark){:root{--paper:#161c1f;--ink:#e8eeee;--muted:#9aa8ad}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--paper);color:var(--ink);
font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:24px;box-sizing:border-box;text-align:center}
main{max-width:340px}h1{font-size:21px;margin:18px 0 6px}p{color:var(--muted);margin:0 0 22px}
button{font:inherit;font-weight:600;background:var(--blue);color:#fff;border:0;border-radius:10px;padding:12px 22px;min-height:44px}
</style></head><body><main>
<img src="/icons/icon-192.png" width="64" height="64" alt="">
<h1>You're offline</h1>
<p>Connect to the internet to see your conversations and leads. Nothing is lost.</p>
<button type="button" onclick="location.reload()">Try again</button>
</main></body></html>"""


@router.get("/offline", response_class=HTMLResponse)
def offline():
    return HTMLResponse(OFFLINE % {"theme": THEME, "paper": PAPER})


@router.get("/icons/{name}")
def icon(name: str):
    path = ICONS / name
    if "/" in name or "\\" in name or not name.endswith(".png") or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


@router.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "favicon.ico", media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=604800"})


ANDROID_APK = "https://github.com/kanav10213/cascade-guard/releases/download/android-latest/cascade-guard.apk"

GET_APP = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="%(theme)s">
<link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<title>Get the app</title>
<style>
:root{--paper:%(paper)s;--sheet:#fff;--ink:#1d2528;--muted:#56646a;--rule:#d3dddf;--blue:%(theme)s;--onblue:#fff}
@media (prefers-color-scheme:dark){:root{--paper:#161c1f;--sheet:#1b2225;--ink:#e5ebe9;--muted:#9aa8ab;--rule:#2f3a3e;
--blue:#8db8e8;--onblue:#0f1720}}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:32px 16px 48px}h1{font-size:26px;margin:14px 0 4px}.lead{color:var(--muted);margin:0 0 24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}
.card{background:var(--sheet);border:1px solid var(--rule);border-radius:14px;padding:18px}
.card h2{font-size:18px;margin:0 0 8px}ol{margin:0;padding-left:20px}li{margin:4px 0}.small{font-size:13.5px;color:var(--muted)}
.btn{display:inline-block;background:var(--blue);color:var(--onblue);text-decoration:none;font-weight:600;
border-radius:10px;padding:11px 18px;margin:6px 0 10px}
a{color:var(--blue)}.mine{outline:2px solid var(--blue)}
</style></head><body><main>
<a href="/"><img src="/icons/icon-192.png" width="56" height="56" alt="%(name)s home"></a>
<h1>%(name)s on your phone</h1>
<p class="lead">See conversations and new leads, add documents and test answers from anywhere.</p>
<div class="grid">
<div class="card" id="android"><h2>Android</h2>
<a class="btn" href="%(apk)s">Download the app</a>
<ol><li>Open the downloaded file.</li><li>If Android asks, allow installing from this source.</li><li>Open %(name)s and sign in.</li></ol>
<p class="small">Or, in Chrome: menu (three dots) then <b>Add to Home screen</b> or <b>Install app</b>.</p></div>
<div class="card" id="iphone"><h2>iPhone and iPad</h2>
<ol><li>Open this page in <b>Safari</b>.</li><li>Tap the <b>Share</b> button.</li><li>Tap <b>Add to Home Screen</b>, then <b>Add</b>.</li>
<li>Open %(name)s from your home screen and sign in.</li></ol>
<p class="small">It opens full screen like any other app.</p></div>
</div>
<p class="small" style="margin-top:22px"><a href="/login">Sign in on the web instead</a></p>
</main>
<script>
var ua = navigator.userAgent;
var ios = /iPhone|iPad|iPod/i.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
var el = /Android/i.test(ua) ? 'android' : ios ? 'iphone' : null;
if(el){ var c = document.getElementById(el); c.classList.add('mine'); c.parentNode.insertBefore(c, c.parentNode.firstChild); }
</script>
</body></html>"""


@router.get("/get-app", response_class=HTMLResponse)
def get_app():
    """How to put the dashboard on a phone: the Android app, or Add to Home Screen on an iPhone."""
    from html import escape
    apk = os.getenv("ANDROID_APP_URL", ANDROID_APK)
    if not apk.startswith("https://"):
        apk = ANDROID_APK
    return HTMLResponse(GET_APP % {"theme": THEME, "paper": PAPER, "name": escape(_name()), "apk": escape(apk, quote=True)})


@router.get("/.well-known/assetlinks.json")
def assetlinks():
    """Lets the Android app open this site's links (set ANDROID_CERT_SHA256 to the app's signing certificate)."""
    certs = [c.strip() for c in os.getenv("ANDROID_CERT_SHA256", "").split(",") if c.strip()]
    if not certs:
        raise HTTPException(404)
    pkg = os.getenv("ANDROID_PACKAGE", "com.cascadeguard.app")
    return [{"relation": ["delegate_permission/common.handle_all_urls"],
             "target": {"namespace": "android_app", "package_name": pkg, "sha256_cert_fingerprints": certs}}]
