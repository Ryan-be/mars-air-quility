# Grow code audit + deferred-items summary (pre-Phase-4)

**Date:** 2026-05-08
**Trigger:** Before starting Phase 4 (polish) work, take stock of:

1. Bugs / dead code / contract drift in everything we've shipped so far
2. Why the e2e suite let several issues reach physical deployment
3. All other deferred grow items that aren't already on the Phase 4 polish or Phase 5 smarts backlogs

Source audits (read these for full detail):
- [`2026-05-08-grow-data-flow-audit.md`](2026-05-08-grow-data-flow-audit.md) — 7-flow trace through firmware ↔ server ↔ DB ↔ UI
- [`2026-05-08-grow-e2e-gap-analysis.md`](2026-05-08-grow-e2e-gap-analysis.md) — coverage analysis across the 8 e2e files
- [`2026-05-07-first-deployment-smoke-test.md`](2026-05-07-first-deployment-smoke-test.md) — original smoke test
- [`2026-05-08-grow-ui-design-critique.md`](2026-05-08-grow-ui-design-critique.md) — design walkthrough

---

## Part 1 — Real bugs / dead ends in shipped code

### 🔴 Severity-H: user-visible breakage or dead feature

**1. Firmware never emits the `capabilities` frame.**
The single biggest data-flow gap. `service.py::_run_main_loop` constructs sensors / actuators / camera but never builds a `Capability` list nor calls `ws.send_text("capabilities", ...)`. The helpers `_try_init_with_health` + `_read_with_health` exist + are tested, but production never calls them.

Net effect: `unit.capabilities` is permanently `[]` for every freshly-enrolled unit. Cascading consequences:
- Live readings shows the "No telemetry yet" empty state forever, even when telemetry IS streaming
- Sensor sanity panel always reads "🔌 never seen"
- `firmware_version` always renders as "—"
- Action buttons (Snap photo, Toggle light) can't grey out for `no_hardware` channels because the channels never exist
- Health pills never appear

Fix: add a capabilities-emit step at the end of `_run_main_loop`, then re-emit on every WS reconnect via `on_reconnect_sync`. ~30 LoC.

**2. `buffer_eviction` events are silently rejected at the server boundary.**
Firmware emits `kind="buffer_eviction"` from `ws_client._handle_buffer_eviction` when the LocalBuffer hits a row/byte cap. But the server's `EventKind` enum doesn't include `BUFFER_EVICTION`, so pydantic validation fails and the frame is logged at WARNING and dropped.

Net effect: when an SD card actually fills up and the unit drops telemetry, the server NEVER sees the eviction event — the whole point of the eviction-notification path. The `grow_errors` row that would surface "your unit lost data" never lands.

Fix: add `BUFFER_EVICTION = "buffer_eviction"` to `contracts/src/mlss_contracts/enums.py:EventKind`, then route it in `handle_event` to insert a warning-severity row. ~5 LoC.

**3. `photo_interval_min_override` is half-wired (column exists, no producer, no GET surface, no UI).**
- Schema column: ✓ defined (`grow_schema.py:43`)
- Contracts model field: ✓ defined (`ConfigPayload.photo_interval_min`, unused)
- PUT endpoint: ✗ none
- GET response: ✗ never includes it
- UI editor: ✗ none
- Firmware reads: hardcoded `LoopConfig.photo_interval_min = 30`

Net effect: there's no way to change the photo cadence; the column is pure dead weight.

Fix: either drop the column / contract field / firmware constructor parameter, OR finish wiring (PUT endpoint + UI editor + GET surface + apply_config). The drop is ~20 LoC; the finish is ~150 LoC.

### 🟡 Severity-M: silent feature gaps, dead branches, contract drift

**4. `safety_override` action `skip_next_soak` is a dead branch.**
Server emits the command, dispatcher sets `state.skip_next_soak = True`, but `SafetyLoop` never receives `override_state` so the flag is never consumed. Admin clicks have zero effect.

**5. `grow_unit_capabilities.last_seen_at` is never updated by `handle_telemetry`.**
Only `_promote_capability_health` bumps it, and only when `pump_state=1` / `light_state=1`. Soil moisture / temp / lux / humidity channels stay forever stale, so the Diagnostics → Sensor sanity panel always shows them as "never seen" even when telemetry is streaming. Compounds with bug #1.

**6. Override cascade is two-deep, spec says three-deep.**
`api_grow_config._resolve_overrides` walks unit-override → plant-profile. The spec wants unit-override → plant-profile → `app_settings.grow_default_*` → hardcoded. Currently `grow_default_soak_window_min` is seeded but never read by any endpoint.

