"""
CrateIQ — FastAPI backend entry point.

Start the server:
  uvicorn backend.app.main:app --reload --port 8000

From the project root (crateIQ/):
  uvicorn backend.app.main:app --reload --port 8000 --app-dir .
"""
from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import analysis as analysis_router
from .api.routes import crates as crates_router
from .api.routes import exports as exports_router
from .api.routes import health as health_router
from .api.routes import insights as insights_router
from .api.routes import jobs as jobs_router
from .api.routes import library as library_router
from .api.routes import metadata_repair as metadata_repair_router
from .api.routes import metadata_sanitation as metadata_sanitation_router
from .api.routes import playlists as playlists_router
from .api.routes import reconciliation as reconciliation_router
from .api.routes import runtime as runtime_router
from .api.routes import sync as sync_router
from .api.routes import tracks as tracks_router
from .core.config import BACKEND_VERSION, PIPELINE_PY, TOOLKIT_ROOT
from .core.db import init_db
from .services import read_only as read_only_service

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    log.info("CrateIQ backend v%s starting up", BACKEND_VERSION)
    log.info("CrateIQ root : %s", TOOLKIT_ROOT)
    log.info("pipeline.py  : %s  (exists=%s)", PIPELINE_PY, PIPELINE_PY.is_file())
    log.info("Library root : %s", read_only_service.get_library_root())
    log.info("Pipeline DB  : %s  (exists=%s)", read_only_service.get_db_path(), read_only_service.db_exists())

    init_db()

    yield

    # --- shutdown ---
    log.info("CrateIQ backend shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CrateIQ API",
    description=(
        "Local-first REST API for the CrateIQ library intelligence pipeline. "
        "Submit pipeline jobs, track their progress, and stream their logs."
    ),
    version=BACKEND_VERSION,
    lifespan=lifespan,
)

# Allow the local frontend dev server (Phase 2) to call the API.
# In production restrict origins explicitly.
cors_origins = [origin.strip() for origin in os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5175"
).split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_request_timing(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
    log.info(
        "request method=%s path=%s status=%s duration_ms=%.1f",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

API_PREFIX = "/api"

app.include_router(health_router.router,     prefix=API_PREFIX)
app.include_router(runtime_router.router,    prefix=API_PREFIX)
app.include_router(jobs_router.router,       prefix=API_PREFIX)
app.include_router(library_router.router,    prefix=API_PREFIX)
app.include_router(tracks_router.router,     prefix=API_PREFIX)
app.include_router(insights_router.router,    prefix=API_PREFIX)
app.include_router(analysis_router.router,   prefix=API_PREFIX)
app.include_router(crates_router.router,     prefix=API_PREFIX)
app.include_router(playlists_router.router,  prefix=API_PREFIX)
app.include_router(metadata_repair_router.router, prefix=API_PREFIX)
app.include_router(metadata_sanitation_router.router, prefix=API_PREFIX)
app.include_router(reconciliation_router.router, prefix=API_PREFIX)
app.include_router(exports_router.router,    prefix=API_PREFIX)
app.include_router(sync_router.router,       prefix=API_PREFIX)
