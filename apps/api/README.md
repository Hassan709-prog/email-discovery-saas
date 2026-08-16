# Email Discovery API (`apps/api`)

FastAPI and PostgreSQL application service for Email Discovery SaaS.

## Architecture & Data Storage Model

### PostgreSQL as the Authoritative Source of Truth
PostgreSQL is the single, durable source of truth for all identity, multi-tenant organization boundaries, memberships, scan jobs, original input lines, processing states, append-only job progress events, and security audit logs.

### Why Redis is Not Authoritative
Redis (to be introduced in future Celery worker phases) will function purely as a ephemeral task queue and cache. Redis state can be evicted or lost without sacrificing durable scan history, raw user inputs, or progress tracking. All state changes are persisted to PostgreSQL.

### Preservation of Raw Inputs, Duplicates, and History
- **Raw Input Preservation**: Every raw input line (`original_input`) and its input order (`original_index`) are preserved in `ScanURL`, including invalid lines and intra-job duplicates (`DUPLICATE`).
- **No Global URL Uniqueness**: `normalized_url` and `normalized_domain` are intentionally NOT globally unique across jobs or tenants.
- **Append-Only History**: `JobEvent` (execution progress events) and `AuditLog` (security/admin actions) are append-only. Service code never mutates or deletes audit logs or event logs.
- **Historical Event Retention**: `JobEvent.scan_url_id` uses `ON DELETE SET NULL` so historical events survive if individual target URL records are pruned. `ScanJob.organization_id` uses `ON DELETE RESTRICT` to prevent accidental deletion of historical scan jobs.

### Data Entities & Relationships
1. **`Organization`**: Primary multi-tenant boundary. Has many `Membership` and `ScanJob` records.
2. **`User`**: User identity and credentials. Has many `Membership` records.
3. **`Membership`**: Composite `(organization_id, user_id)` link with authorization `role` (`OWNER`, `ADMIN`, `MEMBER`, `VIEWER`).
4. **`ScanJob`**: Batch scan execution request owned by an `Organization`. Has many `ScanURL` and `JobEvent` records.
5. **`ScanURL`**: Individual target URL row preserving `original_index` and `original_input`.
6. **`JobEvent`**: Immutable, append-only event log for job execution milestones.
7. **`AuditLog`**: Immutable, append-only security and administrative audit record.

---

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

> **Note on Migrations**: Initial migration `20260816_0001_initial_schema` has been created and verified offline via Alembic. It will be applied to the local PostgreSQL database once Docker Compose is started:
> ```bash
> uv run alembic -c apps/api/alembic.ini upgrade head
> ```

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
