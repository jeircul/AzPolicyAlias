"""Tests for the FastAPI application endpoints.

Uses FastAPI's TestClient with a mocked AzurePolicyService so no real
Azure credentials are needed.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SAMPLE_ALIASES: list[dict[str, Any]] = [
    {
        "namespace": "Microsoft.Compute",
        "resource_type": "virtualMachines",
        "alias_name": "Microsoft.Compute/virtualMachines/osProfile.adminUsername",
        "default_path": "properties.osProfile.adminUsername",
        "default_pattern": None,
        "type": "PlainText",
    },
    {
        "namespace": "Microsoft.Storage",
        "resource_type": "storageAccounts",
        "alias_name": "Microsoft.Storage/storageAccounts/sku.name",
        "default_path": "sku.name",
        "default_pattern": None,
        "type": "PlainText",
    },
    {
        "namespace": "Microsoft.Compute",
        "resource_type": "disks",
        "alias_name": "Microsoft.Compute/disks/sku.name",
        "default_path": "sku.name",
        "default_pattern": None,
        "type": "PlainText",
    },
]

SAMPLE_STATS: dict[str, Any] = {
    "total_aliases": 3,
    "total_namespaces": 2,
    "total_resource_types": 3,
    "cache_age_seconds": 10,
    "cache_valid": True,
    "top_namespaces": [("Microsoft.Compute", 2), ("Microsoft.Storage", 1)],
}


def _make_mock_service():
    """Return an AsyncMock wired up with sample data."""
    svc = AsyncMock()
    svc.get_policy_aliases = AsyncMock(return_value=SAMPLE_ALIASES)
    svc.search_aliases = AsyncMock(return_value=SAMPLE_ALIASES[:1])
    svc.get_statistics = AsyncMock(return_value=dict(SAMPLE_STATS))
    svc.get_namespaces_with_counts = AsyncMock(
        return_value=[
            {"namespace": "Microsoft.Compute", "count": 2},
            {"namespace": "Microsoft.Storage", "count": 1},
        ]
    )
    return svc


@pytest.fixture()
def client():
    """TestClient with Azure service mocked out."""
    import src.main as main_module

    mock_svc = _make_mock_service()

    # Override the lifespan so the real Azure client is never touched
    @asynccontextmanager
    async def mock_lifespan(app) -> AsyncGenerator[None, None]:
        main_module.azure_service = mock_svc
        yield
        main_module.azure_service = None

    # Patch app lifespan and reset rate-limit state between tests
    main_module.app.router.lifespan_context = mock_lifespan
    main_module._last_refresh_time = 0.0

    with TestClient(main_module.app) as c:
        yield c

    main_module.azure_service = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health_returns_200(self, client: TestClient):
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_health_body(self, client: TestClient):
        body = client.get("/api/health").json()
        assert body["status"] == "healthy"
        assert "timestamp" in body
        # Subscription ID should be masked
        assert "..." in body["subscription_id"] or body["subscription_id"] == "not-set"


class TestAliasesEndpoint:
    def test_get_all_aliases(self, client: TestClient):
        resp = client.get("/api/aliases")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == len(SAMPLE_ALIASES)
        assert len(body["aliases"]) == len(SAMPLE_ALIASES)

    def test_query_param_triggers_search(self, client: TestClient):
        resp = client.get("/api/aliases?query=compute")
        assert resp.status_code == 200
        # search_aliases mock returns SAMPLE_ALIASES[:1]
        assert resp.json()["count"] == 1

    def test_namespace_filter(self, client: TestClient):
        resp = client.get("/api/aliases?namespace=Microsoft.Compute")
        assert resp.status_code == 200

    def test_response_schema(self, client: TestClient):
        body = client.get("/api/aliases").json()
        alias = body["aliases"][0]
        required_fields = {"namespace", "resource_type", "alias_name"}
        assert required_fields.issubset(alias.keys())


class TestStatisticsEndpoint:
    def test_statistics_200(self, client: TestClient):
        resp = client.get("/api/statistics")
        assert resp.status_code == 200

    def test_statistics_body(self, client: TestClient):
        body = client.get("/api/statistics").json()
        assert body["total_aliases"] == 3
        assert body["total_namespaces"] == 2
        assert body["cache_valid"] is True
        # top_namespaces should be a list of objects
        assert isinstance(body["top_namespaces"], list)
        assert body["top_namespaces"][0]["namespace"] == "Microsoft.Compute"


class TestNamespacesEndpoint:
    def test_namespaces_200(self, client: TestClient):
        resp = client.get("/api/namespaces")
        assert resp.status_code == 200

    def test_namespaces_with_counts(self, client: TestClient):
        body = client.get("/api/namespaces?with_counts=true").json()
        assert "with_counts" in body
        assert body["with_counts"][0]["count"] == 2

    def test_namespaces_list(self, client: TestClient):
        body = client.get("/api/namespaces").json()
        assert isinstance(body["namespaces"], list)


class TestRefreshEndpoint:
    def test_refresh_returns_200(self, client: TestClient):
        resp = client.post("/api/refresh")
        assert resp.status_code == 200

    def test_refresh_body(self, client: TestClient):
        body = client.post("/api/refresh").json()
        assert body["message"] == "Cache refreshed successfully"
        assert "aliases_count" in body
        assert "refresh_time_ms" in body

    def test_refresh_rate_limited(self, client: TestClient):
        # First call should succeed
        r1 = client.post("/api/refresh")
        assert r1.status_code == 200
        # Immediate second call should be rate-limited
        r2 = client.post("/api/refresh")
        assert r2.status_code == 429


class TestStaticFiles:
    def test_root_returns_html(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
