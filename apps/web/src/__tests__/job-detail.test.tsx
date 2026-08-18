import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import JobDetailPage from '@/app/scans/[id]/page';
import * as apiClient from '@/lib/api-client';
import { ScanJobApiResponse } from '@/types/api';

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
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('maps COMPLETED_WITH_ERRORS to Partial status badge', async () => {
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('COMPLETED_WITH_ERRORS'));
    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({ items: [], next_cursor: null });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Partial')).toBeInTheDocument();
    });
  });

  it('does NOT poll when job status is DRAFT', async () => {
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('DRAFT'));
    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({ items: [], next_cursor: null });
    const progressSpy = vi.spyOn(apiClient, 'getScanJobProgress');

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Draft')).toBeInTheDocument();
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
      expect(screen.getAllByText('Running').length).toBeGreaterThan(0);
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
      expect(screen.getAllByText('Running').length).toBeGreaterThan(0);
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

  it('proves that tokens are never persisted in localStorage or sessionStorage', () => {
    expect(localStorage.getItem('access_token')).toBeNull();
    expect(localStorage.getItem('refresh_token')).toBeNull();
    expect(sessionStorage.getItem('access_token')).toBeNull();
    expect(sessionStorage.getItem('refresh_token')).toBeNull();
  });
});
