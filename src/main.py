import logging
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from azure_service import AzurePolicyService  # pylint: disable=import-error

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class PolicyAlias(BaseModel):
    """Single policy alias."""

    namespace: str = Field(..., description="Azure resource provider namespace")
    resource_type: str = Field(..., description="Resource type within the namespace")
    alias_name: str = Field(..., description="Name of the policy alias")
    default_path: str | None = Field(None, description="Default path for the alias")
    default_pattern: Any | None = Field(None, description="Default pattern if available")
    type: str | None = Field(None, description="Type of the alias")


class AliasesResponse(BaseModel):
    """Response model for aliases endpoint."""

    aliases: list[PolicyAlias]
    count: int = Field(..., description="Total number of aliases returned")
    query_time_ms: float | None = Field(None, description="Query execution time in ms")


class NamespaceCount(BaseModel):
    """Namespace + alias count pair for statistics."""

    namespace: str
    count: int


class StatisticsResponse(BaseModel):
    """Response model for statistics endpoint."""

    total_aliases: int
    total_namespaces: int
    total_resource_types: int
    cache_age_seconds: int | None = None
    cache_valid: bool
    top_namespaces: list[NamespaceCount]


class NamespaceInfo(BaseModel):
    """Namespace with optional alias count."""

    namespace: str
    count: int


class NamespacesResponse(BaseModel):
    """Response model for namespaces endpoint."""

    namespaces: list[str]
    with_counts: list[NamespaceInfo] | None = None


class HealthResponse(BaseModel):
    """Response model for health check."""

    status: str
    subscription_id: str
    timestamp: str


class RefreshResponse(BaseModel):
    """Response model for cache refresh."""

    message: str
    aliases_count: int
    statistics: StatisticsResponse
    refresh_time_ms: float


# ---------------------------------------------------------------------------
# App factory — service initialised inside lifespan to enable testability
# ---------------------------------------------------------------------------

# Module-level reference set by lifespan so endpoints can access it
azure_service: AzurePolicyService | None = None

# Simple in-memory rate-limit state for /api/refresh
_last_refresh_time: float = 0.0
_REFRESH_COOLDOWN_SECONDS = 30


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    global azure_service

    subscription_id = os.getenv("SUBSCRIPTION_ID")
    if not subscription_id:
        logger.error("SUBSCRIPTION_ID environment variable is required")
        raise ValueError("SUBSCRIPTION_ID environment variable must be set")

    azure_service = AzurePolicyService(subscription_id)
    logger.info("Azure service initialised")
    yield
    # Cleanup: nothing async to do, executor shutdown handled by __del__
    logger.info("Azure service shutting down")


