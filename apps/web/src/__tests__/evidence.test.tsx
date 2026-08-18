import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { EvidencePanel } from '@/components/results/EvidencePanel';
import * as apiClient from '@/lib/api-client';
import { FindingEvidenceItemApiResponse, ScanJobResultDetailApiResponse } from '@/types/api';

const mockDetail: ScanJobResultDetailApiResponse = {
  finding_id: 'find-123',
  job_id: 'job-123',
  canonical_email: 'security@target-company.com',
  email_domain: 'target-company.com',
  classification: 'ROLE_BASED',
  is_role_based: true,
  validation_status: 'VALID',
  evidence_count: 2,
  first_found_at: new Date().toISOString(),
  last_found_at: new Date().toISOString(),
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  representative_evidence: [],
};

const mockEvidence: FindingEvidenceItemApiResponse = {
  evidence_id: 'ev-1',
  source_type: 'VISIBLE_TEXT',
  sanitized_page_url: 'https://target-company.com/contact?param=<script>alert("xss")</script>',
  snippet: 'Contact security at security@target-company.com <img src=x onerror=alert(1)>',
  confidence: 0.95,
  crawled_page_status_code: 200,
  crawled_page_depth: 1,
  created_at: new Date().toISOString(),
};

describe('Phase 3C: EvidencePanel Drawer', () => {
  const mockOnClose = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
    mockOnClose.mockClear();
    vi.spyOn(apiClient, 'getScanJobResultDetail').mockResolvedValue(mockDetail);
    vi.spyOn(apiClient, 'listFindingEvidence').mockResolvedValue({
      items: [mockEvidence],
      next_cursor: null,
    });
  });

  it('renders accessible dialog role, title, detail metadata, and safe evidence items', async () => {
    render(<EvidencePanel jobId="job-123" findingId="find-123" onClose={mockOnClose} />);

    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('security@target-company.com')).toBeInTheDocument();
    });

    expect(screen.getByText('Role-based')).toBeInTheDocument();
    expect(screen.getByText('Valid')).toBeInTheDocument();

    // Verify safe plain-text rendering of malicious URL and snippet
    expect(screen.getByText('https://target-company.com/contact?param=<script>alert("xss")</script>')).toBeInTheDocument();
    expect(screen.getByText('Contact security at security@target-company.com <img src=x onerror=alert(1)>')).toBeInTheDocument();
  });

  it('closes panel when Escape key is pressed', async () => {
    render(<EvidencePanel jobId="job-123" findingId="find-123" onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText('security@target-company.com')).toBeInTheDocument();
    });

    fireEvent.keyDown(window, { key: 'Escape' });

    expect(mockOnClose).toHaveBeenCalledTimes(1);
  });

  it('paginates evidence using next_cursor and deduplicates by evidence_id', async () => {
    const ev2: FindingEvidenceItemApiResponse = { ...mockEvidence, evidence_id: 'ev-2', sanitized_page_url: 'https://target-company.com/about' };

    vi.spyOn(apiClient, 'listFindingEvidence')
      .mockResolvedValueOnce({ items: [mockEvidence], next_cursor: 'ev-cursor-2' })
      .mockResolvedValueOnce({ items: [mockEvidence, ev2], next_cursor: null }); // API returns duplicate ev-1

    render(<EvidencePanel jobId="job-123" findingId="find-123" onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText('https://target-company.com/contact?param=<script>alert("xss")</script>')).toBeInTheDocument();
    });

    const loadMoreBtn = screen.getByRole('button', { name: /Load More Evidence/i });
    fireEvent.click(loadMoreBtn);

    await waitFor(() => {
      expect(screen.getByText('https://target-company.com/about')).toBeInTheDocument();
    });

    // Verify mockEvidence (ev-1) is deduplicated and appears only once in the list
    expect(screen.getAllByText('Source: VISIBLE_TEXT')).toHaveLength(2);
  });

  it('prevents stale evidence from finding A overwriting newly selected finding B', async () => {
    const detailB: ScanJobResultDetailApiResponse = { ...mockDetail, finding_id: 'find-B', canonical_email: 'sales@company-b.com' };
    const evB: FindingEvidenceItemApiResponse = { ...mockEvidence, evidence_id: 'ev-B', sanitized_page_url: 'https://company-b.com/sales' };

    vi.spyOn(apiClient, 'getScanJobResultDetail').mockImplementation((_jobId, findingId) => {
      if (findingId === 'find-B') return Promise.resolve(detailB);
      return Promise.resolve(mockDetail);
    });

    vi.spyOn(apiClient, 'listFindingEvidence').mockImplementation((_jobId, findingId) => {
      if (findingId === 'find-B') return Promise.resolve({ items: [evB], next_cursor: null });
      return Promise.resolve({ items: [mockEvidence], next_cursor: null });
    });

    const { rerender } = render(<EvidencePanel jobId="job-123" findingId="find-123" onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText('security@target-company.com')).toBeInTheDocument();
    });

    // Switch findingId prop to find-B
    rerender(<EvidencePanel jobId="job-123" findingId="find-B" onClose={mockOnClose} />);

    await waitFor(() => {
      expect(screen.getByText('sales@company-b.com')).toBeInTheDocument();
    });

    // Ensure finding A's email is no longer displayed
    expect(screen.queryByText('security@target-company.com')).not.toBeInTheDocument();
  });
});
