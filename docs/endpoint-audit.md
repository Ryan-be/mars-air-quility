# HTTP API Endpoint Audit

Read-only audit of every Flask route registered under `mlss_monitor/routes/*.py`.
Focus: identify endpoints that could sensibly be **combined** via query params
or request bodies, flag look-alikes that should **stay separate**, and list
anything **dead** (no caller in `static/js/` or `templates/`).

---

## Endpoint inventory

### Sensor data (`api_data_bp`)
| Method | Path | Handler | Purpose |
|---|---|---|---|
| GET  | `/api/data`                 | `get_data`                | Sensor readings for a `?range=` window (14d default). Returns array of rows. |
| GET  | `/api/download`             | `download_data`           | Same time-windowed rows as `/api/data`, streamed as CSV attachment. |
| POST | `/api/annotate?point=<id>`  | `annotate_point`          | Add a text annotation to a sensor row. |
| DELETE | `/api/annotate?point=<id>`| `remove_annotation_route` | Remove a sensor-row annotation. |

### Fan control (`api_fan_bp`)
| Method | Path | Handler | Purpose |
|---|---|---|---|
| POST | `/api/fan?state=on\|off\|auto` | `control_fan`              | Toggle fan (or enable auto mode). |
| GET  | `/api/fan/status`              | `get_fan_status`           | Live smart-plug state + power draw + mode. |
| GET  | `/api/fan/settings`            | `get_fan_settings_route`   | Current auto-mode thresholds from DB. |
| POST | `/api/fan/settings`            | `update_fan_settings_route`| Persist auto-mode thresholds; syncs `state.fan_mode`. |
| GET  | `/api/fan/auto-status`         | `get_auto_status`          | Last auto-evaluation results (rules triggered, action taken). |

### History (`api_history_bp`)
| Method | Path | Handler | Purpose |
|---|---|---|---|
| GET  | `/api/history/range-analysis` | `range_analysis` | Build FeatureVector + candidate events for a time range. |
| POST | `/api/history/range-tag`      | `tag_range`      | Save an inference + optional tag for a user-selected range. |
| GET  | `/api/history/sensor`         | `sensor_history` | Raw sensor channels (timestamps + per-channel arrays) for a range. |
| GET  | `/api/history/baselines`      | `baselines`      | Current anomaly-detector baselines + threshold factor. |
| GET  | `/api/history/ml-context`     | `ml_context`     | Inferences enriched with attribution + co-movement summary for a range. |
| GET  | `/api/history/narratives`     | `narratives`     | Large narrative payload (summaries, trends, fingerprint narratives, heatmap) for a range. |

### Inferences (`api_inferences_bp`)
| Method | Path | Handler | Purpose |
|---|---|---|---|
| GET  | `/api/inferences`                           | `list_inferences`   | List inferences filtered by limit/dismissed/category/start/end. |
| GET  | `/api/inferences/categories`                | `list_categories`   | Category map + attribution sources. |
| POST | `/api/inferences/<id>/notes`                | `save_notes`        | Save a user note. |
| POST | `/api/inferences/<id>/dismiss`              | `dismiss`           | Mark an inference as dismissed. |
| GET  | `/api/inferences/<id>/tags`                 | `tags` (GET)        | List tags for an inference. |
| POST | `/api/inferences/<id>/tags`                 | `tags` (POST)       | Add a tag (validated against controlled vocabulary). |
| GET  | `/api/inferences/<id>/sparkline`            | `sparkline`         | Sensor window + triggering-channel list for chart rendering. |

