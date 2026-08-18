import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProtectedRoute, sanitizeReturnPath } from '@/components/auth/ProtectedRoute';
import * as authContext from '@/context/auth-context';

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    replace: vi.fn(),
  }),
  usePathname: () => '/dashboard',
}));

describe('ProtectedRoute & sanitizeReturnPath', () => {
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
    });

    render(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    );

    expect(screen.getByText('Loading session...')).toBeInTheDocument();
    expect(screen.queryByText('Protected Content')).not.toBeInTheDocument();
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
    });

    render(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    );

    expect(screen.getByText('Protected Content')).toBeInTheDocument();
  });
});
