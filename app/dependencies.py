"""
app/dependencies.py
───────────────────
Shared FastAPI dependencies.

⚠️  DEV AUTH STUB
    `get_current_user` currently resolves to a single seeded default user so
    that ownership-scoped features (contacts, interactions, scores) work end
    to end before real authentication is built. When the auth phase lands,
    replace the body of get_current_user with real session/JWT resolution —
    the signature stays the same, so no call sites change.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.database import get_db
from app.models import User
from app.services.security import hash_password

# Default dev account — replaced by real auth later.
_DEFAULT_USERNAME = "advisor"
_DEFAULT_EMAIL = "advisor@local.dev"
_DEFAULT_PASSWORD = "changeme"   # dev only; never used in production


async def get_or_create_default_user(db: AsyncSession) -> User:
    """Ensure the default dev user exists and return it."""
    result = await db.execute(
        select(User).where(User.username == _DEFAULT_USERNAME)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            username=_DEFAULT_USERNAME,
            email=_DEFAULT_EMAIL,
            hashed_password=hash_password(_DEFAULT_PASSWORD),
        )
        db.add(user)
        await db.flush()        # assigns user.id without ending the transaction
    return user


async def get_current_user(db: AsyncSession = Depends(get_db)) -> User:
    """
    DEV STUB — returns the seeded default user.
    Replace with real auth resolution in the auth phase.
    """
    return await get_or_create_default_user(db)
