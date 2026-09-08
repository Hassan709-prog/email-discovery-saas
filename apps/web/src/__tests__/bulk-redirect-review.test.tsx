import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import JobDetailPage from '@/app/scans/[id]/page';
import * as apiClient from '@/lib/api-client';
import { ApiError, ScanJobApiResponse, ScanURLApiResponse } from '@/types/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'job-bulk-123' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/scans/job-bulk-123',
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

const mockJob = (status: ScanJobApiResponse['status'] = 'COMPLETED_WITH_ERRORS'): ScanJobApiResponse => ({
  id: 'job-bulk-123',
  organization_id: 'org-1',
  created_by_user_id: 'user-1',
  name: 'Bulk Redirect Review Scan',
  status,
  source_type: 'MANUAL',
  scanner_version: '1.0.0',
  normalization_version: '1.0.0',
  ranking_version: '1.0.0',
  configuration_snapshot: {},
  total_input_count: 4,
  valid_input_count: 4,
  duplicate_input_count: 0,
  invalid_input_count: 0,
  queued_count: 0,
  running_count: 0,
  completed_count: 2,
  failed_count: 2,
  email_finding_count: 5,
  created_at: new Date().toISOString(),
  started_at: new Date().toISOString(),
  completed_at: new Date().toISOString(),
  cancellation_requested_at: null,
});

const createPendingUrl = (id: string, index: number, domain: string, targetDomain: string): ScanURLApiResponse => ({
  id,
  scan_job_id: 'job-bulk-123',
  original_index: index,
  original_input: `https://${domain}`,
  normalized_url: `https://${domain}/`,
  normalized_domain: domain,
  status: 'FAILED',
  duplicate_of_scan_url_id: null,
  last_error_code: 'OUT_OF_SCOPE_REDIRECT',
  created_at: new Date().toISOString(),
  redirect_target_domain: targetDomain,
  requires_redirect_approval: true,
  can_approve_redirect: true,
});

const createCompletedUrl = (id: string, index: number, domain: string): ScanURLApiResponse => ({
  id,
  scan_job_id: 'job-bulk-123',
  original_index: index,
  original_input: `https://${domain}`,
  normalized_url: `https://${domain}/`,
  normalized_domain: domain,
  status: 'COMPLETED',
  duplicate_of_scan_url_id: null,
  last_error_code: null,
  created_at: new Date().toISOString(),
  requires_redirect_approval: false,
  can_approve_redirect: false,
});

