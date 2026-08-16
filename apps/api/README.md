# Email Discovery API (`apps/api`)

FastAPI and PostgreSQL application foundation service for Email Discovery SaaS.

## Developer Setup & Commands

### 1. Install & Sync Dependencies
```bash
uv sync
```

### 2. Start Local PostgreSQL Database
Start local PostgreSQL with Docker Compose:
```bash
docker compose -f infra/docker/docker-compose.yml up -d
```
To stop the database while preserving data:
```bash
docker compose -f infra/docker/docker-compose.yml stop
```
To stop and remove containers while keeping persistent data volumes:
```bash
docker compose -f infra/docker/docker-compose.yml down
```
To delete persistent data volumes completely:
```bash
docker compose -f infra/docker/docker-compose.yml down -v
```

### 3. Run API Server Locally
```bash
uv run uvicorn email_discovery_api.main:app --reload
```

### 4. Health Check & Documentation Endpoints
- **Liveness Probe**: `http://localhost:8000/health/live` (Returns HTTP 200 process status)
- **Readiness Probe**: `http://localhost:8000/health/ready` (Returns HTTP 200 or 503 DB status)
- **OpenAPI Interactive Documentation**: `http://localhost:8000/docs`

### 5. Running Tests
```bash
uv run pytest
```
