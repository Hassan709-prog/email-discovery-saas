# Email Discovery SaaS - Next.js Web Application (`apps/web`)

This directory contains the Phase 3A Next.js frontend MVP for Email Discovery SaaS.

## Architecture

- **Framework**: Next.js App Router (React, TypeScript Strict Mode, Tailwind CSS).
- **Same-Origin Proxy**: Rewrites `/api/v1/:path*` to `API_PROXY_TARGET` (`http://127.0.0.1:8000` by default). Browser makes relative same-origin calls (`credentials: "same-origin"`).
- **Security Model**:
  - Refresh tokens are stored strictly in HttpOnly cookies set with `Path=/api/v1/auth`.
  - CSRF tokens are readable cookies set with `Path=/`.
  - Access tokens are stored strictly in memory (React `AuthContext`). Never written to `localStorage` or `sessionStorage`.
  - Silent refresh is performed on initial app mount via `POST /api/v1/auth/refresh`.
  - Single-flight refresh promise handles concurrent 401 API responses.
  - Multi-tab logout is synchronized via `BroadcastChannel('auth_channel')`.

## Development Commands

```bash
# Install dependencies
npm install

# Run dev server
npm run dev

# Run type checking
npm run type-check

# Run linting
npm run lint

# Run Vitest unit & integration tests
npm run test

# Run production build
npm run build
```
