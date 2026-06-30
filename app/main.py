"""
app/main.py
────────────
FastAPI application factory.

Responsibilities:
  - App creation with lifespan context (startup / shutdown)
  - Middleware registration (CORS, trusted hosts)
  - Router mounting
  - Global exception handlers
  - Background task scheduling (token blocklist pruning)
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.routers import auth, faculty, hod, leave_requests, students
from app.services.token_blocklist import load_blocklist_to_memory, prune_expired_tokens
from app.utils.exceptions import BadRequestError

settings = get_settings()
logger = logging.getLogger(__name__)


# ── Background task: prune expired blocklist entries ─────────────────────────

async def _blocklist_pruner():
    """
    Runs every hour in the background.
    Removes expired JTIs from both the DB table and in-memory set.
    This keeps the blocklist lean without any external scheduler dependency.
    """
    while True:
        await asyncio.sleep(3600)   # 1 hour
        try:
            async with AsyncSessionLocal() as db:
                pruned = await prune_expired_tokens(db)
                await db.commit()
                if pruned:
                    logger.info("Token blocklist pruner: removed %d expired entries", pruned)
        except Exception as exc:
            logger.error("Token blocklist pruner error: %s", exc)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once at startup (before accepting requests) and once at shutdown.

    Startup:
      1. Load the token blocklist from DB into memory (survives process restarts)
      2. Start the background pruner task

    Shutdown:
      3. Cancel the pruner task cleanly
    """
    logger.info("Starting Medical Leave Platform API...")

    # 1. Populate in-memory blocklist from DB
    async with AsyncSessionLocal() as db:
        await load_blocklist_to_memory(db)
    logger.info("Token blocklist loaded into memory")

    # 2. Start background pruner
    pruner_task = asyncio.create_task(_blocklist_pruner())
    logger.info("Token blocklist pruner started")

    yield   # ← Application runs here

    # 3. Shutdown
    pruner_task.cancel()
    try:
        await pruner_task
    except asyncio.CancelledError:
        pass
    logger.info("Medical Leave Platform API shut down cleanly")


# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Medical Leave Platform",
        description=(
            "College medical leave management system with Google OAuth, "
            "Cloudflare R2 document storage, and Gmail SMTP notifications."
        ),
        version="1.0.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Tighten `allow_origins` to your actual frontend domain in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.is_production else [settings.app_base_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(auth.router)
    app.include_router(students.router)
    app.include_router(faculty.router)
    app.include_router(leave_requests.router)
    app.include_router(hod.router)

    # ── Global exception handlers ─────────────────────────────────────────────

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """
        Catch-all for any unhandled exception.
        Never exposes internal error details in production.
        """
        logger.exception("Unhandled exception on %s %s", request.method, request.url)
        detail = str(exc) if not settings.is_production else "An internal error occurred"
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": detail},
        )

    # ── Health check ──────────────────────────────────────────────────────────

    @app.get("/health", tags=["Health"], summary="Health check")
    async def health_check():
        """Used by load balancers and monitoring to verify the service is alive."""
        return {"status": "ok", "version": "1.0.0"}

    return app


app = create_app()
