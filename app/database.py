import logging
from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

logger = logging.getLogger("contacts.database")


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _engine_kwargs(database_url: str) -> dict:
    if not database_url.startswith("sqlite"):
        return {}

    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if ":memory:" in database_url or "mode=memory" in database_url:
        # A plain in-memory SQLite database lives and dies with its connection.
        # StaticPool keeps a single connection alive so every request — and every
        # thread FastAPI hands work to — sees the same data for the process's lifetime.
        kwargs["poolclass"] = StaticPool
    return kwargs


settings = get_settings()

engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    **_engine_kwargs(settings.database_url),
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _column_names(table_name: str) -> set[str]:
    """What the database currently has. A fresh inspector, so nothing is cached."""
    return {column["name"] for column in inspect(engine).get_columns(table_name)}


def _add_missing_columns() -> None:
    """
    Add columns the models declare that an existing table does not have yet.

    `create_all` creates missing *tables* but never alters existing ones, so a
    database created before a column was added — the file-backed SQLite and
    PostgreSQL setups `CONTACTS_DATABASE_URL` documents — would fail every query
    that selects the full entity. Adding a nullable column is the one schema
    change that is always safe to apply automatically. Anything else (drops,
    type changes, backfills) is refused here and needs a real migration tool.

    Every worker runs this at startup, so two of them can look at the same
    database before either has altered it — see the rescue below.
    """
    preparer = engine.dialect.identifier_preparer

    for table in Base.metadata.sorted_tables:
        present = _column_names(table.name)
        for column in table.columns:
            if column.name in present:
                continue
            if not column.nullable:
                raise RuntimeError(
                    f"{table.name}.{column.name} is missing from the database and cannot be added "
                    "automatically because it is not nullable. Migrate the database by hand."
                )
            # Identifiers come from our own metadata, never from a request, and are
            # quoted by the dialect regardless.
            statement = text(
                f"ALTER TABLE {preparer.format_table(table)} "
                f"ADD COLUMN {preparer.format_column(column)} "
                f"{column.type.compile(engine.dialect)}"
            )
            try:
                with engine.begin() as connection:
                    connection.execute(statement)
            except DBAPIError:
                # Looking and altering cannot be one atomic step, so a worker
                # starting alongside this one may have added the column in
                # between — both saw it missing, and the second `ALTER TABLE`
                # fails as a duplicate. Losing that race is the goal reached,
                # not a failure; anything else still is. `ADD COLUMN IF NOT
                # EXISTS` would say this in one line, but SQLite has no such
                # form, and looking again works on every dialect.
                if column.name not in _column_names(table.name):
                    raise
                logger.info("column %s.%s was added concurrently", table.name, column.name)
                continue
            logger.info("added missing column %s.%s", table.name, column.name)


def init_db() -> None:
    """Create tables and add any newly declared columns. Safe to call repeatedly."""
    from app import models  # noqa: F401  (register models on Base.metadata)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session that is always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
