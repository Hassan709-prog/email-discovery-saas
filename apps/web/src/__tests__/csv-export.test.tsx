import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { downloadScanJobCsv, parseSafeCsvFilename, setAccessToken } from '@/lib/api-client';
import { ApiError } from '@/types/api';

describe('Phase 3C: CSV Export & Filename Sanitization', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setAccessToken('valid-jwt-token');
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('parseSafeCsvFilename', () => {
    const fallback = 'fallback-export.csv';

    it('parses valid filename from standard Content-Disposition header', () => {
      const header = 'attachment; filename="scan-job-123-results.csv"';
      expect(parseSafeCsvFilename(header, fallback)).toBe('scan-job-123-results.csv');
    });

    it('parses utf-8 filename from filename*=utf-8\'\' format', () => {
      const header = "attachment; filename*=utf-8''custom_export.csv";
      expect(parseSafeCsvFilename(header, fallback)).toBe('custom_export.csv');
    });

    it('rejects path traversal, control characters, non-CSV extension, or invalid characters and returns fallback', () => {
      expect(parseSafeCsvFilename('attachment; filename="../../../etc/passwd"', fallback)).toBe(fallback);
      expect(parseSafeCsvFilename('attachment; filename="malicious\r\n.csv"', fallback)).toBe(fallback);
      expect(parseSafeCsvFilename('attachment; filename="script.exe"', fallback)).toBe(fallback);
      expect(parseSafeCsvFilename('attachment; filename="<invalid>.csv"', fallback)).toBe(fallback);
      expect(parseSafeCsvFilename(null, fallback)).toBe(fallback);
    });
  });

  describe('downloadScanJobCsv', () => {
    it('executes authenticated CSV download and revokes object URL asynchronously', async () => {
      const mockBlob = new Blob(['canonical_email,email_domain\ntest@ex.com,ex.com\n'], { type: 'text/csv' });
      const mockHeaders = new Headers({
        'Content-Type': 'text/csv; charset=utf-8',
        'Content-Disposition': 'attachment; filename="my-export.csv"',
      });

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        headers: mockHeaders,
        blob: () => Promise.resolve(mockBlob),
      });
      vi.stubGlobal('fetch', mockFetch);

      const createObjectUrlSpy = vi.fn().mockReturnValue('blob:http://localhost/mock-uuid');
      const revokeObjectUrlSpy = vi.fn();
      vi.stubGlobal('URL', {
        createObjectURL: createObjectUrlSpy,
        revokeObjectURL: revokeObjectUrlSpy,
      });

      const filename = await downloadScanJobCsv('job-999');

      expect(filename).toBe('my-export.csv');
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/v1/scan-jobs/job-999/export.csv',
        expect.objectContaining({
          method: 'GET',
          headers: expect.objectContaining({ Authorization: 'Bearer valid-jwt-token' }),
        })
      );
      expect(createObjectUrlSpy).toHaveBeenCalled();
    });

    it('rejects unexpected non-CSV response content-type (e.g. text/html or application/json)', async () => {
      const mockHeaders = new Headers({
        'Content-Type': 'text/html; charset=utf-8',
      });

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        headers: mockHeaders,
      });
      vi.stubGlobal('fetch', mockFetch);

      await expect(downloadScanJobCsv('job-999')).rejects.toThrow(ApiError);
    });

    it('handles non-terminal export ineligible error envelope', async () => {
      const errorEnvelope = {
        error: {
          code: 'EXPORT_INELIGIBLE',
          message: 'Export is available only for terminal scan jobs.',
          request_id: 'req-ineligible-123',
        },
      };

      const mockFetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        json: () => Promise.resolve(errorEnvelope),
      });
      vi.stubGlobal('fetch', mockFetch);

      try {
        await downloadScanJobCsv('job-active');
        expect.fail('Should have thrown ApiError');
      } catch (err) {
        expect(err).toBeInstanceOf(ApiError);
        const apiErr = err as ApiError;
        expect(apiErr.status).toBe(400);
        expect(apiErr.code).toBe('EXPORT_INELIGIBLE');
        expect(apiErr.message).toBe('Export is available only for terminal scan jobs.');
        expect(apiErr.requestId).toBe('req-ineligible-123');
      }
    });

    it('proves zero token persistence in localStorage or sessionStorage', () => {
      expect(localStorage.getItem('access_token')).toBeNull();
      expect(sessionStorage.getItem('access_token')).toBeNull();
    });
  });
});
