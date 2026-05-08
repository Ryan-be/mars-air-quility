# Plant Grow Unit data-flow audit — 2026-05-08

Research-only pass tracing the seven major flows (telemetry, photos,
commands, config sync, capabilities, errors/events, diagnostics) end-to-end
across firmware ↔ server ↔ DB ↔ UI. Severity legend: H = blocking /
user-visible breakage; M = silent feature gap, dead branch, or contract
drift; L = cosmetic, harmless drift, or doc-vs-code mismatch.

## Flow 1: Telemetry

### Findings

- (1) **H — `capabilities` frame is never produced by firmware.** All
  consumers downstream depend on it (Live tile rendering, sensor-sanity,
  health pills). The handler / DB / UI side is fully wired, but the firmware
  side is missing entirely.
  Producer:  none — `grow_unit/src/mlss_grow/service.py:_run_main_loop`
             (lines 288-501) constructs sensors / actuators / camera but
             never builds a `Capability` list nor calls `ws.send_text("capabilities", ...)`.
             The helpers `_try_init_with_health` (service.py:54) and
             `_read_with_health` (service.py:79) exist + are tested, but
             nothing calls them outside the unit tests.
  Consumer:  `mlss_monitor/grow/handlers.py:handle_capabilities` (line 134),
             `mlss_monitor/routes/api_grow_units.py:get_unit` (line 174),
             `static/js/grow/unit_detail.mjs:renderLiveReadings` (line 114)
             which iterates `unit.capabilities`.
  In production a freshly-enrolled unit will never populate
  `grow_unit_capabilities`, so `unit.capabilities` is always `[]`,
  `renderLiveReadings` falls through the `for cap of capabilities` loop and
  shows the "No telemetry yet" empty-state forever even when telemetry IS
  arriving. Fleet card + Diagnostics panel "sensor sanity" rows are also
  empty. Telemetry rows still land in `grow_telemetry`, but the user can't
  see them because the UI is capability-driven.
  Suggested fix: add a capabilities-emit step at the end of `_run_main_loop`
  before `asyncio.gather`, then re-emit after every successful WS reconnect
  via `on_reconnect_sync`.

- (2) **M — `last_telemetry_at` written but never read.**
  Producer:  `mlss_monitor/grow/handlers.py:handle_telemetry` (line 73-76).
  Consumer:  none in `mlss_monitor/`.
  The column is updated on every frame, the comment in the handler says
  "Once heartbeats land, last_seen_at should be set from server clock so
  the fleet view can show 'online recently' independently of telemetry
  cadence" — that follow-up never happened. `_classify_status` in
  api_grow_units.py uses `last_seen_at` only.
  Suggested fix: either drop the column or wire it into the diagnostics
  endpoint as "last telemetry frame" alongside uptime.

- (3) **M — Telemetry handler doesn't bump `grow_unit_capabilities.last_seen_at` for sensor channels.**
  Producer:  `handle_telemetry` only calls `_promote_capability_health` for
             pump/light when their state is non-zero (handlers.py:123-126).
  Consumer:  `api_grow_diagnostics.py:get_diagnostics` reads
             `last_seen_at` per channel (line 129) and computes
             `is_stale` against `grow_sensor_stale_threshold_min`. With
             `last_seen_at` permanently NULL for soil_moisture / soil_temp_c /
             ambient_lux / etc., the sensor-sanity panel always shows them
             as "🔌 never seen" even when telemetry IS streaming.
  Suggested fix: when telemetry arrives, UPDATE
  `grow_unit_capabilities.last_seen_at = ts WHERE unit_id=? AND channel IN (channels_with_non_null_value)`
  for each non-null sensor reading in the payload.

- (4) **L — `_classify_status` parses naive timestamps.**
  Producer:  WS listener stores `ts` as naive UTC datetime (api_grow_ws.py:255-257
             strips tzinfo).
  Consumer:  `_classify_status(last_seen_at)` does
             `datetime.utcnow() - seen` — both naive, fine. But if SQLite
             round-trips it as a string the `fromisoformat` will produce a
             naive datetime; in some Python builds this combination raises
             on aware/naive mixing if a row ever comes back tz-aware.
             Currently safe but fragile.
  Suggested fix: standardise on naive UTC with an explicit comment, or
  always coerce both sides through the same helper.

## Flow 2: Photos

### Findings

