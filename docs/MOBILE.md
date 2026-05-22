# MLSS Mobile (iPhone PWA + Web Push)

The MLSS hub ships as an installable Progressive Web App (PWA) — add it
to your iPhone home screen and you get a fullscreen, app-like UI plus
**lockscreen push notifications** for air-quality alerts, grow-unit
errors, system-health failures, and backup-pipeline issues. Per-user
severity floors keep the noise tunable, and an in-app inbox at
`/notifications` is the durable record (iOS drops lockscreen alerts
after a few days).

[Back to main README](../readme.md)

---

## Prerequisites

- iPhone running **iOS 16.4 or later** — Web Push from a home-screen
  PWA only landed in 16.4; earlier iOS versions can install the PWA but
  notifications silently fail.
- The MLSS hub is reachable from the iPhone — typically over a
  **WireGuard VPN** to the home LAN, or directly on Wi-Fi when at
  home.
- The hub is running on **HTTPS** (Web Push requires it). The default
  install uses a self-signed cert; see
  [PRODUCTION.md](PRODUCTION.md) for cert setup options.
- You're signed in to the hub via GitHub OAuth (Settings →
  Notifications is per-user, so the hub needs to know who you are).

---

## Step 1: Generate the local CA (one-time, hub side)

iOS won't let a PWA install from a site whose certificate it doesn't
trust. The hub ships a script that creates a small root CA + a leaf
cert signed by it; you'll trust the CA on each iPhone (Step 2) and the
leaf cert covers every browser tab automatically.

If `certs/ca.crt` doesn't exist on the hub, generate it once:

```bash
cd /path/to/mars-air-quility
bash scripts/generate_local_ca.sh
sudo systemctl restart mlss-monitor
```

The script issues a 10-year root CA + 5-year leaf cert and is
idempotent — re-running reuses the existing CA. The restart picks up
the new leaf cert.

---

## Step 2: Trust the MLSS CA on your iPhone

The hub exposes the CA bundle as an Apple **`.mobileconfig`** profile
on the admin Settings page. Installing the profile + flipping the
trust toggle is a one-time per-device step.

1. On the iPhone: open **Safari** and browse to
   `https://<hub-hostname-or-IP>/admin`.
2. Scroll to the **"Mobile install (iOS)"** card. Tap
   **"Download iOS Profile"**.
3. The iOS Settings app shows a **"Profile Downloaded"** banner near
   the top. Tap it.
4. Tap **"Install"** (top-right). Enter your passcode if prompted.
   Tap **"Install"** again to confirm.
5. Go to **Settings → General → About → Certificate Trust Settings**.
6. Toggle ON **"MLSS Root CA"**. Tap **Continue** when iOS warns about
   trusting a non-system CA.

> Chrome on iOS **cannot** install configuration profiles — use Safari
> for these steps.

---

## Step 3: Install MLSS to home screen

