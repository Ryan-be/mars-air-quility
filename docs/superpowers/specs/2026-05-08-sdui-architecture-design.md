# SDUI architecture — edge sense+effect platform

**Status:** Design
**Date:** 2026-05-08
**Branch:** `feature/plant-grow-units`
**Supersedes scope of:** Phase 4 polish item #6 ("Local read-only status UI on the grow unit") — that item expanded into this full architecture during brainstorming. The original Phase 4 #6 is fulfilled by sub-project ⑥ below.

---

## TL;DR

The grow-unit codebase has bespoke MLSS code for grow-specific UI: PID editor, light schedule editor, calibration wizard, photo timelapse, etc. As we add new edge unit types (weather station, carbon scrubber, hydroponics), we'd be writing the same kind of bespoke code per unit type. That doesn't scale.

This design replaces that with **Server-Driven UI (SDUI)** — edge units declare *what* their UI should look like (in JSON), MLSS owns the rendering library that turns those declarations into a consistent UI. Edge units own their logic + data + capabilities; MLSS owns the visual style + central data store + auth + audit log. New unit types ship without MLSS code changes for their UI.

The change is foundational and breaks down into nine sub-projects (⓪–⑧), each a coherent ship-able piece. ⓪ is a non-feature rename refactor that has to land first; ① reshapes the telemetry storage; ②–③ build the SDUI machinery; ④ ports the existing grow firmware to use it; ⑤ proves it with a second unit type; ⑥–⑧ are the user-facing payoff (local-on-Pi UI, tactical fallback control, auto-discovery polish).

---

## Goals

1. **Add a new edge unit type without changing MLSS code.** A "weather station" or "carbon scrubber" firmware emits a SDUI declaration; MLSS auto-renders. Adding the unit type is reflashing the Pi, not bumping MLSS.
2. **Keep the visual style consistent** across all unit types. MLSS owns the widget library; edge units pick from a finite vocabulary.
3. **Centralise the data store** but decentralise the logic. All telemetry/photos/actions still flow through MLSS for indexing + audit; the decisions about *what* to capture and *how* to control belong to the edge unit.
4. **Survive MLSS being unreachable** for a few hours (scope-B tactical fallback). Operator can still see the unit's data + invoke a small control surface via the Pi's local HTTP. Authoritative audit reconciles when MLSS comes back.
5. **Don't disturb the existing MLSS air-quality stack.** The hardwired sensors (BME680/SCD41/PM/etc.) keep their schema, dashboard, history page, inference engine — all untouched.

## Non-goals (explicit out-of-scope)

- A public ecosystem of third-party edge unit types. We build all unit types ourselves; consistency over openness.
- Real-time orchestration across edge units. Data fusion is rule-based + batch; not a streaming pipeline.
- General-purpose smart-home interoperability. We're not adopting full W3C WoT or Matter compliance — we steal concepts but keep our own JSON shape.
- Multi-tenant / multi-organisation MLSS. Single deployment, single household.

## Constraints (operator-stated)

- **Look and feel consistent** across all unit types — operator wants one product, not a toolbox.
- **Options/controls/data/plots vary** per unit type — that's the whole reason to do this.
- **Logic + declarations live on the edge** — adding a new unit type is reflashing a Pi, not editing MLSS.
- **Data stored centrally** on MLSS — no per-unit databases; one source of truth for queries + ML.

---

## Architectural overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Browser (UI)                               │
│  Loads widget library from MLSS, renders SDUI declarations from edges   │
└─────────────────────────────────────────────────────────────────────────┘
              │ HTTPS                                 ╲ HTTP (fallback,
              │ (normal path)                          ╲  when MLSS down)
              ▼                                         ▼ (any unit)
┌─────────────────────────────────────────────────────┐
│                         MLSS                        │
│ ┌─────────────────┐ ┌──────────────┐ ┌────────────┐ │
│ │ Widget library  │ │ Central data │ │ WS broker  │ │
│ │ + SDUI renderer │ │ store +      │ │ + ingest   │ │
│ │ (consistent     │ │ audit log    │ │ endpoints  │ │
│ │  look & feel)   │ │ + capability │ │            │ │
│ │                 │ │   registry   │ │            │ │
│ └─────────────────┘ └──────────────┘ └────────────┘ │
│ ┌─────────────────────────────────────────────────┐ │
│ │             Data fusion layer                   │ │
│ │  - rain forecast → throttle grow watering       │ │
│ │  - cross-grow anomaly detection                 │ │
│ │  - external temp + grow temp → leak detection   │ │
│ └─────────────────────────────────────────────────┘ │
│           ▼ pushes config_changed to relevant units │
│ ┌─────────────────────────────────────────────────┐ │
│ │ JWT signing key  + pinned TLS cert + Github OAuth│ │
│ └─────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
                ▲                  ▲                 ▲
                │ WS               │ WS              │ WS
                │ (telemetry +     │                 │
                │  events +        │                 │
                │  photos +        │                 │
                │  capabilities)   │                 │
                │                  │                 │
       ┌────────┴────┐    ┌────────┴────┐    ┌───────┴────────────┐
       │ Grow Unit   │    │ Grow Unit   │    │  Weather Station   │
       │ (Pi Zero)   │    │ (Pi Zero)   │    │  (Pi / micro)      │
       │             │    │             │    │                    │
       │ camera      │    │ camera      │    │ wind, rain,        │
       │ soil sensor │    │ soil sensor │    │ temp, humidity     │
       │ pump, light │    │ pump, light │    │   (no actuators)   │
       │             │    │             │    │                    │
       │ SDUI decl + │    │ SDUI decl + │    │ SDUI decl +        │
       │ edge logic  │    │ edge logic  │    │ edge logic         │
       │ + local UI  │    │ + local UI  │    │ + local UI         │
       │ + cached    │    │ + cached    │    │ + cached widget    │
       │ widget lib  │    │ widget lib  │    │ lib                │
       │ + pinned    │    │ + pinned    │    │ + pinned MLSS pub  │
       │   MLSS pub  │    │   MLSS pub  │    │                    │
       └─────────────┘    └─────────────┘    └────────────────────┘
