"""Demo-only endpoints (DEMO_MODE): resetting the sample customers before a recording."""

from fastapi import APIRouter, Depends, HTTPException

from app.config import get_settings
from app.db import get_pool
from app.limits import limit_reset

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post("/reset", dependencies=[Depends(limit_reset)])
async def reset_demo_data() -> dict[str, str]:
    """Restore the demo customers, charges, refunds and tickets to their starting state.

    Conversations already in progress keep their memory, so the UI starts a new one."""
    if not get_settings().demo_mode:
        raise HTTPException(status_code=404)
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute("select public.reset_demo_data()")
    return {"status": "reset"}
