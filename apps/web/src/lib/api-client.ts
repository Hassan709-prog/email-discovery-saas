import {
  ApiError,
  ApiErrorEnvelope,
  AuthSuccessResponse,
  CreateScanJobApiRequest,
  LoginRequest,
  OrganizationSelectionRequiredResponse,
  PaginatedResponse,
  PreviewScanInputsApiRequest,
  PreviewScanInputsApiResponse,
  RegisterRequest,
  ScanJobApiResponse,
  ScanJobProgressApiResponse,
  ScanURLApiResponse,
  UserProfileResponse,
} from '@/types/api';

let inMemoryAccessToken: string | null = null;
let sessionExpiredListener: (() => void) | null = null;

export const setAccessToken = (token: string | null): void => {
  inMemoryAccessToken = token;
};

export const getAccessToken = (): string | null => {
  return inMemoryAccessToken;
};

export const setOnSessionExpired = (listener: (() => void) | null): void => {
  sessionExpiredListener = listener;
};

export const getCsrfToken = (): string | null => {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
};

let sharedRefreshPromise: Promise<string> | null = null;

export async function performSilentRefresh(): Promise<string> {
  if (sharedRefreshPromise) {
    return sharedRefreshPromise;
  }

  sharedRefreshPromise = (async () => {
    try {
      const csrfToken = getCsrfToken();
      const headers: Record<string, string> = {};
      if (csrfToken) {
        headers['X-CSRF-Token'] = csrfToken;
      }

      const res = await fetch('/api/v1/auth/refresh', {
        method: 'POST',
        headers,
        credentials: 'same-origin',
      });

      if (!res.ok) {
        let errorData: ApiErrorEnvelope | null = null;
        try {
          errorData = await res.json();
        } catch {
          // Response body empty or non-JSON
        }
        const detail = errorData?.error || {
          code: 'REFRESH_FAILED',
          message: 'Silent refresh failed.',
        };
        throw new ApiError(res.status, detail);
      }

      const data: AuthSuccessResponse = await res.json();
      setAccessToken(data.access_token);
      return data.access_token;
    } finally {
      sharedRefreshPromise = null;
    }
  })();

  return sharedRefreshPromise;
}

export async function apiFetch<T>(
  endpoint: string,
  options: RequestInit = {},
  allowRetryOn401 = true
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };

  const token = getAccessToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(endpoint, {
    ...options,
    headers,
    credentials: 'same-origin',
  });

  if (res.status === 401 && allowRetryOn401 && !endpoint.includes('/auth/refresh')) {
    try {
      const newAccessToken = await performSilentRefresh();
      headers['Authorization'] = `Bearer ${newAccessToken}`;
      const retryRes = await fetch(endpoint, {
        ...options,
        headers,
        credentials: 'same-origin',
      });

      if (!retryRes.ok) {
        let errorEnvelope: ApiErrorEnvelope | null = null;
        try {
          errorEnvelope = await retryRes.json();
        } catch {
          // Ignore parse errors
        }
        const detail = errorEnvelope?.error || {
          code: 'UNAUTHORIZED',
          message: 'Authentication failed after token refresh.',
        };
        if (retryRes.status === 401 && sessionExpiredListener) {
          sessionExpiredListener();
        }
        throw new ApiError(retryRes.status, detail);
      }

      if (retryRes.status === 204) {
        return {} as T;
      }

      return (await retryRes.json()) as T;
    } catch (refreshErr) {
      setAccessToken(null);
      if (sessionExpiredListener) {
        sessionExpiredListener();
      }
      throw refreshErr;
    }
  }

  if (!res.ok) {
    let errorEnvelope: ApiErrorEnvelope | null = null;
    try {
      errorEnvelope = await res.json();
    } catch {
      // Ignore parse errors
    }
    const detail = errorEnvelope?.error || {
      code: 'HTTP_ERROR',
      message: `Request failed with status ${res.status}`,
    };
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) {
    return {} as T;
  }

  return (await res.json()) as T;
}