- (1) **L — `white_balance` field harvested but never produced.**
  Producer:  `grow_unit/src/mlss_grow/camera.py:capture` (line 81-87)
             returns `{width, height, jpeg_quality, shutter_us, iso}`. No
             `white_balance` key.
  Consumer:  `mlss_monitor/grow/photo_storage.py:handle_photo_frame`
             (line 153) reads `header.get("white_balance")` and inserts
             into `grow_photos.white_balance` (always NULL).
  No UI ever reads it back. Dead column + dead INSERT clause.
  Suggested fix: drop the column from `grow_schema.py`, and remove the
  field from the INSERT in `photo_storage.py`. Or — if there's intent to
  add it — populate from `picamera2`'s `ColourGains` metadata.

- (2) **M — `grow_photos` dead columns: `classified_phase` / `classifier_confidence` / `classified_at`.**
  Producer:  none. Spec mentions a future image-classifier; nothing writes them.
  Consumer:  none. No API exposes them.
  Pure forward-looking schema noise. The phase-classifier pipeline
  is "future polish" per the spec, with no current producer or consumer.
  Suggested fix: leave for now (cheap to keep), but tag in
  README/ARCHITECTURE as "reserved for image-classifier — Phase 5+".

- (3) **M — `grow_watering_events.soil_pct_after_5min` has no backfill job.**
  Producer:  none — the column was specced to be backfilled 5 min after each
             pulse. No cron / scheduler / background task does this.
  Consumer:  none.
  Same shape as (2): future ML feature with cold plumbing.
  Suggested fix: same — keep but flag as reserved, OR add a tiny cron
  task that joins `grow_watering_events` to `grow_telemetry` 5 min later.

- (4) **L — `taken_at` from firmware is `datetime.utcnow().isoformat() + "Z"`.**
  Producer:  `safety_loop.py:300-303` uses
             `now.isoformat() + "Z"`. `now` is `datetime.utcnow()` (naive).
             So the resulting string is `"2026-05-08T10:00:00Z"` — valid
             but `now` is a naive datetime, formatted with the Z suffix
             implying UTC. The server's `handle_photo_frame`
             (`photo_storage.py:93`) replaces "Z" with "+00:00" and
             parses; the result IS aware, then converted back to naive UTC.
             Round-trip works but is fragile against locale/clock-skew bugs.
  Suggested fix: use `datetime.now(timezone.utc).isoformat()` consistently
  to avoid the naïve-with-Z-suffix ambiguity.

- (5) **M — Live tab "Latest photo" panel doesn't gate on camera capability.**
  Producer/Consumer: `static/js/grow/unit_detail.mjs:renderPhotoPanel`
             always renders the hero image with `/photo/latest` even
             when the unit has no `camera` capability. If the camera
             never produced a photo, the `<img>` falls through to a
             404 background, leaving an empty box. With (1) above fixed
             this becomes graceful (could check `unit.capabilities` for
             a `camera` channel before mounting).
  Suggested fix: omit the panel when the unit has no `camera` capability;
  show "No camera attached to this unit" placeholder.

- (6) **L — `photo_lightbox.mjs` import paths checked, `photo-timelapse.mjs` mounts inside History tab.** All clean. The Live tab (`refreshPhotoPanel`) and Timelapse pull from different endpoints (`/photo/latest` vs `/photos/<id>`) consistently; the immutable cache header on `/photos/<id>` in `api_grow_photos.py:_make_immutable` correctly prevents stale binds.

## Flow 3: Commands (browser → unit)

### Findings

- (1) **M — `safety_override` action `skip_next_soak` is a dead branch.**
  Producer:  Server emits `{kind: "safety_override", action: "skip_next_soak"}`
             (contracts/src/mlss_contracts/config_payloads.py:148, route
             api_grow_config.py:457-465). The firmware's
             `safety_override.invoke_safety_override` (line 84-87) sets
             `state.skip_next_soak = True` on the shared
             `SafetyOverrideState`.
  Consumer:  `safety_override.consume_skip_next_soak` is defined (line 51-57)
             but never called. The `SafetyLoop` constructor in
             `safety_loop.py:71-143` does NOT take an `override_state`
             argument, and `service.py:412-423` does not pass one. The
             `SafetyLoop.tick()` (line 156) never reads the flag.
  So an admin clicking "skip next soak" silently sets a flag and the PID
  loop happily keeps enforcing the soak window on the next tick.
  Suggested fix: either remove the action from
  `_SAFETY_ACTION` Literal in contracts (and the dispatcher branch), OR
  thread `override_state` into `SafetyLoop.__init__` and consume the
  flag at the top of the PID block in `tick`.

