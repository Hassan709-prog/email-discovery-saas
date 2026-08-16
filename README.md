# Email Discovery SaaS

This repository contains the phased implementation of the Email Discovery SaaS.

The first development milestone is **Phase 1A: deterministic URL normalization**. The frontend, API, database, Redis, Celery, and browser fallback will be introduced only in their planned phases.

## Architectural rule

`packages/scanner` must remain independent from FastAPI, Celery, Redis, PostgreSQL models, billing, tenant logic, and frontend contracts.

## Repository map

```text
apps/                  Future user-facing applications
  api/                 FastAPI routes and application services
  web/                 Next.js user interface
packages/
  scanner/             Independent scanner-core Python package
  shared-contracts/    Future generated/shared API contracts
workers/                Future Celery task adapters
infra/                  Docker and deployment configuration
docs/                   Architecture decisions and phase documentation
tests/                  Cross-component fixtures and benchmarks
```

## First milestone

1. Define typed URL-normalization results and errors.
2. Write golden normalization cases.
3. Implement deterministic normalization.
4. Verify normalization idempotency.
5. Benchmark synthetic inputs only.

Do not crawl live websites in Phase 1A.
