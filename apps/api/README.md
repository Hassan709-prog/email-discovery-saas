# Email Discovery API (`apps/api`)

FastAPI and PostgreSQL application service for Email Discovery SaaS.

## Architecture & Data Storage Model

### Transaction Ownership & Repository Scoping
- **Transaction Boundaries**: Services (`ScanJobService`) explicitly own all transaction boundaries using `async with session.begin():`. Repositories NEVER call `commit()` or `rollback()`.
- **Tenant Isolation**: Every repository method requires `organization_id`. Queries filter by `organization_id` (or JOIN `ScanJob` for child entities). Unscoped job or URL queries are strictly prohibited.
- **Organization Creation Serialization**: Job creation locks the tenant `Organization` row (`SELECT ... FOR UPDATE`) before checking membership, active job quota (`max_active_jobs_per_organization`), and idempotency. This serializes creation per organization to prevent quota race conditions without bottlenecking scan execution.

### Input Ingestion & Counter Semantics
- **Deterministic Input Ingestion**: Inputs are validated against pre-ingestion limit policies (`ScanCreationPolicy`) before row construction. Valid URLs are normalized via `email_scanner.normalize_url`. Invalid URLs become `ScanURLStatus.INVALID`.
- **Intra-Job Deduplication**: First occurrence of a valid URL becomes `ScanURLStatus.PENDING`. Subsequent identical URLs within the same job become `ScanURLStatus.DUPLICATE` referencing the first `ScanURL.id`.
- **Counters**:
  - `total_input_count`: Total raw submitted lines.
  - `valid_input_count`: Unique valid `PENDING` URLs eligible for scanning.
  - `duplicate_input_count`: Valid duplicate lines within this job.
  - `invalid_input_count`: Derived as `total_input_count - valid_input_count - duplicate_input_count`.

### Idempotency & Request Fingerprinting
- **Deterministic SHA-256 Fingerprint**: Computes a 64-character hex digest of inputs, tenant, creator, source type, configuration, and scanner versions. Dictionary key ordering is independent; input list ordering is sensitive.
- **Idempotency Conflict**: Requests with matching `idempotency_key` and matching `request_fingerprint` safely return the existing job. Mismatched request contents raise `IDEMPOTENCY_CONFLICT`.
- **Uniqueness Race Recovery**: Database partial unique index `uq_scan_jobs_org_idempotency` catches race conditions. If an `IntegrityError` occurs, the failed transaction is rolled back, and a fresh transaction re-reads the existing job tenant-scoped to verify the fingerprint.

### Event Sequencing & State Transitions
- **Atomic Event Sequence Allocation**: Event sequence numbers are allocated atomically in PostgreSQL via:
  ```sql
  UPDATE scan_jobs
  SET next_event_sequence = next_event_sequence + 1
  WHERE organization_id = :org_id AND id = :job_id
  RETURNING next_event_sequence - 1
  ```
- **State Transition Matrix**: State transitions (`DRAFT` $\rightarrow$ `QUEUED` $\rightarrow$ `RUNNING` $\rightarrow$ `COMPLETED` / `CANCELLED` / `FAILED`) are validated by policy and executed via conditional SQL updates (`WHERE status = expected_status`). Successful transitions append `JOB_STATUS_CHANGED` events within the same transaction.

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

> **Note on Migrations**: Migrations `20260816_0001_initial_schema` and `20260816_0002_idempotency_and_event_sequence` have been verified offline. Apply them to live PostgreSQL using:
> ```bash
> uv run alembic -c apps/api/alembic.ini upgrade head
> ```

### 3. Running Tests
```bash
uv run pytest
```

> **Pending Live Concurrency Verification**: Row locking (`FOR UPDATE`), partial unique-index races, and atomic `UPDATE ... RETURNING` sequence allocation have been structurally verified via unit tests and will be subjected to live PostgreSQL multi-connection concurrency tests when Docker environment is active.
