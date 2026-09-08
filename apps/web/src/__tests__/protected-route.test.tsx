import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProtectedRoute, sanitizeReturnPath } from '@/components/auth/ProtectedRoute';
import * as authContext from '@/context/auth-context';

const mockReplace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({
    replace: mockReplace,
  }),
  usePathname: () => '/dashboard',
}));

describe('ProtectedRoute & sanitizeReturnPath', () => {
  beforeEach(() => {
    mockReplace.mockReset();
  });

  it('sanitizes returnTo paths to prevent open redirects', () => {
    expect(sanitizeReturnPath('/dashboard')).toBe('/dashboard');
    expect(sanitizeReturnPath('/scans/123')).toBe('/scans/123');
    expect(sanitizeReturnPath('//evil.com')).toBe('/dashboard');
    expect(sanitizeReturnPath('http://evil.com')).toBe('/dashboard');
    expect(sanitizeReturnPath('https://evil.com')).toBe('/dashboard');
    expect(sanitizeReturnPath('\\evil.com')).toBe('/dashboard');
    expect(sanitizeReturnPath(null)).toBe('/dashboard');
  });

  it('renders loading spinner when auth status is loading', () => {
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      user: null,
      organization: null,
      status: 'loading',
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      logoutAll: vi.fn(),
      refreshSession: vi.fn(),
      retrySession: vi.fn(),
    });

    render(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    );

    expect(screen.getByText('Loading session...')).toBeInTheDocument();
    expect(screen.queryByText('Protected Content')).not.toBeInTheDocument();
  });

  it('renders Retry Session UI when auth status is restoration_failed', () => {
    const retrySessionMock = vi.fn();
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      user: null,
      organization: null,
      status: 'restoration_failed',
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      logoutAll: vi.fn(),
      refreshSession: vi.fn(),
      retrySession: retrySessionMock,
    });

    render(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    );

    expect(screen.getByText('Session Restoration Failed')).toBeInTheDocument();
    expect(screen.queryByText('Protected Content')).not.toBeInTheDocument();

    // Assert router.replace has not been called automatically
    expect(mockReplace).not.toHaveBeenCalled();

    // Test Retry Session button
    const retryBtn = screen.getByText('Retry Session');
    retryBtn.click();
    expect(retrySessionMock).toHaveBeenCalledTimes(1);

    // Test Sign in again button
    const signInBtn = screen.getByText('Sign in again');
    expect(signInBtn).toBeInTheDocument();
    signInBtn.click();

    expect(mockReplace).toHaveBeenCalledTimes(1);
    expect(mockReplace).toHaveBeenCalledWith('/login?returnTo=%2Fdashboard');
  });

  it('renders protected children when authenticated', () => {
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      user: { id: 'u1', email: 'test@example.com', display_name: null, status: 'ACTIVE' },
      organization: { id: 'o1', name: 'Org', slug: 'org', role: 'OWNER' },
      status: 'authenticated',
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      logoutAll: vi.fn(),
      refreshSession: vi.fn(),
      retrySession: vi.fn(),
    });

    render(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    );

    expect(screen.getByText('Protected Content')).toBeInTheDocument();
  });
});
