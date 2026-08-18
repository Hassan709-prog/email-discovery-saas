import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import { AuthProvider, useAuth, LOGOUT_STORAGE_KEY } from '@/context/auth-context';
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

  it('sets status to unauthenticated if silent refresh fails', async () => {
    vi.spyOn(apiClient, 'performSilentRefresh').mockRejectedValue(new Error('No session'));

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
});
