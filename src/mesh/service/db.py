"""PostgreSQL for the service's own records. The service refuses to start as a role that is a superuser:
nothing here needs it, and the audit trigger must apply to the service like to everyone else."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

Conn = AsyncConnection[DictRow]
Pool = AsyncConnectionPool[Conn]


class UnsafeRole(RuntimeError):
    pass


class IamAuthConnection(AsyncConnection[DictRow]):
    """RDS PostgreSQL with IAM database authentication: a fresh 15-minute token per connection, TLS required."""

    @classmethod
    async def connect(cls, conninfo: str = "", **kwargs: Any) -> Any:
        import boto3

        host, user = os.environ["MESH_DB_HOST"], os.environ.get("MESH_DB_USER", "mesh_api")
        region = os.environ.get("AWS_REGION", "eu-central-1")
        kwargs["password"] = boto3.client("rds", region_name=region).generate_db_auth_token(host, 5432, user, region)
        return await super().connect(f"host={host} port=5432 dbname=mesh user={user} sslmode=verify-full", **kwargs)


def database_url() -> str:
    return os.environ.get("MESH_DATABASE_URL", "")


async def open_pool(url: str, max_size: int = 10) -> Pool:
    iam = os.environ.get("MESH_DB_IAM_AUTH") == "1"
    pool: Pool = AsyncConnectionPool(
        url,
        min_size=1,
        max_size=max_size,
        open=False,
        kwargs={"row_factory": dict_row},
        connection_class=IamAuthConnection if iam else Conn,
    )
    await pool.open(wait=True)
    return pool


async def migrate(pool: Pool) -> list[str]:
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).fetchone()
        if row and row["rolsuper"]:
            raise UnsafeRole("the service must not connect as a superuser")
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        await conn.execute("SELECT pg_advisory_lock(73741)")
        applied: list[str] = []
        try:
            done = {r["version"] for r in await (await conn.execute("SELECT version FROM schema_migrations")).fetchall()}
            files = sorted(
                (f for f in resources.files("mesh.service.migrations").iterdir() if f.name.endswith(".sql")), key=lambda f: f.name
            )
            for f in files:
                if f.name in done:
                    continue
                async with conn.transaction():
                    await conn.execute(f.read_text().encode())
                    await conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (f.name,))
                applied.append(f.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(73741)")
        return applied


@asynccontextmanager
async def tx(pool: Pool) -> AsyncIterator[Conn]:
    async with pool.connection() as conn, conn.transaction():
        yield conn
