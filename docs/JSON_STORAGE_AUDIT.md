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
| `evidence` | `inferences` | `database/db_logger.py:425, 495, 514, 532`; `mlss_monitor/attribution/engine.py:442`; `mlss_monitor/routes/api_history.py:329, 343, 454`; `mlss_monitor/routes/api_inferences.py:219` | `database/db_logger.py:414` (single write site, called from `mlss_monitor/inference_engine.py` and `mlss_monitor/detection_engine.py` — ~25 call sites) | Per-event diagnostic snapshot (e.g. `baseline_tvoc`, `peak_tvoc`, `correlation_r`, `feature_vector`, `attribution_source`, `attribution_confidence`, `_thresholds`). Keys vary by `event_type`. | No — written once at `save_inference()`, never updated | Heterogeneous keys per `event_type`, but readers consistently look up the same handful of fields (`attribution_source`, `attribution_confidence`, `runner_up_source`, `runner_up_confidence`, `range_start`, `range_end`, `feature_vector`) | **PROMOTE-TO-COLUMNS** (roadmap — Phase 3+) — user pre-classified |
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

- **inferences.evidence → typed columns or sub-table** (Phase 3+)
  - Scope: extract the consistently-read fields
    (`attribution_source`, `attribution_confidence`, `runner_up_source`,
    `runner_up_confidence`, `range_start`, `range_end`) into proper
    columns on `inferences` so attribution queries (e.g.
    `get_distinct_attribution_sources`) become indexable instead of
    `SELECT DISTINCT evidence` + Python parsing.
  - Keep a smaller `evidence_extras` JSON column for the genuinely
    heterogeneous diagnostic context (event-specific human-readable
    fields like `baseline_tvoc`, `correlation_r`).
  - Migration is non-trivial: ~25 `evidence={...}` literal call sites
    across `inference_engine.py` + `detection_engine.py`, plus the
    `feature_vector` blob used by ML attribution; should be batched
    into a single PR with a backfill script.
  - Plan reference: future
    `docs/superpowers/plans/<date>-inference-evidence-typed-cols.md`.

- ~~**incidents.signature → blob or sub-table**~~ **DONE** — promoted
  to `incident_signature_features (incident_id, feature_idx, value)`
  with `ON DELETE CASCADE`. Option 2 (queryable sub-table) was chosen
  over Option 1 (BLOB) because it future-proofs against the vector
  growing past 32 features and allows per-feature analytical queries
  (e.g. "incidents whose pm_density bucket is extreme") without a
  Python decode step. Reads via
  `mlss_monitor.incident_signature_storage.load_signature` prefer the
  sub-table and fall back to the legacy JSON column for pre-migration
  incidents. The legacy `incidents.signature` TEXT column is retained
  for one release; a follow-up commit will drop it. See
  `mlss_monitor/incident_signature_storage.py` and
  `tests/test_incident_signature_features.py`.

No DROP-DEAD or REFACTOR-CACHE candidates were found on the
air-quality side — both columns have live readers.

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