Back in Safari, open `https://<hub>/`. The padlock icon should now be
plain green (no warning triangle). If it still warns, Step 2 didn't
take — see [Troubleshooting](#troubleshooting).

1. Tap the **Share** icon (square with an up-arrow, middle of the
   bottom toolbar).
2. Scroll the share sheet down to **Add to Home Screen**. Tap it.
3. Tap **Add** (top-right).

Tap the new **MLSS** icon on your home screen. The app opens
**standalone** (no Safari address bar, no chrome). This is required —
push notifications only work from the standalone PWA, not from a
regular Safari tab.

---

## Step 4: Enable push notifications

1. Open MLSS from the home screen.
2. Tap the **Settings** tab.
3. Scroll to the **Notifications** card.
4. Tap **"Enable push on this device"**.
5. When iOS prompts **"MLSS Would Like to Send You Notifications"**,
   tap **Allow**.
6. (Optional) Give the device a label — e.g. *"Ryan's iPhone"* —
   so the device list on the same card is easy to read when you have
   multiple devices subscribed.

If you accidentally tap **Don't Allow**, re-enable via
**iOS Settings → MLSS → Notifications → Allow Notifications**, then
re-tap "Enable push on this device".

---

## Step 5: Tune your preferences

Same **Notifications** card — set a **severity floor** per category:

| Floor | Meaning |
|---|---|
| `off` | Never notify for this category. |
| `info` | Every event (chatty — useful for debug, noisy day-to-day). |
| `warning` | Warning + critical events (the default — quiet enough to ignore most days). |
| `critical` | Critical only (silent for warnings — best for sleep / focus modes). |

The four categories are:

- **Air quality** — TVOC spikes, eCO2 danger, PM excursions, ozone /
  combustion events the inference engine fires.
- **Grow units** — sensor degraded, safety cap hit, buffer eviction,
  pump anomalies.
- **System health** — a sensor going offline, smart plug
  unreachable, hub disk filling up.
- **Backup pipeline** — backup failed, disabled by an operator, or
  parked in exponential backoff after repeated failures.

Tap **"Save preferences"**.

---

## Step 6: Verify

The fastest way to confirm everything's wired up:

1. Open `https://<hub>/admin` → **Insights Engine** tab → toggle
   **Live mode** on.
2. Wait for the next inference cycle (~60 s).
3. Within ~30 s of an inference firing at or above your severity
   floor, you should see a **notification on the iPhone lockscreen**.
4. Tap the notification — it should open the relevant page
   (`/incidents` for an air-quality alert, `/grow/<id>` for a
   grow-unit error, etc.) **inside the standalone PWA**, not Safari.
5. Visit **/notifications** in the PWA to see the event in the in-app
   inbox.

If nothing arrives in ~5 minutes, jump to
[Troubleshooting](#troubleshooting).

---

## Troubleshooting

**"Add to Home Screen" greyed out / padlock still warning after profile install.**
The CA isn't trusted. Settings → General → About →
**Certificate Trust Settings** is easy to miss — re-do Step 2 and
make sure the "MLSS Root CA" toggle is *on*. If the toggle is already
on, flip it off and back on, then fully close Safari (swipe up from
the bottom and flick the Safari card up) and re-open the hub URL.

**No notification after enabling push.**
Check **iOS Settings → MLSS → Notifications → Allow Notifications**
is on. If it isn't, you probably denied the permission prompt — flip
it on and re-tap "Enable push on this device" so the hub gets a fresh
subscription.

**Notifications stopped after a few days.**
Apple's push service occasionally invalidates a subscription endpoint.
Re-tap **"Enable push on this device"** — the new endpoint silently
supersedes the stale one (the old row is removed on the next push
attempt when Apple replies `410 Gone`).

**iOS profile install fails.**
You're probably in Chrome. iOS only lets **Safari** install
configuration profiles — Chrome silently refuses.

**Push works but the `/notifications` inbox is empty.**
The dispatcher writes a history row when it dispatches a push — it
**does not backfill** prior events. The inbox starts populating from
the moment you first enable push on any device. If you want a
backfill, replay older events into the event bus (see the dev guide
for ad-hoc replays).

**Multiple notifications collapse into a single "3x ..." entry.**
Working as designed. The dispatcher coalesces events per
(user, category, severity) within a **60-second window** to avoid
buzzing your phone three times for the same TVOC spike. The inbox
row's `event_count` increments; the lockscreen alert title becomes
`"3× TVOC spike"` instead of three separate alerts.

---

## Privacy & security

The VAPID keypair (`vapid_public_key` + `vapid_private_key`) lives on
**your** hub — `app_settings` table on disk, generated on first push
attempt. The hub uses [`pywebpush`](https://github.com/web-push-libs/pywebpush)
to deliver notifications via Apple's and Google's public push services,
but the message payload itself is **end-to-end encrypted** using each
subscription's `p256dh` key per RFC 8291 (Web Push Encryption). Apple
and Google see only *"a push for endpoint X has body of length N"* —
they cannot decrypt the title, body, or deep link.

The optional `vapid_contact_email` (Settings → Notifications →
"Contact email") is sent in the VAPID JWT per RFC 8292 so push
services can reach you if your server starts misbehaving. It is
public.

---

## Removing a device

Open MLSS → **Settings** → **Notifications** card → find the device
row → tap **Remove**. The subscription is deleted server-side
immediately; the dispatcher won't send further pushes to it.

The notification permission on the device itself stays granted — you
can re-enable any time from the same card without going through
Settings → Notifications.
