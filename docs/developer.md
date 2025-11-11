# 🛠️ Developer Guide

## Local Development Setup

### 1. Clone & Install

```bash
git clone <repository-url>
cd AzPolicyAlias

# Install dependencies with uv
uv sync
```

### 2. Azure Authentication

```bash
# Login to Azure
az login

# Set subscription (required)
export SUBSCRIPTION_ID=$(az account show --query id -o tsv)
```

### 3. Run Locally

```bash
# Using task
task dev

# Or directly with uv
uv run python src/main.py
```

Visit <http://localhost:8000>

## Project Structure

```text
AzPolicyAlias/
├── src/
│   ├── main.py              # FastAPI application & endpoints
│   ├── azure_service.py     # Azure API client & caching logic
│   └── static/
│       ├── index.html       # Frontend UI
│       ├── style.css        # Tokyo Night theme
│       └── script.js        # Client-side logic
├── docs/                    # Documentation
├── Dockerfile               # Multi-stage production build
├── docker-compose.yml       # Local development stack
├── pyproject.toml           # Project metadata & dependencies
├── Taskfile.yml             # Task automation
└── requirements.txt         # Python dependencies
```

## Key Components

### Backend (`azure_service.py`)

- **AzurePolicyService**: Main service class with caching
- **RetryWithBackoff**: Exponential backoff retry logic
- **Parallel fetching**: 25 concurrent workers for ~10s load time
- **Chained authentication**: AzureCliCredential → ManagedIdentityCredential

### API (`main.py`)

- FastAPI with auto-generated OpenAPI docs at `/docs`
- Endpoints: `/api/aliases`, `/api/statistics`, `/api/namespaces`, `/api/refresh`
- Pydantic models for type-safe validation
- CORS, GZip, and timing middleware

### Frontend (`static/`)

- Tokyo Night theme
- Client-side filtering, sorting, pagination
- CSV export functionality
- Keyboard shortcuts (Ctrl/Cmd+K for search)

## Development Tips

### Hot Reload

Uvicorn auto-reloads on file changes. Just save and refresh!

### Debug Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Testing Azure API

```bash
# Test authentication
az account show

# Test provider query
az provider show --namespace Microsoft.Compute --expand resourceTypes/aliases
```

### Docker Build

```bash
# Using task
task docker:build

# Or directly
docker build -t azpolicyalias:dev .

# Run container
task docker:run SUBSCRIPTION_ID=$SUBSCRIPTION_ID
# Or
docker run -p 8000:8000 -e SUBSCRIPTION_ID=$SUBSCRIPTION_ID azpolicyalias:dev
```

### Code Style

- Python: Follow PEP 8
- JavaScript: ES6+ with functional style
- CSS: BEM-like naming for components

## Performance

### Caching Strategy

- Default: 1 hour cache
- Adjust in `AzurePolicyService(cache_duration_hours=1)`
- Manual refresh via `/api/refresh` endpoint

### Parallel Processing

- 25 concurrent workers for Azure API calls
- Stays within 200 req/min rate limit
- Total load time: ~10-15 seconds for 312 providers

### Frontend

- Pagination: 100 items per page
- Client-side filtering (no server round-trips)
- GZip compression for API responses

## Troubleshooting

### Authentication Errors

```bash
# Re-login to Azure
az login
az account set --subscription <subscription-id>
```

### Cache Issues

Delete cache by restarting the app or calling `/api/refresh?force_refresh=true`

## Task Commands

```bash
task install      # Install dependencies
task dev          # Run development server
task docker:build # Build Docker image
task docker:run   # Run Docker container
task compose:up   # Start with Docker Compose
task compose:down # Stop Docker Compose
task clean        # Clean Python cache files
```

## Tools Used

- **FastAPI**: Modern Python web framework
- **Azure SDK**: azure-mgmt-resource, azure-identity
- **Pydantic**: Data validation
- **Uvicorn**: ASGI server
- **Docker**: Containerization
- **uv**: Fast Python package installer