**7. `is_active=0` units accept config PUTs.**
The read side filters `is_active=1` (the unit disappears from the fleet). The PUT side doesn't, so a controller who knows the URL can edit a soft-deleted unit's config. Harmless (no WS push goes anywhere) but consistency wart.

**8. Holiday-mode toggle doesn't push `config_changed`.**
Documented v1 simplification — units pick up the flag on next reconnect. A unit online for 24h won't see a flipped flag for that long. Worth fixing when an operator complains.

**9. Several `EventKind` enum values are dead.** `STARTUP`, `SHUTDOWN`, `IDENTIFY_COMPLETE`, `CONFIG_APPLIED`, `SENSOR_RECOVERED` are all defined but no firmware code path emits them. `buffer_replay_started/_complete` ARE emitted but the server's `handle_event` only handles 4 kinds, so they're silently dropped.

**10. Several contract models are defined but unused in production.**
- `Severity` enum (production hardcodes the strings)
- `MediumType` enum (`_MEDIUM` Literal duplicates inline)
- `PlantProfile` model (test-only)
- `ConfigPayload` model (no endpoint emits or validates)
- `CommandPayload` model (no endpoint validates before sending — this is how `clear_buffer` and `light_override` ended up in the firmware dispatcher without entering `CommandName`)

**11. `phase_changes: []` in /history response is hardcoded empty** (placeholder for the future image-classifier annotation feature; frontend reads it).

**12. `grow_photos.classified_phase` / `classifier_confidence` / `classified_at` columns have no producer.** Same shape as 11 — Phase 5 image-classifier prep, never touched.

**13. `grow_watering_events.soil_pct_after_5min` has no backfill job.** Specced to backfill 5min after each pulse; nothing schedules it.

**14. Two command-name dispatch shapes coexist** (`kind`-keyed and `name`-keyed in `dispatch.py`). Documented; long-term cleanup.

**15. `grow_unit_capabilities.channel` has no `CHECK` constraint** (DB accepts any string; pydantic catches it at the WS boundary). Defence-in-depth fine, but the DB doesn't enforce.

**16. `pump_cooldown_until` is set on hard-cap path but not persisted** in `state_persistence`. A service restart inside the 5-minute cooldown loses it; the next tick happily pulses again. The 30s hard cap still applies, but the cooldown is one-shot rather than durable.

**17. Reconnect noise filter on /grow/errors couples to `severity='info'`.** Loose coupling — if the severity is ever bumped, the filter silently stops working.

### 🟢 Severity-L: cosmetic / forward-compat / harmless

- `white_balance` field harvested by `photo_storage` but never produced by `Camera.capture` (NULL inserted forever)
- `last_telemetry_at` written but never read
- Naive vs aware datetime ambiguity in a couple of places (works today, fragile)
- `LocalBuffer.pop_all` deprecated, only test references remain
- `_build_reconnect_sync` (service.py) only used by a backwards-compat test
- `'identify_test'` trigger enum value never produced
- `'image_classifier'` phase_set_by value never set
- `grow_plant_profiles.soak_window_min` lacks `NOT NULL DEFAULT`
- Schema-drift duplications between `grow_schema.py` CREATE TABLE and `init_db.py` ALTER migrations (harmless, just noisy)

---

## Part 2 — Why the e2e suite let bugs slip through

8 e2e files, all server-side. Real Flask, real WS, real DB, real session middleware — but every assertion is JSON shape or HTML substring. **No test runs the JS, opens the unit-detail page in a browser, or even renders a fleet card from a real API response.**

Bug-by-bug from the deployment-time discoveries:

| Bug | Why missed |
|---|---|
| Fleet-card photo missing (`last_photo_url`) | `test_e2e_smoke.py` writes a photo but never GETs `/api/grow/units` to read it back. One-line extension would have caught it. |
| Plotly not loaded on Live tab | No e2e test renders `/grow/units/<id>` at all. A single substring assertion (`'cdn.plot.ly/plotly' in html`) would have caught it. |
| `data.events` vs `data.watering_events` | Server contract is pinned by `test_history_e2e.py`; client contract drifted independently. Two sides, no integrating test. Pure JS unit test (jsdom) would catch this. |
| Camera-only empty-box | `test_sense_only_mode_e2e.py` simulates camera-only at the data layer but never renders the unit-detail page in that posture. |
| Photo capture gap 22:00–06:00 | Plumbing was tested; the default value (the assumption itself) wasn't. An "assumption bug" that no integration test would have caught — only a unit test pinning `LoopConfig().photo_active_hours is None` would. |
| Schedule never visible | No test asserts that current config values appear anywhere on the rendered page. UX visibility gap, not a behavioural defect. |

**Root architectural gap:** the only "real" axis is server-side. The infrastructure missing:

