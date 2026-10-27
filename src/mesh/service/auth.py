"""Who is calling. API keys map to a user from config/access.yaml (only a SHA-256 of each key is stored);
behind the load balancer the same user can come from an OIDC token. The user's principal decides what they
may read; the products whose contract names the principal as owner are the ones they decide access to."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request

from mesh.service.db import Pool, tx


@dataclass(frozen=True)
class Caller:
    user: str
    name: str
    principal: str
    owns: tuple[str, ...]


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def create_key(pool: Pool, user: str, key: str | None = None) -> str:
    key = key or "mesh_" + secrets.token_urlsafe(32)
    async with tx(pool) as conn:
        await conn.execute(
            "INSERT INTO api_keys(key_hash, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (hash_key(key), user)
        )
    return key


async def caller(request: Request) -> Caller:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    pool: Pool = request.app.state.pool
    async with tx(pool) as conn:
        row = await (
            await conn.execute(
                "SELECT user_id FROM api_keys WHERE key_hash = %s AND revoked_at IS NULL", (hash_key(header[7:].strip()),)
            )
        ).fetchone()
    if row is None:
        raise HTTPException(401, "invalid or revoked key")
    platform = request.app.state.platform
    user = platform.policy.users.get(row["user_id"])
    if user is None:
        raise HTTPException(403, "this key's user no longer exists")
    owns = tuple(sorted(c.product for c in platform.registry if c.owner == user.principal))
    return Caller(user.id, user.name, user.principal, owns)