```

### Component responsibilities

**MLSS (the central server) owns:**
- The widget library + SDUI renderer (one source of visual truth)
- Central data store: `unit_telemetry` (schemaless, edge units only), `unit_photos`, `unit_audit_log`
- Capability registry (what unit types exist, what capabilities each declared)
- Auth: GitHub OAuth for users, signed JWT issuance for browser↔Pi access, Argon2 bearer tokens for unit↔MLSS auth (existing)
- Data fusion rule engine — JSON-declared rules read from telemetry, push config_changed
- WS broker, ingest endpoints, the existing audit log infrastructure

**Each edge unit owns:**
- Its hardware + the actual control logic (PID, schedule, sensor reads, photo capture)
- Its SDUI declaration: capabilities + control surface + plot bindings (pure JSON, emitted at boot)
- A cached copy of MLSS's widget bundle (refreshed at install + every reconnect) so it can render its own UI when MLSS is down
- A local action queue for tactical-fallback control (scope B) that replays to MLSS audit when reconnected

**Unchanged from today** (deliberately not in scope):
- All MLSS-hardwired sensors (`sensor_data` and friends): BME680, SCD41, PM, gas, etc. Existing dashboard, history, incidents, attribution, inference engine — all stay.

---

## Core protocol — boot frame (extends existing `capabilities` type)

Every WS frame uses the existing `{type, ts, payload}` envelope. The boot frame is the existing `type: "capabilities"` shape with new fields. Bundling means **the capabilities + UI declaration are atomic** — either both land or neither, no half-registered state.

```jsonc
{
  "type": "capabilities",
  "ts": "2026-05-08T12:00:00Z",
  "payload": {
    // ── identity ──────────────────────────────────────
    "unit_type": "grow",                     // NEW — 'grow' | 'weather' | 'scrubber' | …
    "firmware_version": "1.0.0",             // existing
    "hardware_serial": "abc123",             // existing
    "uptime_s": 0.0,                         // existing
    "widget_vocabulary_version": "1.0.0",    // NEW — semver; MLSS validates compat

    // ── capabilities (extended with metadata) ─────────
    "capabilities": [
      {
        "channel": "soil_moisture",
        "data_type": "numeric",              // numeric | boolean | text | image_url
        "unit_label": "raw",
        "semantic_class": "soil_moisture",   // optional, drives default rendering hints
        "value_range": {"min": 200, "max": 2000},
        "observable": true,                  // can be subscribed for live updates
        "writable": false,                   // sensors false; actuators true
        "is_required": true,                 // existing
        "hardware": "Adafruit_Seesaw",       // existing
        "details": {"i2c_address": "0x36"},  // existing
        "health": "connected"                // existing
      },
      // ... one per channel
    ],

    // ── ui_declaration (NEW — the SDUI bundle) ────────
    "ui_declaration": {
      "version": 1,
      "screens": {
        "live":      { /* widget tree, see "SDUI declaration shape" */ },
        "history":   { /* … */ },
        "configure": { /* … */ },
        "diagnostics":{ /* … */ }
      },
      "actions": [ /* declared actions; see "Action invocation" */ ]
    }
  }
}
```

### Server-side handling

`handle_capabilities` extends to:
1. Open a transaction.
2. UPSERT `units(unit_type, name, firmware_version, hardware_serial, …)`.
3. DELETE+INSERT `unit_capabilities(unit_id, channel, data_type, unit_label, semantic_class, …)` (existing pattern).
4. UPSERT `unit_ui_declarations(unit_id, version, declaration_json)`.
5. UPSERT `unit_types(unit_type, last_declaration_json, first_seen_at, last_seen_at)` — idempotent type registration.
6. Commit.

Atomic — a crash mid-write rolls back; the unit's previous boot frame state remains visible until the next clean boot.

---

## Storage — schemaless edge telemetry + wide views

### Wide schema (existing) stays for MLSS-attached sensors

Untouched. `sensor_data`, `hot_tier`, `inferences`, `incidents`, `attribution_*` — all of these continue exactly as today. The MLSS dashboard, History page, inferences page, charts — none of these change.

### New schemaless table for edge unit telemetry

```sql
CREATE TABLE unit_telemetry (
  unit_id        INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
  timestamp_utc  DATETIME NOT NULL,
  channel_key    TEXT    NOT NULL,
  value_num      REAL,                              -- numeric + boolean (0/1) values
  value_text     TEXT,                              -- text + image_url values
  PRIMARY KEY (unit_id, channel_key, timestamp_utc)
);

CREATE INDEX idx_unit_telemetry_unit_time
  ON unit_telemetry(unit_id, timestamp_utc DESC);

CREATE INDEX idx_unit_telemetry_channel_time
  ON unit_telemetry(channel_key, timestamp_utc DESC);
```

Each tick produces N rows (one per channel that reported). Cross-unit per-channel queries (e.g., "all `rain_mm` readings, last 24h") are a single B-tree seek + range scan — actually faster than the wide-table equivalent.

### Auto-generated wide views per unit type

For ML training + bespoke per-unit-type queries, MLSS auto-maintains a wide view per registered unit type, regenerated whenever a unit type's declared channels change:

```sql
-- Auto-generated when unit_type='grow' is registered
CREATE VIEW grow_telemetry_wide AS
SELECT
  unit_id,
  timestamp_utc,
  MAX(CASE WHEN channel_key='soil_moisture'      THEN value_num END) AS soil_moisture_raw,
  MAX(CASE WHEN channel_key='soil_moisture_pct'  THEN value_num END) AS soil_moisture_pct,
  MAX(CASE WHEN channel_key='soil_temp_c'        THEN value_num END) AS soil_temp_c,
  MAX(CASE WHEN channel_key='ambient_lux'        THEN value_num END) AS ambient_lux,
  MAX(CASE WHEN channel_key='light_state'        THEN value_num END) AS light_state,
  MAX(CASE WHEN channel_key='pump_state'         THEN value_num END) AS pump_state