app = FastAPI(
    title="Azure Policy Aliases Viewer",
    description="A high-performance, searchable interface for Azure Policy aliases",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# Restrict CORS to same origin by default; override via ALLOWED_ORIGINS env var
_raw_origins = os.getenv("ALLOWED_ORIGINS", "")
_allowed_origins: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=bool(_allowed_origins),  # credentials only when origins are explicit
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Attach X-Process-Time-Ms to every response."""
    start_time = time.time()
    response = await call_next(request)
    process_time = (time.time() - start_time) * 1000
    response.headers["X-Process-Time-Ms"] = str(round(process_time, 2))
    return response


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _get_service() -> AzurePolicyService:
    if azure_service is None:
        raise HTTPException(status_code=503, detail="Service not initialised")
    return azure_service


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def read_root():
    """Serve the main HTML page."""
    html_path = Path(__file__).parent / "static" / "index.html"
    return FileResponse(str(html_path))


@app.get("/api/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint for monitoring and load balancers."""
    subscription_id = os.getenv("SUBSCRIPTION_ID", "")
    return HealthResponse(
        status="healthy",
        subscription_id=subscription_id[:8] + "..." if subscription_id else "not-set",
        timestamp=datetime.now(UTC).isoformat(),
    )


@app.get("/api/statistics", response_model=StatisticsResponse, tags=["Data"])
async def get_statistics():
    """Get comprehensive statistics about policy aliases."""
    try:
        stats = await _get_service().get_statistics()
        # Convert top_namespaces list[tuple] → list[NamespaceCount]
        stats["top_namespaces"] = [
            NamespaceCount(namespace=ns, count=cnt) for ns, cnt in stats["top_namespaces"]
        ]
        return StatisticsResponse(**stats)
    except HTTPException:
        raise
    except Exception as err:
        logger.error("Error getting statistics: %s", err, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve statistics") from err


@app.get("/api/aliases", response_model=AliasesResponse, tags=["Data"])
async def get_aliases(
    query: str | None = Query(None, description="Search query (supports multiple terms)"),
    namespace: str | None = Query(None, description="Filter by specific namespace"),
    force_refresh: bool = Query(False, description="Force refresh cache from Azure API"),
):
    """
    Get policy aliases with optional search and filtering.

    - **query**: Search across namespace, resource type, alias name, and default path
    - **namespace**: Filter results to a specific namespace
    - **force_refresh**: Bypass cache and fetch fresh data from Azure
    """
    start_time = time.time()
    svc = _get_service()

    try:
        if query or namespace:
            aliases = await svc.search_aliases(query or "", namespace)
        else:
            aliases = await svc.get_policy_aliases(force_refresh)

        return AliasesResponse(
            aliases=[PolicyAlias(**alias) for alias in aliases],
            count=len(aliases),
            query_time_ms=round((time.time() - start_time) * 1000, 2),
        )
    except HTTPException:
        raise
    except Exception as err:
        logger.error("Error getting aliases: %s", err, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve aliases") from err


@app.get("/api/namespaces", response_model=NamespacesResponse, tags=["Data"])
async def get_namespaces(
    with_counts: bool = Query(False, description="Include alias counts per namespace"),
):
    """Get all available namespaces, optionally with alias counts."""
    svc = _get_service()
    try:
        if with_counts:
            namespace_data = await svc.get_namespaces_with_counts()
            return NamespacesResponse(
                namespaces=[ns["namespace"] for ns in namespace_data],
                with_counts=[NamespaceInfo(**ns) for ns in namespace_data],
            )
        aliases = await svc.get_policy_aliases()
        namespaces = sorted({alias["namespace"] for alias in aliases})
        return NamespacesResponse(namespaces=namespaces)
    except HTTPException:
        raise
    except Exception as err:
        logger.error("Error getting namespaces: %s", err, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve namespaces") from err


@app.post("/api/refresh", response_model=RefreshResponse, tags=["Cache"])
async def refresh_cache():
    """Force refresh the policy aliases cache from Azure API."""
    global _last_refresh_time

    # Rate-limit: prevent hammering the Azure API
    now = time.time()
    if now - _last_refresh_time < _REFRESH_COOLDOWN_SECONDS:
        remaining = int(_REFRESH_COOLDOWN_SECONDS - (now - _last_refresh_time))
        raise HTTPException(
            status_code=429,
            detail=f"Refresh rate-limited. Try again in {remaining}s.",
        )
    _last_refresh_time = now

    start_time = time.time()
    svc = _get_service()

    try:
        aliases = await svc.get_policy_aliases(force_refresh=True)
        stats = await svc.get_statistics()
        stats["top_namespaces"] = [
            NamespaceCount(namespace=ns, count=cnt) for ns, cnt in stats["top_namespaces"]
        ]

        return RefreshResponse(
            message="Cache refreshed successfully",
            aliases_count=len(aliases),
            statistics=StatisticsResponse(**stats),
            refresh_time_ms=round((time.time() - start_time) * 1000, 2),
        )
    except HTTPException:
        raise
    except Exception as err:
        logger.error("Error refreshing cache: %s", err, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to refresh cache") from err


@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception):
    """Global catch-all — does not expose internal error details."""
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred", "type": type(exc).__name__},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True, log_level="info")
