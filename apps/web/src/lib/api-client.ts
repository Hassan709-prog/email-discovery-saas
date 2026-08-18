import {
  AnalyticsOverviewResponse,
  ApiError,
  ApiErrorEnvelope,
  AuthSuccessResponse,
  CreateScanJobApiRequest,
  FindingEvidenceItemApiResponse,
  LoginRequest,
  OrganizationSelectionRequiredResponse,
  PaginatedResponse,
  PreviewScanInputsApiRequest,
  PreviewScanInputsApiResponse,
  RegisterRequest,
  ScanJobApiResponse,
  ScanJobProgressApiResponse,
  ScanJobResultDetailApiResponse,
  ScanJobResultItemApiResponse,
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

export async function getAnalyticsOverview(
  period: string = '30d'
): Promise<AnalyticsOverviewResponse> {
  return apiFetch<AnalyticsOverviewResponse>(`/api/v1/analytics/overview?period=${encodeURIComponent(period)}`, {
    method: 'GET',
  });
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

// Phase 3C: Results, Evidence, and CSV Export methods
export async function listScanJobResults(
  jobId: string,
  params?: {
    limit?: number;
    cursor?: string;
    classification?: string;
    validation_status?: string;
    email_domain?: string;
    search_prefix?: string;
  },
  signal?: AbortSignal
): Promise<PaginatedResponse<ScanJobResultItemApiResponse>> {
  const query = new URLSearchParams();
  if (params?.limit) query.set('limit', params.limit.toString());
  if (params?.cursor) query.set('cursor', params.cursor);
  if (params?.classification) query.set('classification', params.classification);
  if (params?.validation_status) query.set('validation_status', params.validation_status);
  if (params?.email_domain) query.set('email_domain', params.email_domain);
  if (params?.search_prefix) query.set('search_prefix', params.search_prefix);

  const url = `/api/v1/scan-jobs/${jobId}/results${query.toString() ? `?${query.toString()}` : ''}`;
  return apiFetch<PaginatedResponse<ScanJobResultItemApiResponse>>(url, { method: 'GET', signal });
}

export async function getScanJobResultDetail(
  jobId: string,
  findingId: string,
  signal?: AbortSignal
): Promise<ScanJobResultDetailApiResponse> {
  return apiFetch<ScanJobResultDetailApiResponse>(
    `/api/v1/scan-jobs/${jobId}/results/${findingId}`,
    { method: 'GET', signal }
  );
}

export async function listFindingEvidence(
  jobId: string,
  findingId: string,
  params?: { limit?: number; cursor?: string },
  signal?: AbortSignal
): Promise<PaginatedResponse<FindingEvidenceItemApiResponse>> {
  const query = new URLSearchParams();
  if (params?.limit) query.set('limit', params.limit.toString());
  if (params?.cursor) query.set('cursor', params.cursor);

  const url = `/api/v1/scan-jobs/${jobId}/results/${findingId}/evidence${
    query.toString() ? `?${query.toString()}` : ''
  }`;
  return apiFetch<PaginatedResponse<FindingEvidenceItemApiResponse>>(url, {
    method: 'GET',
    signal,
  });
}

/**
 * Safely parse filename from Content-Disposition header.
 * Rejects path separators, control characters, unicode ambiguity, or non-CSV extension.
 */
export function parseSafeCsvFilename(dispositionHeader: string | null, fallbackFilename: string): string {
  if (!dispositionHeader) return fallbackFilename;

  // Match filename="..." or filename*=utf-8''...
  let filename = '';
  const utf8Match = dispositionHeader.match(/filename\*=utf-8''([^;]+)/i);
  if (utf8Match && utf8Match[1]) {
    try {
      filename = decodeURIComponent(utf8Match[1]);
    } catch {
      filename = '';
    }
  }

  if (!filename) {
    const stdMatch = dispositionHeader.match(/filename="?([^";]+)"?/i);
    if (stdMatch && stdMatch[1]) {
      filename = stdMatch[1];
    }
  }

  filename = filename.trim();

  // Validate filename safety:
  // Must end with .csv, no path separators (/ or \), no control chars, max length 128
  if (
    !filename ||
    filename.length > 128 ||
    /[\r\n\x00-\x1f\/\\]/.test(filename) ||
    !filename.toLowerCase().endsWith('.csv') ||
    !/^[a-zA-Z0-9._-]+$/.test(filename)
  ) {
    return fallbackFilename;
  }

  return filename;
}

export async function downloadScanJobCsv(jobId: string): Promise<string> {
  const endpoint = `/api/v1/scan-jobs/${jobId}/export.csv`;
  const defaultFilename = `scan-job-${jobId}-results.csv`;

  const headers: Record<string, string> = {};
  const token = getAccessToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  let res = await fetch(endpoint, {
    method: 'GET',
    headers,
    credentials: 'same-origin',
  });

  // Handle 401 with silent refresh
  if (res.status === 401 && !endpoint.includes('/auth/refresh')) {
    try {
      const newAccessToken = await performSilentRefresh();
      headers['Authorization'] = `Bearer ${newAccessToken}`;
      res = await fetch(endpoint, {
        method: 'GET',
        headers,
        credentials: 'same-origin',
      });
    } catch (refreshErr) {
      setAccessToken(null);
      if (sessionExpiredListener) sessionExpiredListener();
      throw refreshErr;
    }
  }

  if (!res.ok) {
    let errorEnvelope: ApiErrorEnvelope | null = null;
    try {
      errorEnvelope = await res.json();
    } catch {
      // Non-JSON error
    }
    const detail = errorEnvelope?.error || {
      code: 'EXPORT_FAILED',
      message: `CSV export failed with status ${res.status}`,
    };
    throw new ApiError(res.status, detail);
  }

  const contentType = (res.headers.get('Content-Type') || '').toLowerCase();
  if (!contentType.startsWith('text/csv')) {
    throw new ApiError(400, {
      code: 'INVALID_CONTENT_TYPE',
      message: `Expected text/csv response, but server returned Content-Type "${contentType}"`,
    });
  }

  const blob = await res.blob();
  const disposition = res.headers.get('Content-Disposition');
  const filename = parseSafeCsvFilename(disposition, defaultFilename);

  let objectUrl: string | null = null;
  try {
    const csvBlob = new Blob([blob], { type: 'text/csv; charset=utf-8' });
    objectUrl = URL.createObjectURL(csvBlob);

    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);

    return filename;
  } finally {
    if (objectUrl) {
      const targetUrl = objectUrl;
      setTimeout(() => {
        try {
          URL.revokeObjectURL(targetUrl);
        } catch {
          // Ignore revocation errors
        }
      }, 200);
    }
  }
}