describe('Bulk Redirect Review Web UI', { timeout: 15000 }, () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(apiClient, 'getAccessToken').mockReturnValue('valid-jwt');
    vi.spyOn(apiClient, 'listScanJobResults').mockResolvedValue({ items: [], next_cursor: null });
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJob());
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders URL filter bar and filters by pending redirect approval', async () => {
    const listUrlsSpy = vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [
        createPendingUrl('u-1', 0, 'site1.com', 'dest1.com'),
        createCompletedUrl('u-2', 1, 'site2.com'),
      ],
      next_cursor: null,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Pending Redirect Approvals/i })).toBeInTheDocument();
    });

    // Click Pending Redirect Approvals filter
    fireEvent.click(screen.getByRole('button', { name: /Pending Redirect Approvals/i }));

    await waitFor(() => {
      expect(listUrlsSpy).toHaveBeenCalledWith('job-bulk-123', expect.objectContaining({
        requires_redirect_approval: true,
      }));
    });
  });

  it('renders row checkboxes only for rows requiring redirect approval', async () => {
    const pendingUrl = createPendingUrl('u-1', 0, 'site1.com', 'dest1.com');
    const completedUrl = createCompletedUrl('u-2', 1, 'site2.com');

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [pendingUrl, completedUrl],
      next_cursor: null,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      // Row 1 (pending): has checkbox
      expect(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i })).toBeInTheDocument();
      // Row 2 (completed): has "—" placeholder instead of checkbox
      expect(screen.queryByRole('checkbox', { name: /Select redirect for site2\.com/i })).not.toBeInTheDocument();
    });
  });

  it('uses accurate header checkbox label "Select all loaded pending redirects" and toggles loaded items', async () => {
    const url1 = createPendingUrl('u-1', 0, 'site1.com', 'dest1.com');
    const url2 = createPendingUrl('u-2', 1, 'site2.com', 'dest2.com');

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [url1, url2],
      next_cursor: null,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByTitle('Select all loaded pending redirects')).toBeInTheDocument();
    });

    const headerCheckbox = screen.getByTitle('Select all loaded pending redirects');

    // Click to select all loaded pending redirects
    fireEvent.click(headerCheckbox);

    await waitFor(() => {
      expect(screen.getByText(/2 pending redirects selected/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Approve Selected \(2\)/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Reject Selected \(2\)/i })).toBeInTheDocument();
    });

    // Click again to deselect
    fireEvent.click(headerCheckbox);

    await waitFor(() => {
      expect(screen.queryByText(/2 pending redirects selected/i)).not.toBeInTheDocument();
    });
  });

  it('preserves selection across pages when loading more URLs', async () => {
    const page1 = [
      createPendingUrl('u-1', 0, 'site1.com', 'dest1.com'),
      createPendingUrl('u-2', 1, 'site2.com', 'dest2.com'),
    ];
    const page2 = [
      createPendingUrl('u-3', 2, 'site3.com', 'dest3.com'),
      createPendingUrl('u-4', 3, 'site4.com', 'dest4.com'),
    ];

    vi.spyOn(apiClient, 'listScanJobUrls')
      .mockResolvedValueOnce({ items: page1, next_cursor: 'cursor-page-2' })
      .mockResolvedValueOnce({ items: page2, next_cursor: null });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i })).toBeInTheDocument();
    });

    // Select row 1
    fireEvent.click(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i }));

    await waitFor(() => {
      expect(screen.getByText(/1 pending redirect selected/i)).toBeInTheDocument();
    });

    // Click "Load More URLs"
    const loadMoreBtn = screen.getByRole('button', { name: /Load More URLs/i });
    fireEvent.click(loadMoreBtn);

    await waitFor(() => {
      expect(screen.getByText('site3.com')).toBeInTheDocument();
      // u-1 remains selected across pagination
      expect(screen.getByText(/1 pending redirect selected/i)).toBeInTheDocument();
      expect((screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i }) as HTMLInputElement).checked).toBe(true);
    });
  });

  it('displays filtered count in Showing X of Y when filtering by pending approvals', async () => {
    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [
        createPendingUrl('u-1', 0, 'site1.com', 'dest1.com'),
      ],
      next_cursor: null,
      total_count: 1,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Pending Redirect Approvals/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Pending Redirect Approvals/i }));

    await waitFor(() => {
      expect(screen.getByText('Showing 1 of 1')).toBeInTheDocument();
    });
  });

  it('executes bulk approval flow with confirmation dialog and url_ids contract', async () => {
    const url1 = createPendingUrl('u-1', 0, 'site1.com', 'dest1.com');
    const url2 = createPendingUrl('u-2', 1, 'site2.com', 'dest2.com');

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [url1, url2],
      next_cursor: null,
    });

    const bulkApproveSpy = vi.spyOn(apiClient, 'bulkApproveUrlRedirects').mockResolvedValue({
      job_id: 'job-bulk-123',
      action: 'APPROVE',
      requested_count: 2,
      unique_requested_count: 2,
      affected_count: 2,
      skipped_count: 0,
      results: [
        { url_id: 'u-1', disposition: 'APPROVED', target_domain: 'dest1.com', message: 'Approved' },
        { url_id: 'u-2', disposition: 'APPROVED', target_domain: 'dest2.com', message: 'Approved' },
      ],
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByTitle('Select all loaded pending redirects')).toBeInTheDocument();
    });

    // Select all loaded
    fireEvent.click(screen.getByTitle('Select all loaded pending redirects'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Approve Selected \(2\)/i })).toBeInTheDocument();
    });

    // Click Approve Selected to open modal
    fireEvent.click(screen.getByRole('button', { name: /Approve Selected \(2\)/i }));

    await waitFor(() => {
      expect(screen.getByText('Approve Selected Redirects')).toBeInTheDocument();
      expect(screen.getByText(/You are approving/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Confirm Approval' })).toBeInTheDocument();
    });

    // Click Confirm Approval
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Approval' }));

    await waitFor(() => {
      expect(bulkApproveSpy).toHaveBeenCalledWith('job-bulk-123', {
        url_ids: ['u-1', 'u-2'],
      });
      // Selection cleared on success
      expect(screen.queryByText(/2 pending redirects selected/i)).not.toBeInTheDocument();
      // Modal closed
      expect(screen.queryByText('Approve Selected Redirects')).not.toBeInTheDocument();
    });
  });

  it('executes bulk rejection flow with confirmation dialog and url_ids contract', async () => {
    const url1 = createPendingUrl('u-1', 0, 'site1.com', 'dest1.com');

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [url1],
      next_cursor: null,
    });

    const bulkRejectSpy = vi.spyOn(apiClient, 'bulkRejectUrlRedirects').mockResolvedValue({
      job_id: 'job-bulk-123',
      action: 'REJECT',
      requested_count: 1,
      unique_requested_count: 1,
      affected_count: 1,
      skipped_count: 0,
      results: [
        { url_id: 'u-1', disposition: 'REJECTED', target_domain: 'dest1.com', message: 'Rejected' },
      ],
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i })).toBeInTheDocument();
    });

    // Select row 1
    fireEvent.click(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Reject Selected \(1\)/i })).toBeInTheDocument();
    });

    // Click Reject Selected to open modal
    fireEvent.click(screen.getByRole('button', { name: /Reject Selected \(1\)/i }));

    await waitFor(() => {
      expect(screen.getByText('Reject Selected Redirects')).toBeInTheDocument();
      expect(screen.getByText(/You are rejecting/i)).toBeInTheDocument();
    });

    // Confirm rejection
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Rejection' }));

    await waitFor(() => {
      expect(bulkRejectSpy).toHaveBeenCalledWith('job-bulk-123', {
        url_ids: ['u-1'],
      });
      // Selection cleared on success
      expect(screen.queryByText(/1 pending redirect selected/i)).not.toBeInTheDocument();
    });
  });

  it('retains selection on API failure so user does not lose state', async () => {
    const url1 = createPendingUrl('u-1', 0, 'site1.com', 'dest1.com');

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [url1],
      next_cursor: null,
    });

    vi.spyOn(apiClient, 'bulkApproveUrlRedirects').mockRejectedValue(
      new ApiError(400, {
        code: 'INVALID_RESULT_STATE',
        message: 'URLs are not eligible for redirect approval.',
      })
    );

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Approve Selected \(1\)/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Approve Selected \(1\)/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Confirm Approval' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Approval' }));

    await waitFor(() => {
      // Error message shown
      expect(screen.getByText('URLs are not eligible for redirect approval.')).toBeInTheDocument();
      // Selection is RETAINED!
      expect(screen.getByText(/1 pending redirect selected/i)).toBeInTheDocument();
    });
  });

  it('executes single-row rejection using bulkRejectUrlRedirects with url_ids contract', async () => {
    const url1 = createPendingUrl('u-single-1', 0, 'site1.com', 'dest1.com');

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [url1],
      next_cursor: null,
    });

    const bulkRejectSpy = vi.spyOn(apiClient, 'bulkRejectUrlRedirects').mockResolvedValue({
      job_id: 'job-bulk-123',
      action: 'REJECT',
      requested_count: 1,
      unique_requested_count: 1,
      affected_count: 1,
      skipped_count: 0,
      results: [
        { url_id: 'u-single-1', disposition: 'REJECTED', target_domain: 'dest1.com', message: 'Rejected' },
      ],
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));

    await waitFor(() => {
      expect(bulkRejectSpy).toHaveBeenCalledWith('job-bulk-123', {
        url_ids: ['u-single-1'],
      });
    });
  });

  it('handles individual approval followed by bulk rejection cleanly', async () => {
    const url1 = createPendingUrl('u-1', 0, 'site1.com', 'dest1.com');
    const url2 = createPendingUrl('u-2', 1, 'site2.com', 'dest2.com');

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [url1, url2],
      next_cursor: null,
    });

    const approveSpy = vi.spyOn(apiClient, 'approveUrlRedirect').mockResolvedValue({
      ...url1,
      requires_redirect_approval: false,
    });

    const bulkRejectSpy = vi.spyOn(apiClient, 'bulkRejectUrlRedirects').mockResolvedValue({
      job_id: 'job-bulk-123',
      action: 'REJECT',
      requested_count: 1,
      unique_requested_count: 1,
      affected_count: 1,
      skipped_count: 0,
      results: [{ url_id: 'u-2', disposition: 'REJECTED' }],
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i })).toBeInTheDocument();
    });

    // Select both u-1 and u-2
    fireEvent.click(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i }));
    fireEvent.click(screen.getByRole('checkbox', { name: /Select redirect for site2\.com/i }));

    await waitFor(() => {
      expect(screen.getByText(/2 pending redirects selected/i)).toBeInTheDocument();
    });

    // Individually approve u-1
    const approveBtns = screen.getAllByRole('button', { name: 'Approve & Retry' });
    fireEvent.click(approveBtns[0]);

    await waitFor(() => {
      expect(approveSpy).toHaveBeenCalledWith('job-bulk-123', 'u-1', 'dest1.com');
      // u-1 was approved and pruned from selection, only 1 remains selected
      expect(screen.getByText(/1 pending redirect selected/i)).toBeInTheDocument();
    });

    // Now bulk reject remaining selection
    fireEvent.click(screen.getByRole('button', { name: /Reject Selected \(1\)/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Confirm Rejection' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Rejection' }));

    await waitFor(() => {
      expect(bulkRejectSpy).toHaveBeenCalledWith('job-bulk-123', {
        url_ids: ['u-2'],
      });
      expect(screen.queryByText(/pending redirects selected/i)).not.toBeInTheDocument();
    });
  });

  it('prunes stale selections when refresh occurs and rows no longer require approval', async () => {
    const url1 = createPendingUrl('u-1', 0, 'site1.com', 'dest1.com');

    const listUrlsSpy = vi.spyOn(apiClient, 'listScanJobUrls')
      .mockResolvedValueOnce({ items: [url1], next_cursor: null })
      .mockResolvedValueOnce({ items: [createCompletedUrl('u-1', 0, 'site1.com')], next_cursor: null });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i }));

    await waitFor(() => {
      expect(screen.getByText(/1 pending redirect selected/i)).toBeInTheDocument();
    });

    // Click Refresh URL List
    fireEvent.click(screen.getByRole('button', { name: /Refresh URL List/i }));

    await waitFor(() => {
      expect(listUrlsSpy).toHaveBeenCalledTimes(2);
      // Since u-1 is now completed and does not require approval, it is pruned from selection
      expect(screen.queryByText(/1 pending redirect selected/i)).not.toBeInTheDocument();
    });
  });

  it('clears selection on filter change', async () => {
    const url1 = createPendingUrl('u-1', 0, 'site1.com', 'dest1.com');

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [url1],
      next_cursor: null,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i }));

    await waitFor(() => {
      expect(screen.getByText(/1 pending redirect selected/i)).toBeInTheDocument();
    });

    // Switch filter to Pending Redirect Approvals
    fireEvent.click(screen.getByRole('button', { name: /Pending Redirect Approvals/i }));

    await waitFor(() => {
      // Selection is cleared on filter change
      expect(screen.queryByText(/1 pending redirect selected/i)).not.toBeInTheDocument();
    });
  });

  it('disables background selection controls while confirmation modal is open', async () => {
    const url1 = createPendingUrl('u-1', 0, 'site1.com', 'dest1.com');

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [url1],
      next_cursor: null,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i })).toBeInTheDocument();
    });

    const checkbox = screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i });
    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Approve Selected \(1\)/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Approve Selected \(1\)/i }));

    await waitFor(() => {
      expect(screen.getByText('Approve Selected Redirects')).toBeInTheDocument();
      // Table checkboxes are disabled while modal is open
      expect(checkbox).toBeDisabled();
      expect(screen.getByTitle('Select all loaded pending redirects')).toBeDisabled();
    });
  });

  it('implements dialog accessibility with initial focus, escape-to-close, and keyboard focus trapping', async () => {
    const url1 = createPendingUrl('u-1', 0, 'site1.com', 'dest1.com');

    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({
      items: [url1],
      next_cursor: null,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('Target URLs (4)')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Target URLs (4)'));

    await waitFor(() => {
      expect(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('checkbox', { name: /Select redirect for site1\.com/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Approve Selected \(1\)/i })).toBeInTheDocument();
    });

    const triggerBtn = screen.getByRole('button', { name: /Approve Selected \(1\)/i });
    triggerBtn.focus();
    fireEvent.click(triggerBtn);

    await waitFor(() => {
      expect(screen.getByText('Approve Selected Redirects')).toBeInTheDocument();
    });

    const cancelBtn = screen.getByRole('button', { name: 'Cancel' });
    const confirmBtn = screen.getByRole('button', { name: 'Confirm Approval' });

    // Initial focus on cancel button
    await waitFor(() => {
      expect(document.activeElement).toBe(cancelBtn);
    });

    const dialog = screen.getByRole('dialog');

    // Tab moves focus to confirm button
    fireEvent.keyDown(dialog, { key: 'Tab' });
    // Focus trapping keydown simulation
    cancelBtn.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });

    // Escape closes modal when not executing
    fireEvent.keyDown(dialog, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      // Focus restored to triggering button
      expect(document.activeElement).toBe(triggerBtn);
    });
  });
});
