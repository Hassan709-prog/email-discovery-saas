import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import DashboardPage from '@/app/dashboard/page';
import * as apiClient from '@/lib/api-client';
import { ScanJobApiResponse } from '@/types/api';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/dashboard',
}));

vi.mock('@/context/auth-context', () => ({
  useAuth: () => ({
    user: { id: 'u1', email: 'test@example.com', display_name: 'Test User', status: 'ACTIVE' },
    organization: { id: 'o1', name: 'Test Org', slug: 'test-org', role: 'OWNER' },
    status: 'authenticated',
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    logoutAll: vi.fn(),
    refreshSession: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

const mockJob = (id: string, name: string, status: ScanJobApiResponse['status'] = 'DRAFT'): ScanJobApiResponse => ({
  id,
  organization_id: 'org-123',
  created_by_user_id: 'user-123',
  name,
  status,
  source_type: 'MANUAL',
  scanner_version: '1.0.0',
  normalization_version: '1.0.0',
  ranking_version: '1.0.0',
  configuration_snapshot: {},
  total_input_count: 10,
  valid_input_count: 8,
  duplicate_input_count: 1,
  invalid_input_count: 1,
  queued_count: 0,
  running_count: 0,
  completed_count: 0,
  failed_count: 0,
  email_finding_count: 5,
  created_at: new Date().toISOString(),
  started_at: null,
  completed_at: null,
  cancellation_requested_at: null,
});

describe('DashboardPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(apiClient, 'getAccessToken').mockReturnValue('valid-jwt');
    vi.spyOn(apiClient, 'getAnalyticsOverview').mockResolvedValue({
      period: '30d',
      start_at: null,
      end_at: new Date().toISOString(),
      total_scans: 2,
      active_scans: 1,
      websites_submitted: 18,
      websites_processed: 10,
      websites_completed: 10,
      websites_failed: 0,
      emails_discovered: 5,
      successful_processing_rate: 100.0,
      status_distribution: {},
      findings_by_classification: {},
      findings_by_validation_status: {},
      scan_activity_timeline: [],
      recent_completed_scans: [],
    });
  });

  it('renders loading state initially and populates scan jobs list', async () => {
    vi.spyOn(apiClient, 'listScanJobs').mockResolvedValue({
      items: [mockJob('j1', 'Acme Crawl', 'RUNNING'), mockJob('j2', 'Beta Scan', 'COMPLETED')],
      next_cursor: null,
    });

    render(<DashboardPage />);

    expect(screen.getByText(/Loading your scans/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('Acme Crawl')).toBeInTheDocument();
      expect(screen.getByText('Beta Scan')).toBeInTheDocument();
    });

    expect(screen.getAllByText('Scanning').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Completed').length).toBeGreaterThan(0);
  });

  it('renders empty state graphic when zero jobs returned', async () => {
    vi.spyOn(apiClient, 'listScanJobs').mockResolvedValue({
      items: [],
      next_cursor: null,
    });

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText('No scans found')).toBeInTheDocument();
    });
  });

  it('resets items and pagination cursor when status filter changes', async () => {
    const listSpy = vi.spyOn(apiClient, 'listScanJobs').mockResolvedValue({
      items: [mockJob('j1', 'Draft Scan', 'DRAFT')],
      next_cursor: 'cursor-1',
    });

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText('Draft Scan')).toBeInTheDocument();
    });

    const filterSelect = screen.getByLabelText(/Filter Status/i);
    fireEvent.change(filterSelect, { target: { value: 'COMPLETED' } });

    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith({
        limit: 20,
        cursor: undefined,
        status: 'COMPLETED',
      });
    });
  });

  it('deduplicates appended jobs by job ID during pagination', async () => {
    const job1 = mockJob('j1', 'Job 1', 'COMPLETED');
    const job2 = mockJob('j2', 'Job 2', 'COMPLETED');

    vi.spyOn(apiClient, 'listScanJobs')
      .mockResolvedValueOnce({ items: [job1], next_cursor: 'cursor-2' })
      .mockResolvedValueOnce({ items: [job1, job2], next_cursor: null }); // API returns duplicate job1

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText('Job 1')).toBeInTheDocument();
    });

    const loadMoreBtn = screen.getByRole('button', { name: /Load More Scans/i });
    fireEvent.click(loadMoreBtn);

    await waitFor(() => {
      expect(screen.getByText('Job 2')).toBeInTheDocument();
    });

    // Verify Job 1 rendered only once
    expect(screen.getAllByText('Job 1')).toHaveLength(1);
  });

  it('renders accessible chart info buttons that trigger explanation modals dismissible via Escape key', async () => {
    vi.spyOn(apiClient, 'listScanJobs').mockResolvedValue({ items: [], next_cursor: null });

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Information about Findings by Category/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Information about Findings by Review Status/i })).toBeInTheDocument();
    });

    const categoryInfoBtn = screen.getByRole('button', { name: /Information about Findings by Category/i });
    fireEvent.click(categoryInfoBtn);

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getByText('Findings by Category Explained')).toBeInTheDocument();
      expect(screen.getByText(/An email that appears connected to a person, such as/i)).toBeInTheDocument();
    });

    // Press Escape to dismiss modal
    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });

  it('renders the prominent mailbox-verification disclaimer banner on dashboard', async () => {
    vi.spyOn(apiClient, 'listScanJobs').mockResolvedValue({ items: [], next_cursor: null });

    render(<DashboardPage />);

    await waitFor(() => {
      expect(
        screen.getByText(
          /Email Discovery checks email formatting and records where an address was publicly found\. It does not send messages, probe mailboxes, perform SMTP checks, or guarantee deliverability\./i
        )
      ).toBeInTheDocument();
    });
  });
});
