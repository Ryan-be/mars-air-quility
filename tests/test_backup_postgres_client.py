"""Postgres client — connect, batch upsert, test, run_ddl.

Mocks psycopg2 — no real Postgres instance required. The integration
test (later) will hit a real instance.
"""
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture
def client():
    from mlss_monitor.backup.postgres_client import PostgresClient
    return PostgresClient(
        host="server.local", port=5432, database="mlss",
        user="mlss", password="secret", source_pi_id="pi-1",
    )


def test_init_does_not_connect():
    """PostgresClient.__init__ must NOT connect — connection is deferred
    to the first call. Otherwise creating a misconfigured client raises
    instead of returning a usable test_connection() result."""
    from mlss_monitor.backup.postgres_client import PostgresClient
    with patch("mlss_monitor.backup.postgres_client.psycopg2.connect") as mock:
        PostgresClient(host="x", port=5432, database="d", user="u",
                       password="p", source_pi_id="pi-1")
        mock.assert_not_called()


def test_test_connection_returns_ok_on_success(client):
    """Happy path: connect, run SELECT version(), return version string."""
    with patch("mlss_monitor.backup.postgres_client.psycopg2.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ("PostgreSQL 16.0 on x86_64-pc-linux-gnu",)
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_connect.return_value.__enter__.return_value = mock_conn
        result = client.test_connection()
    assert result["ok"] is True
    assert "PostgreSQL" in result["version"]


def test_test_connection_returns_error_on_auth_failure(client):
    """Auth failure surfaces as ok:False with error message — must NOT
    raise (caller is a Flask route returning JSON)."""
    import psycopg2
    with patch(
        "mlss_monitor.backup.postgres_client.psycopg2.connect",
        side_effect=psycopg2.OperationalError("password authentication failed"),
    ):
        result = client.test_connection()
    assert result["ok"] is False
    assert "authentication" in result["error"].lower()


def test_test_connection_returns_error_on_dns_failure(client):
    """DNS / network failure — generic exception path also surfaces as
    ok:False, not raises."""
    with patch(
        "mlss_monitor.backup.postgres_client.psycopg2.connect",
        side_effect=Exception("could not translate host name"),
    ):
        result = client.test_connection()
    assert result["ok"] is False
    assert "host name" in result["error"].lower()


def test_upsert_rows_no_op_on_empty_list(client):
    """An empty rows list must NOT open a connection (worker may batch
    a zero-row drain after a successful ship cycle)."""
    with patch("mlss_monitor.backup.postgres_client.psycopg2.connect") as mock_connect:
        client.upsert_rows(table="sensor_data", pk_columns=["id"], rows=[])
        mock_connect.assert_not_called()


def test_upsert_rows_builds_correct_sql(client):
    """The SQL should be:
        INSERT INTO sensor_data (id, temperature, source_pi_id) VALUES (%s, %s, %s)
        ON CONFLICT (id, source_pi_id) DO UPDATE SET temperature=EXCLUDED.temperature
    The pk column is in the conflict target but NOT in the SET list
    (we don't update the pk to its own value).
    """
    with patch("mlss_monitor.backup.postgres_client.psycopg2.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_connect.return_value.__enter__.return_value = mock_conn
        client.upsert_rows(
            table="sensor_data", pk_columns=["id"],
            rows=[{"id": 1, "temperature": 22.0}],
        )
    # Verify execute(many) was called once with the right SQL shape
    assert mock_cur.executemany.called
    sql, values = mock_cur.executemany.call_args[0]
    assert "INSERT INTO sensor_data" in sql
    assert "ON CONFLICT (id, source_pi_id)" in sql
    assert "DO UPDATE SET temperature=EXCLUDED.temperature" in sql
    # id is in the conflict target but NOT in the SET clause
    assert "id=EXCLUDED.id" not in sql


def test_upsert_rows_injects_source_pi_id_into_values(client):
    """The client's source_pi_id should be appended to every row tuple
    so the conflict target matches."""
    with patch("mlss_monitor.backup.postgres_client.psycopg2.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_connect.return_value.__enter__.return_value = mock_conn
        client.upsert_rows(
            table="sensor_data", pk_columns=["id"],
            rows=[
                {"id": 1, "temperature": 22.0},
                {"id": 2, "temperature": 22.5},
            ],
        )
    _sql, values = mock_cur.executemany.call_args[0]
    assert values == [(1, 22.0, "pi-1"), (2, 22.5, "pi-1")]


def test_upsert_rows_with_composite_pk(client):
    """Tables with composite PK (e.g. incident_alerts has
    (incident_id, alert_id)) — both should appear in the conflict
    target, neither in the SET clause."""
    with patch("mlss_monitor.backup.postgres_client.psycopg2.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_connect.return_value.__enter__.return_value = mock_conn
        client.upsert_rows(
            table="incident_alerts",
            pk_columns=["incident_id", "alert_id"],
            rows=[{"incident_id": "INC-1", "alert_id": 7, "is_primary": 1}],
        )
    sql, _values = mock_cur.executemany.call_args[0]
    assert "ON CONFLICT (incident_id, alert_id, source_pi_id)" in sql
    assert "is_primary=EXCLUDED.is_primary" in sql
    assert "incident_id=EXCLUDED.incident_id" not in sql
    assert "alert_id=EXCLUDED.alert_id" not in sql


def test_run_ddl_executes_sql_and_commits(client):
    """run_ddl is used by POST /init?pipeline=db to apply schema. Must
    actually execute + commit, not just open a connection."""
    with patch("mlss_monitor.backup.postgres_client.psycopg2.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_connect.return_value.__enter__.return_value = mock_conn
        client.run_ddl("CREATE TABLE foo (id int)")
    mock_cur.execute.assert_called_once_with("CREATE TABLE foo (id int)")


def test_delete_scope_empty_scope_produces_pi_only_where(client):
    """Empty scope dict means 'wipe everything for this Pi from this
    table'. SQL must reduce to just the source_pi_id predicate — the
    only safety net preventing one Pi from nuking another Pi's data."""
    with patch("mlss_monitor.backup.postgres_client.psycopg2.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_connect.return_value.__enter__.return_value = mock_conn
        client.delete_scope(table="incidents", scope={})
    mock_cur.execute.assert_called_once()
    sql, values = mock_cur.execute.call_args[0]
    assert sql == "DELETE FROM incidents WHERE source_pi_id = %s"
    assert values == ["pi-1"]


def test_delete_scope_populated_scope_appends_and_clauses(client):
    """A populated scope dict adds an ``AND col = %s`` for each key.
    Used for narrower wipes like 'all of unit_id=3's
    grow_unit_capabilities for this Pi'."""
    with patch("mlss_monitor.backup.postgres_client.psycopg2.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_connect.return_value.__enter__.return_value = mock_conn
        client.delete_scope(
            table="grow_unit_capabilities", scope={"unit_id": 3},
        )
    sql, values = mock_cur.execute.call_args[0]
    assert sql == (
        "DELETE FROM grow_unit_capabilities "
        "WHERE source_pi_id = %s AND unit_id = %s"
    )
    # source_pi_id is ALWAYS the first parameter — the order matters
    # because the SQL is built in that order.
    assert values == ["pi-1", 3]


def test_delete_scope_source_pi_id_is_always_first_param(client):
    """With multiple scope keys, source_pi_id stays the first parameter
    so the SQL placeholders line up correctly."""
    with patch("mlss_monitor.backup.postgres_client.psycopg2.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_connect.return_value.__enter__.return_value = mock_conn
        client.delete_scope(
            table="grow_light_windows",
            scope={"unit_id": 5, "phase": "vegetative"},
        )
    sql, values = mock_cur.execute.call_args[0]
    assert sql.startswith("DELETE FROM grow_light_windows WHERE source_pi_id = %s")
    assert values[0] == "pi-1"
    # Both scope columns appear in the WHERE clause after source_pi_id.
    assert "unit_id = %s" in sql
    assert "phase = %s" in sql
    assert set(values[1:]) == {5, "vegetative"}


def test_init_passes_ssl_options_to_psycopg2():
    """Verify the sslmode + sslrootcert are forwarded to psycopg2.connect."""
    from mlss_monitor.backup.postgres_client import PostgresClient
    c = PostgresClient(
        host="h", port=5432, database="d", user="u", password="p",
        source_pi_id="pi-1", sslmode="verify-full",
        sslrootcert="/etc/ssl/ca.crt", timeout=30,
    )
    with patch("mlss_monitor.backup.postgres_client.psycopg2.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = ("PostgreSQL 16",)
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_connect.return_value.__enter__.return_value = mock_conn
        c.test_connection()
    kwargs = mock_connect.call_args.kwargs
    assert kwargs["sslmode"] == "verify-full"
    assert kwargs["sslrootcert"] == "/etc/ssl/ca.crt"
    assert kwargs["connect_timeout"] == 30