- (2) **L — Two command-name dispatch shapes coexist.** `dispatch.py:81-108`
  accepts both `kind`-keyed (`config_changed`, `safety_override`) and
  `name`-keyed (`identify`, `water_now`, `snap_photo`, `light_override`,
  `reload_config`, `reboot`, `clear_buffer`). Already documented in the
  module docstring; long-term the spellings should converge. No
  user-visible bug.
  Suggested fix: pick one, migrate the rest, deprecate.

- (3) **M — `CommandPayload` contract model is defined but no endpoint validates against it.**
  Producer:  `contracts/src/mlss_contracts/ws_messages.py:90` defines
             `CommandPayload` (name + args) using the `CommandName` enum.
  Consumer:  Neither `api_grow_units._push_command_blocking` nor
             `api_grow_config._push_config_changed` validates against
             `CommandPayload` before serialising. Server is free to push
             any string as `name`.
  This is how `clear_buffer` and `light_override` payloads ended up in
  the firmware dispatcher without ever entering `CommandName`. The
  firmware's dispatcher is permissive enough that this works, but the
  contract is no longer authoritative.
  Suggested fix: either validate every command via `CommandPayload`
  before sending, or delete the unused contract model.

- (4) **L — `light-toggle` falls back to "on" when no telemetry exists.**
  `api_grow_units.py:light_toggle` (line 360) reads
  `current_on = bool(row[0]) if row else False`. So a unit with no
  telemetry yet always toggles to "on" first. Reasonable default; just
  worth knowing for tests.

- (5) **L — `_push_command_blocking` race: the auth check vs. `send_to_unit` is not atomic.**
  Already documented in the function (line 263-267) — the disconnect-between-
  `is_connected` and `send` returns 503. Acceptable.

## Flow 4: Config sync (UI Configure tab → DB → push → firmware pull)

### Findings

- (1) **H — `photo_interval_min_override` half-wired (column exists, no producer, no GET surface).**
  Schema:    `database/grow_schema.py:43` defines
             `photo_interval_min_override INTEGER`.
  Producer:  No PUT endpoint writes it. `api_grow_config.py:_PID_FIELDS`
             tuple does not include it. UI has no editor.
  Consumer:  `api_grow_config.py:get_unit_config` does not include
             `photo_interval_min` in the response, so firmware always
             gets the hardcoded `LoopConfig.photo_interval_min = 30`
             default (`grow_unit/src/mlss_grow/safety_loop.py:48`).
             The contract model `ConfigPayload.photo_interval_min`
             (ws_messages.py:102) is also unused — never sent in any
             direction.
  Net effect: there's no way for an admin to change the photo cadence,
  and the column is dead weight. Either drop it or add a Configure-tab
  editor + push it through `apply_config`.
  Suggested fix: drop the column, the contracts field, and the
  `LoopConfig.photo_interval_min` constructor parameter — OR finish wiring.

- (2) **M — `_resolve_overrides` cascade is two-deep, spec says three-deep.**
  Producer/Consumer: `api_grow_config.py:_resolve_overrides`
             (line 520-545) walks unit-override → plant-profile. The
             spec (specs/2026-05-03-plant-grow-unit-system-design.md:419)
             says the cascade is unit-override → plant-profile →
             `app_settings.grow_default_*` → hardcoded.
  Consequences:
   - `grow_plant_profiles.soak_window_min` is nullable (no `NOT NULL DEFAULT`).
     A profile row with NULL `soak_window_min` returns NULL from
     `_resolve_overrides`, the firmware skips it in `_PID_FIELD_MAP` and
     uses its own hardcoded `30` — happens to match
     `grow_default_soak_window_min` ("30") so user-visible behaviour is
     correct, but the app-setting is unread.
   - `grow_default_soak_window_min` app_setting is seeded
     (grow_schema.py:301) but never read by any endpoint.
  Suggested fix: either implement the third level in `_resolve_overrides`
  (read `app_settings.grow_default_*` keys when both override + profile
  are NULL) or drop the seeded setting + `grow_default_*` references.

- (3) **M — `is_active=0` units accept config PUTs.**
  Producer:  PUT `/profile`, `/pid`, `/light_windows`, `/calibration`,
             `/photo_schedule` (api_grow_config.py:108-414) only filter on
             `WHERE id=?`, not `is_active=1`.
  Consumer:  After soft-delete (`DELETE /api/grow/units/<id>`), the unit
             is filtered out of the fleet view. But its row still
             accepts PUTs from a controller who knows the URL. The push
             to WS is a no-op (unit is disconnected forever). Harmless
             but a minor consistency wart with the read side which DOES
             filter on `is_active=1`.
  Suggested fix: add `AND is_active=1` to all UPDATEs and return 404 when
  the row is inactive (mirrors the `delete_unit` posture).

