"""Plant Grow Unit database schema. All grow_* tables created here.

Called from database.init_db.create_db() so table creation happens in the
same transaction as the existing MLSS schema.
"""


def create_grow_schema(cur):
    """Create all grow_* tables. Idempotent (uses CREATE TABLE IF NOT EXISTS)."""
    # Tables added by later tasks in the implementation plan.
    pass
