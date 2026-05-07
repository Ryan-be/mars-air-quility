# Smoke test: live MLSS + grow unit, first physical deployment

**Date:** 2026-05-07 (overnight session, ~22:00 BST)
**Live URL:** `https://mlss.local:5000/`
**Hardware posture:** one Pi Zero W "Choclolate habanaro", **camera only** — no
soil sensor, no pump, no light wired.
**Scope:** walk every grow tab in the running UI, capture network + console,
record bugs/anomalies, fix the obvious ones in the same session.

---

## Test method

Drove the live UI via `Claude in Chrome` MCP — `navigate`, `read_page`,
`read_network_requests`, `read_console_messages`. No pytest dependency for
the smoke test itself; the goal was to verify what's actually rendering for
a real user, not what the unit tests assert.

Pages walked:

| Page | Status |
|---|---|
| `/grow` (fleet) | rendered, but no card photo (see Bug #1) |
| `/grow/1` Live tab | rendered, photo present, watering chart broken (Bug #2) |
| `/grow/1` History tab | rendered fully — moisture chart + photo timelapse work; 12 photos visible |
| `/grow/1` Configure tab | rendered fully — profile, PID, light windows, calibration, safety override all present |
| `/grow/1` Diagnostics tab | rendered fully — firmware, buffer, connection log, sensor sanity, danger zone |
| `/grow/errors` | rendered — many "unit online" entries (Anomaly #1) |
| `/grow/settings` | rendered — enrollment key rotator, 15 plant profiles, holiday mode |

No console errors anywhere. No 4xx/5xx network responses.

---

## Bugs found

### Bug #1 — Fleet card shows "— No photo yet —" even when photos exist  ✅ FIXED

**Severity:** Medium — UX confusion; user assumed the unit hadn't taken any
photos when in fact it had captured 12.

**Location:**
- Server: `mlss_monitor/routes/api_grow_units.py::_last_known_state`
- Client: `static/js/grow/components/grow-card.mjs:55`

**Diagnosis:** Client reads `unit.last_known_state?.last_photo_url`, but the
server's `_last_known_state` never populated that key. The optional chaining
silently resolved to `undefined` → "no photo" branch fired. Worse, for the
"camera only" deployment posture the server returned `last_known_state=null`
entirely (because no telemetry rows existed), so even if the client were
fixed it would have nothing to read.

**Fix:**
1. `_last_known_state` now queries `grow_photos` and adds `last_photo_url`
   pointing at `/api/grow/units/<id>/photos/<photo_id>` — the immutable
   per-id endpoint we just added cache headers to, so the fleet poll
   doesn't re-fetch the JPEG every time.
2. The function no longer returns `None` when telemetry is absent but
   photos exist — it stubs the telemetry fields with `None` (the
   client's `?? null` and `!= null` guards already handle this) and
   surfaces the photo URL.
3. `None` contract is preserved for units with truly zero data so the
   "Brand-new" no-data card still shows the placeholder.

**Tests added:** four cases in `tests/grow_server/test_grow_units_api.py`:
- photo URL surfaces when photos exist
- `None` when no photos
- camera-only unit (photos but no telemetry) still gets `last_known_state`
- zero-data unit still returns `last_known_state=null`

---

### Bug #2 — "Plotly not loaded" on unit detail Live tab  ✅ FIXED

**Severity:** Medium — entire watering-history chart panel non-functional.

**Location:** `templates/grow_unit_detail.html`

**Diagnosis:** `static/js/grow/components/sensor-event-chart.mjs:10` checks
for `typeof Plotly === "undefined"` and renders the literal text "Plotly
not loaded" as a fallback. Plotly is loaded only on `dashboard.html` and
`history.html`; `grow_unit_detail.html` was never updated to include it.

**Fix:** Added the same `<script src="https://cdn.plot.ly/plotly-basic-2.35.2.min.js">`
tag the dashboard uses. `plotly-basic` includes scatter + bar, which is what
the watering-history chart needs.

**Test:** Manual verification only — the chart is on the Live tab and only
fires once telemetry exists (currently no soil sensor wired). Will be
covered by the existing `test_e2e_smoke.py` suite once Plotly DOM rendering
is mocked, but that's out of scope tonight.

---

## Anomalies (not fixed; flagged for follow-up)

### Anomaly #1 — Connection thrashing produces grow_errors noise

**Severity:** Low — cosmetic but annoying.

**Observed:** Diagnostics tab → Connection log shows the unit going
online → offline → online ~10 times in a 1-minute window around 21:00
BST. Each reconnect is filed as a `grow_error` of severity `info` and
kind `online` ("unit online") on `/grow/errors`. Result: the errors page
is dominated by harmless reconnect entries.

**Plausible causes:**
- WiFi flakiness on the Pi Zero W (the `armv6l` build of `wpa_supplicant`
  is known-flaky; signal strength matters a lot)
- The MLSS service was being restarted as I shipped this overnight
  session's commits — every restart kicks the WS connection
- WS ping/pong keep-alive interval might be tighter than the network
  round-trip, causing false dead-peer detection

**Recommendation:** Don't file `info`-severity reconnects as `grow_errors`
at all — they're effectively log lines. Keep the connection log table
under Diagnostics → Connection log; that's the right home for this
churn. Only file an actual `grow_error` if a unit stays offline for >5
minutes (matching the existing `_OFFLINE_AFTER` threshold) — that's a
genuine alert. Add an audit/follow-up ticket to the backlog rather than
fixing tonight; needs investigation of WS keepalive too.

---

### Anomaly #2 — Sensor sanity panel says "No capabilities reported yet"

**Severity:** None — expected behavior for the camera-only posture.

**Observed:** Diagnostics → Sensor sanity → "No capabilities reported yet."

**Diagnosis:** The unit hasn't sent a `capabilities` WS frame, presumably
because no I2C sensors / actuators are wired up. The capabilities frame
is sent on boot once hardware is detected. With only the Pi camera
(which is not on I2C), there's nothing to enumerate.

**Recommendation:** When all-non-camera-channels are absent, render
"Camera-only deployment — no I2C sensors detected" rather than the
generic "No capabilities reported yet". Cosmetic; not urgent.

---

### Anomaly #3 — Many `/api/grow/units` polls captured during fleet view

**Severity:** None — expected behavior.

**Observed:** Six identical `GET /api/grow/units` 200 responses in the
~5 seconds the fleet view was open.

**Diagnosis:** The fleet poll interval is short by design (so cards
update live). Confirmed in `static/js/grow/fleet.mjs`. This isn't a bug
but worth knowing when reasoning about cache effectiveness — the fleet
poll's response is small (~2KB JSON) and cheap to revalidate. The new
fleet-card photo URL points at the immutable `/photos/<id>` endpoint,
so the actual JPEG bytes are NOT re-fetched per poll, only the
metadata.

---

## What works well

- Photo capture → upload → DB join all working end-to-end with the new
  picamera2 API fix and absolute-path photo storage default
- History tab timelapse: 12 photos rendered, scrubber + autoplay both
  functional, lightbox click works, /photos/<id> served with the new
  immutable cache headers
- Diagnostics tab fully populated: firmware version, uptime (38m),
  buffer (0 rows = idle), connection log (despite the noise issue
  above), token rotator + decommission + clear-buffer all present
- Settings page: 15 plant profiles loaded cleanly, holiday-mode toggle
  + enrollment-key rotator both rendered
- Configure tab: profile editor, PID editor, light windows editor,
  calibration wizard, safety override all rendered without error
- WS keepalive (sd_notify WATCHDOG=1 every 10s) is preventing the
  systemd watchdog SIGABRT loop — uptime 38m through this session
- No console errors on any page

---

## Files touched

```
mlss_monitor/routes/api_grow_units.py          (Bug #1 fix)
templates/grow_unit_detail.html                (Bug #2 fix)
tests/grow_server/test_grow_units_api.py       (Bug #1 tests)
docs/superpowers/audits/2026-05-07-first-deployment-smoke-test.md  (this doc)
```

Two follow-up audit items deferred to backlog:
- Anomaly #1: don't file `info`-severity reconnect events as
  `grow_errors`; investigate WS keepalive flapping on Pi Zero W
- Anomaly #2: surface "Camera-only deployment" instead of the generic
  "No capabilities reported yet" message