- (4) **M — Holiday-mode toggle does not push `config_changed` to connected units.**
  Producer:  `api_grow_settings.set_holiday_mode` (line 200-227) — the
             docstring explicitly says "v1 deliberately does NOT broadcast
             a config_changed push". Units pick up the new flag on next
             reconnect-pull.
  Consumer:  `safety_loop.py:228` reads `loop_cfg.holiday_mode` each tick.
             A unit that's been online for 24h won't see a flipped flag
             until it reconnects — could be days.
  Documented as a v1 simplification, fine. Worth re-checking when the
  user reports it as a real annoyance.
  Suggested fix: when ready, iterate `state.grow_ws_registry.connected_unit_ids()`
  and push `config_changed` to each.

- (5) **L — `apply_config` skips None-valued PID fields silently.**
  `grow_unit/src/mlss_grow/config_sync.py:165-169` — on a server that
  sent `{kp: null}`, the firmware just leaves the previous Kp in place.
  Defensible (treat null as "use existing"), but means a UI "Reset to
  default" only takes effect at the firmware level if `_resolve_overrides`
  returns a concrete number — which depends on (2) being fixed.

## Flow 5: Capabilities (firmware self-report)

The dominant finding here was already covered in Flow 1, item (1) — the
capabilities frame is never produced. Additional findings:

- (1) **M — `Capability.health` accepts 4 states; firmware would only ever emit 2.**
  Producer:  `_try_init_with_health` returns `"untested"` or
             `"no_hardware"`; `_read_with_health` returns `"connected"`
             or `"no_hardware"`. None of them ever produce `"unresponsive"`
             — that state is only set lazily by the server-side
             `health_watchdog` and `_promote_capability_health`. So the
             firmware-emitted set is a strict subset.
  Consumer:  `applyHealthStyling` in unit_detail.mjs:277 handles all four.
  No bug, but the contract `CapabilityHealth` type accepts inputs that
  the firmware will never emit on the boot path. Worth a comment.

- (2) **M — `grow_unit_capabilities.is_required` set per-cap by firmware,
  but nothing on the server validates the REQUIRED set was actually emitted.**
  Spec: `Channel.SOIL_MOISTURE`, `LIGHT`, `PUMP`, `CAMERA` are
  documented as REQUIRED. If a unit emits a capabilities frame with only
  `SOIL_MOISTURE`, the server happily accepts it. There's no check
  that all required channels are present.
  Suggested fix: add a server-side validator in `handle_capabilities` that
  inserts missing required capabilities with `health="no_hardware"` so
  the UI tile renders correctly even if the firmware omits them.

- (3) **M — `Channel` enum mismatch with grow_unit_capabilities CHECK.**
  Schema:    `database/grow_schema.py:78-91` defines
             `grow_unit_capabilities` with a `CHECK(health IN (...))` but
             NO check on `channel`. Any string is accepted.
  Contract:  `Channel` enum in contracts has the canonical 9 values.
  WS validation:  `api_grow_ws._validate_payload` validates
             `CapabilitiesPayload`, which validates each `Capability`,
             which validates `channel: Channel` — so a bad channel from
             the firmware is rejected at the boundary. Defence-in-depth
             is fine, but the DB doesn't enforce.
  Suggested fix: add a `CHECK(channel IN (...))` to the schema. (The
  init_db.py comment on line 232 acknowledges SQLite limits ALTER for
  CHECKs, so wait for the next table-recreate migration.)

## Flow 6: Errors / events

### Findings

- (1) **H — `buffer_eviction` event is rejected by server pydantic validator.**
  Producer:  `grow_unit/src/mlss_grow/ws_client.py:_handle_buffer_eviction`
             (line 125-150) emits an event with
             `kind="buffer_eviction"` when LocalBuffer hits a row/byte cap.
  Consumer:  `api_grow_ws._validate_payload(msg_type="event", payload)`
             (line 57-80) validates against `EventPayload` whose
             `kind: EventKind` strict-enum REJECTS `buffer_eviction`
             (the enum has only 11 values; `BUFFER_EVICTION` is missing).
             Validation fails, the frame is logged at WARNING and dropped.
  Net effect: when the SD card gets close to cap and the firmware ACTUALLY
  loses telemetry, the server NEVER sees the eviction event — the whole
  point of the eviction notification path. The `grow_errors` row that
  would surface "your unit dropped data" never lands.
  Suggested fix: add `BUFFER_EVICTION = "buffer_eviction"` to
  `contracts/src/mlss_contracts/enums.py:EventKind`, then route it in
  `handle_event` to insert a warning-severity grow_errors row.

