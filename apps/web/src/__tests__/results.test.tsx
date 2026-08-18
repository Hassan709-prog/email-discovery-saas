import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import JobDetailPage from '@/app/scans/[id]/page';
import * as apiClient from '@/lib/api-client';
import { ScanJobApiResponse, ScanJobResultItemApiResponse } from '@/types/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'job-results-123' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/scans/job-results-123',
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

const mockJobDetail = (status: ScanJobApiResponse['status'] = 'COMPLETED'): ScanJobApiResponse => ({
  id: 'job-results-123',
  organization_id: 'org-1',
  created_by_user_id: 'user-1',
  name: 'Completed Results Job',
  status,
  source_type: 'MANUAL',
  scanner_version: '1.0.0',
  normalization_version: '1.0.0',
  ranking_version: '1.0.0',
  configuration_snapshot: {},
  total_input_count: 1,
  valid_input_count: 1,
  duplicate_input_count: 0,
  invalid_input_count: 0,
  queued_count: 0,
  running_count: 0,
  completed_count: 1,
  failed_count: 0,
  email_finding_count: 2,
  created_at: new Date().toISOString(),
  started_at: new Date().toISOString(),
  completed_at: new Date().toISOString(),
  cancellation_requested_at: null,
});

const mockFinding = (id: string, email: string, classification = 'PERSONAL_OR_NAMED', validation = 'VALID'): ScanJobResultItemApiResponse => ({
  finding_id: id,
  canonical_email: email,
  email_domain: email.split('@')[1] || 'example.com',
  classification,
  is_role_based: classification === 'ROLE_BASED',
  validation_status: validation,
  evidence_count: 3,
  first_found_at: new Date().toISOString(),
  last_found_at: new Date().toISOString(),
  representative_evidence: [],
});

describe('Phase 3C: Email Findings List & Filtering', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(apiClient, 'getAccessToken').mockReturnValue('valid-jwt');
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('COMPLETED'));
    vi.spyOn(apiClient, 'listScanJobUrls').mockResolvedValue({ items: [], next_cursor: null });
  });

  it('renders populated findings list with exact classification and validation status badges', async () => {
    vi.spyOn(apiClient, 'listScanJobResults').mockResolvedValue({
      items: [
        mockFinding('f1', 'jane.doe@acme.com', 'PERSONAL_OR_NAMED', 'VALID'),
        mockFinding('f2', 'support@acme.com', 'ROLE_BASED', 'UNVERIFIED'),
      ],
      next_cursor: null,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('jane.doe@acme.com')).toBeInTheDocument();
      expect(screen.getByText('support@acme.com')).toBeInTheDocument();
    });

    expect(screen.getAllByText('Personal/Named').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Role-based').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Valid').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Unverified').length).toBeGreaterThan(0);
  });

  it('renders empty findings state when zero email findings discovered', async () => {
    vi.spyOn(apiClient, 'listScanJobResults').mockResolvedValue({
      items: [],
      next_cursor: null,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('No email findings discovered')).toBeInTheDocument();
    });
  });

  it('resets accumulated findings items and cursor when filters change', async () => {
    const listSpy = vi.spyOn(apiClient, 'listScanJobResults').mockResolvedValue({
      items: [mockFinding('f1', 'info@example.com')],
      next_cursor: 'cursor-1',
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('info@example.com')).toBeInTheDocument();
    });

    const domainInput = screen.getByLabelText(/Email Domain/i);
    fireEvent.change(domainInput, { target: { value: 'target.org' } });

    const applyBtn = screen.getByRole('button', { name: /Apply Filters/i });
    fireEvent.click(applyBtn);

    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith(
        'job-results-123',
        expect.objectContaining({ email_domain: 'target.org' }),
        expect.anything()
      );
    });
  });

  it('deduplicates appended results by finding_id during cursor pagination', async () => {
    const finding1 = mockFinding('f1', 'user1@acme.com');
    const finding2 = mockFinding('f2', 'user2@acme.com');

    vi.spyOn(apiClient, 'listScanJobResults')
      .mockResolvedValueOnce({ items: [finding1], next_cursor: 'cursor-2' })
      .mockResolvedValueOnce({ items: [finding1, finding2], next_cursor: null }); // API returns duplicate finding1

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText('user1@acme.com')).toBeInTheDocument();
    });

    const loadMoreBtn = screen.getByRole('button', { name: /Load More Findings/i });
    fireEvent.click(loadMoreBtn);

    await waitFor(() => {
      expect(screen.getByText('user2@acme.com')).toBeInTheDocument();
    });

    expect(screen.getAllByText('user1@acme.com')).toHaveLength(1);
  });

  it('renders active scan notice banner for running jobs alongside persisted findings', async () => {
    vi.spyOn(apiClient, 'getScanJob').mockResolvedValue(mockJobDetail('RUNNING'));
    vi.spyOn(apiClient, 'listScanJobResults').mockResolvedValue({
      items: [mockFinding('f1', 'partial@active.com')],
      next_cursor: null,
    });

    render(<JobDetailPage />);

    await waitFor(() => {
      expect(screen.getByText(/Scan job is currently active and processing target URLs/i)).toBeInTheDocument();
      expect(screen.getByText('partial@active.com')).toBeInTheDocument();
    });
  });
});
