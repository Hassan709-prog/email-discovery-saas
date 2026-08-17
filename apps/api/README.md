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

### Crawl Result Persistence & Findings Mapper (Phase 2F)
- **Entities & Tables**:
  - `CrawlAttempt` (`crawl_attempts`): Durably records attempt metadata (`outcome`, `retryable`, `result_checksum`, timing, byte counts).
  - `CrawledPage` (`crawled_pages`): Records processed page outcomes (`requested_url`, `final_url`, `status_code`, `page_score`, `fetch_result`, `robots_decision`). Raw HTML bodies are strictly omitted.
  - `EmailFinding` (`email_findings`): Canonical job-scoped email storage (`canonical_email`, `domain`, `classification`, `validation_status`, `evidence_count`). Uses `ON CONFLICT (scan_job_id, canonical_email) DO NOTHING` so job counter `ScanJob.email_finding_count` increments strictly for newly discovered canonical emails.
  - `EmailEvidence` (`email_evidence`): Fine-grained proof tying findings to crawled pages (`source_type`, `evidence_snippet`, `candidate_hash`). Uses `ON CONFLICT DO NOTHING` and increments `EmailFinding.evidence_count` only for genuine insertions.
  - `RejectedEmailCandidate` (`rejected_email_candidates`): Bounded record of masked rejected candidates (e.g. `j***e@domain.com`). Raw candidates are never logged or stored in `__repr__`.
- **URL Privacy Policy**: URLs stripped of userinfo credentials (`user:pass@`), query parameters (`?param=...`), and fragments (`#frag`) before digest computation and storage.
- **Idempotent Replay & Serialized Locking**:
  - `ResultPersistenceService.persist_site_scan_result` locks `ScanURL` via `SELECT ... FOR UPDATE` tenant-scoped.
  - Replaying an attempt with identical `result_checksum` returns `is_replay=True` without duplicate rows or counter increments.
  - Replaying an attempt with a conflicting `result_checksum` raises `RESULT_CONFLICT`.
  - ScanJob completion/failed counters and `SCAN_URL_COMPLETED` / `SCAN_URL_FAILED` events emit ONLY on initial transition `SCANNING` $\rightarrow$ terminal state.

---

## HTTP API Contracts (`/api/v1/scan-jobs`)

### Endpoints Table

| Method | Path | Summary | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/scan-jobs/preview` | Input Preview | Performs network-free URL input normalization & limit validation. Does not write to DB. |
| `POST` | `/api/v1/scan-jobs` | Create Scan Job | Ingests inputs, checks quota & idempotency. Returns `201` + `Location` on creation; `200` on replay. |
| `GET` | `/api/v1/scan-jobs` | List Jobs | Lists tenant jobs with keyset pagination (`created_at`, `id`) and optional status filter. |
| `GET` | `/api/v1/scan-jobs/{job_id}` | Get Job Detail | Returns detail for authorized tenant job (HTTP 404 for missing or cross-tenant). |
| `GET` | `/api/v1/scan-jobs/{job_id}/progress` | Get Job Progress | Returns execution progress derived from persisted database counters. |
| `GET` | `/api/v1/scan-jobs/{job_id}/urls` | List Job URLs | Lists URL input rows with keyset pagination (`original_index`, `id`). |
| `GET` | `/api/v1/scan-jobs/{job_id}/events` | List Job Events | Lists audit event history with sequence pagination (`sequence_number`, `id`). |
| `POST` | `/api/v1/scan-jobs/{job_id}/queue` | Queue Job | Transitions job from `DRAFT` to `QUEUED`. Persists intent state (worker dispatch deferred). |
| `POST` | `/api/v1/scan-jobs/{job_id}/cancel` | Cancel Job | Transitions `QUEUED` $\rightarrow$ `CANCELLED` or `RUNNING` $\rightarrow$ `CANCELLING`. Replays return `200` without duplicate events. |

### Authentication & Session Security (`/api/v1/auth`)
- **Argon2id Password Hashing**: Passwords hashed with Argon2id (OWASP profile: 64MB memory, 3 iterations, 4 parallelism). Hashing runs off event loop via `asyncio.to_thread` with a bounded semaphore (`AUTH_HASH_CONCURRENCY_LIMIT`). Dummy hash is verified for missing users to mitigate account enumeration timing.
- **HS256 JWT Access Tokens**: Short-lived JWT access tokens signed using fixed `HS256` algorithm. Explicit claim validation (`sub`, `org`, `ver`, `jti`, `typ="access"`, `iss`, `aud`, `iat`, `nbf`, `exp`). Reject booleans and malformed UUIDs with generic HTTP 401.
- **Opaque Refresh Token Rotation**: 256-bit opaque refresh tokens stored only as SHA-256 digests. Atomic rotation under `SELECT ... FOR UPDATE`. Reusing a `ROTATED` refresh token revokes all sessions in the token family as `COMPROMISED` and commits the compromise before returning HTTP 401 (`REFRESH_REUSE_DETECTED`).
- **HttpOnly Cookies & CSRF Defense**: Refresh tokens transmitted strictly in `HttpOnly` cookies (`Path=/api/v1/auth`, `SameSite=Lax`, `Secure` in prod). CSRF tokens stored as SHA-256 digests, sent as readable CSRF cookies, and validated against `X-CSRF-Token` headers using constant-time `hmac.compare_digest`.
- **Global Auth Invalidation (`auth_version`)**: `POST /api/v1/auth/logout-all` atomically increments `User.auth_version` and revokes all refresh sessions. Access tokens fail immediately on next request because `RequestPrincipal` checks `user.auth_version`.
- **Local Rate Limiting**: In-memory sliding window limiter bounds memory to max 10,000 keys. Distributed multi-server rate limiting is deferred to Redis.

### Idempotency-Key Header
- Clients may supply an `Idempotency-Key` header (1-128 printable ASCII chars).
- Replayed identical requests return `HTTP 200 OK` with the same `Location` header.
- Reusing a key with different request content returns `HTTP 409 Conflict`.

### Keyset Cursor Pagination
- Paginating endpoints return opaque `next_cursor` tokens (URL-safe base64 encoded JSON).
- Enforces format version `1`, resource type match (`"jobs"`, `"urls"`, `"events"`), maximum byte size (512 bytes), and strict field schema.
- Cursors encode boundary ordering values only and NEVER bypass tenant scoping.

### Standard Error Response Envelope
All API errors return a standard JSON error envelope:
```json
{
  "error": {
    "code": "STABLE_ERROR_CODE",
    "message": "Safe human-readable error message.",
    "request_id": "req-12345"
  }
}
```
Every response preserves the `X-Request-ID` header. Unhandled server errors return HTTP 500 with a generic message, while logging tracebacks server-side.

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

Apply migrations:
```bash
uv run alembic -c apps/api/alembic.ini upgrade head
```

### 3. Running Code Quality & Test Suite
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest
```