- (2) **M — `handle_event` only knows 4 kinds; emits silently drop the rest.**
  Producer:  Firmware emits (per code review):
             `watering_pulse`, `sensor_degraded`, `safety_cap_hit`,
             `buffer_replay_started`, `buffer_replay_complete`,
             `buffer_eviction`. Plus the contract enum has
             `STARTUP`, `SHUTDOWN`, `IDENTIFY_COMPLETE`, `CONFIG_APPLIED`,
             `SENSOR_RECOVERED` — none of which are emitted by any
             firmware code path.
  Consumer:  `mlss_monitor/grow/handlers.py:handle_event` (line 215-280)
             handles only `watering_pulse`, `sensor_degraded`,
             `sensor_recovered`, `safety_cap_hit`. Comment at line 273
             says "Other event kinds (startup, shutdown, identify_complete, etc.)
             are logged-only — no DB row needed in Phase 1." Phase 1 is
             past; nothing landed.
  Buffer-replay started/complete are now emitted but invisible. Operators
  could reasonably want to see "this unit just replayed 200 buffered
  messages" in the connection-log panel.
  Suggested fix: add `buffer_replay_*` to handle_event with an info-severity
  row; emit `STARTUP` from firmware on first WS connect; etc.

- (3) **M — `Severity` enum in contracts is unused.**
  Producer:  `contracts/src/mlss_contracts/enums.py:40` defines
             `Severity = info/warning/critical`.
  Consumer:  No import in production code. Strings hardcoded in
             handlers.py / api_grow_errors.py / app code.
  Pure dead enum. Either start using it (importing into handlers.py for
  type hints + DB INSERT values) or delete from contracts.

- (4) **L — Reconnect noise filter on `/api/grow/errors` is correct but couples to `severity='info'`.**
  `api_grow_errors.py:128-129` filters out
  `(kind='online' AND severity='info')`. If a future change ever bumps
  online-row severity to 'warning' (e.g. for noisy reconnects), the
  filter silently stops working. Loose coupling; tag it with a comment.

## Flow 7: Diagnostics (Phase 3)

### Findings

- (1) **M — `buffer_summary` / `photo_buffer_summary` only populate every 10th tick AND require capabilities.**
  Producer:  `safety_loop.py:351-363` piggybacks summaries every Nth
             tick (default 10 — 5 min cadence). Stored on `grow_units.last_*_summary_json`
             with omit-doesn't-clobber semantics in `handle_telemetry`
             (handlers.py:107-112).
  Consumer:  `api_grow_diagnostics.py:get_diagnostics` reads + parses.
             `static/js/grow/components/buffer-inspector.mjs:renderOneSummary`
             handles `null` / `size==0` empty states correctly.
  Working as designed; just note that for the first 5 minutes after a
  fresh deploy the Diagnostics tab buffer panel shows "no summary yet"
  even when the buffer is empty. Acceptable.

