"""Regression guard — every write to a replicated table must go through the
centralised save helper so the @tee_to_outbox decorator catches it.

If this test fails, someone added a raw INSERT/UPDATE somewhere; refactor it
to use the appropriate helper instead.

Replicated tables list comes from ``mlss_monitor/backup/replicated_tables``
(the same module the backup worker imports from) so the lint allowlist and
the worker's PK schema can never drift.
"""
import re
from pathlib import Path
import pytest

from mlss_monitor.backup.replicated_tables import REPLICATED_TABLES


REPO = Path(__file__).resolve().parent.parent

# Files allowed to write to these tables directly (the canonical save helpers).
# All other writes must go through helpers in this allowlist.
#
# As Tasks 5-8 refactor each save site to use @tee_to_outbox, the corresponding
# helper file STAYS in this allowlist (it's where the raw SQL lives). What
# changes is that OTHER call sites stop having direct INSERTs and start calling
# the helper. So this allowlist remains stable across the refactor.
ALLOWED_FILES = {
    "database/init_db.py",                      # schema creation, seed data
    "database/db_logger.py",                    # save_sensor_data, save_inference
    "database/grow_schema.py",                  # grow-schema seed data
    "database/import_csv_to_db.py",             # one-shot CSV import tool (manual operator script, not part of live pipeline)
    "mlss_monitor/grow/handlers.py",            # handle_telemetry, handle_capabilities, handle_event
    "mlss_monitor/grow/photo_storage.py",       # write_photo
    "mlss_monitor/grow/timelapse_jobs.py",      # render-job runner: marks queued→running→complete/failed (companion to api_grow_timelapse.py which only schedules)
    "mlss_monitor/incident_grouper.py",         # save_incident
    "mlss_monitor/incident_signature_storage.py", # save_signature: incident similarity-vector writer
    "mlss_monitor/inference_evidence_storage.py", # store_evidence: UPDATE inferences with typed evidence columns
    "mlss_monitor/backup/outbox.py",            # holds example SQL inside tee_to_outbox docstring; does not itself write replicated tables
    "mlss_monitor/routes/api_grow_journal.py",  # journal CRUD
    "mlss_monitor/routes/api_grow_units.py",    # decommission, capability writes
    "mlss_monitor/routes/api_grow_config.py",   # PUT config writes
    "mlss_monitor/routes/api_grow_settings.py", # plant profile updates
    "mlss_monitor/routes/api_grow_timelapse.py", # timelapse job writes
    "mlss_monitor/routes/api_grow_danger.py",   # delete unit, clear buffer
    "mlss_monitor/routes/api_grow_enroll.py",   # first-boot enrollment: insert/refresh grow_units row + bearer-token hash
    "mlss_monitor/routes/api_grow_errors.py",   # PATCH /api/grow/errors/<id>: UPDATE grow_errors (ack/resolve/snooze)
    "mlss_monitor/routes/api_grow_ws.py",       # WS connection-event audit: INSERT grow_errors + UPDATE resolved_at on reconnect
    "scripts/migrate_categories.py",            # one-shot inference category migration (operator tool, idempotent)
    "tests/",                                   # tests can write directly
}


def _is_allowed_path(rel: str) -> bool:
    return any(rel.startswith(prefix) or rel == prefix.rstrip("/")
               for prefix in ALLOWED_FILES)


@pytest.mark.parametrize("table", REPLICATED_TABLES)
def test_no_unauthorised_writes(table):
    """Search for raw INSERT INTO <table> / UPDATE <table> outside the
    allowlist. Catches anyone bypassing the save helpers."""
    pattern = re.compile(
        rf"\b(INSERT\s+(OR\s+\w+\s+)?INTO|UPDATE)\s+{table}\b",
        re.IGNORECASE,
    )
    offenders = []
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if _is_allowed_path(rel):
            continue
        if ".claude/" in rel or "node_modules/" in rel:
            continue
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, (
        f"Direct writes to replicated table '{table}' found outside the "
        f"allowlist of canonical save helpers:\n" + "\n".join(offenders)
    )