FROM unit_telemetry
WHERE unit_id IN (SELECT id FROM units WHERE unit_type='grow')
GROUP BY unit_id, timestamp_utc;
```

ML training queries hit `grow_telemetry_wide` and get wide rows for free. Storage cost: zero — views are query rewrites, not duplicated data. Performance is sufficient at 10-unit scale; can promote to materialised (trigger-maintained) per-unit-type if needed later.

### Cross-domain queries

A fusion rule can join across the two domains:

```sql
-- "Recent indoor humidity (MLSS sensor) + recent grow soil_moisture readings"
SELECT s.timestamp, s.humidity_pct, t.value_num AS soil_moisture
FROM sensor_data s
JOIN unit_telemetry t
  ON t.timestamp_utc BETWEEN datetime(s.timestamp, '-30 seconds')
                          AND datetime(s.timestamp, '+30 seconds')
WHERE t.channel_key='soil_moisture_pct'
  AND s.timestamp > datetime('now', '-1 hour');
```

The two storage shapes coexist; queries that need both worlds JOIN them at query time. Documented as part of the fusion-rule authoring guide.

---

## Widget vocabulary v1

Twenty-nine widget types in v1. Each ships as a registered renderer in MLSS's widget library. Each is independently versioned within `widget_vocabulary_version` (semver — see Versioning).

### Input / control widgets (15)

| Widget | Renders as | Used by (today) |
|---|---|---|
| `number-field` | Bounded numeric input | PID kp/ki/kd/target |
| `range-slider` | Slider with min/max/step | Fan speed (scrubber, future) |
| `toggle` | On/off switch | Light override |
| `time-of-day` | HH:MM picker | Photo schedule, light windows |
| `date-field` | Date picker | sown_at |
| `select` | Single-choice dropdown | plant_type, current_phase, medium_type |
| `multi-select` | Chip-picker, multi-value | Roles, alerts subscriptions |
| `text-field` | Short string input | Unit name, label |
| `textarea` | Multi-line string | Description, journal entry |
| `image-upload` | File-picker for image | Reference photos, ID stickers |
| `interval-field` | Duration picker | Photo cadence, soak window |
| `color-picker` | RGB/HSL picker | Addressable grow lights (future) |
| `confirm-button` | Single-action button + confirm modal | Identify, Snap photo |
| `danger-action` | Red button + type-to-confirm | Decommission, Clear photos |
| `multistep-wizard` | Multi-step flow | Calibration wizard |

### Display widgets (7)

| Widget | Renders as | Used by (today) |
|---|---|---|
| `gauge` | Value + min/max bounds visual | Current readings |
| `stat-tile` | Single value + label | Live readings tile grid |
| `chart` | Plotly chart (kind: line / scatter / bar / area / heatmap / multi-axis) | Watering history, future cross-channel |
| `schedule-bar` | 24h horizontal bar with NOW marker | Light schedule |
| `photo-timelapse` | Scrubber + autoplay over photos | History tab |
| `image-display` | Static or URL-bound image | Latest photo, status visuals |
| `status-pill` | Small coloured pill (NOMINAL / CAUTION / OFFLINE) | Status |

### Layout widgets (4)

| Widget | Renders as |
|---|---|
| `section` | Titled vertical container with optional collapsible behaviour |
| `grid` | CSS grid container with column count |
| `tabs` | Tab navigation (mounts one screen at a time) |
| `accordion` | Per-phase collapsible (used by light-windows-editor) |

### Diagnostic display widgets (3)

| Widget | Renders as |
|---|---|
| `connection-log-table` | The Diagnostics tab connection log |
| `error-row` | Single error entry on /errors or in the Diagnostics open-errors block |
| `buffer-inspector` | Buffered-message + photo-buffer summary panel |

### Why not "custom widget escape hatch"

Decision locked: strict `kind` enum on the chart widget; no `kind: "custom"` with raw Plotly traces. Adding a new chart shape (wind rose, radar, 3D) requires a minor-version bump to the widget vocabulary + a MLSS update. This trades some edge flexibility for consistency guarantees — a registered widget always renders the same way regardless of which unit declares it.

---

## SDUI declaration shape

Each unit declares one SDUI tree per "screen" (live / history / configure / diagnostics). The tree is a recursive structure of widget nodes. Each node has a `type` (one of the v1 widgets), zero or more child nodes (for layout widgets), and props specific to that widget type.

### Example — grow unit's `live` screen

```jsonc
{
  "type": "section",
  "title": "Live readings",
  "children": [
    {
      "type": "grid",
      "columns": 3,
      "children": [
        {"type": "stat-tile", "channel": "soil_moisture_pct", "label": "Moisture",
         "format": "{value}%", "warn_below": 35},
        {"type": "stat-tile", "channel": "soil_temp_c", "label": "Soil temp",
         "format": "{value}°C"},
        {"type": "stat-tile", "channel": "ambient_lux", "label": "Ambient lux"}
      ]
    },
    {"type": "image-display", "src_channel": "latest_photo_url",
     "fallback": "No photo yet"},
    {"type": "schedule-bar", "windows_channel": "light_windows",
     "now_channel": "_clock"},
    {
      "type": "section",
      "title": "Quick controls",
      "children": [
        {"type": "confirm-button", "label": "⚡ Identify",
         "action": {"name": "identify", "args": {"duration_s": 10}}},
        {"type": "confirm-button", "label": "💧 Water 5s",
         "action": {"name": "water_now", "args": {"duration_s": 5}}},
        {"type": "confirm-button", "label": "💡 Toggle light",
         "action": {"name": "light_toggle"}},
        {"type": "confirm-button", "label": "📷 Snap photo",
         "action": {"name": "snap_photo"}}
      ]
    }
  ]
}
```

### Example — weather station's `live` screen

```jsonc
{
  "type": "section",
  "title": "Current conditions",
  "children": [
    {
      "type": "grid",
      "columns": 4,
      "children": [
        {"type": "gauge", "channel": "wind_speed_ms", "label": "Wind",
         "min": 0, "max": 50, "format": "{value} m/s"},
        {"type": "gauge", "channel": "rain_mm", "label": "Rain",
         "min": 0, "max": 100, "format": "{value} mm"},
        {"type": "gauge", "channel": "outdoor_temp_c", "label": "Temp",
         "min": -20, "max": 50, "format": "{value}°C"},
        {"type": "gauge", "channel": "outdoor_humidity_pct", "label": "Humidity",
         "min": 0, "max": 100, "format": "{value}%"}
      ]
    },
    {"type": "chart", "kind": "line",
     "x": {"channel": "_time"},
     "y": [{"channel": "wind_speed_ms"}, {"channel": "outdoor_temp_c"}],
     "title": "Wind + temperature, last 24h", "range": "24h"}
  ]
}
```

The renderer turns these declarations into DOM. Widget props referencing channels (`channel: "soil_moisture_pct"`) bind to the latest value from `unit_telemetry` for that unit. Channels prefixed with `_` are renderer-provided pseudo-channels (`_time`, `_clock`).

### How declarations get stored + cached

- **On boot**: unit emits the bundled boot frame, server stores into `unit_ui_declarations(unit_id, version, declaration_json, received_at)`.
- **On render**: `GET /api/units/<id>/ui` returns the declaration (cacheable).
- **On declaration change**: unit re-emits boot frame on next reconnect; server UPSERT.
- **Pi-served fallback**: the Pi serves the same declaration from its local cache + the cached MLSS widget bundle, both fetched at install time + refreshed on every WS reconnect.

---

## Action invocation

Generic endpoint replaces all bespoke per-unit-type action endpoints today.

### Server-side

```
POST /api/units/<id>/actions/<action_name>
Headers: Authorization: Bearer <mlss-signed-jwt>
Body: {"duration_s": 5}              // matches the declared args_schema
```

Server flow:
1. Validate signed JWT, extract user_id + role + units claim.
2. Look up `units` row by `id`; return 404 if soft-deleted or missing.
3. Look up the declared action in `unit_capabilities` (or in the cached UI declaration). Return 400 if not declared by this unit.
4. Check `role` against the action's declared `rbac` list. Return 403 on mismatch.
5. Validate body against the declared `args_schema` (JSON Schema). Return 400 with detail on validation failure.
6. Forward to firmware via WS as `{type: "command", payload: {name: "<action_name>", args: <body>}}`.
7. Wait for the firmware's ack (existing pattern); return 202 + `{queued: true}` on success, 503 if the unit is disconnected, 504 on timeout.
8. Write a row to `unit_audit_log` with user, role, action, args, timestamp, JWT signature for non-repudiation.

### How an action is declared (in the boot frame)

```jsonc
"actions": [
  {
    "name": "water_now",
    "label": "Water now",
    "args_schema": {
      "type": "object",
      "properties": {
        "duration_s": {"type": "integer", "minimum": 1, "maximum": 30}
      },
      "required": ["duration_s"]
    },
    "args_default": {"duration_s": 5},
    "rbac": ["controller", "admin"],
    "intent": "actuator-pulse",         // optional hint for the renderer
    "confirmation": "single-modal"      // 'single-modal' | 'type-label' | 'three-click-fsm'
  },
  // ... one per action
]
```

### Browser-side

A `confirm-button` widget references `{action: {name: "water_now", args: {...}}}`. Clicking opens the appropriate confirmation flow (per `confirmation` hint), then POSTs the body.

### Why one endpoint, not per-action

- Adding a new action to a unit type = adding an entry to its `actions` array. Zero MLSS code change.
- The action's behaviour (what it does on the firmware) is implemented in firmware code; MLSS just routes.
- RBAC + arg validation + audit log are all centralised in the one endpoint.

### Migration of existing per-unit-type endpoints

Existing endpoints (`POST /api/grow/units/<id>/water-now`, etc.) become thin shims that call the generic endpoint internally. Deprecation cycle: shim for one minor version, drop in the version after. Firmware doesn't notice — it never sees these endpoints (it receives WS commands).

---

## Auth model — MLSS-signed JWT

Three identity layers, each with a distinct mechanism:

| Identity | Authenticated via | Lifetime |
|---|---|---|
| **Unit** (firmware → MLSS) | Argon2-hashed bearer token, minted at enrolment | Until rotated |
| **User** (browser → MLSS) | GitHub OAuth → Flask session cookie | Until logout |
| **User** (browser → Pi, fallback) | MLSS-signed JWT, presented in `Authorization: Bearer …` | ~1h, refreshed in background |

### JWT issuance + refresh

When a user logs into MLSS, the session middleware silently issues a short-lived signed JWT. Browser caches it (cookie or localStorage). A `setInterval` in the SPA refreshes it every ~50 minutes via `GET /api/auth/refresh`. Refresh endpoint validates the session and returns a fresh JWT.

JWT payload:

```jsonc
{
  "iss": "mlss",
  "sub": "ryan-be",
  "role": "admin",
  "iat": 1715180000,
  "exp": 1715183600,                          // 1h
  "units": ["*"]                              // or list of allowed unit_ids
}
```

Signed with Ed25519 (small, fast, modern). Claims match what MLSS-side endpoints already check (`role` for RBAC, `sub` for audit-log attribution).

### Pi-side verification

At enrolment, the Pi pins MLSS's signing public key (`/etc/mlss/signing.pub`, mode 0644) — alongside the existing TLS cert pin. When a request arrives at the Pi's local UI:

1. Extract `Authorization: Bearer <jwt>`.
2. Verify signature using the pinned public key.
3. Check `exp` against the Pi's local clock (NTP-synced; if drift >5min, refuse).
4. Check `role` against the requested action's `rbac` list.
5. Allow or deny.

No network call to MLSS for auth — Pi works fully offline against a not-yet-expired JWT.

### Key rotation

Procedure:
1. MLSS generates new keypair.
2. MLSS pushes new public key to all connected units via a new WS command (`{type: "command", payload: {kind: "rotate_signing_key", new_pubkey: "..."}}`).
3. Units add the new key to a multi-key list (old + new both valid for verification).
4. MLSS starts signing new JWTs with the new key.
5. Old JWTs continue to verify against the old key until they expire (max 1h after rotation).
6. After 1h, MLSS sends a follow-up command to remove the old public key.

Compromised key: same flow, but step 5's grace period drops to "immediate" (force-revoke all old JWTs by removing the old public key from all Pis right away). Operator may need to log users out of MLSS too, depending on whether the compromise affects the Flask session signing.

### MVP simplification

For v1: reuse the pinned MLSS TLS keypair as the JWT signing key. Single keypair, single rotation procedure. Document that we may split later for security hygiene (TLS server-auth and JWT-signing have different lifecycles; sharing is OK for now but not best practice long-term).

### What about scope-A diagnostic-only?

Even read-only operator access to the Pi's local UI requires a valid JWT. Reasoning: telemetry can include sensitive data (location, schedule of when the operator is away — the holiday-mode flag is queryable). LAN-trust isn't enough; we want defence-in-depth. Cost: zero (the JWT is already in the browser from MLSS login).

---

## Versioning — semver, major/minor strict, patch lenient

```
widget_vocabulary_version: "1.4.2"
                            │ │ └── patch — backwards-compatible (renderer bug fixes,
                            │ │       additive optional props on existing widgets)
                            │ └──── minor — NOT backwards-compatible (new widget types,
                            │       new mandatory props). MLSS at older y can render
                            │       declarations using only widgets it knows; can warn
                            │       about unknown widgets (placeholder cards).
                            └────── major — breaking (declaration shape change,
                                    removed widgets). Hard refuse to render.