- (2) **M — `firmware_version` only set by `handle_capabilities` — and capabilities are never sent (Flow 1 #1).**
  Producer:  `mlss_monitor/grow/handlers.py:handle_capabilities`
             (line 173-178). No telemetry path bumps it.
  Consumer:  `firmware-info.mjs:_formatUptime` shows "—" for null.
  As long as Flow 1 #1 is unfixed, the Diagnostics tab firmware version
  is permanently "—". Once capabilities frames start landing, it Just
  Works.

- (3) **L — `connection_log` slice is bounded to 20 rows but client-side pairing has no overflow notice.**
  `api_grow_diagnostics.py:36` caps at 20. `connection-log.mjs:_pairOfflineToOnline`
  walks the visible window only — an offline whose resolving online is
  outside the 20-row window shows as "ongoing" even if it actually
  resolved earlier. Acceptable trade-off for a 20-row diagnostic table.

- (4) **L — `open_errors` excludes online/offline kinds.** Correctly
  filtered (line 154). Mirrors the `/api/grow/errors` `include_reconnects=false`
  default. Consistent.

- (5) **M — Diagnostics-panel async fetch hand-off is consistent, BUT
  `unit_detail.mjs:switchSubtab` lazy-builds.** The subtab switcher does
  `await renderDiagnosticsPanel(unit)` (line 466) — so the diagnostics
  fetch only runs when the user clicks the tab. Fine for a Phase 3
  payload.

## Cross-cutting

### Schema drift between `grow_schema.py` (CREATE TABLE) and `init_db.py` (ALTER migrations)

- `init_db.py` migrations array (line 211-294) tries
  `ALTER TABLE grow_units DROP COLUMN light_phase_override_json` and
  `... last_known_state_json`, but the CREATE TABLE in `grow_schema.py:16-71`
  does NOT define those columns. On a fresh DB the DROP fails (caught by
  the bare `try/except`); on a pre-Phase-2 DB it succeeds. The migrations
  list ALSO contains
  `ALTER TABLE grow_unit_capabilities ADD COLUMN health TEXT NOT NULL DEFAULT 'untested'`
  + `ALTER TABLE grow_unit_capabilities ADD COLUMN last_seen_at DATETIME`
  — both columns are ALREADY in the CREATE TABLE (grow_schema.py:86-88),
  so on a fresh DB these ALTERs fail too. Net result: harmless
  duplication. Worth a comment in `init_db.py` saying "these are
  no-ops on fresh DBs; kept for upgrade-from-Phase-1 paths".
- `init_db.py:230-237` mentions
  `ALTER TABLE grow_unit_capabilities ADD COLUMN health TEXT NOT NULL DEFAULT 'untested'`
  but the CHECK enum can't be added via ALTER (also acknowledged in the
  same comment). On fresh DBs the CHECK is enforced; on upgraded DBs
  it isn't. The schema-cleanup comment is accurate but the enforcement
  asymmetry should be in `docs/DATABASE.md`.
- `grow_plant_profiles.soak_window_min` lacks `NOT NULL DEFAULT` — every
  other tunable has one. The `_SHIPPED_PROFILES` seed always provides a
  value, so it's only NULL if a future user-edited row sets it to NULL
  explicitly via PUT. Could drift unless `_ProfileUpdate` rejects null
  (currently does, since `Optional[int] = Field(None, ge=0, le=240)`
  accepts None as "leave unchanged" but the SQL UPDATE with
  `exclude_none=True` skips it). Net safe but worth a `NOT NULL DEFAULT 30`.

### Files that exist but are never imported

- `grow_unit/src/mlss_grow/buffer.py:LocalBuffer.pop_all` — explicitly
  marked DEPRECATED in the docstring (line 306-318). Only test references
  remain. Safe to delete after tests are migrated.
- `grow_unit/src/mlss_grow/service.py:_build_reconnect_sync` (line 274) —
  backwards-compat wrapper used only by `tests/grow_unit/test_service.py`.
  Production wires `_build_reconnect_sync_and_retention` directly.
- `grow_unit/src/mlss_grow/service.py:_try_init_with_health` and
  `_read_with_health` — defined + tested but never called inside
  `_run_main_loop`. They WOULD be called by a capabilities-emit step
  if Flow 1 #1 ever lands.
- `contracts/src/mlss_contracts/ws_messages.py:CommandPayload` — defined
  but no production code validates against it.
