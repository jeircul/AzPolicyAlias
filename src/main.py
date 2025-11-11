from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from pathlib import Path
import os
import logging
import time
from azure_service import AzurePolicyService

# Configure structured logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Pydantic models for request/response validation
class PolicyAlias(BaseModel):
    """Model for a single policy alias"""

    namespace: str = Field(..., description="Azure resource provider namespace")
    resource_type: str = Field(..., description="Resource type within the namespace")
    alias_name: str = Field(..., description="Name of the policy alias")
    default_path: Optional[str] = Field(None, description="Default path for the alias")
    default_pattern: Optional[Any] = Field(
        None, description="Default pattern if available"
    )
    type: Optional[str] = Field(None, description="Type of the alias")


class AliasesResponse(BaseModel):
    """Response model for aliases endpoint"""

    aliases: List[PolicyAlias]
    count: int = Field(..., description="Total number of aliases returned")
    query_time_ms: Optional[float] = Field(
        None, description="Query execution time in milliseconds"
    )


class StatisticsResponse(BaseModel):
    """Response model for statistics endpoint"""

    total_aliases: int
    total_namespaces: int
    total_resource_types: int
    cache_age_seconds: Optional[int] = None
    cache_valid: bool
    top_namespaces: List[tuple]


class NamespaceInfo(BaseModel):
    """Model for namespace information"""

    namespace: str
    count: int


class NamespacesResponse(BaseModel):
    """Response model for namespaces endpoint"""

    namespaces: List[str]
    with_counts: Optional[List[NamespaceInfo]] = None


class HealthResponse(BaseModel):
    """Response model for health check"""

    status: str
    subscription_id: str
    timestamp: str


class RefreshResponse(BaseModel):
    """Response model for cache refresh"""

    message: str
    aliases_count: int
    statistics: StatisticsResponse
    refresh_time_ms: float


# Initialize FastAPI app
app = FastAPI(
    title="Azure Policy Aliases Viewer",
    description="A high-performance, searchable interface for Azure Policy aliases",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)


# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Add processing time header to all responses"""
    start_time = time.time()
    response = await call_next(request)
    process_time = (time.time() - start_time) * 1000
    response.headers["X-Process-Time-Ms"] = str(round(process_time, 2))
    return response


# Mount static files - using Path to get correct directory relative to this file
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Initialize Azure service
subscription_id = os.getenv("SUBSCRIPTION_ID")
if not subscription_id:
    logger.error("SUBSCRIPTION_ID environment variable is required")
    raise ValueError("SUBSCRIPTION_ID environment variable must be set")

azure_service = AzurePolicyService(subscription_id)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def read_root():
    """Serve the main HTML page"""
    html_path = Path(__file__).parent / "static" / "index.html"
    return FileResponse(str(html_path))


@app.get("/api/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """
    Health check endpoint for monitoring and load balancers

    Returns system status and basic information
    """
    from datetime import datetime

    return HealthResponse(
        status="healthy",
        subscription_id=subscription_id[:8] + "..." if subscription_id else "not-set",
        timestamp=datetime.utcnow().isoformat(),
    )


@app.get("/api/statistics", response_model=StatisticsResponse, tags=["Data"])
async def get_statistics():
    """
    Get comprehensive statistics about policy aliases

    Returns counts, cache status, and top namespaces by alias count
    """
    try:
        stats = await azure_service.get_statistics()
        return StatisticsResponse(**stats)
    except Exception as e:
        logger.error(f"Error getting statistics: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve statistics: {str(e)}"
        )


@app.get("/api/aliases", response_model=AliasesResponse, tags=["Data"])
async def get_aliases(
    query: Optional[str] = Query(
        None, description="Search query (supports multiple terms)"
    ),
    namespace: Optional[str] = Query(None, description="Filter by specific namespace"),
    force_refresh: bool = Query(
        False, description="Force refresh cache from Azure API"
    ),
):
    """
    Get policy aliases with optional search and filtering

    - **query**: Search across namespace, resource type, alias name, and default path
    - **namespace**: Filter results to a specific namespace
    - **force_refresh**: Bypass cache and fetch fresh data from Azure
    """
    start_time = time.time()

    try:
        if query or namespace:
            aliases = await azure_service.search_aliases(query or "", namespace)
        else:
            aliases = await azure_service.get_policy_aliases(force_refresh)

        query_time_ms = (time.time() - start_time) * 1000

        return AliasesResponse(
            aliases=[PolicyAlias(**alias) for alias in aliases],
            count=len(aliases),
            query_time_ms=round(query_time_ms, 2),
        )
    except Exception as e:
        logger.error(f"Error getting aliases: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve aliases: {str(e)}"
        )


@app.get("/api/namespaces", response_model=NamespacesResponse, tags=["Data"])
async def get_namespaces(
    with_counts: bool = Query(False, description="Include alias counts per namespace"),
):
    """
    Get all available namespaces

    - **with_counts**: Include the number of aliases per namespace
    """
    try:
        if with_counts:
            namespace_data = await azure_service.get_namespaces_with_counts()
            return NamespacesResponse(
                namespaces=[ns["namespace"] for ns in namespace_data],
                with_counts=[NamespaceInfo(**ns) for ns in namespace_data],
            )
        else:
            aliases = await azure_service.get_policy_aliases()
            namespaces = sorted(set(alias["namespace"] for alias in aliases))
            return NamespacesResponse(namespaces=namespaces)
    except Exception as e:
        logger.error(f"Error getting namespaces: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve namespaces: {str(e)}"
        )


@app.post("/api/refresh", response_model=RefreshResponse, tags=["Cache"])
async def refresh_cache():
    """
    Force refresh the policy aliases cache from Azure API

    This will fetch fresh data from Azure and update the cache
    """
    start_time = time.time()

    try:
        aliases = await azure_service.get_policy_aliases(force_refresh=True)
        stats = await azure_service.get_statistics()

        refresh_time_ms = (time.time() - start_time) * 1000

        return RefreshResponse(
            message="Cache refreshed successfully",
            aliases_count=len(aliases),
            statistics=StatisticsResponse(**stats),
            refresh_time_ms=round(refresh_time_ms, 2),
        )
    except Exception as e:
        logger.error(f"Error refreshing cache: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to refresh cache: {str(e)}"
        )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unexpected errors"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred", "type": type(exc).__name__},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
