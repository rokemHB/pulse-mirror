"""Version 1 of the public API. Changes here must be additive (see CLAUDE.md)."""

from fastapi import APIRouter

from pulse_mirror.api.v1 import core, events, tokens, wellbeing

router = APIRouter(prefix="/api/v1")
router.include_router(core.router)
router.include_router(events.router)
router.include_router(wellbeing.router)
router.include_router(tokens.router)
