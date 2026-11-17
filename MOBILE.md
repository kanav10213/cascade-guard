# Cascade Guard on Android and iPhone

The phone app is the dashboard for business owners. It has five tabs: **Home**, **Chats**, **Leads**, **Try it**
and **More**. Each lead has one-tap **Call**, **WhatsApp**, **SMS** or **Email**. The app can **alert you** when a
website visitor leaves their number. Pull down to refresh. It signs in to the same account as the website.

| | Android | iPhone and iPad |
|---|---|---|
| **Today, free** | Download the app from `/get-app` (built by GitHub), or in Chrome: menu, then **Install app** | Safari, **Share**, then **Add to Home Screen** |
| **Phone alerts** | In Chrome: on now. In the app: after the Firebase steps below | From the home-screen app on iOS 16.4 or newer: on now |
| **In the store** | Google Play: US$25 once. Everything to upload is in `mobile/store/` | App Store: US$99 a year, and a Mac |

## Phone alerts

When a visitor leaves their phone or email in the website chat, every manager with alerts on gets a notification.
They can also get one when a question can't be answered from the documents (at most one every 15 minutes). Both
can be switched on or off in **Settings**, then **Phone alerts**. Each person turns alerts on for each of their own
devices.

- **Browsers and the iPhone home-screen app** (Web Push) work as soon as this version is deployed, with nothing to
  set up. The server makes its own signing key once and keeps it in the database. Set `VAPID_PRIVATE_KEY` only if
  you want to supply your own.
- **The Android app** uses Firebase Cloud Messaging, which is free. Set it up once:
  1. Go to console.firebase.google.com, choose **Add project**, and turn Google Analytics off.
  2. **Add app**, then **Android**. The package name is `com.cascadeguard.app`. Download `google-services.json`.
  3. In GitHub, go to **Settings**, then **Secrets and variables**, then **Actions**, then **New repository
     secret**. Add `GOOGLE_SERVICES_JSON` and paste the whole file as its value. The next Android build can
     receive alerts.
  4. In Firebase, go to **Project settings**, then **Service accounts**, then **Generate new private key**. On
     Render, add the key as `FCM_SERVICE_ACCOUNT`, with the whole JSON file as the value. The server then sends
     alerts to the app.
  5. Install the new app build, open **Settings**, and tap **Turn on alerts on this device**, then **Send a test
     alert**.
- **The iPhone app from the App Store** gets no alerts yet. iPhone users can install the home-screen app instead,
  which does get alerts.

The alert shows the visitor's contact details on the lock screen. That is what makes it useful, so say so to
customers who share phones.

## What is in the repository

- `mobile/`: the Android and iOS app (Capacitor 8). It opens `https://cascade-guard.onrender.com/app` directly.
  This lets the dashboard use the phone's back button and notifications.
  - `www/offline.html`: shown when the server can't be reached. `www/index.html` is a fallback that is normally
    never shown.
  - `assets/`: the icon and splash screens. `native/android/res/`: the white tick shown in the status bar for
    alerts.
  - `store/`: the Play Store kit. It has `listing.md` (every text field), `data-safety.md` (the Data safety form),
    `feature-graphic.png`, `play-icon-512.png` and five screenshots (1080 × 1920).
- `cascade_guard/company/alerts.py`: sends the alerts. Web Push messages are encrypted for each browser (RFC
  8291, checked against the RFC's own example), the signing follows RFC 8292, and Android alerts go through
  FCM v1. It sends only to real push services and forgets devices that have gone.
- `cascade_guard/company/pwa.py`: the install manifest, the service worker (it shows alerts; it stores only the
  offline page and icons), and the `/get-app` page.
- Inside the app, prices and payment links are hidden, as the stores require. `/delete-account` and
  **Settings**, then **Delete my account**, cover the stores' account-deletion rule.

## How the apps are built: GitHub Actions (`.github/workflows/mobile.yml`)

Every push that changes `mobile/` builds the apps. You can also run it by hand: **Actions**, then **mobile**, then
**Run workflow**.

- **Android:** builds `cascade-guard.apk` and publishes it on the **"Android app (latest build)"** release.
  Without signing secrets it is a test build: Android may warn about an unknown developer, and you may need to
  remove the old version before installing a new one.
- **iOS:** builds for the iPhone simulator, which proves it compiles. Sometimes GitHub has no Mac free; run it
  again later.

**To use your own server address:** a company that runs its own server should build its own copy of the app. In
GitHub, go to **Settings**, then **Secrets and variables**, then **Actions**, then **Variables**, and add
`CASCADE_SERVER_URL` = `https://assistant.yourcompany.com`. Then change `appId` and `appName` in
`mobile/capacitor.config.json`.

## Google Play: step by step

1. **Create a signing key once**, on any computer with Java:
   `keytool -genkeypair -v -keystore cascade-guard.jks -alias cascade -keyalg RSA -keysize 2048 -validity 10000`
   Keep the file and both passwords safe.
2. **Add four GitHub secrets:**
   - `ANDROID_KEYSTORE_BASE64`: the file as text. On Linux or Mac: `base64 -w0 cascade-guard.jks`. On Windows
     PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("cascade-guard.jks"))`.
   - `ANDROID_KEYSTORE_PASSWORD`
   - `ANDROID_KEY_ALIAS` (`cascade`)
   - `ANDROID_KEY_PASSWORD`

   The next build is signed, and the release then also has `cascade-guard-play-store.aab`.
3. **Set up alerts** (the Firebase steps above), so the first store version already has them.
4. **Open a Google Play developer account** at play.google.com/console. It costs US$25 once and needs an
   identity check.
5. **Create the app** and fill everything in from `mobile/store/listing.md` and `mobile/store/data-safety.md`.
   Upload the icon, feature graphic and screenshots from `mobile/store/`.
6. **Make a reviewer account** with the sample documents and a few leads, and put its login under **App access**.
7. **Upload `cascade-guard-play-store.aab`** to **Testing**, then **Closed testing**. New personal developer
   accounts need **12 testers for 14 days** before they can publish. Ask your first customers and classmates.
   Then promote the build to **Production**.
8. After it is live:
   - Set `ANDROID_APP_URL` on the server to the Play Store link, so `/get-app` points there.
   - Set `ANDROID_CERT_SHA256` to the fingerprint shown under **App integrity**, so links to your site open in
     the app.

## App Store: step by step

1. Join the **Apple Developer Program** (US$99 a year).
2. You need **Xcode**, which runs only on a Mac. A friend's Mac or a rented cloud Mac (for example MacinCloud) works.
3. On the Mac, run:
   `cd mobile && npm ci && npx cap add ios && npm run assets -- --ios && npx cap open ios`
4. In Xcode, select the **App** target, then **Signing & Capabilities**, and choose your team. The bundle id is
   `com.cascadeguard.app`.
5. Choose **Product**, then **Archive**, then **Distribute App**, then **App Store Connect**. Test it through
   **TestFlight**, then submit it for review.
6. **What App Review checks:**
   - Apple can reject apps that are only a website in a frame (guideline 4.2). In the review notes, point to what
     this app adds: phone-sized tabs, one-tap call and WhatsApp for leads, pull to refresh, and the offline
     screen. Native iPhone alerts would strengthen this; they are not built yet.
   - Give the reviewers a demo account.
   - Payment is hidden inside the app, as required.

## Limits

- The app needs the internet. Without it, it shows the "can't reach the server" screen.
- The App Store version has no alerts yet; the iPhone home-screen app does.
- The free Render server sleeps, so the first open after a quiet period can take up to a minute.
