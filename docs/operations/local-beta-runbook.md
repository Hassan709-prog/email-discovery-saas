# Local beta operations runbook

This deployment is intended for a small, no-funding local beta. PostgreSQL is the authoritative
store. Redis only coordinates wake-ups, distributed rate limits, and advisory worker presence.

## Start and stop

Copy `.env.example` to `.env`, then start PostgreSQL and Redis:

```powershell
docker compose --env-file .env -f infra/docker/docker-compose.yml up -d postgres redis
```

Apply migrations and start the API, one or more workers, and the frontend in separate terminals:

```powershell
$env:DATABASE_URL='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/email_discovery'
.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
.venv\Scripts\python.exe -m uvicorn email_discovery_api.main:app --host 127.0.0.1 --port 8000
docker compose --env-file .env -f infra/docker/docker-compose.yml --profile workers up -d --scale worker=2 worker
npm --prefix apps/web run dev
```

Stop workers gracefully before infrastructure:

```powershell
docker compose --env-file .env -f infra/docker/docker-compose.yml --profile workers stop -t 30 worker
docker compose --env-file .env -f infra/docker/docker-compose.yml stop redis postgres
```

## Health and private operations

- `GET http://127.0.0.1:8000/health/live` checks only the API process.
- `GET http://127.0.0.1:8000/health/ready` checks PostgreSQL and configured Redis readiness.
- `/api/v1/operations/*` is hidden by default. Set `OPERATIONS_ENABLED=true` and put only trusted
  user UUIDs in `OPERATIONS_ADMIN_USER_IDS`. Tenant OWNER or ADMIN membership is insufficient.
- `GET /api/v1/operations/metrics` returns bounded aggregates.
- `GET /api/v1/operations/diagnostics` is always read-only.
- `POST /api/v1/operations/recovery/jobs/{job_id}` requires `{"confirm": true}`, invokes the
  existing tenant-safe recovery service, and creates a sanitized audit record.

Redis worker records are explicitly advisory. Missing or stale presence does not prove that
PostgreSQL work was lost. Check authoritative lease and URL state before taking action.

## Offline controlled load

The harness uses the real worker claim, attempt, fencing, persistence, counter, and finalization
paths with an offline orchestrator. It never contacts fixture hostnames.

```powershell
.venv\Scripts\python.exe -m tools.operational_load.cli --size 100 --workers 1 --timeout 120
.venv\Scripts\python.exe -m tools.operational_load.cli --size 500 --workers 2 --timeout 300
.venv\Scripts\python.exe -m tools.operational_load.cli --size 1000 --workers 4 --timeout 600
```

Valid sizes are exactly 100, 500, and 1,000; valid worker counts are 1, 2, and 4. A report is
accepted only after counters, attempts, findings, checksums, fences, leases, and shutdown are
validated. Stop the 1,000-URL matrix after the first slow, timed-out, or incorrect result.

Throughput and latency describe deterministic local capacity, not internet crawl performance.
Retries and failures should be zero for the default fixture. Expired leases or stale presence need
diagnosis; they are not corrected by the metrics endpoint.

## Privacy and practical limits

Never publish operations endpoints or logs containing target URLs, domains, emails, tenant data,
job/URL identifiers, worker names, credentials, tokens, database URLs, or Redis URLs. Do not use
operations output as customer analytics. Keep the API bound to localhost unless authentication,
TLS, network controls, backups, and secret management have been reviewed.

For a local beta, begin with one or two workers at concurrency two, PostgreSQL's configured pool,
and the supplied Redis memory limit. Increase replicas only after the bounded harness shows clean
shutdown, stable checksums, acceptable database connections, and useful throughput.
