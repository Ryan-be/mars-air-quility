# Off-Pi backup

The MLSS hub can ship its SQLite rows + JPEG/model files to a separate
Postgres + S3-compatible blob store for archival and ML training.
Disabled by default; opt in from `/admin/backup`.

[Back to main README](../readme.md)

---

## What gets backed up

- Sensor + inference + incident tables — the **replicated tables**.
  The canonical list lives in
  [`mlss_monitor/backup/replicated_tables.py`](../mlss_monitor/backup/replicated_tables.py),
  enforced by
  [`tests/test_no_direct_writes_to_replicated_tables.py`](../tests/test_no_direct_writes_to_replicated_tables.py).
- Grow photos under `data/grow_images/` (one JPEG per snap).
- ML model artefacts: anomaly detector, multivar anomaly detector, and
  the attribution classifier pickles.

What does **not** get backed up: `app_settings`, `users`, `login_log` —
local-only state that wouldn't be useful on the server.

---

## Setup (three steps from `/admin/backup`)

1. **Test connection** for both pipelines. The hub tries to reach the
   Postgres + S3 endpoints with the configured credentials. Returns the
   Postgres `version()` string or a `list_buckets` ack on success — a
   failing connect surfaces as `{"ok": false, "error": ...}` so you can
   diagnose before flipping anything live.

2. **Initialise** the file pipeline (creates the four buckets:
   `mlss-photos`, `mlss-anomaly`, `mlss-multivar-anomaly`,
   `mlss-attribution`). Init is idempotent (S3Client swallows
   `BucketAlreadyOwnedByYou`). DB pipeline init is operator-managed for
   now — set up the receiving Postgres schema separately following the
   spec.

3. **Enable** the master toggle and per-pipeline toggles. Workers start
   immediately — the PUT /api/admin/backup/config endpoint reconciles
   running workers against the new config without restarting the hub.
   On the next process restart (gunicorn fork or `systemctl restart
   mlss-monitor`), `_start_background_services()` re-reads the config
   and re-spawns the workers if they're enabled.

---

## Operator semantics

**Append-mostly tables.** When you clear photos on a grow unit or
delete an inference dismissal, the change does **not** propagate to
the backup server. The server keeps everything observed —
disaster-recovery of accidentally-deleted-on-Pi data is the whole
point of this feature.

**Strict-mirror tables** — `incidents`, `incident_alerts`,
`incident_signature_features`, `grow_light_windows`,
`grow_unit_capabilities`. For these, DELETE+INSERT replace patterns
*do* propagate (via `outbox_delete_scope`). The server doesn't
accumulate stale versions when the operator re-groups incidents or
replaces a light schedule.

---

## Status panel

Each pipeline shows its current state (idle / draining / backoff /
paused / disabled), backoff delay, and pending counts. **Backoff**
means the last ship attempt failed; the worker retries with
exponential backoff (1 s → 600 s cap, resetting on the first
successful ship). The panel updates over SSE — saving new config in
the form publishes a `backup_config_changed` event that wakes the
worker without a process restart.

---

## Advanced controls (confirm-gated)

- **Pause / Resume** — halt or resume shipping without disabling.
  Useful for planned network maintenance windows or while you copy
  the receiving DB to a fresh disk.

- **Force re-bootstrap** — re-scans every replicated table and the
  filesystem trees, enqueuing every row/file. The outbox keeps
  idempotency (`ON CONFLICT DO UPDATE` on the Postgres side, content
  hashing on the S3 side), so the receiving end won't see duplicates
  — but the shipping cost is real if your dataset is large.

- **Clear outbox** — wipe the local `outbox_changes` /
  `outbox_blobs` / `outbox_delete_scope` tables. Use **only** when
  you've intentionally lost data and don't want the worker churning
  through entries that no longer have matching live rows. Audit-logged
  at WARNING level with the admin's user.

---

## Security note

Backup credentials (Postgres password + S3 secret_key) are stored
cleartext in the hub's `app_settings` SQLite table — Pi-level disk
encryption and filesystem ACLs are the only protection. This matches
how `bearer_token_hash` for grow units is handled. Don't reuse
high-value credentials here; mint dedicated backup users with the
minimum privileges:

- **Postgres** — `INSERT, UPDATE` on the replicated tables + the
  `source_pi_id` discriminator column.
- **S3** — `PutObject`, `HeadObject`, and `ListBuckets` on the
  `mlss-*` buckets only.

---

## Reference

- [`mlss_monitor/backup/`](../mlss_monitor/backup/) — outbox helpers,
  the `@tee_to_outbox` decorator, settings, Postgres + S3 clients,
  and the BackupWorker.
- [`docs/DATABASE.md`](DATABASE.md) — schema reference for the
  `outbox_changes` / `outbox_blobs` / `outbox_delete_scope` tables.