- `contracts/src/mlss_contracts/ws_messages.py:ConfigPayload` — same.
  No endpoint emits or validates a `ConfigPayload`. The
  `photo_interval_min` field on it is doubly-orphaned (no producer, no
  consumer; see Flow 4 #1).
- `contracts/src/mlss_contracts/plant_profiles.py:PlantProfile` — defined
  but never imported by production code (only `LightWindow` and
  `WateringConfig` from this module are used, by `ConfigPayload`, which
  is itself unused). Test-only.
- `contracts/src/mlss_contracts/enums.py:Severity` — defined but never
  imported by production code.
- `contracts/src/mlss_contracts/enums.py:MediumType` — same.
  `_MEDIUM = Literal[...]` in `config_payloads.py:31` duplicates the
  values inline.
- `static/js/grow/components/` — every `.mjs` file is imported by either
  `unit_detail.mjs`, `fleet.mjs`, `errors.mjs`, `settings.mjs`,
  `diagnostics-panel.mjs`, or `history-panel.mjs`. None are dead.

### Contract enum vs DB CHECK drift

| What                              | contracts                                                          | DB CHECK                                                   | UI                                                |
|-----------------------------------|--------------------------------------------------------------------|------------------------------------------------------------|----------------------------------------------------|
| Phase                             | `Phase` enum (5 values, `enums.py:25-31`) and `_PHASE` literal in `config_payloads.py:30` | `current_phase CHECK IN (...)` (5 values, `grow_schema.py:26-27`) | `PHASES = [...]` (5 values, profile-editor.mjs:17, fleet-filter-row.mjs:31, light-windows-editor.mjs:26) — consistent |
| Medium                            | `MediumType` enum + `_MEDIUM` literal (4 values)                   | `medium_type CHECK IN (...)` (4 values, `grow_schema.py:33`) | `MEDIUMS = [...]` profile-editor.mjs:18 — consistent |
| Severity                          | `Severity` enum (3 values) — UNUSED by production                  | `severity CHECK IN ('info','warning','critical')`           | hardcoded strings in error-row.mjs:23-27 — consistent |
| Channel                           | `Channel` enum (9 values)                                          | NO CHECK on `grow_unit_capabilities.channel`                 | `CHANNEL_DISPLAY` map (7 visible) in unit_detail.mjs:85-100 — consistent for the visible 7; AIR_HUMIDITY_PCT, AIR_TEMP_C present in CHANNEL_DISPLAY |
| EventKind                         | `EventKind` enum (11 values, includes `BUFFER_REPLAY_STARTED/COMPLETE` but **NOT** `BUFFER_EVICTION`) | NO CHECK on `grow_errors.kind` (free text)                  | error-row.mjs reads `row.kind` as free text — works |
| CommandName                       | `CommandName` enum (6 values: `IDENTIFY/WATER_NOW/LIGHT_OVERRIDE/SNAP_PHOTO/RELOAD_CONFIG/REBOOT`) | n/a                                                        | `clear_buffer` is sent by the server (api_grow_units.py:526) but is NOT in `CommandName`. Same drift as Flow 3 #3. |
| `_SAFETY_ACTION` literal          | 5 values in `config_payloads.py:148-152` (incl. `skip_next_soak`)  | n/a                                                        | safety-override.mjs uses 4 of them; `skip_next_soak` is a dead branch (Flow 3 #1) |
| Trigger (watering)                | n/a                                                                | `trigger CHECK IN ('pid','manual','identify_test')` (`grow_schema.py:124`) | `'pid'`/`'manual'` rendered in chart legend; `'identify_test'` is never produced (firmware never wires identify-with-pump) — third value is dead |
| `phase_set_by`                    | n/a                                                                | `phase_set_by CHECK IN ('user','image_classifier')`         | Server hardcodes `'user'` on PUT; `'image_classifier'` never set (no classifier exists yet) |

### Inconsistent defaults

- **Soak window**: contracts `WateringConfig.soak_window_min = 30` +
  firmware `PIDConfig.soak_window_min = 30` + every shipped profile
  in `_SHIPPED_PROFILES` = 30/45/60 + `app_settings.grow_default_soak_window_min = "30"`
  (unread). Three sources of truth, one of them dead. See Flow 4 #2.
- **Photo interval**: hardcoded `30` in `LoopConfig.photo_interval_min`
  (firmware) and contracts `ConfigPayload.photo_interval_min` (unused),
  schema `photo_interval_min_override` (unwritten). See Flow 4 #1.
- **Buffer retention**: firmware `_DEFAULT_BUFFER_RETENTION_DAYS = 7`
  (service.py:201) + `app_settings.grow_default_buffer_retention_days = "7"`
  (seeded but read only for *display* in DATABASE.md, unread by code).
  Per-unit override at `grow_units.buffer_retention_days` is the only
  authoritative path. The seeded app-setting is consistent with the
  firmware default but the cascade isn't actually walked.
- **Photo active hours**: firmware default `None` (24/7), contract
  default `None`, schema default both NULL. Consistent. UI editor
  (`photo-schedule-editor.mjs:30-31`) uses `PRELOAD_DEFAULT_START = 6` /
  `PRELOAD_DEFAULT_END = 22` as the *pre-fill* values when the user
  unchecks 24/7 — these were the OLD firmware defaults, now only used as
  the dropdown's initial selection. Good UX but worth knowing if the
  defaults ever change again.

### Empty-state / null branches

- `unit_detail.mjs:renderLiveReadings` (line 145) handles empty grid →
  empty-state placeholder. ✓
- `unit_detail.mjs:renderWateringHistoryPanel` wraps `renderSensorEventChart`
  in try/catch for Plotly-not-loaded (line 400-411). ✓
- `sensor-event-chart.mjs:24` checks `typeof Plotly === "undefined"`. ✓
- `grow-card.mjs:55-61` handles `last_photo_url == null`. ✓
- `moisture-history-chart.mjs:141-153` empty-state for empty range. ✓
- `photo-timelapse.mjs:119-127` empty state for no photos. ✓
- `connection-log.mjs:141-147` empty state for no connection events. ✓
- `sensor-sanity.mjs:74-79` empty state when sanity is empty. (Will
  trigger always, until Flow 1 #1 is fixed.)
- `buffer-inspector.mjs:82-89` handles null vs empty summary
  distinguishably. ✓
- `diagnostics-panel.mjs:54-79` handles fetch failure by mounting
  danger-zone but skipping the data-driven children. ✓

### Pre-Phase-1 design-doc fields that were never wired

- `grow_units.photo_interval_min_override` (Flow 4 #1).
- `grow_units.last_telemetry_at` written but never read (Flow 1 #2).
- `grow_photos.classified_phase` / `classifier_confidence` /
  `classified_at` (Flow 2 #2).
- `grow_watering_events.soil_pct_after_5min` (Flow 2 #3).
- `grow_units.phase_set_by = 'image_classifier'` — never set.
- `app_settings.grow_default_soak_window_min` seeded but unread.
- `Severity` enum in contracts.
- `MediumType` enum in contracts (dup of `_MEDIUM` literal).
- `PlantProfile` contract model.
- `ConfigPayload` + `CommandPayload` contract models.
- `grow_history.phase_changes: []` always-empty key (api_grow_history.py:122).
  Frontend reads it and overlays nothing — placeholder for the
  classifier annotation feature.
- `EventKind`: `STARTUP`, `SHUTDOWN`, `IDENTIFY_COMPLETE`, `CONFIG_APPLIED`,
  `SENSOR_RECOVERED` — no firmware producer.
- `Trigger`: `'identify_test'` enum value — dead.
- `_SAFETY_ACTION`: `'skip_next_soak'` action — set, never consumed
  (Flow 3 #1).
- `safety_override.consume_skip_next_soak` method — unused.
- `_build_reconnect_sync` (service.py) — only used by tests.
- `LocalBuffer.pop_all` — explicitly deprecated.

### Misc smaller observations

- `safety_loop.SafetyLoop._pid_state.pump_cooldown_until` is set on the
  hard-cap path but `state_persistence.PersistedState` only round-trips
  `error_integral`, `last_error`, `last_pulse_at_iso`. So a service
  restart inside the 5-minute cooldown loses the cooldown — the next
  tick would happily pulse the pump again. Defence-in-depth from the
  hard 30s cap still applies, but the cooldown is one-shot rather than
  durable. Worth adding `pump_cooldown_until_iso` to PersistedState.
- `WSClient._handle_buffer_eviction` stuffs the eviction-event into
  the buffer it's currently evicting from. The internal `_eviction_in_progress`
  guard prevents recursion (buffer.py:96-101), but the event goes onto
  the same buffer — by definition, when the buffer is full, this is the
  first row that gets dropped on the NEXT eviction cycle. The feature
  is best-effort by design.
- `api_grow_units.py:list_units` selects `last_buffer_size` but no other
  diagnostic fields. The fleet card uses `last_buffer_size` to render
  the 📦 N buffered badge (`grow-card.mjs:42-48`) — the only "diagnostic
  bleed" from the detail view into the fleet view. Consistent.
- `api_grow_dist.peek_enrollment_key` requires `admin` role, but
  `static/js/grow/fleet.mjs:_fetchEnrollmentKey` is called by every page
  load — viewers/controllers will get 403 and silently fall through to
  the "Already revealed" empty-state branch. Cosmetic. Could short-circuit
  the fetch if `body.dataset.role !== 'admin'`.

---

## Highest-leverage fixes (in order)

1. **Flow 1 #1 — emit `capabilities` from firmware.** Unblocks Live tile
   rendering, sensor-sanity, health pills, firmware_version, and
   `grow_unit_capabilities.last_seen_at` for sensors. This is the single
   biggest gap.
2. **Flow 6 #1 — add `BUFFER_EVICTION = "buffer_eviction"` to `EventKind`.**
   Small contract change; restores the SD-card-fill notification.
3. **Flow 4 #1 — drop `photo_interval_min_override` (or finish wiring).**
   Either way, eliminates a phantom override field.
4. **Flow 3 #1 — wire `override_state` into `SafetyLoop` or remove
   `skip_next_soak`.** Closes a dead admin action.
5. **Flow 1 #3 — bump `grow_unit_capabilities.last_seen_at` for sensor
   channels in `handle_telemetry`.** Makes sensor-sanity panel useful.
