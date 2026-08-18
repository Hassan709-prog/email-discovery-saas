'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import { StatusBadge } from '@/components/jobs/StatusBadge';
import {
  ApiError,
  ScanJobApiResponse,
  ScanJobProgressApiResponse,
  ScanURLApiResponse,
} from '@/types/api';
import {
  cancelScanJob,
  getScanJob,
  getScanJobProgress,
  listScanJobUrls,
  queueScanJob,
} from '@/lib/api-client';
import { ArrowLeft, RefreshCw, AlertCircle, Play, Ban, FileText } from 'lucide-react';

const ACTIVE_STATUSES = new Set(['QUEUED', 'RUNNING', 'CANCELLING']);

export default function JobDetailPage() {
  const params = useParams();
  const jobId = params?.id as string;

  const [job, setJob] = useState<ScanJobApiResponse | null>(null);
  const [progress, setProgress] = useState<ScanJobProgressApiResponse | null>(null);
  const [urls, setUrls] = useState<ScanURLApiResponse[]>([]);
  const [urlsNextCursor, setUrlsNextCursor] = useState<string | null>(null);

  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingUrls, setIsLoadingUrls] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // In-flight abort controller reference for polling
  const abortControllerRef = useRef<AbortController | null>(null);
  const isPollingRef = useRef(false);

  // Initial fetch of job detail & URLs
  const fetchJobDetail = useCallback(async () => {
    if (!jobId) return;
    try {
      setIsLoading(true);
      setError(null);
      const [jobData, urlsData] = await Promise.all([
        getScanJob(jobId),
        listScanJobUrls(jobId, { limit: 50 }),
      ]);
      setJob(jobData);
      setUrls(urlsData.items);
      setUrlsNextCursor(urlsData.next_cursor);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(new ApiError(500, { code: 'FETCH_ERROR', message: 'Failed to load job detail.' }));
      }
    } finally {
      setIsLoading(false);
    }
  }, [jobId]);

  const loadMoreUrls = async () => {
    if (!jobId || !urlsNextCursor) return;
    try {
      setIsLoadingUrls(true);
      const res = await listScanJobUrls(jobId, { limit: 50, cursor: urlsNextCursor });
      setUrls((prev) => [...prev, ...res.items]);
      setUrlsNextCursor(res.next_cursor);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(new ApiError(500, { code: 'FETCH_URLS_ERROR', message: 'Failed to load URLs.' }));
      }
    } finally {
      setIsLoadingUrls(false);
    }
  };

  const refreshUrls = useCallback(async () => {
    if (!jobId) return;
    try {
      setIsLoadingUrls(true);
      const res = await listScanJobUrls(jobId, { limit: 50 });
      setUrls(res.items);
      setUrlsNextCursor(res.next_cursor);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(new ApiError(500, { code: 'REFRESH_URLS_ERROR', message: 'Failed to refresh URLs.' }));
      }
    } finally {
      setIsLoadingUrls(false);
    }
  }, [jobId]);

  useEffect(() => {
    fetchJobDetail();
  }, [fetchJobDetail]);

  const jobStatus = job?.status;

  // Polling effect: Polls ONLY /progress for active statuses (QUEUED, RUNNING, CANCELLING)
  useEffect(() => {
    if (!jobId || !jobStatus) return;

    // Do NOT poll DRAFT or terminal statuses
    if (!ACTIVE_STATUSES.has(jobStatus)) {
      return;
    }

    let isMounted = true;

    const pollProgress = async () => {
      if (isPollingRef.current) return; // Prevent overlapping requests
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;

      isPollingRef.current = true;
      abortControllerRef.current = new AbortController();

      try {
        const progData = await getScanJobProgress(jobId, abortControllerRef.current.signal);
        if (!isMounted) return;

        setProgress(progData);
        setJob((prev) => (prev ? { ...prev, status: progData.status } : null));

        // If job transitioned to terminal, refresh URL list once
        if (!ACTIVE_STATUSES.has(progData.status)) {
          refreshUrls();
        }
      } catch (err) {
        if ((err as Error)?.name === 'AbortError') return;
        if (err instanceof ApiError) {
          setError(err);
        } else {
          setError(new ApiError(500, { code: 'POLL_ERROR', message: 'Failed to poll progress.' }));
        }
      } finally {
        isPollingRef.current = false;
        abortControllerRef.current = null;
      }
    };

    const intervalId = setInterval(() => {
      if (isMounted) pollProgress();
    }, 3000);

    return () => {
      isMounted = false;
      clearInterval(intervalId);
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [jobId, jobStatus, refreshUrls]);

  const handleQueueJob = async () => {
    if (!jobId) return;
    try {
      setActionLoading(true);
      const updated = await queueScanJob(jobId);
      setJob(updated);
      refreshUrls();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(new ApiError(500, { code: 'QUEUE_ERROR', message: 'Failed to queue job.' }));
      }
    } finally {
      setActionLoading(false);
    }
  };

  const handleCancelJob = async () => {
    if (!jobId) return;
    try {
      setActionLoading(true);
      const updated = await cancelScanJob(jobId);
      setJob(updated);
      refreshUrls();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(new ApiError(500, { code: 'CANCEL_ERROR', message: 'Failed to cancel job.' }));
      }
    } finally {
      setActionLoading(false);
    }
  };

  if (isLoading) {
    return (
      <ProtectedRoute>
        <div className="max-w-6xl mx-auto px-4 py-16 text-center space-y-3">
          <div className="w-8 h-8 border-4 border-brand-600 border-t-transparent rounded-full animate-spin mx-auto" />
          <p className="text-sm font-medium text-slate-600">Loading scan job details...</p>
        </div>
      </ProtectedRoute>
    );
  }

  if (error || !job) {
    return (
      <ProtectedRoute>
        <div className="max-w-4xl mx-auto px-4 py-12 space-y-4">
          <div className="bg-rose-50 border border-rose-200 text-rose-800 p-6 rounded-xl space-y-2">
            <h3 className="font-semibold text-base">Error Loading Scan Job</h3>
            <p className="text-sm">{error?.message || 'Job not found or inaccessible.'}</p>
            {error?.requestId && (
              <p className="text-xs font-mono text-rose-600">Request ID: {error.requestId}</p>
            )}
          </div>
          <Link href="/dashboard" className="inline-flex items-center text-xs font-semibold text-brand-600 hover:underline">
            ← Back to Dashboard
          </Link>
        </div>
      </ProtectedRoute>
    );
  }

  const currentProgressPct = progress?.progress_percentage ?? 0;

  return (
    <ProtectedRoute>
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        {/* Header toolbar */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div className="flex items-center space-x-3">
            <Link
              href="/dashboard"
              className="p-2 text-slate-500 hover:text-slate-900 rounded-lg hover:bg-slate-100 transition-colors"
            >
              <ArrowLeft className="w-5 h-5" />
            </Link>
            <div>
              <div className="flex items-center space-x-3">
                <h1 className="text-2xl font-bold text-slate-900 tracking-tight">
                  {job.name || 'Untitled Scan Job'}
                </h1>
                <StatusBadge status={job.status} />
              </div>
              <p className="text-xs font-mono text-slate-500 mt-1">ID: {job.id}</p>
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex items-center space-x-3">
            {job.status === 'DRAFT' && (
              <button
                onClick={handleQueueJob}
                disabled={actionLoading}
                className="inline-flex items-center px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white font-semibold text-sm rounded-lg shadow-sm disabled:opacity-50 transition-colors"
              >
                <Play className="w-4 h-4 mr-2" />
                {actionLoading ? 'Queueing...' : 'Queue Job'}
              </button>
            )}

            {(job.status === 'QUEUED' || job.status === 'RUNNING') && (
              <button
                onClick={handleCancelJob}
                disabled={actionLoading}
                className="inline-flex items-center px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white font-semibold text-sm rounded-lg shadow-sm disabled:opacity-50 transition-colors"
              >
                <Ban className="w-4 h-4 mr-2" />
                {actionLoading ? 'Cancelling...' : 'Cancel Scan'}
              </button>
            )}
          </div>
        </div>

        {/* Global Error Alert */}
        {error && (
          <div className="bg-rose-50 border border-rose-200 text-rose-800 p-4 rounded-xl text-sm flex items-start space-x-3">
            <AlertCircle className="w-5 h-5 text-rose-600 shrink-0 mt-0.5" />
            <div className="space-y-0.5">
              <p className="font-semibold">{(error as ApiError).message}</p>
              {(error as ApiError).requestId && (
                <p className="text-xs font-mono text-rose-600">Request ID: {(error as ApiError).requestId}</p>
              )}
            </div>
          </div>
        )}

        {/* Progress & Metrics Summary Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Progress Bar Card */}
          <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm md:col-span-2 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500">Execution Progress</h2>
              <span className="text-lg font-bold text-slate-900">{currentProgressPct.toFixed(1)}%</span>
            </div>

            {/* Progress Bar Container */}
            <div className="w-full h-3 bg-slate-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-brand-600 transition-all duration-500 ease-out"
                style={{ width: `${Math.min(100, Math.max(0, currentProgressPct))}%` }}
              />
            </div>

            {/* Progress Breakdown */}
            <div className="grid grid-cols-4 gap-2 pt-2 text-center text-xs">
              <div className="bg-slate-50 p-2 rounded">
                <p className="font-semibold text-slate-600">Queued</p>
                <p className="text-base font-bold text-slate-900 mt-0.5">{progress?.queued_count ?? job.queued_count}</p>
              </div>
              <div className="bg-blue-50 p-2 rounded">
                <p className="font-semibold text-blue-800">Running</p>
                <p className="text-base font-bold text-blue-900 mt-0.5">{progress?.running_count ?? job.running_count}</p>
              </div>
              <div className="bg-emerald-50 p-2 rounded">
                <p className="font-semibold text-emerald-800">Completed</p>
                <p className="text-base font-bold text-emerald-900 mt-0.5">{progress?.completed_count ?? job.completed_count}</p>
              </div>
              <div className="bg-rose-50 p-2 rounded">
                <p className="font-semibold text-rose-800">Failed</p>
                <p className="text-base font-bold text-rose-900 mt-0.5">{progress?.failed_count ?? job.failed_count}</p>
              </div>
            </div>
          </div>

          {/* Email Finding Counter Card */}
          <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm flex flex-col justify-between space-y-4">
            <div>
              <p className="text-xs font-bold uppercase tracking-wider text-slate-500">Email Findings Discovered</p>
              <p className="text-3xl font-extrabold text-emerald-700 mt-2">
                {progress?.email_finding_count ?? job.email_finding_count}
              </p>
              <p className="text-xs text-slate-500 mt-1">Persisted business email addresses found on crawled pages.</p>
            </div>
            <div className="text-xs text-slate-500 space-y-1 border-t border-slate-100 pt-3">
              <p>Created: {new Date(job.created_at).toLocaleString()}</p>
              {job.started_at && <p>Started: {new Date(job.started_at).toLocaleString()}</p>}
              {job.completed_at && <p>Completed: {new Date(job.completed_at).toLocaleString()}</p>}
            </div>
          </div>
        </div>

        {/* Input Quality Breakdown Card */}
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm space-y-3">
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500">Pre-Ingestion Input Quality</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
            <div className="p-3 bg-slate-50 rounded-lg">
              <p className="text-slate-500">Total Inputs</p>
              <p className="text-lg font-bold text-slate-900 mt-0.5">{job.total_input_count}</p>
            </div>
            <div className="p-3 bg-emerald-50/50 rounded-lg">
              <p className="text-emerald-800">Valid Inputs</p>
              <p className="text-lg font-bold text-emerald-900 mt-0.5">{job.valid_input_count}</p>
            </div>
            <div className="p-3 bg-amber-50/50 rounded-lg">
              <p className="text-amber-800">Duplicate Inputs</p>
              <p className="text-lg font-bold text-amber-900 mt-0.5">{job.duplicate_input_count}</p>
            </div>
            <div className="p-3 bg-rose-50/50 rounded-lg">
              <p className="text-rose-800">Invalid Syntax</p>
              <p className="text-lg font-bold text-rose-900 mt-0.5">{job.invalid_input_count}</p>
            </div>
          </div>
        </div>

        {/* URL List Table */}
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden space-y-4 p-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <FileText className="w-5 h-5 text-slate-500" />
              <h3 className="text-base font-bold text-slate-900">Target URLs ({urls.length})</h3>
            </div>
            <button
              onClick={refreshUrls}
              disabled={isLoadingUrls}
              className="inline-flex items-center text-xs font-medium text-slate-600 hover:text-slate-900 px-3 py-1.5 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${isLoadingUrls ? 'animate-spin' : ''}`} />
              Refresh URL List
            </button>
          </div>

          {urls.length === 0 ? (
            <div className="p-8 text-center text-sm text-slate-500">No URL rows loaded.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-600 uppercase tracking-wider">
                    <th className="px-4 py-3">#</th>
                    <th className="px-4 py-3">Original Input</th>
                    <th className="px-4 py-3">Normalized Domain</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Last Error</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200 text-xs font-mono">
                  {urls.map((u) => (
                    <tr key={u.id} className="hover:bg-slate-50">
                      <td className="px-4 py-3 text-slate-500">{u.original_index + 1}</td>
                      <td className="px-4 py-3 text-slate-900 whitespace-pre-wrap break-all">{u.original_input}</td>
                      <td className="px-4 py-3 text-slate-600">{u.normalized_domain || '—'}</td>
                      <td className="px-4 py-3">
                        <span className="px-2 py-0.5 rounded text-[11px] font-sans font-semibold bg-slate-100 text-slate-700 border border-slate-200">
                          {u.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-rose-700 font-sans">{u.last_error_code || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {urlsNextCursor && (
            <div className="text-center pt-2">
              <button
                onClick={loadMoreUrls}
                disabled={isLoadingUrls}
                className="px-4 py-2 bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 font-semibold text-xs rounded-lg shadow-sm disabled:opacity-50 transition-colors"
              >
                {isLoadingUrls ? 'Loading...' : 'Load More URLs'}
              </button>
            </div>
          )}
        </div>
      </div>
    </ProtectedRoute>
  );
}