- **A jsdom-based test harness for components** (cheapest, ~50 LoC per test) — `tests/js/test_grow_card.mjs` is a working pattern, not extended to other components
- **A Flask-test-client substring assertion on rendered templates** (~30 LoC for a new test file) — catches script-tag absence, empty-state copy, "current value visible" without any JS execution
- **A headless-browser harness** (Playwright, one-time infrastructure cost) — only way to systematically catch the "JS rendering vs server contract" class

**Highest-ROI test additions** (in priority order):

1. ~10 LoC — extend `test_e2e_smoke.py` to GET `/api/grow/units` after the photo lifecycle and assert `last_known_state.last_photo_url` populates. Catches Bug 1 directly.
2. ~30 LoC — new substring-render test for `/grow/units/<id>`: assert Plotly script tag, empty-state copy, current `photo_active_hours` value. Catches Bugs 2, 4, 6.
3. ~50 LoC — jsdom test for `sensor-event-chart.mjs` against the API's actual `{moisture, watering_events}` shape. Catches Bug 3.
4. ~Significant — Playwright harness + first smoke test. Pays ongoing dividends; catches the next bug in this family without per-bug effort.
5. Pin product-policy defaults: assert `LoopConfig().photo_active_hours is None` etc. Stops Bug 5 from silently regressing.

---

## Part 3 — Other deferred items (not Phase 4 polish, not Phase 5 smarts)

### Investigation / root-cause work

- **WS keepalive flapping on Pi Zero W.** Yesterday's smoke test saw the unit reconnect ~10 times in a 1-minute window around 21:00. Could be Pi Zero W WiFi flakiness, my deploy restarts, or WS ping/pong tighter than network round-trip causing false dead-peer detection. Filter is in place on `/grow/errors` to hide the noise (commit `2f3aa51`), but the underlying flapping still happens. Worth `journalctl` correlation against the actual `mlss-grow` reconnect path.

- **Why the pre-existing `test_e2e_full_phase3_observability_story` is flaky.** Asserts `'online' in connection_log_kinds` but gets `[]`. Failed before any of this branch's commits; failed in baseline. Pre-existing test smell — likely a fixture timing issue between the fake firmware connect and the diagnostics fetch. Currently we treat it as "the one acceptable failure" but it should either be fixed or quarantined.

### UX gaps surfaced by smoke test + design critique that aren't on Phase 4

- **"Camera-only deployment" copy in Sensor sanity.** Currently shows "No capabilities reported yet" which is misleading when the unit is intentionally camera-only. Becomes moot once bug #1 (capabilities frame) lands — the panel will then show real `no_hardware` rows.

- **Connection log: "12 reconnects in 2 minutes" coalescing.** Rapid-fire reconnect rows are noisy in the table. We added relative time + status dots in commit `5a071c6`, but didn't group consecutive events. Worth doing once the capabilities and flapping items are sorted (might fix the underlying noise).

- **Buffered-message replay UI.** Mentioned in the original Phase 3 backlog. Diagnostics → Buffer inspector shows summary counts; there's no UI for "show me the last N messages currently buffered" or "replay this specific message". Probably fine to defer indefinitely but worth flagging.

- **Photo capture: gate on actual ambient light, not wall-clock hours.** The user pointed out that "Light might not come from the grow light". Current photo-schedule editor is wall-clock based. A more sophisticated future version could:
  - Use the camera's auto-exposure shutter time as a proxy ("if shutter > 200ms, scene is too dark to photograph usefully")
  - Or, if a lux sensor is wired, gate on lux > threshold
  - Or, integrate with the light-windows so photos automatically follow whatever schedule the operator set (no separate config)

- **No discoverability for product-policy decisions.** Bugs 5 + 6 from the e2e gap analysis. The `photo_active_hours = None` default isn't shown anywhere; a buffered-message-replay isn't shown anywhere; the soak-window value isn't shown next to the water-now button. Ops can't easily find out "why didn't this happen?" without reading source.

- **Plant profile cards.** Click affordance fixed (commit `5a071c6`). Still unresolved: the "Loaded N profiles" footer was removed but there's no error toast when the load fails.

### Skipped from design critique (deliberately, but worth re-checking later)

- **#3 Filter pill groups asymmetric padding.** Three groups (PHASE/STATUS/PLANT) currently render as one flat row with uppercase labels. Skipped as cosmetic but with more units the group boundaries get fuzzy.

- **#15 Save-All sticky-bottom button.** Configure tab has 5 SAVE buttons. We deliberately kept the per-phase save because partial-save halfway through a network blip shouldn't blow away unrelated edits — the audit doc explained this. If the user reports the multi-save as friction in practice, revisit.

