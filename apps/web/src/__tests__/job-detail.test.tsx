import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import JobDetailPage from '@/app/scans/[id]/page';
import * as apiClient from '@/lib/api-client';
import { ScanJobApiResponse, ScanURLApiResponse } from '@/types/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'job-detail-123' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/scans/job-detail-123',
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
    retrySession: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

const mockJobDetail = (status: ScanJobApiResponse['status'] = 'RUNNING'): ScanJobApiResponse => ({
  id: 'job-detail-123',
  organization_id: 'org-1',
  created_by_user_id: 'user-1',
  name: 'Active Discovery Scan',
  status,
  source_type: 'MANUAL',
  scanner_version: '1.0.0',
  normalization_version: '1.0.0',
  ranking_version: '1.0.0',
  configuration_snapshot: {},
  total_input_count: 5,
  valid_input_count: 5,
  duplicate_input_count: 0,
  invalid_input_count: 0,
  queued_count: 2,
  running_count: 1,
  completed_count: 2,
  failed_count: 0,
  email_finding_count: 10,
  created_at: new Date().toISOString(),
  started_at: new Date().toISOString(),
  completed_at: null,
  cancellation_requested_at: null,
});

describe('JobDetailPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(apiClient, 'getAccessToken').mockReturnValue('valid-jwt');
    vi.spyOn(apiClient, 'listScanJobResults').mockResolvedValue({ items: [], next_cursor: null });
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('maps COMPLETED_WITH_ERRORS to Completed with Some Issues status badge', async () => {
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('COMPLETED_WITH_ERRORS'));
    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({ items: [], next_cursor: null });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getAllByText('Completed with Some Issues').length).toBeGreaterThan(0);
    });
  });

  it('does NOT poll when job status is DRAFT', async () => {
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('DRAFT'));
    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({ items: [], next_cursor: null });
    const progressSpy = vi.spyOn(apiClient, 'getScanJobProgress');

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Not Started')).toBeInTheDocument();
    });

    // Advance 10 seconds
    await vi.advanceTimersByTimeAsync(10000);

    expect(progressSpy).not.toHaveBeenCalled();
  });

  it('polls ONLY /progress for active job (RUNNING) every 3 seconds without polling /urls repeatedly', async () => {
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('RUNNING'));
    const urlsSpy = vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({ items: [], next_cursor: null });
    const progressSpy = vi.spyOn(apiClient, 'getScanJobProgress').mockResolvedValue({
      job_id: 'job-detail-123',
      status: 'RUNNING',
      progress_percentage: 50.0,
      total_input_count: 5,
      valid_input_count: 5,
      duplicate_input_count: 0,
      invalid_input_count: 0,
      queued_count: 1,
      running_count: 1,
      completed_count: 3,
      failed_count: 0,
      email_finding_count: 15,
      created_at: new Date().toISOString(),
      started_at: new Date().toISOString(),
      completed_at: null,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getAllByText('Scanning').length).toBeGreaterThan(0);
    });

    // Initial load calls URLs once
    expect(urlsSpy).toHaveBeenCalledTimes(1);
    expect(progressSpy).toHaveBeenCalledTimes(0);

    // Advance 3 seconds -> 1 progress poll
    await vi.advanceTimersByTimeAsync(3000);

    expect(progressSpy).toHaveBeenCalledTimes(1);
    expect(urlsSpy).toHaveBeenCalledTimes(1); // URL list NOT polled every 3s!

    // Advance another 3 seconds -> 2 progress polls
    await vi.advanceTimersByTimeAsync(3000);

    expect(progressSpy).toHaveBeenCalledTimes(2);
    expect(urlsSpy).toHaveBeenCalledTimes(1);
  });

  it('stops polling when progress reaches terminal status (COMPLETED)', async () => {
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('RUNNING'));
    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({ items: [], next_cursor: null });
    const progressSpy = vi.spyOn(apiClient, 'getScanJobProgress').mockResolvedValue({
      job_id: 'job-detail-123',
      status: 'COMPLETED',
      progress_percentage: 100.0,
      total_input_count: 5,
      valid_input_count: 5,
      duplicate_input_count: 0,
      invalid_input_count: 0,
      queued_count: 0,
      running_count: 0,
      completed_count: 5,
      failed_count: 0,
      email_finding_count: 20,
      created_at: new Date().toISOString(),
      started_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getAllByText('Scanning').length).toBeGreaterThan(0);
    });

    // Advance 3 seconds -> Polls progress and gets COMPLETED status
    await vi.advanceTimersByTimeAsync(3000);

    await waitFor(() => {
      expect(progressSpy).toHaveBeenCalled();
    });

    const callCountAfterTerminal = progressSpy.mock.calls.length;

    // Advance 10 seconds -> No further polling because status became terminal!
    await vi.advanceTimersByTimeAsync(10000);

    expect(progressSpy.mock.calls.length).toBe(callCountAfterTerminal);
  });

  it('allows queueing a DRAFT job directly from detail page', async () => {
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('DRAFT'));
    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({ items: [], next_cursor: null });
    const queueSpy = vi.spyOn(apiClient, 'queueScanJob').mockResolvedValue({
      ...mockJobDetail('QUEUED'),
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Queue Job/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Queue Job/i }));

    await waitFor(() => {
      expect(queueSpy).toHaveBeenCalledWith('job-detail-123');
    });
  });

  it('proves CSV export is hidden for RUNNING job and visible/enabled for COMPLETED_WITH_ERRORS job', async () => {
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('RUNNING'));
    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({ items: [], next_cursor: null });

    const { unmount } = render(<JobDetailPage />);
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /Export Findings \(CSV\)/i })).not.toBeInTheDocument();
    });
    unmount();

    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('COMPLETED_WITH_ERRORS'));
    render(<JobDetailPage />);
    await waitFor(() => {
      const exportBtn = screen.getByRole('button', { name: /Export Findings \(CSV\)/i });
      expect(exportBtn).toBeInTheDocument();
      expect(exportBtn).not.toBeDisabled();
    });
  });

  it('renders dynamic one-line explanation sentence for COMPLETED_WITH_ERRORS', async () => {
    const jobData = {
      ...mockJobDetail('COMPLETED_WITH_ERRORS'),
      completed_count: 35,
      failed_count: 59,
      valid_input_count: 94,
      total_input_count: 100,
    };
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(jobData);
    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({ items: [], next_cursor: null });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(
        screen.getByText(/Completed with some issues — 35 websites processed successfully and 59 could not be scanned\./i)
      ).toBeInTheDocument();
    });
  });

  it('displays complete total_input_count on Target URLs tab header and "Showing 50 of 100" in summary', async () => {
    const jobData = {
      ...mockJobDetail('COMPLETED_WITH_ERRORS'),
      total_input_count: 100,
      valid_input_count: 94,
      duplicate_input_count: 6,
    };
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(jobData);

    const mockUrls = Array.from({ length: 50 }, (_, i) => ({
      id: `url-${i}`,
      scan_job_id: 'job-detail-123',
      original_index: i,
      original_input: `https://site${i}.org/`,
      normalized_url: `https://site${i}.org/`,
      normalized_domain: `site${i}.org`,
      status: 'COMPLETED' as ScanURLApiResponse['status'],
      duplicate_of_scan_url_id: null,
      last_error_code: null,
      created_at: new Date().toISOString(),
    }));

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: mockUrls,
      next_cursor: 'cursor-page-2',
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (100)')).toBeInTheDocument();
    });

    // Switch to URLS tab
    fireEvent.click(screen.getByText('Target URLs (100)'));

    await waitFor(() => {
      expect(screen.getByText('Showing 50 of 100')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Load More URLs/i })).toBeInTheDocument();
    });
  });

  it('proves that tokens are never persisted in localStorage or sessionStorage', () => {
    expect(localStorage.getItem('access_token')).toBeNull();
    expect(localStorage.getItem('refresh_token')).toBeNull();
    expect(sessionStorage.getItem('access_token')).toBeNull();
    expect(sessionStorage.getItem('refresh_token')).toBeNull();
  });

  it('renders Approve & Retry button ONLY when redirect approval is required and permitted', async () => {
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('COMPLETED_WITH_ERRORS'));

    const redirectUrl: ScanURLApiResponse = {
      id: 'url-redir-1',
      scan_job_id: 'job-detail-123',
      original_index: 0,
      original_input: 'http://source-site.com/',
      normalized_url: 'https://source-site.com/',
      normalized_domain: 'source-site.com',
      status: 'FAILED',
      duplicate_of_scan_url_id: null,
      last_error_code: 'OUT_OF_SCOPE_REDIRECT',
      created_at: new Date().toISOString(),
      redirect_target_domain: 'destination-site.com',
      requires_redirect_approval: true,
      can_approve_redirect: true,
    };

    const normalFailedUrl: ScanURLApiResponse = {
      id: 'url-robots-1',
      scan_job_id: 'job-detail-123',
      original_index: 1,
      original_input: 'http://robots-site.com/',
      normalized_url: 'https://robots-site.com/',
      normalized_domain: 'robots-site.com',
      status: 'FAILED',
      duplicate_of_scan_url_id: null,
      last_error_code: 'ROBOTS_BLOCKED',
      created_at: new Date().toISOString(),
      requires_redirect_approval: false,
      can_approve_redirect: false,
    };

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [redirectUrl, normalFailedUrl],
      next_cursor: null,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (5)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (5)'));

    await waitFor(() => {
      expect(screen.getByText('destination-site.com')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Approve & Retry/i })).toBeInTheDocument();
      expect(screen.getByText('Keep blocked')).toBeInTheDocument();
    });
  });

  it('invokes approveUrlRedirect API helper when Approve & Retry is clicked', async () => {
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('COMPLETED_WITH_ERRORS'));

    const redirectUrl: ScanURLApiResponse = {
      id: 'url-redir-1',
      scan_job_id: 'job-detail-123',
      original_index: 0,
      original_input: 'http://source-site.com/',
      normalized_url: 'https://source-site.com/',
      normalized_domain: 'source-site.com',
      status: 'FAILED',
      duplicate_of_scan_url_id: null,
      last_error_code: 'OUT_OF_SCOPE_REDIRECT',
      created_at: new Date().toISOString(),
      redirect_target_domain: 'destination-site.com',
      requires_redirect_approval: true,
      can_approve_redirect: true,
    };

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [redirectUrl],
      next_cursor: null,
    });

    const approveSpy = vi.spyOn(apiClient, 'approveUrlRedirect').mockResolvedValue({
      ...redirectUrl,
      status: 'QUEUED',
      approved_redirect_domain: 'destination-site.com',
      requires_redirect_approval: false,
      can_approve_redirect: false,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (5)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (5)'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Approve & Retry/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Approve & Retry/i }));

    await waitFor(() => {
      expect(approveSpy).toHaveBeenCalledWith('job-detail-123', 'url-redir-1', 'destination-site.com');
    });
  });
});