### Insights Engine (`api_insights_bp`)
| Method | Path | Handler | Purpose |
|---|---|---|---|
| POST | `/insights-engine/dry-run`                                 | `toggle_dry_run`       | Toggle engine dry-run flag. |
| GET  | `/api/insights-engine/rules`                               | `get_rules`            | List all rules. |
| POST | `/api/insights-engine/rules`                               | `save_rules`           | Replace full rule set. |
| PATCH| `/api/insights-engine/rules/<rule_id>`                     | `patch_rule`           | Update one rule in place. |
| GET  | `/api/insights-engine/fingerprints`                        | `get_fingerprints`     | List all fingerprints. |
| POST | `/api/insights-engine/fingerprints`                        | `save_fingerprints`    | Replace full fingerprint set. |
| PATCH| `/api/insights-engine/fingerprints/<fp_id>`                | `patch_fingerprint`    | Update one fingerprint. |
| POST | `/api/insights-engine/fingerprints/<fp_id>/preview`        | `preview_fingerprint`  | Score one fingerprint against the live FV. |
| GET  | `/api/insights-engine/anomaly`                             | `get_anomaly`          | Anomaly-detector config + per-channel cold-start status. |
| POST | `/api/insights-engine/anomaly`                             | `save_anomaly`         | Persist anomaly threshold / cold-start. |
| POST | `/api/insights-engine/anomaly/<channel>/reset`             | `reset_anomaly_channel`| Reset one channel's River model. |
| GET  | `/api/insights/engine-status`                              | `engine_status`        | Summary of rules + fps + anomaly channels for admin tab. |
| GET  | `/api/insights-engine/sources`                             | `get_sources`          | List data sources + enabled flag + last-reading timestamp. |
| POST | `/api/insights-engine/sources/<name>/enable`               | `enable_source`        | Flip source to enabled. |
| POST | `/api/insights-engine/sources/<name>/disable`              | `disable_source`       | Flip source to disabled. |
| GET  | `/api/classifier/stats`                                    | `classifier_stats`     | Per-tag classifier stats. |
| POST | `/api/classifier/retrain`                                  | `retrain_classifier`   | Retrain attribution classifier from tags. |

### Settings (`api_settings_bp`)
| Method | Path | Handler | Purpose |
|---|---|---|---|
| GET  | `/api/settings/location`    | `get_location_route`      | Saved `{lat, lon, name}`. |
| POST | `/api/settings/location`    | `save_location_route`     | Persist location. |
| GET  | `/api/settings/energy`      | `get_energy_settings`     | Unit rate in pence. |
| POST | `/api/settings/energy`      | `save_energy_settings`    | Persist unit rate. |
| GET  | `/api/settings/thresholds`  | `get_thresholds_route`    | All inference thresholds. |
| POST | `/api/settings/thresholds`  | `save_thresholds_route`   | Update any subset of thresholds. |

### Stream / tags / weather / users / system (misc)
| Method | Path | Handler | Purpose |
|---|---|---|---|
| GET | `/api/stream`                 | `stream`              | SSE long-poll — live event push, 10 min lifetime. |
| GET | `/api/stream/history`         | `stream_history`      | Recent event buffer as JSON (optional `?event=`). |
| GET | `/api/tags`                   | `list_tags`           | Valid tag vocabulary from fingerprints. |
| GET  | `/api/users`                 | `get_users`           | List users. |
| POST | `/api/users`                 | `create_user_route`   | Add GitHub user with role. |
| PATCH| `/api/users/<id>/role`       | `update_role`         | Change role (or suspend via `inactive`). |
| GET  | `/api/users/<id>/logins`     | `get_user_logins`     | Last 20 logins. |
| DELETE| `/api/users/<id>`           | `delete_user`         | Hard-delete user. |
| GET  | `/api/weather`                | `weather`             | Current weather (cached ≤90 min). |
| GET  | `/api/weather/forecast`       | `forecast`            | Hourly forecast via Open-Meteo. |
| GET  | `/api/weather/forecast/daily` | `daily_forecast`      | Daily forecast (14-day). |
| GET  | `/api/weather/history`        | `weather_history`     | Past weather rows for `?range=` window. |
| GET  | `/api/geocode?q=`             | `geocode`             | Geocode lookup. |
| GET  | `/system_health`              | `system_health`       | CPU / memory / disk / sensor / plug health. |

### Page + auth routes (listed for completeness)
Pages: `/`, `/history`, `/controls`, `/admin`, `/settings/insights-engine`, `/settings/insights-engine/config`, `/settings/insights-engine/{rules,fingerprints,anomaly,sources}` (latter four are redirects → `ie_config#anchor`).
Auth: `/login`, `/logout`, `/auth/github`, `/auth/callback`.