### Hardware / reliability deferred (not on Phase 4 backlog, not Phase 5)

- **Hardware watchdog (`/dev/watchdog`)** on Pi Zero — designed in but not wired up due to risk of misconfigured timer rebooting healthy Pi mid-write. Keep deferred unless a unit silently wedges in production despite the systemd watchdog (which IS wired up, commit `bfc2709`).

- **MLSS server SD card → USB SSD migration.** Caused the deployment-night outage; on the polish backlog as a doc, but the actual migration of the server hasn't been done. The Pi Zero grow units are write-light enough that SD is probably fine, but the MLSS server SD has been thrashed for weeks of dev. Worth scheduling.

- **Grow unit RTC.** No real-time clock on the Pi Zero. NTP sync at boot is the current source of time; if a unit boots without WiFi (rare but possible) the timestamps drift. Telemetry rows would be wrong-but-recoverable; photo `taken_at` would be wrong-but-recoverable; but the firmware's photo-schedule gating uses local clock — a unit booting at "2000-01-01" would skip-or-take-photos based on whatever-hour-it-thinks-it-is.

### Schema / migration items

- **`grow_unit_capabilities.channel` has no CHECK constraint** (item 15 above). Wait for the next table-recreate migration.
- **`grow_plant_profiles.soak_window_min` lacks `NOT NULL DEFAULT`.** Inconsistent with every other tunable in that table.
- **Schema-drift duplications between `grow_schema.py` CREATE TABLE and `init_db.py` ALTER migrations.** Mostly harmless on fresh DBs; documented in the data-flow audit. Worth a docstring in `init_db.py` saying "these ALTERs are no-ops on fresh DBs; kept for upgrade-from-Phase-1 paths".

### Documentation

- **`docs/DATABASE.md` is out of sync** with the schema. The CHECK enforcement asymmetry between fresh-vs-upgraded DBs (item 15 above) isn't documented. The dead columns (item 12, 13) aren't tagged as "reserved for image-classifier — Phase 5+" so a future contributor will wonder why they're empty.

### Tooling

- **`bin/deploy` doesn't do schema-drift check.** It runs `poetry install`, builds wheels, restarts the service. If a migration was pushed, the service comes up against the un-migrated DB and either backfills (good) or crashes loudly (also fine). But there's no pre-restart "✓ schema is current" check.

- **No `bin/test` script.** `python -m pytest tests/grow_server tests/grow_unit tests/contracts && node --test tests/js/*.mjs` is the full sweep but not codified anywhere.

---

## Part 4 — Recommended order before Phase 4

Three rough buckets, picked for highest-leverage-per-LoC:

### Bucket A — fix the broken paths (1-2 days)

1. **Emit `capabilities` from firmware** (bug #1). Unblocks Live tile rendering, sensor sanity, health pills, firmware version, sensor `last_seen_at` updates, the camera-only-vs-misconfigured-soil-sensor distinction. Single biggest improvement.
2. **Add `BUFFER_EVICTION` to `EventKind` + route in `handle_event`** (bug #2). 5 LoC; restores SD-card-fill notifications.
3. **Wire `override_state` into `SafetyLoop` OR remove `skip_next_soak`** (bug #4). Closes a dead admin action.
4. **Bump `last_seen_at` for sensor channels in `handle_telemetry`** (bug #5). Compounds with #1 to make sensor-sanity actually useful.
5. **Decide on `photo_interval_min_override`** (bug #3). Either drop or finish wiring.

### Bucket B — close the e2e holes that let those bugs through (1 day)

1. Extend `test_e2e_smoke.py` (~10 LoC) — fleet GET assertion.
2. New substring-render test for `/grow/units/<id>` (~30 LoC) — Plotly tag, empty-state, current values.
3. jsdom test for `sensor-event-chart.mjs` (~50 LoC) — catches API/UI key drift.
4. Pin defaults — `LoopConfig().photo_active_hours is None` etc.

### Bucket C — deferred items worth doing before Phase 4 polish (each independent)

1. Investigate WS keepalive flapping (no fix yet, just `journalctl` correlation).
2. Fix or quarantine the flaky `test_e2e_full_phase3_observability_story`.
3. Document schema enforcement asymmetry in `docs/DATABASE.md`; tag `grow_photos.classified_*` columns as "reserved for Phase 5".
4. Clean up the dead contract models (`Severity`, `MediumType`, `PlantProfile`, `ConfigPayload`, `CommandPayload`) — either start using them or delete.

After Buckets A + B + C, Phase 4 work starts on solid ground rather than on top of latent bugs. Bucket A in particular is small enough that postponing it past Phase 4 means polishing UI on top of broken data.
