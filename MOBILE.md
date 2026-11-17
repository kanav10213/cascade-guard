# Cascade Guard on Android and iPhone

The phone app is the dashboard: conversations, new leads, documents, test chat, the website widget, team and
settings. It signs in to the same account as the website. Everything is checked the same way.

There are two ways to get it on a phone.

| | Android | iPhone and iPad |
|---|---|---|
| **Today, free** | Download the app from `/get-app` (the APK built by GitHub), or in Chrome: menu, then **Install app** | Safari, **Share**, then **Add to Home Screen** |
| **In the store** | Google Play: a one-time US$25 developer account | App Store: Apple Developer Program, US$99 a year, and a Mac or a cloud Mac |

`/get-app` on your server shows these steps, with the visitor's own phone first. The website home page and the
dashboard link to it.

## What is in the repository

- `mobile/`: the Android and iOS app (Capacitor 8). It opens a small start screen, then the dashboard from the
  server.
  - `www/index.html`: the start screen. **Continue** opens the default server, and **My company runs its own server**
    takes any other address, for a company that runs Cascade Guard on its own server. The address is remembered.
  - `www/offline.html`: shown when the server cannot be reached.
  - `assets/`: the icon and splash screens. Re-draw them with your own logo and keep the file names.
  - `capacitor.config.json`: app id `com.cascadeguard.app`, name, and the user agent `CascadeGuardApp/1.0` that the
    server recognises.
- `cascade_guard/company/pwa.py`: the server side. It provides the web manifest, a service worker (it stores only
  the offline page and icons, never conversations or documents), the `/get-app` page, and the Android link file.
- Inside the app, the server hides prices and payment links, and shows "Plans are changed on the website" instead.
  Both stores refuse apps that take payment outside their own billing.

## How the apps are built: GitHub Actions (`.github/workflows/mobile.yml`)

Each push that changes `mobile/` builds both apps. You can also run it by hand: **Actions**, then **mobile**, then **Run workflow**.

- **Android:** builds `cascade-guard.apk` and publishes it on the **"Android app (latest build)"** release:
  `https://github.com/kanav10213/cascade-guard/releases/download/android-latest/cascade-guard.apk`
  Without signing secrets this is a test build. It installs and works, but Android may warn that it comes from an
  unknown developer, and a new build may need the old one removed first.
- **iOS:** builds the app for the iPhone simulator, which proves it compiles. The zip is under the run's
  **Artifacts** and opens in Xcode's Simulator on a Mac.

To make the app open a different server by default (for example your own domain), go to **Settings**, then
**Secrets and variables**, then **Actions**, then **Variables**. Add `CASCADE_SERVER_URL` = `https://your-domain`.

## Google Play: step by step

1. **Create a signing key once**, on any computer with Java:
   `keytool -genkeypair -v -keystore cascade-guard.jks -alias cascade -keyalg RSA -keysize 2048 -validity 10000`
   Keep the file and both passwords safe. If you lose them, you can't update the app without asking Google to
   reset the key.
2. **Add four secrets in GitHub**: **Settings**, then **Secrets and variables**, then **Actions**, then **New repository secret**.
   - `ANDROID_KEYSTORE_BASE64`: the file as text. On Linux or Mac: `base64 -w0 cascade-guard.jks`. On Windows
     PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("cascade-guard.jks"))`.
   - `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS` (`cascade`), `ANDROID_KEY_PASSWORD`.
   The next build is signed. The release then also has `cascade-guard-play-store.aab`.
3. **Open a Google Play developer account** at play.google.com/console (US$25 once, identity check).
4. **Create the app.** Upload `cascade-guard-play-store.aab` to **Internal testing** first and install it from
   the test link. Then fill in the store listing, the privacy policy link (`https://your-domain/privacy`), the data
   safety form (email, the documents and chats a business uploads), and the content rating.
5. New personal developer accounts must run a **closed test with at least 12 testers for 14 days** before they can
   publish. Ask your first customers and classmates.
6. Optional: so that links to your site open in the app, set `ANDROID_CERT_SHA256` on the server to the
   SHA-256 fingerprint shown in Play Console under **App integrity**. The server then serves
   `/.well-known/assetlinks.json`.
7. Once it is live, set `ANDROID_APP_URL` on the server to the Play Store link, and `/get-app` will point there.

## App Store: step by step

1. Join the **Apple Developer Program** (US$99 a year).
2. You need **Xcode**, which runs only on a Mac. A friend's Mac or a rented cloud Mac (for example MacinCloud) works.
3. On the Mac, run:
   `cd mobile && npm ci && npx cap add ios && npm run assets -- --ios && npx cap open ios`
4. In Xcode, select the **App** target, then **Signing & Capabilities**. Choose your team. The bundle id is
   `com.cascadeguard.app`, or change it to one you own.
5. Choose **Product**, then **Archive**, then **Distribute App**, then **App Store Connect**. Test it through
   **TestFlight**, then submit it for review.
6. **What App Review checks:**
   - Apple can reject apps that are only a website in a frame (guideline 4.2). This app has its own start screen,
     works with a company's own server, has an offline screen, and keeps people signed in. Say this in the review
     notes.
   - Give the reviewers a demo account with documents in it.
   - Payment is hidden inside the app, as required.

## Limits

- The app needs the internet, or the company's own network. There is no offline mode beyond the "can't
  connect" screen.
- There are no push notifications for new leads yet. The leads badge shows when you open the app.
- The free Render server sleeps, so the first open after a quiet period can take up to a minute. The start
  screen says so.
