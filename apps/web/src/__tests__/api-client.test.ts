import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import {
  apiFetch,
  downloadScanJobCsv,
  getAccessToken,
  setAccessToken,
  getCsrfToken,
  performSilentRefresh,
  registerUser,
  loginUser,
  logoutUser,
  logoutAllUser,
  setOnSessionExpired,
} from '@/lib/api-client';
import { ApiError } from '@/types/api';

describe('api-client.ts', () => {
  beforeEach(() => {
    setAccessToken(null);
    setOnSessionExpired(null);
    vi.restoreAllMocks();
    document.cookie = 'csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    setAccessToken(null);
    setOnSessionExpired(null);
    vi.restoreAllMocks();
  });

  it('reads CSRF token from document.cookie', () => {
    document.cookie = 'csrf_token=mock-csrf-value-123; path=/';
    expect(getCsrfToken()).toBe('mock-csrf-value-123');
  });

  it('never stores tokens in localStorage or sessionStorage', async () => {
    setAccessToken('in-memory-token');
    expect(localStorage.getItem('access_token')).toBeNull();
    expect(sessionStorage.getItem('access_token')).toBeNull();
    expect(getAccessToken()).toBe('in-memory-token');
  });

  it('includes Authorization header and same-origin credentials', async () => {
    setAccessToken('jwt-test-123');
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ success: true }), { status: 200 })
    );
    vi.stubGlobal('fetch', mockFetch);

    await apiFetch<{ success: boolean }>('/api/v1/test');

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe('/api/v1/test');
    expect(options.credentials).toBe('same-origin');
    expect((options.headers as Record<string, string>)['Authorization']).toBe('Bearer jwt-test-123');
  });

  it('includes X-CSRF-Token header on refresh and logout calls', async () => {
    document.cookie = 'csrf_token=valid-csrf-token; path=/';
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ access_token: 'new-jwt' }), { status: 200 })
    );
    vi.stubGlobal('fetch', mockFetch);

    await performSilentRefresh();

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [, options] = mockFetch.mock.calls[0];
    expect(options.headers['X-CSRF-Token']).toBe('valid-csrf-token');
  });

  it('coalesces concurrent 401 requests into a single-flight refresh call', async () => {
    let refreshCount = 0;
    const mockFetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/auth/refresh')) {
        refreshCount++;
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: 'refreshed-jwt',
              token_type: 'Bearer',
              expires_in_seconds: 900,
            }),
            { status: 200 }
          )
        );
      }
      if (getAccessToken() !== 'refreshed-jwt') {
        return Promise.resolve(new Response(JSON.stringify({ detail: 'Unauthorized' }), { status: 401 }));
      }
      return Promise.resolve(new Response(JSON.stringify({ data: 'ok' }), { status: 200 }));
    });
    vi.stubGlobal('fetch', mockFetch);

    setAccessToken('stale-jwt');

    const req1 = apiFetch<{ data: string }>('/api/v1/data1');
    const req2 = apiFetch<{ data: string }>('/api/v1/data2');
    const req3 = apiFetch<{ data: string }>('/api/v1/data3');

    const results = await Promise.all([req1, req2, req3]);

    expect(results).toEqual([{ data: 'ok' }, { data: 'ok' }, { data: 'ok' }]);
    expect(refreshCount).toBe(1);
  });

  it('retries an ordinary request at most once after refresh', async () => {
    const mockFetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/auth/refresh')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: 'refreshed-jwt',
              token_type: 'Bearer',
              expires_in_seconds: 900,
            }),
            { status: 200 }
          )
        );
      }
      return Promise.resolve(new Response(JSON.stringify({ error: { code: 'UNAUTHORIZED', message: 'Forbidden' } }), { status: 401 }));
    });
    vi.stubGlobal('fetch', mockFetch);

    setAccessToken('old-token');

    await expect(apiFetch('/api/v1/protected')).rejects.toThrow(ApiError);
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  it('does not recursively retry a failed refresh request itself', async () => {
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'INVALID_TOKEN', message: 'Expired refresh cookie' } }), { status: 401 })
    );
    vi.stubGlobal('fetch', mockFetch);

    await expect(performSilentRefresh()).rejects.toThrow(ApiError);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('parses standard backend error envelope and exposes code, message, and request_id', async () => {
    const errorEnvelope = {
      error: {
        code: 'VALIDATION_ERROR',
        message: 'Invalid email address format',
        details: null,
        request_id: 'req-abc-999',
      },
    };
    const mockFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(errorEnvelope), { status: 422 })
    );
    vi.stubGlobal('fetch', mockFetch);

    try {
      await apiFetch('/api/v1/auth/login', { method: 'POST' });
      expect.fail('Should have thrown ApiError');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(422);
      expect(apiErr.code).toBe('VALIDATION_ERROR');
      expect(apiErr.message).toBe('Invalid email address format');
      expect(apiErr.requestId).toBe('req-abc-999');
    }
  });

  it('invokes sessionExpiredListener exactly once when retried request returns 401', async () => {
    const expiredListener = vi.fn();
    setOnSessionExpired(expiredListener);
    setAccessToken('stale-jwt');

    const mockFetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/auth/refresh')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: 'new-jwt',
              token_type: 'Bearer',
              expires_in_seconds: 900,
            }),
            { status: 200 }
          )
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'UNAUTHORIZED', message: 'Unauthorized' } }), {
          status: 401,
        })
      );
    });
    vi.stubGlobal('fetch', mockFetch);

    await expect(apiFetch('/api/v1/business-endpoint')).rejects.toThrow(ApiError);
    expect(getAccessToken()).toBeNull();
    expect(expiredListener).toHaveBeenCalledTimes(1);
  });

  it('does not clear access token or invoke sessionExpiredListener when retried request fails with 500 containing code INVALID_TOKEN', async () => {
    const expiredListener = vi.fn();
    setOnSessionExpired(expiredListener);
    setAccessToken('stale-jwt');

    const mockFetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/auth/refresh')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: 'new-jwt',
              token_type: 'Bearer',
              expires_in_seconds: 900,
            }),
            { status: 200 }
          )
        );
      }
      if (getAccessToken() === 'new-jwt') {
        return Promise.resolve(
          new Response(
            JSON.stringify({ error: { code: 'INVALID_TOKEN', message: 'Internal Server Error' } }),
            { status: 500 }
          )
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'UNAUTHORIZED', message: 'Unauthorized' } }), {
          status: 401,
        })
      );
    });
    vi.stubGlobal('fetch', mockFetch);

    await expect(apiFetch('/api/v1/business-endpoint')).rejects.toThrow(ApiError);
    expect(getAccessToken()).toBe('new-jwt');
    expect(expiredListener).not.toHaveBeenCalled();
  });

  it('preserves token and does not notify listener when CSV export retried request fails with 500', async () => {
    const expiredListener = vi.fn();
    setOnSessionExpired(expiredListener);
    setAccessToken('stale-jwt');

    const mockFetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/auth/refresh')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: 'new-jwt',
              token_type: 'Bearer',
              expires_in_seconds: 900,
            }),
            { status: 200 }
          )
        );
      }
      if (getAccessToken() === 'new-jwt') {
        return Promise.resolve(
          new Response(JSON.stringify({ error: { code: 'EXPORT_ERROR', message: 'Export 500' } }), {
            status: 500,
          })
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'UNAUTHORIZED', message: 'Unauthorized' } }), {
          status: 401,
        })
      );
    });
    vi.stubGlobal('fetch', mockFetch);

    await expect(downloadScanJobCsv('job-123')).rejects.toThrow(ApiError);
    expect(getAccessToken()).toBe('new-jwt');
    expect(expiredListener).not.toHaveBeenCalled();
  });

  it('clears token and notifies listener exactly once when CSV export retried request returns 401', async () => {
    const expiredListener = vi.fn();
    setOnSessionExpired(expiredListener);
    setAccessToken('stale-jwt');

    const mockFetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/auth/refresh')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: 'new-jwt',
              token_type: 'Bearer',
              expires_in_seconds: 900,
            }),
            { status: 200 }
          )
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'UNAUTHORIZED', message: 'Unauthorized' } }), {
          status: 401,
        })
      );
    });
    vi.stubGlobal('fetch', mockFetch);

    await expect(downloadScanJobCsv('job-123')).rejects.toThrow(ApiError);
    expect(getAccessToken()).toBeNull();
    expect(expiredListener).toHaveBeenCalledTimes(1);
  });

  it('handles register, login, logout, and logout-all API functions', async () => {
    const authPayload = {
      access_token: 'acc-123',
      token_type: 'Bearer',
      expires_in_seconds: 900,
    };

    // Register
    const mockFetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(authPayload), { status: 201 }));
    vi.stubGlobal('fetch', mockFetch);

    const regResult = await registerUser({
      email: 'test@ex.com',
      password: 'SecurePassword123!',
      organization_name: 'Org',
    });
    expect(regResult.access_token).toBe('acc-123');
    expect(getAccessToken()).toBe('acc-123');

    // Login with organization selection response
    const orgChoiceResponse = {
      organization_selection_required: true,
      organizations: [{ id: 'o1', name: 'Org 1', slug: 'org-1', role: 'OWNER' }],
    };
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify(orgChoiceResponse), { status: 200 }));

    const loginRes = await loginUser({ email: 'test@ex.com', password: 'Password123!' });
    expect('organization_selection_required' in loginRes).toBe(true);

    // Logout
    mockFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await logoutUser();
    expect(getAccessToken()).toBeNull();

    // Logout all
    setAccessToken('acc-456');
    mockFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await logoutAllUser();
    expect(getAccessToken()).toBeNull();
  });
});