**Total /api/* + /system_health + /insights-engine/dry-run endpoints inventoried: 50.**

---

## Consolidation candidates

### 1. `/api/data` + `/api/download` → `/api/data?format=json|csv`  (Priority: Medium)
- **Current**: `GET /api/data?range=24h` (JSON), `GET /api/download?range=24h` (CSV stream).
- **Proposed**: `GET /api/data?range=24h&format=csv` — default JSON, `format=csv` returns `send_file` attachment. Or Accept-header negotiation.
- **Callers**: `static/js/dashboard.js`, `static/js/history.js` (JSON); `static/js/history.js:9` (CSV via `window.open`).
- **Risk**: Low. Two distinct calling sites, trivial to update. CSV currently returns only 7 columns while JSON returns 15 — a merge would need to decide whether CSV gains the extra columns (probably yes; current CSV is a historical subset).
- **Priority**: Medium. Clear DRY win; callers are few.

### 2. `/api/weather/forecast` + `/api/weather/forecast/daily` → `/api/weather/forecast?resolution=hourly|daily` (Priority: Medium)
- **Current**: two endpoints, both take no query args, both call `state.open_meteo.get_*_forecast`.
- **Proposed**: `GET /api/weather/forecast?resolution=hourly` (default) / `?resolution=daily&days=14`.
- **Callers**: `dashboard.js:182` (hourly) and `dashboard.js:190` (daily).
- **Risk**: Low. Two fetches in one file.
- **Priority**: Medium.

### 3. `/api/insights-engine/sources/<name>/enable` + `/disable` → `PATCH /api/insights-engine/sources/<name>` (Priority: Medium)
- **Current**: two separate POSTs that toggle `state.data_source_enabled[name]`.
- **Proposed**: `PATCH /api/insights-engine/sources/<name>` with body `{"enabled": true|false}`.
- **Callers**: `templates/ie_config.html:807` already picks `action` dynamically — one-line refactor.
- **Risk**: Low. Single caller.
- **Priority**: Medium. Matches REST conventions; mirrors `PATCH` usage on rules/fingerprints already in the same blueprint.

### 4. `/api/insights-engine/anomaly/<channel>/reset` → fold into `PATCH /api/insights-engine/anomaly` (Priority: Low)
- **Current**: POST to reset a single River model.
- **Proposed**: `PATCH /api/insights-engine/anomaly` with body `{"reset_channel": "tvoc_ppb"}` (or keep as-is).
- **Callers**: `templates/ie_config.html:709`.
- **Risk**: Low but churny — the current URL is explicit and auditable. Reset is a distinct side-effecting action, arguably deserves its own endpoint.
- **Priority**: Low. Not worth it. (Listed so reviewers consciously decide to keep it separate.)

### 5. `/api/inferences/<id>/dismiss` + `/api/inferences/<id>/notes` → `PATCH /api/inferences/<id>` (Priority: Medium)
- **Current**: two POST endpoints, each doing one field update on an inference.
- **Proposed**: `PATCH /api/inferences/<id>` with body `{"dismissed": true}` or `{"notes": "..."}` (or both).
- **Callers**: `dashboard.js:775`, `detections_insights.js:751` (notes); no in-tree caller of `/dismiss` was found (see Dead endpoints). README documents it, and it's plausibly wired via SSE/external use, but the grep shows nothing in `static/js/` or `templates/`.
- **Risk**: Low if we keep existing routes as thin shims during migration.
- **Priority**: Medium. Combines with fixing the unused `/dismiss` endpoint.

### 6. Engine-status overlap: `/api/insights/engine-status` vs the four section GETs (Priority: Low, document only)
- **Current**: `/api/insights/engine-status` returns a summary (id + a few fields) for rules, fingerprints, anomaly. The dedicated `/api/insights-engine/{rules,fingerprints,anomaly}` GETs return the full objects. No duplication in the write-heavy paths, but the summary-shape is a subset of the union of the other three.
- **Proposed**: keep as-is, but add `?summary=1` to each section GET so the admin status tab can call one URL pattern instead of three — *or* drop `/engine-status` entirely and have the admin tab do three parallel fetches (only called once on page load).
- **Callers**: `templates/admin.html:830`.
- **Risk**: Noticeable — would flatten multiple fetches into one on admin page load. Not a clear win.
- **Priority**: Low.

### 7. `/api/history/range-analysis` vs `/api/history/sensor` vs `/api/history/ml-context` (Priority: Low — keep separate, but document)
- All three take `?start&end`. It's *tempting* to fold them into `/api/history?start&end&include=sensor,analysis,ml-context` but the payloads are very different sizes (narratives is ~1 MB with caching, sensor is raw arrays, range-analysis is a single FV). See "Keep-separate" below.

### 8. `/api/history/narratives` + `/api/history/ml-context` (Priority: Low — keep separate)
- Both take `?start&end` and both return attribution / dominance summaries. But `narratives` is a much larger payload (trend indicators, fingerprint narratives, heatmaps, drift flags, 7d baselines) and is **cached** with a 60 s TTL, while `ml-context` is cheaper and only returns what the correlation chart needs. Callers use them for different UI surfaces (`charts_correlation.js` vs `detections_insights.js`). Keep separate — merging would force unnecessary work or awkward `?include=` gating.

---

## Keep-separate endpoints

- **`/api/fan/settings` vs `/api/settings/thresholds`** — both relate to numeric thresholds, but fan settings belong to the fan auto-controller (temp/TVOC/humidity/PM ranges + `enabled` flags) while `/settings/thresholds` are inference-detection thresholds used by rule evaluation. Different tables, different consumers.
- **`/api/fan/auto-status` vs `/api/fan/status`** — `status` is smart-plug telemetry (power, kWh, on/off); `auto-status` is controller-decision telemetry (which rule fired, last action). Same domain, different data, different cache lifetimes.
- **`/api/fan` vs `/api/fan/settings`** — one is a runtime toggle, one is persistent config. Keep as two verbs.
- **`/api/stream` vs `/api/stream/history`** — one is a long-poll SSE; the other is a plain JSON snapshot for late-joining clients. Very different response types.
- **`/api/weather` vs `/api/weather/history`** — one is live (with a 90-min log cache), the other is a DB query over `?range=`. Different layers.
- **`/api/inferences/<id>/tags` (GET vs POST on the same URL)** — already consolidated; no action needed.
- **`/api/insights-engine/rules` POST (bulk replace) vs `/.../rules/<id>` PATCH (single update)** — bulk save and single-row edit; tests + UI rely on both. Same pattern on fingerprints.
- **`/api/history/range-analysis` vs `/api/history/range-tag`** — analysis is a GET preview; range-tag is a POST that writes an inference. Different verbs, different side-effects.
- **`/api/insights/engine-status` vs `/api/insights-engine/*`** — note the path inconsistency (`/api/insights/` vs `/api/insights-engine/`). The first is a cheap summary-only read; the others each hit file/engine. Worth considering renaming `/api/insights/engine-status` → `/api/insights-engine/status` for consistency, but functionally keep separate.

---

## Dead endpoints

Grepped `static/js/**`, `templates/**`, and `readme.md` for each route. No hits in `static/js/` or `templates/`:

- **`POST /api/inferences/<id>/dismiss`** — documented in `readme.md:909` but zero JS/template callers. May be planned-but-unused; safe to remove if no external client depends on it, or fold into the proposed `PATCH /api/inferences/<id>`.
- **`PATCH /api/insights-engine/rules/<rule_id>`** — `templates/ie_config.html:487` calls it, so **NOT dead** (false alarm — listed here only to note it's used).
- **`POST /api/insights-engine/rules` (bulk save)** — `templates/ie_config.html:527` — used.
- **`POST /api/insights-engine/fingerprints` (bulk save)** — grep shows no caller in static/templates. `ie_config.html` only uses the PATCH-per-id path. Candidate for removal (or kept as a bulk-import admin path).
- **`POST /api/insights-engine/anomaly/<channel>/reset`** — `ie_config.html:709` — used.

Confirmed dead candidates: **2** (`/api/inferences/<id>/dismiss`, `POST /api/insights-engine/fingerprints`).

---

## Notes & path-style inconsistencies (no action required, flagged for awareness)

- The app mixes two path styles: `/api/insights-engine/...` (kebab in the noun) and `/api/insights/engine-status` (slash-split noun). Pick one.
- `POST /insights-engine/dry-run` is the only `/api/*`-less endpoint in that blueprint. Presumably a legacy URL kept for compatibility — comment in code confirms.
- `/api/annotate` uses `?point=<id>` rather than `/api/annotate/<id>` — inconsistent with the `/api/inferences/<id>/...` style elsewhere.
- `/api/fan?state=on|off|auto` passes the action in a query param on a POST — body would be more idiomatic.
