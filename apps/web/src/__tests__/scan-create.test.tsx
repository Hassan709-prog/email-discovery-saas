import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import CreateScanPage from '@/app/scans/create/page';
import * as apiClient from '@/lib/api-client';
import { ApiError, ScanJobApiResponse } from '@/types/api';

const mockPush = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/scans/create',
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

const mockCreatedJob = (id: string): ScanJobApiResponse => ({
  id,
  organization_id: 'org-1',
  created_by_user_id: 'user-1',
  name: 'Test Scan',
  status: 'DRAFT',
  source_type: 'MANUAL',
  scanner_version: '1.0.0',
  normalization_version: '1.0.0',
  ranking_version: '1.0.0',
  configuration_snapshot: {},
  total_input_count: 2,
  valid_input_count: 2,
  duplicate_input_count: 0,
  invalid_input_count: 0,
  queued_count: 0,
  running_count: 0,
  completed_count: 0,
  failed_count: 0,
  email_finding_count: 0,
  created_at: new Date().toISOString(),
  started_at: null,
  completed_at: null,
  cancellation_requested_at: null,
});

describe('CreateScanPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(apiClient, 'getAccessToken').mockReturnValue('valid-jwt');
    mockPush.mockClear();
  });

  it('previews URL inputs and renders classification counts and invalid error reason table', async () => {
    vi.spyOn(apiClient, 'previewScanInputs').mockResolvedValue({
      total_input_count: 3,
      valid_input_count: 1,
      duplicate_input_count: 1,
      invalid_input_count: 1,
      previews: [
        {
          original_index: 0,
          original_input: 'https://example.com',
          normalized_url: 'https://example.com/',
          normalized_domain: 'example.com',
          classification: 'VALID',
          duplicate_of_index: null,
          error_code: null,
          error_message: null,
        },
        {
          original_index: 1,
          original_input: 'https://example.com',
          normalized_url: 'https://example.com/',
          normalized_domain: 'example.com',
          classification: 'DUPLICATE',
          duplicate_of_index: 0,
          error_code: null,
          error_message: null,
        },
        {
          original_index: 2,
          original_input: 'not-a-url',
          normalized_url: null,
          normalized_domain: null,
          classification: 'INVALID',
          duplicate_of_index: null,
          error_code: 'INVALID_SYNTAX',
          error_message: 'Invalid URL scheme or format',
        },
      ],
    });

    render(<CreateScanPage />);

    const textarea = screen.getByLabelText(/Paste website addresses/i);
    fireEvent.change(textarea, {
      target: { value: 'https://example.com\nhttps://example.com\nnot-a-url' },
    });

    const previewBtn = screen.getByRole('button', { name: /Review Websites/i });
    fireEvent.click(previewBtn);

    await waitFor(() => {
      expect(screen.getByText('Total Websites')).toBeInTheDocument();
      expect(screen.getByText('Invalid URL scheme or format')).toBeInTheDocument();
    });
  });

  it('generates and reuses Idempotency-Key on create retry for identical payload, but generates a new key if payload changes', async () => {
    const createSpy = vi.spyOn(apiClient, 'createScanJob').mockResolvedValue(mockCreatedJob('job-1'));
    vi.spyOn(apiClient, 'queueScanJob').mockRejectedValue(new Error('Queue connection dropped'));
    vi.spyOn(apiClient, 'previewScanInputs').mockResolvedValue({
      total_input_count: 1,
      valid_input_count: 1,
      duplicate_input_count: 0,
      invalid_input_count: 0,
      previews: [
        {
          original_index: 0,
          original_input: 'https://test.com',
          normalized_url: 'https://test.com/',
          normalized_domain: 'test.com',
          classification: 'VALID',
          duplicate_of_index: null,
          error_code: null,
          error_message: null,
        },
      ],
    });

    render(<CreateScanPage />);

    const textarea = screen.getByLabelText(/Paste website addresses/i);
    fireEvent.change(textarea, { target: { value: 'https://test.com' } });

    fireEvent.click(screen.getByRole('button', { name: /Review Websites/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save and Start Scan/i })).toBeInTheDocument();
    });

    // First create attempt
    fireEvent.click(screen.getByRole('button', { name: /Save and Start Scan/i }));

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledTimes(1);
    });

    const firstKey = createSpy.mock.calls[0][1];
    expect(firstKey).toBeDefined();

    // Change input payload
    fireEvent.change(textarea, { target: { value: 'https://new-test.com' } });

    fireEvent.click(screen.getByRole('button', { name: /Review Websites/i }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save and Start Scan/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Save and Start Scan/i }));

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledTimes(2);
    });

    const secondKey = createSpy.mock.calls[1][1];
    expect(secondKey).not.toEqual(firstKey); // Payload changed -> new idempotency key!
  });

  it('prevents double-creation by retrying ONLY queueing if create succeeds but queueing fails', async () => {
    const createSpy = vi.spyOn(apiClient, 'createScanJob').mockResolvedValue(mockCreatedJob('job-saved-123'));
    const queueSpy = vi
      .spyOn(apiClient, 'queueScanJob')
      .mockRejectedValueOnce(new ApiError(500, { code: 'QUEUE_FAILED', message: 'Queue temporary down' }))
      .mockResolvedValueOnce({ ...mockCreatedJob('job-saved-123'), status: 'QUEUED' });

    vi.spyOn(apiClient, 'previewScanInputs').mockResolvedValue({
      total_input_count: 1,
      valid_input_count: 1,
      duplicate_input_count: 0,
      invalid_input_count: 0,
      previews: [
        {
          original_index: 0,
          original_input: 'https://test.com',
          normalized_url: 'https://test.com/',
          normalized_domain: 'test.com',
          classification: 'VALID',
          duplicate_of_index: null,
          error_code: null,
          error_message: null,
        },
      ],
    });

    render(<CreateScanPage />);

    fireEvent.change(screen.getByLabelText(/Paste website addresses/i), { target: { value: 'https://test.com' } });
    fireEvent.click(screen.getByRole('button', { name: /Review Websites/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save and Start Scan/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Save and Start Scan/i }));

    // Queue fails -> Shows banner with "Start Scan Again"
    await waitFor(() => {
      expect(screen.getByText(/Your scan was saved but has not started/i)).toBeInTheDocument();
    });

    expect(createSpy).toHaveBeenCalledTimes(1);
    expect(queueSpy).toHaveBeenCalledTimes(1);

    // Click retry queue button
    const retryBtn = screen.getByRole('button', { name: /Start Scan Again/i });
    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith('/scans/job-saved-123');
    });

    // Verify create was NEVER called a second time!
    expect(createSpy).toHaveBeenCalledTimes(1);
    expect(queueSpy).toHaveBeenCalledTimes(2);
  });
});
