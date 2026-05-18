"""init_db calls create_grow_schema during create_db."""
from database.init_db import create_db


def test_create_grow_schema_is_called(monkeypatch, tmp_path):
    """When create_db runs, it must invoke create_grow_schema with the cursor."""
    called_with = []

    def fake_create_grow_schema(cur):
        called_with.append(cur)

    monkeypatch.setattr("database.init_db.create_grow_schema", fake_create_grow_schema)
    monkeypatch.setattr("database.init_db.DB_FILE", str(tmp_path / "test.db"))
    create_db()

    assert len(called_with) == 1
    assert called_with[0] is not None  # was given a cursor