// Authentication API methods
export async function registerUser(payload: RegisterRequest): Promise<AuthSuccessResponse> {
  const data = await apiFetch<AuthSuccessResponse>('/api/v1/auth/register', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  setAccessToken(data.access_token);
  return data;
}

export async function loginUser(
  payload: LoginRequest
): Promise<AuthSuccessResponse | OrganizationSelectionRequiredResponse> {
  const data = await apiFetch<AuthSuccessResponse | OrganizationSelectionRequiredResponse>(
    '/api/v1/auth/login',
    {
      method: 'POST',
      body: JSON.stringify(payload),
    }
  );

  if ('access_token' in data && data.access_token) {
    setAccessToken(data.access_token);
  }
  return data;
}

export async function getCurrentUser(): Promise<UserProfileResponse> {
  return apiFetch<UserProfileResponse>('/api/v1/auth/me', {
    method: 'GET',
  });
}

export async function logoutUser(): Promise<void> {
  const csrfToken = getCsrfToken();
  const headers: Record<string, string> = {};
  if (csrfToken) {
    headers['X-CSRF-Token'] = csrfToken;
  }

  try {
    await apiFetch<void>('/api/v1/auth/logout', {
      method: 'POST',
      headers,
    });
  } finally {
    setAccessToken(null);
  }
}

export async function logoutAllUser(): Promise<void> {
  try {
    await apiFetch<void>('/api/v1/auth/logout-all', {
      method: 'POST',
    });
  } finally {
    setAccessToken(null);
  }
}

// Scan Jobs API methods
export async function listScanJobs(params?: {
  limit?: number;
  cursor?: string;
  status?: string;
}): Promise<PaginatedResponse<ScanJobApiResponse>> {
  const query = new URLSearchParams();
  if (params?.limit) query.set('limit', params.limit.toString());
  if (params?.cursor) query.set('cursor', params.cursor);
  if (params?.status) query.set('status', params.status);

  const url = `/api/v1/scan-jobs${query.toString() ? `?${query.toString()}` : ''}`;
  return apiFetch<PaginatedResponse<ScanJobApiResponse>>(url, { method: 'GET' });
}

export async function previewScanInputs(
  payload: PreviewScanInputsApiRequest
): Promise<PreviewScanInputsApiResponse> {
  return apiFetch<PreviewScanInputsApiResponse>('/api/v1/scan-jobs/preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function createScanJob(
  payload: CreateScanJobApiRequest,
  idempotencyKey?: string
): Promise<ScanJobApiResponse> {
  const headers: Record<string, string> = {};
  if (idempotencyKey) {
    headers['Idempotency-Key'] = idempotencyKey;
  }

  return apiFetch<ScanJobApiResponse>('/api/v1/scan-jobs', {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  });
}

export async function getScanJob(jobId: string): Promise<ScanJobApiResponse> {
  return apiFetch<ScanJobApiResponse>(`/api/v1/scan-jobs/${jobId}`, {
    method: 'GET',
  });
}

export async function getScanJobProgress(
  jobId: string,
  signal?: AbortSignal
): Promise<ScanJobProgressApiResponse> {
  return apiFetch<ScanJobProgressApiResponse>(`/api/v1/scan-jobs/${jobId}/progress`, {
    method: 'GET',
    signal,
  });
}

export async function listScanJobUrls(
  jobId: string,
  params?: { limit?: number; cursor?: string; status?: string }
): Promise<PaginatedResponse<ScanURLApiResponse>> {
  const query = new URLSearchParams();
  if (params?.limit) query.set('limit', params.limit.toString());
  if (params?.cursor) query.set('cursor', params.cursor);
  if (params?.status) query.set('status', params.status);

  const url = `/api/v1/scan-jobs/${jobId}/urls${query.toString() ? `?${query.toString()}` : ''}`;
  return apiFetch<PaginatedResponse<ScanURLApiResponse>>(url, { method: 'GET' });
}

export async function queueScanJob(jobId: string): Promise<ScanJobApiResponse> {
  return apiFetch<ScanJobApiResponse>(`/api/v1/scan-jobs/${jobId}/queue`, {
    method: 'POST',
  });
}

export async function cancelScanJob(jobId: string): Promise<ScanJobApiResponse> {
  return apiFetch<ScanJobApiResponse>(`/api/v1/scan-jobs/${jobId}/cancel`, {
    method: 'POST',
  });
}
