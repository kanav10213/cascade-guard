"""Installing the dashboard as an app, and the pieces the Android and iPhone apps rely on."""
import json
from pathlib import Path

import pytest

MOBILE = Path(__file__).resolve().parent.parent / "mobile"


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from cascade_guard.api import app
    monkeypatch.setenv("API_KEY", "owner-key")
    return TestClient(app)


def test_manifest_is_installable(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    m = r.json()
    assert m["display"] == "standalone" and m["start_url"].startswith("/app") and m["scope"] == "/"
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes and any(i.get("purpose") == "maskable" for i in m["icons"])
    for i in m["icons"]:
        got = client.get(i["src"])
        assert got.status_code == 200 and got.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_service_worker_never_caches_private_data(client):
    r = client.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert r.headers.get("service-worker-allowed") == "/"
    body = r.text
    assert "r.method !== 'GET'" in body and "mode === 'navigate'" in body
    # only the offline page and icons are stored; API answers are never put in the cache
    assert "cache.put" not in body and "c.put" not in body
    assert client.get("/offline").status_code == 200


def test_pages_link_the_manifest_and_script(client):
    for path in ("/app", "/login", "/"):
        html = client.get(path).text
        if path == "/" and "manifest" not in html:   # "/" is the old dashboard unless PUBLIC_SIGNUP is on
            continue
        assert '<link rel="manifest" href="/manifest.webmanifest">' in html
        assert '<script src="/pwa.js" defer></script>' in html
    js = client.get("/pwa.js").text
    assert "CascadeGuardApp" in js and "in-app" in js


def test_icons_cannot_escape_their_folder(client):
    for bad in ("..%2Fapp.html", "icon-192.svg", "nothing.png"):
        assert client.get("/icons/" + bad).status_code == 404
    assert client.get("/favicon.ico").status_code == 200


def test_android_link_file_only_when_configured(client, monkeypatch):
    assert client.get("/.well-known/assetlinks.json").status_code == 404
    monkeypatch.setenv("ANDROID_CERT_SHA256", "AA:BB")
    data = client.get("/.well-known/assetlinks.json").json()
    assert data[0]["target"]["package_name"] == "com.cascadeguard.app"
    assert data[0]["target"]["sha256_cert_fingerprints"] == ["AA:BB"]


def test_app_hides_payment_inside_the_phone_app():
    html = (Path(__file__).resolve().parent.parent / "cascade_guard" / "static" / "app.html").read_text("utf-8")
    assert "classList.contains('in-app')" in html and "Plans are changed on the website" in html


@pytest.mark.skipif(not MOBILE.is_dir(), reason="the mobile/ folder is not in this copy")
def test_phone_app_config_matches_the_server():
    cfg = json.loads((MOBILE / "capacitor.config.json").read_text("utf-8"))
    assert cfg["appId"] == "com.cascadeguard.app" and cfg["webDir"] == "www"
    for platform in ("android", "ios"):
        assert cfg[platform]["appendUserAgent"].startswith("CascadeGuardApp/")
    assert cfg["server"]["errorPath"] == "offline.html" and "url" not in cfg["server"]
    start = (MOBILE / "www" / "index.html").read_text("utf-8")
    assert "var DEFAULT_SERVER = 'https://cascade-guard.onrender.com';" in start
    assert (MOBILE / "www" / "offline.html").is_file()
    for name in ("icon-only.png", "icon-foreground.png", "icon-background.png", "splash.png", "splash-dark.png"):
        assert (MOBILE / "assets" / name).read_bytes()[:4] == b"\x89PNG"


def test_get_app_page(client, monkeypatch):
    html = client.get("/get-app").text
    assert "Add to Home Screen" in html and "releases/download/android-latest/cascade-guard.apk" in html
    monkeypatch.setenv("ANDROID_APP_URL", "https://play.google.com/store/apps/details?id=com.cascadeguard.app")
    assert "play.google.com" in client.get("/get-app").text
    monkeypatch.setenv("ANDROID_APP_URL", "javascript:alert(1)")
    assert "javascript:" not in client.get("/get-app").text
