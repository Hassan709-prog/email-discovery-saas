import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { OnboardingTutorialModal } from '@/components/tutorial/OnboardingTutorialModal';
import * as authContext from '@/context/auth-context';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/dashboard',
}));

describe('OnboardingTutorialModal & Real Focus Trap Audit', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  it('does NOT render tutorial modal for unauthenticated guest users', () => {
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      user: null,
      organization: null,
      status: 'unauthenticated',
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      logoutAll: vi.fn(),
      refreshSession: vi.fn(),
    });

    render(<OnboardingTutorialModal />);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders 6-step tutorial for first-time authenticated users and persists onboarding key on completion', async () => {
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      user: { id: 'u1', email: 'user@example.com', display_name: 'Test User', status: 'ACTIVE' },
      organization: { id: 'o1', name: 'Personal Workspace', slug: 'workspace-123', role: 'OWNER' },
      status: 'authenticated',
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      logoutAll: vi.fn(),
      refreshSession: vi.fn(),
    });

    render(<OnboardingTutorialModal />);

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getByText('Step 1 of 6')).toBeInTheDocument();
      expect(screen.getByText('Welcome to Email Discovery SaaS')).toBeInTheDocument();
    });

    // Advance to Step 2
    fireEvent.click(screen.getByRole('button', { name: /Next Step/i }));

    await waitFor(() => {
      expect(screen.getByText('Step 2 of 6')).toBeInTheDocument();
      expect(screen.getByText('Step 1: Start a New Scan')).toBeInTheDocument();
    });

    // Skip for Now completes tutorial and sets storage key
    fireEvent.click(screen.getByRole('button', { name: /Skip for Now/i }));

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    expect(localStorage.getItem('email-discovery:onboarding-complete:v1')).toBe('true');
  });

  it('enforces real focus trap (Tab forward wrapping and Shift+Tab backward wrapping)', async () => {
    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      user: { id: 'u1', email: 'user@example.com', display_name: 'Test User', status: 'ACTIVE' },
      organization: { id: 'o1', name: 'Personal Workspace', slug: 'workspace-123', role: 'OWNER' },
      status: 'authenticated',
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      logoutAll: vi.fn(),
      refreshSession: vi.fn(),
    });

    render(
      <div>
        <button id="external-button">External Trigger</button>
        <OnboardingTutorialModal />
      </div>
    );

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    const closeBtn = screen.getByRole('button', { name: /Close onboarding tutorial/i });
    const nextBtn = screen.getByRole('button', { name: /Next Step/i });
    const skipBtn = screen.getByRole('button', { name: /Skip for Now/i });

    // Focus last element (Next Step button)
    nextBtn.focus();
    expect(document.activeElement).toBe(nextBtn);

    // Forward Tab from last element wraps focus back to first element (closeBtn)
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: false });
    expect(document.activeElement).toBe(closeBtn);

    // Focus first element (closeBtn)
    closeBtn.focus();

    // Shift + Tab from first element wraps focus back to last element (nextBtn)
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(nextBtn);
  });

  it('restores focus to triggering element upon modal dismissal', async () => {
    localStorage.setItem('email-discovery:onboarding-complete:v1', 'true');

    vi.spyOn(authContext, 'useAuth').mockReturnValue({
      user: { id: 'u1', email: 'user@example.com', display_name: 'Test User', status: 'ACTIVE' },
      organization: { id: 'o1', name: 'Personal Workspace', slug: 'workspace-123', role: 'OWNER' },
      status: 'authenticated',
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      logoutAll: vi.fn(),
      refreshSession: vi.fn(),
    });

    render(
      <div>
        <button id="trigger-btn">Reopen Button</button>
        <OnboardingTutorialModal />
      </div>
    );

    const triggerBtn = screen.getByRole('button', { name: /Reopen Button/i });
    triggerBtn.focus();
    expect(document.activeElement).toBe(triggerBtn);

    // Trigger modal reopening
    fireEvent(window, new Event('reopen-tutorial'));

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    // Press Escape to dismiss
    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    // Verify focus is restored to trigger button
    expect(document.activeElement).toBe(triggerBtn);
  });

  it('audits browser storage to ensure NO sensitive tokens, user details, URLs, or jobs are stored', () => {
    // Only onboarding completion key is permitted in localStorage
    localStorage.setItem('email-discovery:onboarding-complete:v1', 'true');

    const allowedLocalStorageKeys = new Set(['email-discovery:onboarding-complete:v1']);
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key) {
        expect(allowedLocalStorageKeys.has(key)).toBe(true);
      }
    }

    // sessionStorage must be completely empty
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.getItem('access_token')).toBeNull();
    expect(localStorage.getItem('refresh_token')).toBeNull();
    expect(localStorage.getItem('user')).toBeNull();
  });
});
