"""
migrate_categories.py — one-shot migration for inference category corrections.

Changes applied:
  - mould_risk: "alert" → "warning"
  - annotation_context_* events: any → "pattern"

Safe to re-run: all updates are guarded by WHERE category != <target>.
"""

import sqlite3
import sys
from pathlib import Path

# ── Reclassify specific event types ──────────────────────────────────────────
RECLASSIFY = {
    "mould_risk": "warning",
}


def migrate(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()

        # Check that the inferences table has a 'category' column before doing
        # any SQL.  Abort gracefully if it doesn't exist yet.
        cur.execute("PRAGMA table_info(inferences)")
        columns = {row["name"] for row in cur.fetchall()}
        if "category" not in columns:
            print(
                "ERROR: 'category' column not found in inferences table. "
                "Run the schema migration first.",
                file=sys.stderr,
            )
            return

        # 1. Reclassify individual event types listed in RECLASSIFY.
        for event_type, new_category in RECLASSIFY.items():
            cur.execute(
                "UPDATE inferences SET category = ? "
                "WHERE event_type = ? AND category != ?",
                (new_category, event_type, new_category),
            )
            print(
                f"  {event_type}: {cur.rowcount} row(s) updated → {new_category!r}"
            )

        # 2. Update all annotation_context_* events to "pattern".
        cur.execute(
            "UPDATE inferences SET category = 'pattern' "
            "WHERE event_type LIKE 'annotation_context_%' AND category != 'pattern'"
        )
        print(f"  annotation_context_*: {cur.rowcount} row(s) updated → 'pattern'")

        conn.commit()
        print("Migration complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {Path(__file__).name} <path-to-db>", file=sys.stderr)
        sys.exit(1)
    migrate(sys.argv[1])
