import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import HelpPage from '@/app/help/page';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/help',
}));

vi.mock('@/context/auth-context', () => ({
  useAuth: () => ({
    user: { id: 'u1', email: 'test@example.com', display_name: 'Test User', status: 'ACTIVE' },
    organization: { id: 'o1', name: 'Personal Workspace', slug: 'workspace-123', role: 'OWNER' },
    status: 'authenticated',
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    logoutAll: vi.fn(),
    refreshSession: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

describe('Help & Tutorial Page', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders all category explanations in simple language', () => {
    render(<HelpPage />);

    expect(screen.getByRole('heading', { name: /Findings by Category/i })).toBeInTheDocument();

    // Category 1: Named Contact
    expect(screen.getAllByText(/Named Contact/i).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/An email that appears connected to a person, such as/i)
    ).toBeInTheDocument();

    // Category 2: Role Address
    expect(screen.getAllByText(/Role Address/i).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/A general business inbox for a department or purpose, such as/i)
    ).toBeInTheDocument();

    // Category 3: No-Reply Address
    expect(screen.getAllByText(/No-Reply Address/i).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/An automated address, such as.*that usually should not receive replies/i)
    ).toBeInTheDocument();

    // Category 4: Other
    expect(screen.getAllByText(/Other/i).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/The system found an email but could not confidently place it in the categories above/i)
    ).toBeInTheDocument();
  });

  it('renders all review-status explanations in simple language without claiming mailbox verification', () => {
    render(<HelpPage />);

    expect(screen.getByRole('heading', { name: /Findings by Review Status/i })).toBeInTheDocument();

    // Review Status 1: Format Accepted
    expect(screen.getAllByText(/Format Accepted/i).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/The email has a valid-looking written format. This does not prove that the mailbox exists or receives messages/i)
    ).toBeInTheDocument();

    // Review Status 2: Not Independently Verified
    expect(screen.getAllByText(/Not Independently Verified/i).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/The email was found on a public web page, but the system did not contact, ping, or test the mailbox/i)
    ).toBeInTheDocument();

    // Review Status 3: Rejected Format
    expect(screen.getAllByText(/Rejected Format/i).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/The discovered text looked like an email but failed the system’s formatting or safety checks/i)
    ).toBeInTheDocument();
  });

  it('displays the prominent mailbox-verification disclaimer alert', () => {
    render(<HelpPage />);

    expect(
      screen.getByText(
        /Email Discovery checks email formatting and records where an address was publicly found\. It does not send messages, probe mailboxes, perform SMTP checks, or guarantee deliverability\./i
      )
    ).toBeInTheDocument();
  });
});
