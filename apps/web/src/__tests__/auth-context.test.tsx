import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import { AuthProvider, useAuth, LOGOUT_STORAGE_KEY } from '@/context/auth-context';
import { ApiError } from '@/types/api';
import * as apiClient from '@/lib/api-client';

const TestComponent = () => {
  const { user, organization, status, logout, logoutAll } = useAuth();
  return (
    <div>
      <div data-testid="status">{status}</div>
      <div data-testid="user-email">{user?.email || 'no-user'}</div>
      <div data-testid="org-name">{organization?.name || 'no-org'}</div>
      <button onClick={() => logout()}>Logout</button>
      <button onClick={() => logoutAll()}>Logout All</button>
    </div>
  );
};

describe('AuthContext', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    apiClient.setAccessToken(null);
    localStorage.clear();
    sessionStorage.clear();
  });

  it('performs silent refresh on app mount and sets authenticated user profile', async () => {
    vi.spyOn(apiClient, 'performSilentRefresh').mockResolvedValue('jwt-token-123');
    vi.spyOn(apiClient, 'getCurrentUser').mockResolvedValue({
      id: 'u1',
      email: 'alex@example.com',
      display_name: 'Alex',
      status: 'ACTIVE',
      organization_id: 'o1',
      organization_name: 'Alex Org',
      organization_slug: 'alex-org',
      role: 'OWNER',
    });

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    expect(screen.getByTestId('status')).toHaveTextContent('loading');

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated');
    });

    expect(screen.getByTestId('user-email')).toHaveTextContent('alex@example.com');
    expect(screen.getByTestId('org-name')).toHaveTextContent('Alex Org');
  });

  it('sets status to unauthenticated if silent refresh fails with confirmed 401', async () => {
    vi.spyOn(apiClient, 'performSilentRefresh').mockRejectedValue(
      new ApiError(401, { code: 'UNAUTHORIZED', message: 'No session' })
    );

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated');
    });

    expect(screen.getByTestId('user-email')).toHaveTextContent('no-user');
  });

  it('enters restoration_failed status on transient 5xx/network error without setting unauthenticated', async () => {
    vi.spyOn(apiClient, 'performSilentRefresh').mockRejectedValue(
      new ApiError(500, { code: 'INTERNAL_SERVER_ERROR', message: 'DB temp error' })
    );

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('restoration_failed');
    });

    expect(screen.getByTestId('user-email')).toHaveTextContent('no-user');
  });

  it('allows deliberate retrySession click to perform a new restoration attempt after transient failure', async () => {
    const refreshSpy = vi
      .spyOn(apiClient, 'performSilentRefresh')
      .mockRejectedValueOnce(new ApiError(500, { code: 'SERVER_ERROR', message: '500 Internal Error' }))
      .mockResolvedValueOnce('jwt-token-123');

    vi.spyOn(apiClient, 'getCurrentUser').mockResolvedValue({
      id: 'u1',
      email: 'alex@example.com',
      display_name: 'Alex',
      status: 'ACTIVE',
      organization_id: 'o1',
      organization_name: 'Alex Org',
      organization_slug: 'alex-org',
      role: 'OWNER',
    });

    let retryFn: () => Promise<void> = async () => {};
    const Harness = () => {
      const { status, user, retrySession } = useAuth();
      retryFn = retrySession;
      return (
        <div>
          <div data-testid="status">{status}</div>
          <div data-testid="user-email">{user?.email || 'no-user'}</div>
        </div>
      );
    };

    render(
      <AuthProvider>
        <Harness />
      </AuthProvider>
    );

    // 1. Transient bootstrap failure enters restoration_failed
    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('restoration_failed');
    });
    expect(refreshSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('user-email')).toHaveTextContent('no-user');

    // 2. User retry performs one new restoration attempt and authenticates user
    await act(async () => {
      await retryFn();
    });

    expect(refreshSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('status')).toHaveTextContent('authenticated');
    expect(screen.getByTestId('user-email')).toHaveTextContent('alex@example.com');
  });

  it('keeps restoration_failed retryable if second retry attempt also fails with transient error', async () => {
    const refreshSpy = vi
      .spyOn(apiClient, 'performSilentRefresh')
      .mockRejectedValue(new ApiError(503, { code: 'UNAVAILABLE', message: '503 Service Unavailable' }));

    let retryFn: () => Promise<void> = async () => {};
    const Harness = () => {
      const { status, retrySession } = useAuth();
      retryFn = retrySession;
      return <div data-testid="status">{status}</div>;
    };

    render(
      <AuthProvider>
        <Harness />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('restoration_failed');
    });
    expect(refreshSpy).toHaveBeenCalledTimes(1);

    // Retry fails again
    await act(async () => {
      await retryFn();
    });

    expect(refreshSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('status')).toHaveTextContent('restoration_failed');
  });

  it('clears session to unauthenticated when retrySession receives confirmed HTTP 401', async () => {
    vi.spyOn(apiClient, 'performSilentRefresh')
      .mockRejectedValueOnce(new ApiError(500, { code: 'SERVER_ERROR', message: 'Transient 500' }))
      .mockRejectedValueOnce(new ApiError(401, { code: 'UNAUTHORIZED', message: 'Session expired' }));

    let retryFn: () => Promise<void> = async () => {};
    const Harness = () => {
      const { status, retrySession } = useAuth();
      retryFn = retrySession;
      return <div data-testid="status">{status}</div>;
    };

    render(
      <AuthProvider>
        <Harness />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('restoration_failed');
    });

    await act(async () => {
      await retryFn();
    });

    expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated');
  });

  it('shares single in-flight request across multiple simultaneous retry clicks', async () => {
    let resolveRefresh: ((token: string) => void) | null = null;
    const refreshPromise = new Promise<string>((resolve) => {
      resolveRefresh = resolve;
    });

    const refreshSpy = vi.spyOn(apiClient, 'performSilentRefresh').mockReturnValue(refreshPromise);
    vi.spyOn(apiClient, 'getCurrentUser').mockResolvedValue({
      id: 'u1',
      email: 'alex@example.com',
      display_name: 'Alex',
      status: 'ACTIVE',
      organization_id: 'o1',
      organization_name: 'Alex Org',
      organization_slug: 'alex-org',
      role: 'OWNER',
    });

    let retryFn: () => Promise<void> = async () => {};
    const Harness = () => {
      const { status, retrySession } = useAuth();
      retryFn = retrySession;
      return <div data-testid="status">{status}</div>;
    };

    render(
      <AuthProvider>
        <Harness />
      </AuthProvider>
    );

    // Initial mount calls performSilentRefresh
    expect(refreshSpy).toHaveBeenCalledTimes(1);

    // Trigger multiple simultaneous retry clicks while in flight
    let p1: Promise<void> | null = null;
    let p2: Promise<void> | null = null;
    act(() => {
      p1 = retryFn();
      p2 = retryFn();
    });

    // Both clicks share the initial in-flight request
    expect(refreshSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveRefresh!('jwt-token-123');
      await Promise.all([p1, p2]);
    });

    expect(screen.getByTestId('status')).toHaveTextContent('authenticated');
  });

  it('executes exactly one bootstrap refresh across StrictMode double-mount', async () => {
    const refreshSpy = vi.spyOn(apiClient, 'performSilentRefresh').mockResolvedValue('jwt-token-123');
    vi.spyOn(apiClient, 'getCurrentUser').mockResolvedValue({
      id: 'u1',
      email: 'alex@example.com',
      display_name: 'Alex',
      status: 'ACTIVE',
      organization_id: 'o1',
      organization_name: 'Alex Org',
      organization_slug: 'alex-org',
      role: 'OWNER',
    });

    render(
      <React.StrictMode>
        <AuthProvider>
          <TestComponent />
        </AuthProvider>
      </React.StrictMode>
    );

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated');
    });

    expect(refreshSpy).toHaveBeenCalledTimes(1);
  });

  it('clears state and broadcasts logout on user logout', async () => {
    vi.spyOn(apiClient, 'performSilentRefresh').mockResolvedValue('jwt-token-123');
    vi.spyOn(apiClient, 'getCurrentUser').mockResolvedValue({
      id: 'u1',
      email: 'alex@example.com',
      display_name: 'Alex',
      status: 'ACTIVE',
      organization_id: 'o1',
      organization_name: 'Alex Org',
      organization_slug: 'alex-org',
      role: 'OWNER',
    });
    vi.spyOn(apiClient, 'logoutUser').mockResolvedValue();

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated');
    });

    await act(async () => {
      screen.getByText('Logout').click();
    });

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated');
    });
    expect(screen.getByTestId('user-email')).toHaveTextContent('no-user');

    // Verify storage marker was written without sensitive token data
    expect(localStorage.getItem(LOGOUT_STORAGE_KEY)).not.toBeNull();
    expect(localStorage.getItem('access_token')).toBeNull();
    expect(localStorage.getItem('refresh_token')).toBeNull();
  });

  it('synchronizes multi-tab logout via StorageEvent fallback', async () => {
    vi.spyOn(apiClient, 'performSilentRefresh').mockResolvedValue('jwt-token-123');
    vi.spyOn(apiClient, 'getCurrentUser').mockResolvedValue({
      id: 'u1',
      email: 'alex@example.com',
      display_name: 'Alex',
      status: 'ACTIVE',
      organization_id: 'o1',
      organization_name: 'Alex Org',
      organization_slug: 'alex-org',
      role: 'OWNER',
    });

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated');
    });

    // Simulate cross-tab logout storage event
    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', {
          key: LOGOUT_STORAGE_KEY,
          newValue: Date.now().toString(),
        })
      );
    });

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated');
    });
    expect(screen.getByTestId('user-email')).toHaveTextContent('no-user');
  });

  it('resets ambiguous refresh state when user logs in successfully, allowing subsequent retrySession calls', async () => {
    const refreshSpy = vi
      .spyOn(apiClient, 'performSilentRefresh')
      .mockRejectedValue(new ApiError(500, { code: 'SERVER_ERROR', message: '500 Error' }));
    vi.spyOn(apiClient, 'loginUser').mockResolvedValue({
      access_token: 'login-token-999',
      token_type: 'Bearer',
      expires_in_seconds: 900,
    });
    vi.spyOn(apiClient, 'getCurrentUser').mockResolvedValue({
      id: 'u2',
      email: 'login@example.com',
      display_name: 'Login User',
      status: 'ACTIVE',
      organization_id: 'o2',
      organization_name: 'Login Org',
      organization_slug: 'login-org',
      role: 'MEMBER',
    });

    let loginFn: (p: any) => Promise<any> = async () => {};
    let refreshFn: () => Promise<void> = async () => {};
    const Harness = () => {
      const { status, login, refreshSession } = useAuth();
      loginFn = login;
      refreshFn = refreshSession;
      return <div data-testid="status">{status}</div>;
    };

    render(
      <AuthProvider>
        <Harness />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('restoration_failed');
    });
    expect(refreshSpy).toHaveBeenCalledTimes(1);

    // Call background refreshSession while ambiguous guard is active -> blocked!
    await act(async () => {
      await refreshFn();
    });
    expect(refreshSpy).toHaveBeenCalledTimes(1);

    // Perform login -> resets ambiguous guard!
    await act(async () => {
      await loginFn({ email: 'login@example.com', password: 'Password123!' });
    });
    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated');
    });

    // Call background refreshSession after login -> now unblocked and issues refresh!
    await act(async () => {
      await refreshFn();
    });
    expect(refreshSpy).toHaveBeenCalledTimes(2);
  });

  it('resets ambiguous refresh state when user registers successfully, allowing subsequent refreshSession calls', async () => {
    const refreshSpy = vi
      .spyOn(apiClient, 'performSilentRefresh')
      .mockRejectedValue(new ApiError(500, { code: 'SERVER_ERROR', message: '500 Error' }));
    vi.spyOn(apiClient, 'registerUser').mockResolvedValue({
      access_token: 'reg-token-888',
      token_type: 'Bearer',
      expires_in_seconds: 900,
    });
    vi.spyOn(apiClient, 'getCurrentUser').mockResolvedValue({
      id: 'u3',
      email: 'reg@example.com',
      display_name: 'Reg User',
      status: 'ACTIVE',
      organization_id: 'o3',
      organization_name: 'Reg Org',
      organization_slug: 'reg-org',
      role: 'OWNER',
    });

    let regFn: (p: any) => Promise<any> = async () => {};
    let refreshFn: () => Promise<void> = async () => {};
    const Harness = () => {
      const { status, register, refreshSession } = useAuth();
      regFn = register;
      refreshFn = refreshSession;
      return <div data-testid="status">{status}</div>;
    };

    render(
      <AuthProvider>
        <Harness />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('restoration_failed');
    });
    expect(refreshSpy).toHaveBeenCalledTimes(1);

    // Call background refreshSession while ambiguous guard is active -> blocked!
    await act(async () => {
      await refreshFn();
    });
    expect(refreshSpy).toHaveBeenCalledTimes(1);

    // Perform registration -> resets ambiguous guard!
    await act(async () => {
      await regFn({ email: 'reg@example.com', password: 'Password123!', organization_name: 'Reg Org' });
    });
    await waitFor(() => {
      expect(screen.getByTestId('status')).toHaveTextContent('authenticated');
    });

    // Call background refreshSession after registration -> now unblocked and issues refresh!
    await act(async () => {
      await refreshFn();
    });
    expect(refreshSpy).toHaveBeenCalledTimes(2);
  });
});
