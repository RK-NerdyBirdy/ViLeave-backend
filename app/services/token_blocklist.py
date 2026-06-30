"""
app/services/token_blocklist.py
────────────────────────────────
JWT invalidation on logout via a server-side blocklist.

Design:
  - The blocklist is a PostgreSQL table (token_blocklist).
  - An in-memory set provides an O(1) fast-path check on every request,
    populated at startup and updated on every logout.
  - Expired tokens are pruned automatically by a background task so the
    table never grows unboundedly.

Trade-off vs Redis:
  This uses Postgres to avoid adding a Redis dependency. For very high
  traffic (10k+ logouts/day), migrate this to Redis with TTL keys.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, AsyncSessionLocal


# ── ORM model for the blocklist table ────────────────────────────────────────

class BlockedToken(Base):
    """
    Stores JTI (JWT ID) of invalidated tokens until their natural expiry.
    We only need to block tokens that haven't expired yet — once a token
    is past its `exp`, it fails validation anyway.
    """
    __tablename__ = "token_blocklist"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    blocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


# ── In-memory fast-path set ───────────────────────────────────────────────────

_memory_blocklist: set[str] = set()


async def load_blocklist_to_memory(db: AsyncSession) -> None:
    """
    Called once at application startup.
    Loads all non-expired blocked JTIs into the in-memory set.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(BlockedToken.jti).where(BlockedToken.expires_at > now)
    )
    jti_list = result.scalars().all()
    _memory_blocklist.update(jti_list)


def is_token_blocked(jti: str) -> bool:
    """O(1) check used in every authenticated request."""
    return jti in _memory_blocklist


async def block_token(jti: str, expires_at: datetime, db: AsyncSession) -> None:
    """
    Add a JTI to both the in-memory set and the DB.
    Called from POST /auth/logout.
    """
    _memory_blocklist.add(jti)
    blocked = BlockedToken(jti=jti, expires_at=expires_at)
    db.add(blocked)
    await db.flush()


async def prune_expired_tokens(db: AsyncSession) -> int:
    """
    Delete all expired tokens from the DB and clean the in-memory set.
    Intended to be called by a periodic background task (e.g. every hour).
    Returns the count of pruned rows.
    """
    now = datetime.now(timezone.utc)

    # Prune DB
    result = await db.execute(
        delete(BlockedToken).where(BlockedToken.expires_at <= now)
    )
    pruned_count = result.rowcount
    await db.flush()

    # Rebuild memory set from DB (simplest safe approach after pruning)
    result = await db.execute(
        select(BlockedToken.jti).where(BlockedToken.expires_at > now)
    )
    _memory_blocklist.clear()
    _memory_blocklist.update(result.scalars().all())

    return pruned_count