```

### Validation on boot-frame receipt

```python
def validate_widget_vocab_compat(unit_version: SemVer, mlss_version: SemVer) -> str:
    if unit_version.major != mlss_version.major:
        return "incompatible_major"          # hard refuse
    if unit_version.minor > mlss_version.minor:
        return "unit_ahead"                  # warn + best-effort render
    if unit_version.minor < mlss_version.minor:
        return "compatible_unit_behind"      # render normally
    return "exact_match"                     # render normally
```

UI behaviour for each outcome:
- `exact_match`, `compatible_unit_behind` → render normally
- `unit_ahead` → render + show a small banner: "Unit firmware is ahead of MLSS by minor version. Some widgets may render as placeholders. Update MLSS for full UI."
- `incompatible_major` → don't render; show "Unit firmware is incompatible with this MLSS. Update MLSS or downgrade unit firmware."

### Bundle-shipping cadence

The Pi pulls the renderer bundle on every successful WS reconnect. ETag / If-Modified-Since for efficiency (304 Not Modified when unchanged). After an MLSS upgrade, all Pis converge on the new bundle within minutes.

### Initial cut

v1.0.0 ships with the 29 widget types listed above. Subsequent v1.x.0 minor versions add new widget types (e.g., `wind-rose` for the weather station, `radar-chart` for some future unit). v2.0.0 happens when we change the declaration shape itself — far in the future, deliberately rare.

---

## Tactical fallback control (scope B)

When MLSS is reachable, all routing goes through MLSS — browser → MLSS → WS → unit. When MLSS is unreachable, the operator can hit the Pi directly to view data + invoke a small set of actions.

### Pi-side queue

```sql
-- on the Pi, in /var/lib/mlss-grow/pending_audit.sqlite
CREATE TABLE pending_audit (
  id              INTEGER PRIMARY KEY,
  ts              DATETIME NOT NULL,
  user            TEXT NOT NULL,
  role            TEXT NOT NULL,
  action_name     TEXT NOT NULL,
  args_json       TEXT NOT NULL,
  result          TEXT,                     -- 'ok' | 'failed' | error message
  jwt_signature   TEXT NOT NULL             -- non-repudiation
);
```

When a user invokes an action via the Pi's local UI:
1. Pi verifies JWT signature against pinned public key (as in Auth model).
2. Pi looks up declared action (in its cached SDUI declaration).
3. Pi enforces RBAC.
4. Pi executes the action locally (through the same firmware path the WS command would).
5. Pi writes a row to `pending_audit`.
6. Pi returns success/failure to the browser.

### Replay on reconnect

On WS reconnect, before resuming normal operation:
1. Pi reads all rows from `pending_audit` ordered by `ts ASC`.
2. For each row, sends `{type: "event", ts: <original ts>, payload: {kind: "tactical_action_replayed", details: {...}}}`.
3. MLSS's `handle_event` writes a row to the central audit log with `severity: "info"` + the original action context preserved (user, JWT signature, args, original timestamp).
4. Pi deletes replayed rows.
5. Replay completes; resume normal operation.

This means MLSS's audit log captures every tactical-fallback action eventually, even if it landed during an outage. Non-repudiation: the JWT signature in the audit row proves the action was authorised by an MLSS-signed token.

### Scope of action set in fallback mode

Same action set as normal mode — no special restriction. The user has the same RBAC role they had when MLSS was up; the JWT carries it; the Pi enforces it.

### Caveats documented for the operator

- JWT expiry is the limiting factor — if MLSS is down for more than ~1h, the user can't refresh the JWT. The operator gets a clear "session expired, MLSS unreachable" message. This is acceptable: MLSS-down for hours is meant to be tactical; if it's down for days, that's a different operational problem.
- Tactical actions don't show up in MLSS's audit log until reconnect. Documented; visually surfaced in the diagnostics tab of the unit detail page once we have it.
- Two operators acting on the same unit during a fallback (one via MLSS, one direct to Pi) is impossible because if MLSS is reachable the local-on-Pi UI redirects to MLSS for that unit. (More on this in Open Questions.)

---

## Data fusion — JSON-declared rule engine

Rules live as files in `data/fusion_rules/` (or in a `fusion_rules` DB table — see Open Questions). Each rule is a JSON object:

```jsonc
{
  "name": "rain_throttles_grow_watering",
  "description": "When the weather station sees rain, reduce target_pct on co-located grow units for the next 6h.",
  "trigger": {
    "kind": "telemetry",
    "channel": "rain_mm",
    "where": {"unit_type": "weather"},
    "condition": "value > 5"           // simple expression DSL; see below
  },
  "effect": {
    "kind": "config_override",
    "target": {"unit_type": "grow", "roles_includes": "co_located_with_weather_1"},
    "override": {"watering_target_override_pct": -15},   // delta or absolute
    "ttl_minutes": 360
  }
}
```

### Trigger evaluation

The fusion engine subscribes to incoming telemetry. For each frame:
1. Find rules whose `trigger.channel` matches.
2. Evaluate `trigger.condition` against the new value (simple DSL: `>`, `<`, `==`, `!=`, `between`, `in`, `not in`, with optional `over_window: "5m"` for rolling-window checks).
3. If true, apply the `effect` to the matching units.

### Effect types

- **`config_override`** — write into `units.*_override` columns with TTL; push `config_changed` to affected units. Existing apply_config flow handles the rest.
- **`alert`** — insert into `unit_audit_log` with `severity: "warning"` + a description.
- **`tag`** — add a role to the target units (auto-assigned, distinguishable from operator-assigned by `assigned_by: "fusion:<rule_name>"`).

### Authoring + management

- v1: rules in JSON files, hand-edited, hot-reloaded via a watcher.
- v2 (out of scope for this design): MLSS Settings → Fusion Rules editor that renders a JSON-Schema form against the rule shape (uses the same SDUI machinery — meta-recursive!).

---

## Sub-project decomposition

The full vision is too big for a single implementation plan. Each sub-project below ships independently and is a coherent piece of work in itself.

### ⓪ MLSS becomes unit-type-agnostic

**Goal**: rename + generalise plumbing so MLSS isn't grow-specific in tables, routes, templates, or JS modules.

**Changes**:
- DB tables: `grow_units` → `units`, `grow_unit_capabilities` → `unit_capabilities`, `grow_telemetry` (refactored in ①), `grow_photos` → `unit_photos`, `grow_errors` → `unit_errors`, `grow_watering_events` stays grow-specific (it's an action-log, fine to keep typed)
- Add `unit_type` column to `units`, default `'grow'` for existing rows
- Add `unit_roles` table (many-to-many tags)
- Add `unit_types` registry table
- Add `unit_audit_log` table — separate from `unit_errors` because audit entries (action invocations) have different fields than alerts (severity / kind / resolution). Schema: `id, ts, unit_id, user, role, action_name, args_json, result, jwt_signature, source` where `source ∈ {"mlss", "pi-fallback-replay"}`.
- Routes: `/api/grow/units/<id>` + alias `/api/units/<id>` (the alias is the new path; the grow path is the deprecated alias)
- Templates: `grow_unit_detail.html` → `unit_detail.html` (with redirect from old)
- JS modules: rename module names + selectors that include `grow-` prefix
- Fleet view: group by `unit_type` (collapsible sections); role filter chips
- "+ Add Unit" wizard: type picker (only `grow` available initially)
- Settings → Plant profiles: only renders for units with `unit_type='grow'`

**Visible behaviour**: zero change. Same product as today, just generalised plumbing. Internal-only refactor.

**Test count estimate**: ~50 new + ~20 modified tests. Big diff but mechanical.

### ① Telemetry storage refactor

**Goal**: schemaless `unit_telemetry` table replaces wide `grow_telemetry`. Auto-generated wide views per unit type.

**Changes**:
- Create `unit_telemetry` (schemaless event store, edge-only)
- Migrate existing `grow_telemetry` rows: each row becomes 9 rows (one per channel), preserving timestamps + values
- Drop `grow_telemetry` after migration
- Generate `grow_telemetry_wide` view; document the auto-generation pattern
- Adapt all readers (`api_grow_units::_last_known_state`, `api_grow_history::_maybe_downsample`, etc.) to read from view
- Update `handle_telemetry` to insert N rows instead of 1 wide row
- Bench: confirm query performance is acceptable at current data volume + 10×

**Visible behaviour**: zero change. Same data, different storage.

### ② Widget vocabulary + SDUI renderer (MLSS-side)

**Goal**: ship the v1 widget library + a renderer that takes a SDUI declaration and turns it into DOM.

**Changes**:
- New library `static/js/sdui/` containing the renderer + each widget type as a Custom Element
- Port existing 5 grow Web Components (`schedule-bar`, `sensor-event-chart`, `photo-timelapse`, `calibration-wizard`, `safety-override`) as registered widgets in the vocabulary
- Build the new ones (chart with kind variants, gauge, image-display, image-upload, multi-select, interval-field, color-picker)
- Renderer: walks the declaration tree, instantiates Custom Elements, binds props from a context object (channel data + action handlers)
- Versioning: declared `widget_vocabulary_version` constant; compat-validate on receipt of boot frame

**Visible behaviour**: nothing yet — the renderer has nothing to render. Sub-project ④ wires it up.

### ③ SDUI declaration protocol

**Goal**: formalise the JSON shape of `ui_declaration` + `actions` blocks. Pydantic schemas. Server-side validation on boot-frame receipt.

**Changes**:
- New `mlss_contracts.sdui` module with pydantic models for: declaration tree, widget node, action descriptor, args schema (using JSON Schema as embedded type)
- Server-side `handle_capabilities` validates declarations against schema; rejects malformed boot frames with a clear log
- Versioning: declarations carry `version: 1` at the root; we bump if the protocol shape itself changes (separate from `widget_vocabulary_version`)

**Visible behaviour**: nothing yet.

### ④ Port grow firmware to emit SDUI declarations

**Goal**: existing grow unit boots emit the bundled boot frame including a complete `ui_declaration` for the grow live/history/configure/diagnostics screens. MLSS renders the page from the declaration instead of the bespoke Jinja template.

**Changes**:
- New module `mlss_grow.ui_declaration` builds the declaration object once at boot from the unit's hardware capabilities
- Existing `service.py::_run_main_loop` includes the declaration in the capabilities frame
- MLSS unit-detail page becomes a thin shell that fetches the declaration + lets the renderer take over
- All bespoke grow JS (`unit_detail.mjs`, `renderLiveContent`, etc.) deletes — replaced by the renderer

**Visible behaviour**: grow unit detail page looks identical to today. Code-wise: every screen now goes through the renderer.

**Test plan**: golden-image-style — render every screen with the declaration, snapshot the DOM, assert it matches the bespoke version. Then delete the bespoke version once snapshots are stable.

### ⑤ First non-grow unit type

**Goal**: a weather station or carbon scrubber as the second SDUI consumer, proving the protocol generalises.

**Changes**:
- New firmware package (e.g. `mlss_weather` or `mlss_scrubber`) — minimal, mostly its own sensor reading + emit code, plus a `ui_declaration.py` that builds the declaration
- Pi image stage for the new type (same stage-mlss-grow pattern from Phase 4 #3)
- Maybe one new widget type if the unit has visualisation needs that aren't covered (`wind-rose` for weather)
- MLSS Settings → Plant profiles only renders for grow units (gated on `unit_type='grow'`)

**Visible behaviour**: enrolling the second unit type → it appears in the fleet view, has its own detail page rendered from its declaration, sends telemetry into `unit_telemetry`, no MLSS code change beyond optional new widget.

### ⑥ Local-on-Pi UI fallback

**Goal**: each unit serves its own SDUI declaration + renderer bundle on its local HTTP. Reachable via `http://<pi-ip>:8080/`. Browser uses an MLSS-signed JWT for auth.

