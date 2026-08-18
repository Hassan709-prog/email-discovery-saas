import {
  ApiError,
  ApiErrorEnvelope,
  AuthSuccessResponse,
  LoginRequest,
  OrganizationSelectionRequiredResponse,
  RegisterRequest,
  UserProfileResponse,
} from '@/types/api';

// In-memory access token storage (NEVER written to localStorage or sessionStorage)
let inMemoryAccessToken: string | null = null;

// Shared single-flight refresh promise to coalesce concurrent 401 requests
let sharedRefreshPromise: Promise<string> | null = null;

// Global callback for refresh failure to clear AuthContext state
let onSessionExpiredCallback: (() => void) | null = null;

export function getAccessToken(): string | null {
  return inMemoryAccessToken;
}

export function setAccessToken(token: string | null): void {
  inMemoryAccessToken = token;
}

export function setOnSessionExpired(callback: (() => void) | null): void {
  onSessionExpiredCallback = callback;
}

/**
 * Extracts readable CSRF token from document.cookie.
 */
export function getCsrfToken(): string {
  if (typeof document === 'undefined') return '';
  const cookies = document.cookie.split(';');
  for (const cookie of cookies) {
    const [name, value] = cookie.trim().split('=');
    if (name === 'csrf_token') {
      return decodeURIComponent(value || '');
    }
  }
  return '';
}

/**
 * Parses HTTP response error envelope into typed ApiError.
 */
async function parseErrorResponse(response: Response): Promise<ApiError> {
  const status = response.status;
  try {
    const data = (await response.json()) as ApiErrorEnvelope | Record<string, unknown>;
    if (data && typeof data === 'object' && 'error' in data && data.error) {
      const err = data.error as { code?: string; message?: string; details?: unknown; request_id?: string };
      return new ApiError(status, {
        code: err.code || 'API_ERROR',
        message: err.message || `Request failed with status ${status}`,
        details: err.details,
        request_id: err.request_id,
      });
    }
    if (data && typeof data === 'object' && 'detail' in data && typeof data.detail === 'string') {
      return new ApiError(status, {
        code: 'API_ERROR',
        message: data.detail,
      });
    }
  } catch {
    // Non-JSON error body
  }
  return new ApiError(status, {
    code: 'HTTP_ERROR',
    message: `Request failed with HTTP status ${status}`,
  });
}

/**
 * Core fetch wrapper with automatic Bearer token, CSRF header, 401 single-flight refresh, and error parsing.
 */
export async function apiFetch<T>(
  endpoint: string,
  options: RequestInit = {},
  isRetry = false
): Promise<T> {
  const headers = new Headers(options.headers || {});
  headers.set('Accept', 'application/json');

  // Inject Bearer access token if available
  if (inMemoryAccessToken && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${inMemoryAccessToken}`);
  }

  // Inject CSRF header for auth endpoints requiring CSRF validation
  if (endpoint.includes('/auth/refresh') || endpoint.includes('/auth/logout')) {
    const csrfToken = getCsrfToken();
    if (csrfToken && !headers.has('X-CSRF-Token')) {
      headers.set('X-CSRF-Token', csrfToken);
    }
  }

  const fetchOptions: RequestInit = {
    ...options,
    headers,
    credentials: 'same-origin',
  };

  const response = await fetch(endpoint, fetchOptions);

  // Handle 401 Unauthorized for ordinary requests
  if (response.status === 401 && !endpoint.includes('/auth/refresh') && !isRetry) {
    try {
      const newToken = await performSilentRefresh();
      // Retry original request exactly once with new token
      const retryHeaders = new Headers(options.headers || {});
      retryHeaders.set('Accept', 'application/json');
      retryHeaders.set('Authorization', `Bearer ${newToken}`);
      
      if (endpoint.includes('/auth/logout')) {
        const csrfToken = getCsrfToken();
        if (csrfToken) retryHeaders.set('X-CSRF-Token', csrfToken);
      }

      return await apiFetch<T>(endpoint, { ...options, headers: retryHeaders }, true);
    } catch {
      // Refresh failed: clear session state and reject with original/refresh error
      setAccessToken(null);
      if (onSessionExpiredCallback) {
        onSessionExpiredCallback();
      }
      throw await parseErrorResponse(response);
    }
  }

  if (!response.ok) {
    throw await parseErrorResponse(response);
  }

  // 204 No Content handling
  if (response.status === 204) {
    return {} as T;
  }

  return (await response.json()) as T;
}

/**
 * Performs silent refresh. Coalesces concurrent calls into a single flight promise.
 */
export async function performSilentRefresh(): Promise<string> {
  if (sharedRefreshPromise) {
    return sharedRefreshPromise;
  }

  sharedRefreshPromise = (async () => {
    try {
      const csrfToken = getCsrfToken();
      const headers: Record<string, string> = {
        Accept: 'application/json',
      };
      if (csrfToken) {
        headers['X-CSRF-Token'] = csrfToken;
      }

      const res = await fetch('/api/v1/auth/refresh', {
        method: 'POST',
        headers,
        credentials: 'same-origin',
      });

      if (!res.ok) {
        const error = await parseErrorResponse(res);
        setAccessToken(null);
        throw error;
      }

      const data = (await res.json()) as AuthSuccessResponse;
      setAccessToken(data.access_token);
      return data.access_token;
    } finally {
      sharedRefreshPromise = null;
    }
  })();

  return sharedRefreshPromise;
}

// Typed API Authentication Service Calls

export async function registerUser(payload: RegisterRequest): Promise<AuthSuccessResponse> {
  const data = await apiFetch<AuthSuccessResponse>('/api/v1/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  setAccessToken(data.access_token);
  return data;
}

export async function loginUser(
  payload: LoginRequest
): Promise<AuthSuccessResponse | OrganizationSelectionRequiredResponse> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  
  const res = await fetch('/api/v1/auth/login', {
    method: 'POST',
    headers,
    credentials: 'same-origin',
    body: JSON.stringify(payload),
  });

  if (res.status === 400) {
    const data = await res.json();
    if (data && typeof data === 'object' && 'organization_selection_required' in data && data.organization_selection_required) {
      return data as OrganizationSelectionRequiredResponse;
    }
  }

  if (!res.ok) {
    throw await parseErrorResponse(res);
  }

  const data = (await res.json()) as AuthSuccessResponse;
  setAccessToken(data.access_token);
  return data;
}

export async function getCurrentUser(): Promise<UserProfileResponse> {
  return await apiFetch<UserProfileResponse>('/api/v1/auth/me');
}

export async function logoutUser(): Promise<void> {
  try {
    await apiFetch<void>('/api/v1/auth/logout', { method: 'POST' });
  } finally {
    setAccessToken(null);
  }
}

export async function logoutAllUser(): Promise<void> {
  try {
    await apiFetch<void>('/api/v1/auth/logout-all', { method: 'POST' });
  } finally {
    setAccessToken(null);
  }
}
