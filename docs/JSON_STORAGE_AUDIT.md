# JSON-in-TEXT-column Audit (2026-05-07)

## Principle

Anything mutated at runtime by a running service belongs in a typed
column. JSON-in-TEXT is only legitimate for genuinely heterogeneous
opaque blobs (different shape per row, write-once-read-as-blob).

This audit covers the air-quality side of the schema only. The grow
side was cleaned up in Phase 2 batch C1 — see "Already-cleaned" at the
bottom.

## Method

`Grep`'d `json\.dumps` / `json\.loads` across `mlss_monitor/`,
`database/`, and `tests/`, then cross-referenced every match against
the schema in `database/init_db.py`. Two TEXT columns on the
air-quality side hold serialised JSON; everything else is either:

- a string scalar (e.g. `app_settings.value` — one stringified value
  per key, no structure);
- a hash / token (e.g. `grow_enrollment_key_hash`);
- a `json.dumps`/`json.loads` over the wire (HTTP responses,
  WebSocket frames, SSE payloads) — not persisted as JSON.

## Status by column

### Server (data/sensor_data.db)

| Column | Table | Read sites | Write sites | What it stores | Mutates after insert? | Static shape? | Verdict |
|---|---|---|---|---|---|---|---|
| `evidence` | `inferences` | `database/db_logger.py` (`get_inferences`, `get_inference_by_id` — both rebuild the dict via `inference_evidence_storage.rebuild_evidence_from_row`); `mlss_monitor/attribution/engine.py:442` and `mlss_monitor/routes/api_inferences.py:219` (read pre-decoded `inf["evidence"]`); `mlss_monitor/routes/api_history.py:329, 343, 454` | `database/db_logger.py:save_inference` (single write site, called from `mlss_monitor/inference_engine.py` + `mlss_monitor/detection_engine.py` — ~24 literal `evidence={...}` call sites). The save site delegates the typed-column split to `inference_evidence_storage.persist_evidence`. | Per-event diagnostic snapshot (e.g. `baseline_tvoc`, `peak_tvoc`, `correlation_r`, `feature_vector`, `attribution_source`, `attribution_confidence`, `_thresholds`). Keys vary by `event_type`. | No — written once at `save_inference()`, never updated | Heterogeneous keys per `event_type`, but readers consistently look up the same handful of fields (`attribution_source`, `attribution_confidence`, `runner_up_id`, `runner_up_confidence`, `detection_method`) | **DONE** — promoted to typed columns (`evidence_attribution_source`, `evidence_attribution_confidence`, `evidence_runner_up_id`, `evidence_runner_up_confidence`, `evidence_detection_method`) plus a smaller `evidence_extras` JSON for the genuinely-heterogeneous remainder. Legacy `evidence` TEXT column retained for one release per `DATABASE.md`'s deprecation policy. The 24 callers building `evidence={...}` literals are unchanged — `inference_evidence_storage.persist_evidence` splits the dict at write time. |
| `signature` | `incidents` | `mlss_monitor/routes/api_incidents.py:113, 321` | `mlss_monitor/incident_grouper.py:498` (called from `regroup_incidents()` — full table rebuild) | A 32-element `list[float]` from `build_incident_similarity_vector()` — fixed layout documented at `incident_grouper.py:283-291` (peak deltas, sensor presence flags, detection-method one-hot, severity one-hot, duration, mean confidence, time-of-day) | No — written once when incidents are regrouped | Yes — fixed 32-float vector, schema-versioned by code | **DONE** — promoted to `incident_signature_features (incident_id, feature_idx, value)` sub-table; legacy TEXT column retained for one release per `DATABASE.md` |

That's it. No other JSON-bearing TEXT columns exist on the
air-quality side.

### Grow unit (/var/lib/mlss-grow/buffer.sqlite)

The grow-unit local buffer DB has no JSON-in-TEXT columns. The
on-device buffer (`buffer.sqlite`) stores raw frames as scalar
columns only (`msg_type`, `body`, `timestamp_utc`); see
`grow_unit/src/mlss_grow/buffer.py`. No `_json` field exists.

## Roadmap items

All entries below are deferred — no code changes in Phase 2 C3.

- ~~**inferences.evidence → typed columns or sub-table**~~ **DONE
  AND DROPPED** — promoted to 5 typed columns
  (`evidence_attribution_source`, `evidence_attribution_confidence`,
  `evidence_runner_up_id`, `evidence_runner_up_confidence`,
  `evidence_detection_method`) plus a smaller `evidence_extras`
  TEXT/JSON column for genuinely heterogeneous diagnostic context
  (`feature_vector`, `thresholds_used`, `baseline_*`,
  `range_start`/`range_end`, etc.). The 24 `evidence={...}` literal
  call sites in `mlss_monitor/inference_engine.py` and
  `mlss_monitor/detection_engine.py` are unchanged — the central
  `save_inference()` in `database/db_logger.py` delegates to
  `mlss_monitor.inference_evidence_storage.persist_evidence` which
  splits the dict at write time. Reads via `get_inferences` /
  `get_inference_by_id` use `rebuild_evidence_from_row` to read from
  the typed columns + extras blob. `get_distinct_attribution_sources`
  queries the typed column directly (indexable). After commit
  `d0a1d07` back-filled all historic rows, the legacy
  `inferences.evidence` TEXT column was dropped — the typed
  representation is now the single source of truth. See
  `mlss_monitor/inference_evidence_storage.py` and
  `tests/test_inference_evidence_storage.py`.

- ~~**incidents.signature → blob or sub-table**~~ **DONE AND
  DROPPED** — promoted to
  `incident_signature_features (incident_id, feature_idx, value)`
  with `ON DELETE CASCADE`. Option 2 (queryable sub-table) was chosen
  over Option 1 (BLOB) because it future-proofs against the vector
  growing past 32 features and allows per-feature analytical queries
  (e.g. "incidents whose pm_density bucket is extreme") without a
  Python decode step. Reads via
  `mlss_monitor.incident_signature_storage.load_signature` go directly
  to the sub-table. After commit `d0a1d07` back-filled all historic
  rows, the legacy `incidents.signature` TEXT column was dropped —
  the sub-table is now the single source of truth. See
  `mlss_monitor/incident_signature_storage.py` and
  `tests/test_incident_signature_features.py`.

No DROP-DEAD or REFACTOR-CACHE candidates remain on the
air-quality side.

## Already-cleaned (Phase 2 C1 batch)

- `grow_unit_capabilities.details_json`: capability `health` was
  promoted to a typed `TEXT NOT NULL DEFAULT 'untested'` column with
  pydantic-validated values; `details_json` is now reserved for
  legitimately heterogeneous capability metadata (e.g. sensor I2C
  address, calibration coefficients). See migration at
  `database/init_db.py:220-227`.
- `grow_units.last_known_state_json`: DROPPED. Replaced by a `SELECT`
  against `grow_telemetry` (already indexed by
  `(unit_id, timestamp_utc DESC)`). See migration at
  `database/init_db.py:234`.
- `grow_units.light_phase_override_json`: DROPPED (dead, superseded
  by the `grow_light_windows` table introduced in Phase 1). See
  migration at `database/init_db.py:233`.