**Changes**:
- Tiny Flask app on the Pi listening on port 8080
- Routes: `GET /` (HTML shell), `GET /api/ui` (the unit's SDUI declaration), `GET /widget-bundle/<version>.js` (cached MLSS renderer), `GET /api/telemetry/<channel>?range=...` (read-only data)
- JWT verification middleware
- Periodic refresh of the cached renderer bundle on each WS reconnect

**Visible behaviour**: when MLSS is up, the unit detail page works identically. When MLSS is down, navigating to the Pi's IP shows the same unit detail page (read-only). New mDNS-friendly URL patterns documented.

### ⑦ Tactical fallback control (scope B)

**Goal**: when MLSS is down, the Pi's local UI accepts actions, executes them locally, queues for replay.

**Changes**:
- Pi: `pending_audit.sqlite` table + write-on-action + replay-on-reconnect logic
- Pi: `POST /api/actions/<name>` endpoint that mirrors the MLSS-side validation flow (JWT, RBAC, schema, execute)
- MLSS: `handle_event` extended for `kind: "tactical_action_replayed"` — writes a row to `unit_audit_log` with `source: "pi-fallback-replay"` so the audit trail clearly distinguishes online from fallback actions
- UI: a small banner on the local-served fallback page indicating "MLSS unreachable — actions queued for replay"

**Visible behaviour**: operator can water-now / toggle-light from the Pi-direct URL when MLSS is down. Audit log captures it post-hoc.

### ⑧ Auto-registration + fleet polish

**Goal**: Settings → Units page that auto-discovers all known unit types from `unit_types` table; per-unit-type customisation surfaces (assign default roles, view all units of a type, common settings).

**Changes**:
- Settings → Units page — auto-built grouped list
- Per-unit-type customisation lives in unit-type configs (driven by metadata in the unit type's declaration)
- Cross-unit-type fleet views (e.g., "all alarming units across all types")

**Visible behaviour**: gives the operator clean tools for managing a multi-unit-type fleet. Polish on top of the working multi-unit-type system.

---

## Migration plan

**⓪ → ① → ② → ③** is the foundation chain, in order. None of these change user-visible behaviour; together they replace the plumbing.

**④** is the proof: existing grow unit feels identical to today, but every screen now goes through the renderer.

**⑤** is the second proof: a non-grow unit type exists.

**⑥, ⑦** are the user-facing payoff for tactical fallback.

**⑧** is polish.

Each sub-project can be paused indefinitely at its boundary without leaving the system in a broken state. ① + ② + ③ are heavy refactors that don't change the user-visible product; if we get to ④ and decide we don't like it, we can revert ④ alone and keep the cleaner plumbing from ①–③.

### Backwards compatibility for existing grow firmware

During the rollout window:
- ⓪ ships → existing firmware sees the new `/api/units/<id>/...` paths via aliases from the `/api/grow/units/<id>/...` paths
- ① ships → existing firmware emits the same telemetry frame; server-side handler writes to the new schemaless table; reads continue working through wide views
- ② + ③ ship → no firmware change
- ④ ships → firmware now emits the SDUI declaration in its boot frame. MLSS reads + renders. Old firmware (without the declaration) gets the bespoke fallback templates for one minor version, then we drop those.

### Database migrations

Each sub-project's DB changes go in `database/init_db.py`'s migration list (existing pattern: `try: cur.execute(...) except: pass`). The sequence:
- ⓪: rename grow_* tables, add unit_type/unit_roles/unit_types
- ①: create `unit_telemetry`, copy data, drop `grow_telemetry`
- (others as needed)

Migrations are idempotent so a partially-deployed environment converges.

---

## Risks

### High

- **Sub-project ④ (port grow firmware) is the make-or-break.** If the renderer can't faithfully reproduce the existing grow UI, this design fails. Mitigation: do a renderer prototype with the most exotic existing widget (calibration-wizard or photo-timelapse) early in ②/③ to prove the harder cases work. If the prototype fails, escalate.
- **Cross-domain queries between `sensor_data` (wide) and `unit_telemetry` (schemaless) get awkward.** Documented but might surface a nasty performance issue at scale. Mitigation: bench a representative fusion query early (e.g., "indoor humidity + grow soil_moisture, hourly avg, last 7d") at ① ship-time.

### Medium

- **JWT key rotation procedure is manual.** A misconfigured rotation could lock everyone out. Mitigation: document the procedure, build a `rotate-signing-key` admin endpoint with the multi-key grace-period flow baked in, test the rotation in dev before any prod use.
- **Widget vocabulary v1 might miss a needed widget that surfaces during ④.** Mitigation: minor-version bump is cheap; the strict semver gate only prevents MAJOR mismatches.
- **The renderer bundle shipping to the Pi is a new attack surface.** A compromised MLSS could push malicious JS to all Pis. Mitigation: bundle is signed at build time; Pi verifies signature against the pinned MLSS signing key before caching.

### Low

- **Tactical-fallback action replay could conflict with normal-flow actions.** If MLSS comes back up while the operator is mid-action on the Pi, two paths are writing to MLSS. Mitigation: action handlers are idempotent (existing pattern); replay events are tagged distinctly from normal events for audit clarity.
- **Pi-side cache of the renderer bundle could go stale.** The Pi might serve old widgets to a browser that's used to newer ones. Mitigation: ETag + version check at fetch time; Pi refuses to serve a bundle older than `unit.widget_vocabulary_version - 1.minor`.

---

## Open questions

These can be resolved during implementation; capturing here so they don't get lost.

1. **Fusion rule storage location**: JSON files on disk vs DB table? Files are easier to hand-edit + git-version. DB table makes the future Settings → Fusion Rules editor (v2) trivial. Lean: start with files, migrate to DB when the editor is built.
2. **JWT signing key separation from TLS**: MVP shares the keypair with the pinned TLS cert; document that we may split later. When? Probably when we add multi-tenant or external integrations.
3. **Concurrent operator scenario**: two operators acting on the same unit during MLSS-down (one direct to Pi, one waiting for MLSS to come back) needs a tie-breaking strategy. Lean: the local-on-Pi UI redirects to MLSS when MLSS is reachable; the local UI is fallback-only by design.
4. **Renderer bundle signing**: how does the Pi verify the bundle is genuinely from MLSS and hasn't been tampered with in transit? Options: TLS pin (existing), bundle signature (Ed25519 sign at build time), per-bundle hash distributed via WS. Lean: bundle signature, verified before caching.
5. **Channel namespacing**: does `soil_moisture_pct` collide between unit types? Today no, but if a hydroponics unit and a grow unit both report `soil_moisture_pct` with different sensor models, queries that JOIN on channel_key get confused. Lean: enforce unit-type scoping in the wide view definitions; document that channel keys are unit-type-scoped semantically.
6. **Time-zone handling in scheduling widgets**: light schedule and photo schedule are wall-clock UTC today. If the operator + the Pi are in different timezones, the editor + display need to handle that. Lean: store everything in UTC, render in operator's local time. Existing.
7. **Migrating per-action endpoints**: how aggressively do we deprecate `POST /api/grow/units/<id>/water-now`? Lean: keep as alias for two minor versions (so existing tooling doesn't break), then drop.

---

## Out of scope for this design

- **Public ecosystem of third-party edge unit types.** We build all edge units; any public release is a separate decision.
- **Cellular / NB-IoT edge units.** Bandwidth assumptions assume LAN/WiFi. Adding cellular would change the wire format (LwM2M instead of WS).
- **MLSS-internal sensors (BME680/SCD41/PM/etc.).** Their schema, dashboard, and UI stay exactly as today. SDUI does not apply.
- **Plant profile editor for non-grow unit types.** Plant profiles are grow-specific; the Settings page only renders them when `unit_type='grow'`.
- **Streaming / real-time orchestration.** Data fusion is rule-based + batch; not Kafka-style streaming.
- **Mobile-native app.** All UI is browser-based.
- **Offline operation longer than ~1h** (limited by JWT expiry). MLSS-down for days is a different operational problem.

---

## Acceptance criteria for the full system

When all 9 sub-projects (⓪–⑧) are shipped:

1. ✓ A new edge unit type can be added to MLSS's fleet view by reflashing a Pi and letting it enrol — no MLSS code change for its UI.
2. ✓ The grow UI, in browser, looks identical to before but is now driven by SDUI declarations.
3. ✓ A second unit type (weather or scrubber) is in the fleet, sending telemetry into `unit_telemetry`, rendering its own UI.
4. ✓ Operator can navigate to a Pi's IP directly when MLSS is down and see live data (read-only at scope-A; small action set at scope-B).
5. ✓ Tactical-fallback actions land in the central audit log when MLSS comes back.
6. ✓ Telemetry stored in `unit_telemetry` is queryable per-channel cross-unit-type.
7. ✓ Auto-generated wide views support ML training queries against per-unit-type telemetry.
8. ✓ MLSS-internal sensor stack (`sensor_data`, dashboard, inferences, etc.) is unchanged — zero regression.

---

## Decisions locked during brainstorming

| | Decision | Rationale |
|---|---|---|
| ✓ | SDUI architecture (Approach 4), not microfrontends | Visual consistency requirement rules out edge-shipped UI code |
| ✓ | Schemaless edge telemetry + auto-generated wide views | Schema flexibility for new unit types + ML training simplicity |
| ✓ | MLSS-internal sensors untouched | Tighter blast radius; zero migration risk for the air-quality stack |
| ✓ | Bundled boot frame (capabilities + ui_declaration atomic) | Crash-mid-boot leaves no half-state |
| ✓ | Widget vocabulary v1 — 29 widget types | Covers existing grow + plausible non-grow units |
| ✓ | Strict chart `kind` enum (no custom escape) | Visual consistency over edge flexibility |
| ✓ | Fusion rules in JSON | Declarative + future Settings editor possible |
| ✓ | Unit type from firmware; name + roles from operator | Clean ownership split |
| ✓ | Roles many-to-many; auto-discovered from assignments | Operator-friendly tagging |
| ✓ | Sub-project ⓪: MLSS becomes unit-type-agnostic first | Foundation for everything else |
| ✓ | Versioning: semver, major/minor strict, patch lenient | Predictable upgrade flow |
| ✓ | Auth: MLSS-signed JWT, ~1h, Ed25519, pinned MLSS pubkey on Pi | Cryptographic, MLSS-down-tolerant |
| ✓ | Actions: generic `/actions/<name>` + JSON Schema args | One endpoint covers all future unit types |
| ✓ | Channel metadata in per-unit boot frame | Edge owns its own declarations |
| ✓ | Photo storage: `unit_photos` (generalised) | Any unit type can have photos |
| ✓ | Tactical fallback queue with JWT-signed audit replay | Non-repudiation for offline actions |
| ✓ | Auto-discovery of unit types: implicit via boot frame | No explicit registration step |
| ✓ | Renderer bundle: pulled at install + every WS reconnect | Auto-converges after MLSS upgrades |

---

## Next steps

1. **User review of this spec** — verify the decisions match your mental model.
2. **Sub-project plans** — once the spec is approved, write per-sub-project implementation plans starting with ⓪ (the rename + generalisation refactor). The plans go to `docs/superpowers/plans/2026-XX-XX-sdui-N-<name>.md`.
3. **Execution** — sub-project-driven development per the established pattern.

The first plan (⓪) is the riskiest because it touches every grow_* identifier in the codebase. Recommend a careful subagent-driven cycle on it, with extensive test sweeping at each commit.
