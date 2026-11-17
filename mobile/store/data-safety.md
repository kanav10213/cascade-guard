# Google Play "Data safety" form: answers for this app

These answers describe what the code in this repository does. If you add analytics, ads or crash reporting
later, update the form.

## Data collection and security

| Question | Answer |
|---|---|
| Does your app collect or share any of the required user data types? | **Yes** |
| Is all of the user data collected by your app encrypted in transit? | **Yes** (HTTPS only) |
| Do you provide a way for users to request that their data is deleted? | **Yes**: [your website]/delete-account |

## Data types to tick

For each type, the answers are: **Collected: yes. Shared: no. Processed ephemerally: no. Required.**
Purposes are **App functionality** and **Account management** only.

| Category | Type | Why |
|---|---|---|
| Personal info | **Name** | Account holder's name (optional at sign-up) |
| Personal info | **Email address** | Sign-in and account |
| Personal info | **Phone number** | Visitors' phone numbers left as leads, shown to the business |
| Messages | **Other in-app messages** | Questions visitors ask and the answers, shown to the business |
| Files and docs | **Files and docs** | Documents the business uploads for the assistant to answer from |
| App info and performance | **Other app performance data** | Server request logs kept for security (time, address, page, result) |
| Device or other IDs | **Device or other IDs** | Push notification token, only if alerts are turned on |

**Do not tick:** location, contacts, photos and videos, audio, calendar, health, financial info, web browsing,
app interactions, or advertising ID. The app does not collect any of these.

**"Shared"** means given to another company for its own use. Hosting, database, AI and notification providers
that process data only on your behalf count as **service providers**, which Google says is not sharing. So
answer **No** for every type.
