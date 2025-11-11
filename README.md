# 🔍 Azure Policy Aliases Viewer

**Fast, searchable viewer for 70,000+ Azure Policy aliases** with a beautiful Tokyo Night theme.

[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=flat&logo=docker&logoColor=white)](https://hub.docker.com)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)](https://fastapi.tiangolo.com)

## ✨ Features

- 🚀 **Blazing fast** - Loads 72,000+ aliases in ~10 seconds
- 🔎 **Instant search** - Filter by namespace, resource type, or alias name
- 📊 **Smart sorting** - Sort any column with one click
- 📥 **CSV export** - Download filtered results
- 🎨 **Beautiful UI** - Tokyo Night theme with responsive design
- 💾 **Smart caching** - 1-hour cache reduces API calls
- ⚡ **Live updates** - Manual refresh when you need fresh data

## 🚀 Quick Start

```bash
# Local development
uv sync
az login
export SUBSCRIPTION_ID=$(az account show --query id -o tsv)
task dev
```

Visit <http://localhost:8000>

## 🚢 Deployment

**Prerequisites:**

- UAMI with federated credential for your cluster
- Service account subject: `system:serviceaccount:default:azpolicyalias`

```bash
# Update k8s/helmrelease.yaml with your values, then:
kubectl apply -f k8s/helmrelease.yaml
```

CI/CD builds and pushes the Docker image automatically.

## 🔧 Configuration

**Local**: Uses Azure CLI credentials (`az login`)

**Production**: Set these in `k8s/helmrelease.yaml`:

- `SUBSCRIPTION_ID`
- `AZURE_CLIENT_ID` (UAMI)
- `AZURE_TENANT_ID`

## 🎯 Usage

1. **Search** - Type in the search box (Ctrl/Cmd+K)
2. **Filter** - Select a namespace from the dropdown
3. **Sort** - Click any column header
4. **Export** - Click "Export CSV" to download results
5. **Refresh** - Click "Refresh Cache" for latest data

## 🏗️ Architecture

- **Backend**: FastAPI with Azure SDK
- **Frontend**: Vanilla JS with Tokyo Night theme
- **Caching**: In-memory with 1-hour TTL
- **Auth**: ChainedTokenCredential (CLI → Managed Identity → Default)

## 📖 Documentation

- [Developer Guide](docs/developer.md) - Local development setup

## 📝 License

MIT License - see LICENSE file for details.
