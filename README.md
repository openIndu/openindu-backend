# openIndu Backend

> **Language:** English | [中文](README_ZH.md)

## What is it?

openIndu Backend is the server-side platform powering the [openIndu](https://openindu.com) open industrial automation ecosystem. It provides both a REST API (for the Portal and Admin web applications) and an MCP Server (for Claude Code AI Agent knowledge retrieval).

## Tech Stack

| Layer               | Technology                          |
| ------------------- | ----------------------------------- |
| Framework           | FastAPI 0.115                       |
| ASGI Server         | Uvicorn                             |
| ORM                 | SQLAlchemy 2.x + Alembic            |
| Database            | PostgreSQL 15                       |
| Vector Database     | Milvus 2.4                          |
| Object Storage      | MinIO (dev) / Alibaba Cloud OSS     |
| MCP SDK             | mcp 1.9                             |
| Task Scheduling     | APScheduler                         |
| Rate Limiting       | slowapi                             |
| SMS                 | Alibaba Cloud SMS                   |
| LLM Integration     | OpenAI SDK                          |

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+ (for local development outside Docker)

### Run with Docker Compose

```bash
# 1. Clone the repo
git clone https://github.com/openIndu/openindu-backend.git
cd openindu-backend

# 2. Create .env from the template
cp .env.example .env

# 3. Start all services
docker compose up -d --build
```

This starts the following services:

| Service  | Port   | Description                     |
| -------- | ------ | ------------------------------- |
| Web API  | `8004` | REST API for Portal & Admin     |
| MCP API  | `8005` | MCP Server (Claude Code knowledge retrieval) |
| PostgreSQL | `5432` | Business database               |
| Milvus   | `19530` | Vector database                 |
| MinIO    | `9000` | S3-compatible object storage    |

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Start Web API
uvicorn app.web_app:app --reload --port 8004

# Start MCP Server (separate terminal)
uvicorn app.mcp_app:app --reload --port 8005
```

## Project Structure

```
openindu-backend/
├── app/
│   ├── api/              # REST API route handlers
│   │   ├── auth.py       #   Authentication (SMS login)
│   │   ├── users.py      #   User management
│   │   ├── documents.py  #   Knowledge base documents
│   │   ├── files.py      #   File upload / download
│   │   ├── chat.py       #   RAG chat endpoint
│   │   ├── software.py   #   Software catalog
│   │   ├── admin.py      #   Admin dashboard endpoints
│   │   ├── portal.py     #   Portal CMS endpoints
│   │   ├── stats.py      #   Statistics (PV/UV)
│   │   ├── tags.py       #   Tag management
│   │   ├── sync.py       #   Data synchronization
│   │   ├── brand_mapping.py # Brand name mappings
│   │   ├── visits.py     #   Visit tracking
│   │   └── ...
│   ├── models/           # SQLAlchemy ORM models
│   ├── core/             # Configuration, database session, utilities
│   ├── services/         # Business logic layer
│   │   ├── auth_service.py
│   │   ├── chat_service.py
│   │   ├── file_storage.py
│   │   ├── milvus_service.py
│   │   ├── rag_sync_service.py
│   │   └── ...
│   ├── mcp/              # MCP Server (Model Context Protocol)
│   │   ├── server.py
│   │   └── tools.py
│   ├── middleware/        # Custom middleware
│   ├── tasks/             # Scheduled background tasks
│   ├── web_app.py         # Web API entry point (port 8004)
│   └── mcp_app.py         # MCP Server entry point (port 8005)
├── alembic/              # Database migration scripts
├── tests/                # Test suite (pytest)
├── scripts/              # Utility scripts
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## License

[Apache-2.0](LICENSE)
