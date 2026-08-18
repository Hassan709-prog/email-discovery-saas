import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Navbar } from '@/components/layout/Navbar';
import * as authContext from '@/context/auth-context';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/',
}));

describe('Header & Navigation Audit', () => {
  const mockLogout = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders exactly one visible brand logo and guest navigation actions', () => {
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      user: null,
      organization: null,
      status: 'unauthenticated',
      login: vi.fn(),
      register: vi.fn(),
      logout: mockLogout,
      logoutAll: vi.fn(),
      refreshSession: vi.fn(),
    });

    render(<Navbar />);

    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(screen.getByText('Email Discovery')).toBeInTheDocument();
    expect(screen.getByText('Help & Tutorial')).toBeInTheDocument();
    expect(screen.getByText('Log in')).toBeInTheDocument();
    expect(screen.getByText('Register')).toBeInTheDocument();
  });

  it('renders authenticated navigation actions and user display name without org ID or OWNER badge', () => {
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      user: { id: 'u1', email: 'user@example.com', display_name: 'Hassan Malik', status: 'ACTIVE' },
      organization: { id: 'org-secret-123', name: 'Personal Workspace', slug: 'workspace-456', role: 'OWNER' },
      status: 'authenticated',
      login: vi.fn(),
      register: vi.fn(),
      logout: mockLogout,
      logoutAll: vi.fn(),
      refreshSession: vi.fn(),
    });

    render(<Navbar />);

    expect(screen.getByText('Hassan Malik')).toBeInTheDocument();
    expect(screen.getByText('Your Scans')).toBeInTheDocument();
    expect(screen.getByText('Start New Scan')).toBeInTheDocument();
    expect(screen.getByText('Logout')).toBeInTheDocument();

    // Verify secret organization ID and internal OWNER badge are NOT exposed
    expect(screen.queryByText('org-secret-123')).not.toBeInTheDocument();
    expect(screen.queryByText('OWNER')).not.toBeInTheDocument();
  });

  it('toggles mobile menu drawer on button click with aria-expanded update', () => {
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      user: null,
      organization: null,
      status: 'unauthenticated',
      login: vi.fn(),
      register: vi.fn(),
      logout: mockLogout,
      logoutAll: vi.fn(),
      refreshSession: vi.fn(),
    });

    render(<Navbar />);

    const toggleBtn = screen.getByRole('button', { name: /Toggle Navigation Menu/i });
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(toggleBtn);
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(toggleBtn);
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'false');
  });
});
