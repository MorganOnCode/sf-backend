"""
Startup has to bring an existing database up to date with the models.

`create_all` only creates missing tables, so a file-backed SQLite or PostgreSQL
database created before a column was added would fail every query that selects
the full entity. `_add_missing_columns` closes that gap for nullable columns.
"""

import pytest
from sqlalchemy import inspect, text

from app.database import _add_missing_columns, engine


def columns() -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("contacts")}


def test_a_column_missing_from_an_existing_table_is_added(client):
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE contacts DROP COLUMN photo"))
    assert "photo" not in columns()

    _add_missing_columns()

    assert "photo" in columns()


def test_the_upgraded_table_serves_pre_existing_rows(client, payload):
    """A row written before the column existed reads back with a null photo."""
    contact_id = client.post("/api/v1/contacts", json=payload).json()["id"]
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE contacts DROP COLUMN photo"))

    _add_missing_columns()

    response = client.get(f"/api/v1/contacts/{contact_id}")
    assert response.status_code == 200
    assert response.json()["photo"] is None


def test_running_it_twice_changes_nothing(client):
    _add_missing_columns()
    before = columns()

    _add_missing_columns()

    assert columns() == before


def test_a_missing_required_column_is_refused_rather_than_guessed(client):
    """Only nullable columns are safe to add blind; anything else needs a migration."""
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE contacts DROP COLUMN created_at"))

    with pytest.raises(RuntimeError, match="not nullable"):
        _add_missing_columns()
