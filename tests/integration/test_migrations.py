"""Integration test — dbmate vs. create_tables() schema drift guard.

Mechanism
---------
1. Create two scratch databases on the same Postgres instance.
2. Apply ``create_tables()`` (the dev heavy-mode path) to one.
3. Apply ``dbmate up`` (the prod path) to the other.
4. Query ``information_schema`` on both and compare column definitions,
   index definitions, and constraint definitions.
5. Fail if any diff exists — proves the two schema paths stay in lockstep.

Why this matters
----------------
``src/personabot/db/models.py::_CREATE_SQL`` is the source of truth for
development mode (the DROP+CREATE path gated on ENVIRONMENT). The dbmate
migration file at ``db/migrations/20260413000001_initial_schema.sql`` is
the source of truth for production (the migrate init container in
docker-compose.prod.yml). If these drift apart — e.g. someone adds a
column to models.py but forgets the migration — the dev bot would see
the new column and prod wouldn't, silently.

This test makes that drift a CI failure. It only runs under the
``integration`` marker, which is a separate job in ``.github/workflows/ci.yml``
with a Postgres service container and the dbmate CLI preinstalled.

Prerequisites
-------------
- A reachable Postgres instance (TEST_DATABASE_URL env var)
- ``dbmate`` on PATH (installed in CI via the integration job)
- ``db/migrations/20260413000001_initial_schema.sql`` exists

Run locally
-----------
Requires the dev Postgres container and dbmate (e.g. via the official
Docker image). From the repo root::

    TEST_DATABASE_URL=postgresql://personabot:dev@localhost:5433/personabot \\
        poetry run pytest -m integration
"""

import os
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

from personabot.db.models import create_tables

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = _REPO_ROOT / "db" / "migrations"


def _scratch_db_url(base_url: str, db_name: str) -> str:
    """Replace the db path in a DSN with a new name."""
    parsed = urlparse(base_url)
    return urlunparse(parsed._replace(path=f"/{db_name}"))


async def _dump_schema_shape(dsn: str) -> dict[str, list[tuple]]:
    """Return a stable structural dump: columns + indexes + constraints.

    Each entry is a list of tuples sorted deterministically so the diff
    is insensitive to query ordering. Skips the ``schema_migrations``
    table that dbmate creates for its own bookkeeping.
    """
    conn = await asyncpg.connect(dsn)
    try:
        cols = await conn.fetch(
            """
            SELECT table_name, column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name != 'schema_migrations'
            ORDER BY table_name, ordinal_position
            """
        )
        idxs = await conn.fetch(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename != 'schema_migrations'
            ORDER BY indexname
            """
        )
        cons = await conn.fetch(
            """
            SELECT conrelid::regclass::text AS on_table, conname,
                   pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE connamespace = 'public'::regnamespace
              AND conrelid::regclass::text != 'schema_migrations'
            ORDER BY conrelid::regclass::text, conname
            """
        )
        return {
            "columns": [tuple(r) for r in cols],
            "indexes": [tuple(r) for r in idxs],
            "constraints": [tuple(r) for r in cons],
        }
    finally:
        await conn.close()


async def _create_scratch_db(admin_dsn: str, db_name: str) -> None:
    """CREATE DATABASE on a connection to the default `postgres` DB."""
    # Connect to the postgres admin DB (which is the default) so we can
    # issue CREATE DATABASE. asyncpg refuses to run that statement inside
    # a transaction, so we pass it via execute() on an autocommit
    # connection — asyncpg is autocommit-by-default outside a transaction
    # block, so this works as long as we don't open one first.
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin.close()


async def _drop_scratch_db(admin_dsn: str, db_name: str) -> None:
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
    finally:
        await admin.close()


async def test_create_tables_and_dbmate_produce_identical_schema() -> None:
    """Parity guard: create_tables() and dbmate up must match bit-for-bit.

    Runs in CI against a Postgres service container. Any drift — a
    missing column, a reordered index, a renamed FK — fails this test.
    """
    base_dsn = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://personabot:dev@localhost:5433/personabot",
    )
    # Derive an admin DSN by pointing at the default "postgres" database
    # on the same host. This is where CREATE/DROP DATABASE runs.
    admin_dsn = _scratch_db_url(base_dsn, "postgres")

    suffix = secrets.token_hex(4)
    create_tables_db = f"parity_createtables_{suffix}"
    dbmate_db = f"parity_dbmate_{suffix}"
    create_tables_dsn = _scratch_db_url(base_dsn, create_tables_db)
    dbmate_dsn = _scratch_db_url(base_dsn, dbmate_db)

    await _create_scratch_db(admin_dsn, create_tables_db)
    await _create_scratch_db(admin_dsn, dbmate_db)
    try:
        # 1. Apply create_tables() (with drop_first, though the scratch DB
        # is fresh so the drop is a no-op) to the first scratch DB.
        conn = await asyncpg.connect(create_tables_dsn)
        try:
            await create_tables(conn, drop_first=True)
        finally:
            await conn.close()

        # 2. Apply dbmate up to the second. dbmate wants a postgres://
        # scheme, not postgresql://, so rewrite it for the env var.
        dbmate_env_url = dbmate_dsn.replace("postgresql://", "postgres://", 1)
        if "?" not in dbmate_env_url:
            dbmate_env_url += "?sslmode=disable"
        result = subprocess.run(
            [
                "dbmate",
                "--migrations-dir",
                str(_MIGRATIONS_DIR),
                "--no-dump-schema",  # don't overwrite db/schema.sql in CI
                "up",
            ],
            env={**os.environ, "DATABASE_URL": dbmate_env_url},
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.fail(
                f"dbmate up failed:\nstdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

        # 3. Dump both and diff.
        ct_shape = await _dump_schema_shape(create_tables_dsn)
        dbm_shape = await _dump_schema_shape(dbmate_dsn)

        assert ct_shape["columns"] == dbm_shape["columns"], (
            "Column drift between create_tables() and dbmate migration. "
            f"create_tables: {ct_shape['columns']}\n"
            f"dbmate: {dbm_shape['columns']}"
        )
        assert ct_shape["indexes"] == dbm_shape["indexes"], (
            "Index drift between create_tables() and dbmate migration. "
            f"create_tables: {ct_shape['indexes']}\n"
            f"dbmate: {dbm_shape['indexes']}"
        )
        assert ct_shape["constraints"] == dbm_shape["constraints"], (
            "Constraint drift between create_tables() and dbmate "
            f"migration. create_tables: {ct_shape['constraints']}\n"
            f"dbmate: {dbm_shape['constraints']}"
        )
    finally:
        await _drop_scratch_db(admin_dsn, create_tables_db)
        await _drop_scratch_db(admin_dsn, dbmate_db)
