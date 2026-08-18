'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import { StatusBadge } from '@/components/jobs/StatusBadge';
import { ClassificationBadge, ValidationBadge } from '@/components/results/FindingBadges';
import { EvidencePanel } from '@/components/results/EvidencePanel';
import {
  ApiError,
  ScanJobApiResponse,
  ScanJobProgressApiResponse,
  ScanJobResultItemApiResponse,
  ScanURLApiResponse,
} from '@/types/api';
import {
  cancelScanJob,
  downloadScanJobCsv,
  getScanJob,
  getScanJobProgress,
  listFindingEvidence,
  listScanJobResults,
  listScanJobUrls,
  queueScanJob,
} from '@/lib/api-client';
import {
  ArrowLeft,
  RefreshCw,
  AlertCircle,
  Play,
  Ban,
  FileText,
  Download,
  Mail,
  Search,
  Filter,
  Eye,
  Info,
} from 'lucide-react';

const ACTIVE_STATUSES = new Set(['QUEUED', 'RUNNING', 'CANCELLING']);
const TERMINAL_STATUSES = new Set(['COMPLETED', 'COMPLETED_WITH_ERRORS', 'CANCELLED', 'FAILED']);

export default function JobDetailPage() {
  const params = useParams();
  const jobId = params?.id as string;

  const [job, setJob] = useState<ScanJobApiResponse | null>(null);
  const [progress, setProgress] = useState<ScanJobProgressApiResponse | null>(null);
  const [activeTab, setActiveTab] = useState<'FINDINGS' | 'URLS'>('FINDINGS');

  // URL list state
  const [urls, setUrls] = useState<ScanURLApiResponse[]>([]);
  const [urlsNextCursor, setUrlsNextCursor] = useState<string | null>(null);
  const [isLoadingUrls, setIsLoadingUrls] = useState(false);

  // Results & Findings state
  const [results, setResults] = useState<ScanJobResultItemApiResponse[]>([]);
  const [resultsNextCursor, setResultsNextCursor] = useState<string | null>(null);
  const [isLoadingResults, setIsLoadingResults] = useState(false);
  const [isLoadingMoreResults, setIsLoadingMoreResults] = useState(false);

  // Results Filter State
  const [filterSearchPrefix, setFilterSearchPrefix] = useState('');
  const [filterEmailDomain, setFilterEmailDomain] = useState('');
  const [filterClassification, setFilterClassification] = useState('ALL');
  const [filterValidationStatus, setFilterValidationStatus] = useState('ALL');

  // Applied Filters State (Triggered on submit or apply)
  const [appliedFilters, setAppliedFilters] = useState({
    searchPrefix: '',
    emailDomain: '',
    classification: 'ALL',
    validationStatus: 'ALL',
  });

  // Selected finding for Evidence Panel
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  const findingTriggerRef = useRef<HTMLButtonElement | null>(null);

  // Export CSV state
  const [isExporting, setIsExporting] = useState(false);

  // General & Error states
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // AbortControllers and request generation tracking to discard stale responses
  const progressAbortRef = useRef<AbortController | null>(null);
  const resultsAbortRef = useRef<AbortController | null>(null);
  const resultsGenRef = useRef(0);
  const isPollingRef = useRef(false);

  // Fetch scan job detail
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

  const isLoadingMoreResultsRef = useRef(false);

  // Fetch results list with generation ID protection against stale responses
  const fetchResults = useCallback(
    async (
      filters: typeof appliedFilters,
      cursor?: string | null,
      append = false
    ) => {
      if (!jobId) return;
      if (append && isLoadingMoreResultsRef.current) return;

      // Abort previous in-flight results request
      if (resultsAbortRef.current) {
        resultsAbortRef.current.abort();
      }
      const controller = new AbortController();
      resultsAbortRef.current = controller;

      const currentGen = ++resultsGenRef.current;

      try {
        if (append) {
          isLoadingMoreResultsRef.current = true;
          setIsLoadingMoreResults(true);
        } else {
          setIsLoadingResults(true);
        }

        const params: Record<string, string> = { limit: '50' };
        if (cursor) params.cursor = cursor;
        if (filters.classification !== 'ALL') params.classification = filters.classification;
        if (filters.validationStatus !== 'ALL') params.validation_status = filters.validationStatus;

        const cleanDomain = filters.emailDomain.trim().toLowerCase();
        if (cleanDomain) params.email_domain = cleanDomain;

        const cleanPrefix = filters.searchPrefix.trim().toLowerCase();
        if (cleanPrefix && cleanPrefix.length >= 2) params.search_prefix = cleanPrefix;

        const res = await listScanJobResults(
          jobId,
          {
            limit: 50,
            cursor: cursor || undefined,
            classification: filters.classification !== 'ALL' ? filters.classification : undefined,
            validation_status: filters.validationStatus !== 'ALL' ? filters.validationStatus : undefined,
            email_domain: cleanDomain || undefined,
            search_prefix: cleanPrefix && cleanPrefix.length >= 2 ? cleanPrefix : undefined,
          },
          controller.signal
        );

        // Discard stale response if newer request generation started
        if (currentGen !== resultsGenRef.current || controller.signal.aborted) return;

        setResults((prev) => {
          if (!append) return res.items;
          const existingIds = new Set(prev.map((item) => item.finding_id));
          const newItems = res.items.filter((item) => !existingIds.has(item.finding_id));
          return [...prev, ...newItems];
        });
        setResultsNextCursor(res.next_cursor);
      } catch (err) {
        if ((err as Error)?.name === 'AbortError') return;
        if (currentGen !== resultsGenRef.current) return;

        if (err instanceof ApiError) {
          setError(err);
        } else {
          setError(new ApiError(500, { code: 'RESULTS_ERROR', message: 'Failed to load email findings.' }));
        }
      } finally {
        if (currentGen === resultsGenRef.current) {
          isLoadingMoreResultsRef.current = false;
          setIsLoadingResults(false);
          setIsLoadingMoreResults(false);
        }
      }
    },
    [jobId]
  );

  // Initial load
  useEffect(() => {
    fetchJobDetail();
  }, [fetchJobDetail]);

  // Fetch results when appliedFilters change
  useEffect(() => {
    setResultsNextCursor(null);
    fetchResults(appliedFilters, null, false);
  }, [appliedFilters, fetchResults]);

  const handleApplyFilters = (e: React.FormEvent) => {
    e.preventDefault();
    setAppliedFilters({
      searchPrefix: filterSearchPrefix,
      emailDomain: filterEmailDomain,
      classification: filterClassification,
      validationStatus: filterValidationStatus,
    });
  };

  const handleResetFilters = () => {
    setFilterSearchPrefix('');
    setFilterEmailDomain('');
    setFilterClassification('ALL');
    setFilterValidationStatus('ALL');
    setAppliedFilters({
      searchPrefix: '',
      emailDomain: '',
      classification: 'ALL',
      validationStatus: 'ALL',
    });
  };

  const jobStatus = job?.status;

  const refreshUrls = useCallback(async () => {
    if (!jobId) return;
    try {
      setIsLoadingUrls(true);
      const res = await listScanJobUrls(jobId, { limit: 50 });
      setUrls(res.items);
      setUrlsNextCursor(res.next_cursor);
    } catch (err) {
      if (err instanceof ApiError) setError(err);
    } finally {
      setIsLoadingUrls(false);
    }
  }, [jobId]);

  // Polling effect: Polls ONLY /progress for active statuses (QUEUED, RUNNING, CANCELLING)
  useEffect(() => {
    if (!jobId || !jobStatus) return;
    if (!ACTIVE_STATUSES.has(jobStatus)) return;

    let isMounted = true;

    const pollProgress = async () => {
      if (isPollingRef.current) return;
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;

      isPollingRef.current = true;
      progressAbortRef.current = new AbortController();

      try {
        const progData = await getScanJobProgress(jobId, progressAbortRef.current.signal);
        if (!isMounted) return;

        setProgress(progData);
        setJob((prev) => (prev ? { ...prev, status: progData.status } : null));

        // If job transitioned to terminal, refresh URL list and findings ONCE
        if (TERMINAL_STATUSES.has(progData.status)) {
          refreshUrls();
          fetchResults(appliedFilters, null, false);
        }
      } catch (err) {
        if ((err as Error)?.name === 'AbortError') return;
        if (err instanceof ApiError) setError(err);
      } finally {
        isPollingRef.current = false;
        progressAbortRef.current = null;
      }
    };

    const intervalId = setInterval(() => {
      if (isMounted) pollProgress();
    }, 3000);

    return () => {
      isMounted = false;
      clearInterval(intervalId);
      if (progressAbortRef.current) {
        progressAbortRef.current.abort();
      }
    };
  }, [jobId, jobStatus, fetchResults, appliedFilters, refreshUrls]);

  const loadMoreUrls = async () => {
    if (!jobId || !urlsNextCursor || isLoadingUrls) return;
    try {
      setIsLoadingUrls(true);
      const res = await listScanJobUrls(jobId, { limit: 50, cursor: urlsNextCursor });
      setUrls((prev) => [...prev, ...res.items]);
      setUrlsNextCursor(res.next_cursor);
    } catch (err) {
      if (err instanceof ApiError) setError(err);
    } finally {
      setIsLoadingUrls(false);
    }
  };

  const handleExportCsv = async () => {
    if (!jobId || isExporting) return;
    setError(null);
    setIsExporting(true);

    try {
      await downloadScanJobCsv(jobId);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(new ApiError(500, { code: 'EXPORT_ERROR', message: 'Failed to download CSV export.' }));
      }
    } finally {
      setIsExporting(false);
    }
  };

  const handleQueueJob = async () => {
    if (!jobId) return;
    try {
      setActionLoading(true);
      const updated = await queueScanJob(jobId);
      setJob(updated);
      refreshUrls();
    } catch (err) {
      if (err instanceof ApiError) setError(err);
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
      if (err instanceof ApiError) setError(err);
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

  if (error && !job) {
    return (
      <ProtectedRoute>
        <div className="max-w-4xl mx-auto px-4 py-12 space-y-4">
          <div className="bg-rose-50 border border-rose-200 text-rose-800 p-6 rounded-xl space-y-2">
            <h3 className="font-semibold text-base">Error Loading Scan Job</h3>
            <p className="text-sm">{error.message}</p>
            {error.requestId && (
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

  if (!job) return null;

  const currentProgressPct = progress?.progress_percentage ?? 0;
  const isJobActive = ACTIVE_STATUSES.has(job.status);
  const isJobTerminal = TERMINAL_STATUSES.has(job.status);

  return (
    <ProtectedRoute>
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        {/* Header Toolbar */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div className="flex items-center space-x-3">
            <Link
              href="/dashboard"
              className="p-2 text-slate-500 hover:text-slate-900 rounded-lg hover:bg-slate-100 transition-colors"
            >
              <ArrowLeft className="w-5 h-5" />
            </Link>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-xl sm:text-2xl font-bold text-slate-900 tracking-tight break-words">
                  {job.name || 'Untitled Scan Job'}
                </h1>
                <StatusBadge status={job.status} />
              </div>
              <p className="text-xs font-mono text-slate-500 mt-1 break-all">ID: {job.id}</p>
            </div>
          </div>

          {/* Action Buttons */}
          <div className="flex flex-wrap items-center gap-3">
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

            {isJobActive && (
              <button
                onClick={handleCancelJob}
                disabled={actionLoading}
                className="inline-flex items-center px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white font-semibold text-sm rounded-lg shadow-sm disabled:opacity-50 transition-colors"
              >
                <Ban className="w-4 h-4 mr-2" />
                {actionLoading ? 'Cancelling...' : 'Cancel Scan'}
              </button>
            )}

            {isJobTerminal && (
              <button
                onClick={handleExportCsv}
                disabled={isExporting}
                className="inline-flex items-center px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-sm rounded-lg shadow-sm disabled:opacity-50 transition-colors"
              >
                <Download className={`w-4 h-4 mr-2 ${isExporting ? 'animate-bounce' : ''}`} />
                {isExporting ? 'Downloading CSV...' : 'Export Findings (CSV)'}
              </button>
            )}
          </div>
        </div>

        {/* Global Error Banner */}
        {error && (
          <div className="bg-rose-50 border border-rose-200 text-rose-800 p-4 rounded-xl text-sm flex items-start space-x-3">
            <AlertCircle className="w-5 h-5 text-rose-600 shrink-0 mt-0.5" />
            <div className="space-y-0.5">
              <p className="font-semibold">{error.message}</p>
              {error.requestId && (
                <p className="text-xs font-mono text-rose-600">Request ID: {error.requestId}</p>
              )}
            </div>
          </div>
        )}

        {/* Active Job Warning Notice */}
        {isJobActive && (
          <div className="bg-blue-50 border border-blue-200 text-blue-900 p-4 rounded-xl text-sm flex items-start space-x-3">
            <Info className="w-5 h-5 text-blue-600 shrink-0 mt-0.5" />
            <div className="flex-1 space-y-0.5">
              <p className="font-semibold">Scan job is currently active and processing target URLs.</p>
              <p className="text-xs text-blue-800">
                Persisted email findings discovered so far are shown below. Use the manual refresh action to fetch newly persisted findings.
              </p>
            </div>
            <button
              onClick={() => fetchResults(appliedFilters, null, false)}
              disabled={isLoadingResults}
              className="text-xs font-semibold text-blue-900 underline hover:no-underline shrink-0"
            >
              Refresh Findings
            </button>
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
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 text-center text-xs">
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

        {/* Tab Selection Header */}
        <div className="border-b border-slate-200 flex space-x-6 overflow-x-auto">
          <button
            onClick={() => setActiveTab('FINDINGS')}
            className={`pb-3 text-sm font-bold flex items-center space-x-2 border-b-2 transition-colors ${
              activeTab === 'FINDINGS'
                ? 'border-brand-600 text-brand-600'
                : 'border-transparent text-slate-500 hover:text-slate-900'
            }`}
          >
            <Mail className="w-4 h-4" />
            <span>Email Findings ({progress?.email_finding_count ?? job.email_finding_count})</span>
          </button>
          <button
            onClick={() => setActiveTab('URLS')}
            className={`pb-3 text-sm font-bold flex items-center space-x-2 border-b-2 transition-colors ${
              activeTab === 'URLS'
                ? 'border-brand-600 text-brand-600'
                : 'border-transparent text-slate-500 hover:text-slate-900'
            }`}
          >
            <FileText className="w-4 h-4" />
            <span>Target URLs ({urls.length})</span>
          </button>
        </div>

        {/* Tab Content: FINDINGS */}
        {activeTab === 'FINDINGS' && (
          <div className="space-y-6">
            {/* Filter Toolbar Form */}
            <form
              onSubmit={handleApplyFilters}
              className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm space-y-4"
            >
              <div className="flex items-center space-x-2 text-xs font-bold uppercase tracking-wider text-slate-600">
                <Filter className="w-4 h-4 text-slate-500" />
                <span>Filter Findings</span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                {/* Search Prefix */}
                <div>
                  <label htmlFor="filterSearchPrefix" className="block text-xs font-semibold text-slate-700 mb-1">
                    Email starts with (min 2 chars)
                  </label>
                  <input
                    id="filterSearchPrefix"
                    type="text"
                    value={filterSearchPrefix}
                    onChange={(e) => setFilterSearchPrefix(e.target.value)}
                    placeholder="e.g. contact, info, sales"
                    className="w-full px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-mono text-slate-900 focus:ring-2 focus:ring-indigo-500"
                  />
                </div>

                {/* Email Domain */}
                <div>
                  <label htmlFor="filterEmailDomain" className="block text-xs font-semibold text-slate-700 mb-1">
                    Email Domain
                  </label>
                  <input
                    id="filterEmailDomain"
                    type="text"
                    value={filterEmailDomain}
                    onChange={(e) => setFilterEmailDomain(e.target.value)}
                    placeholder="e.g. example.com"
                    className="w-full px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-mono text-slate-900 focus:ring-2 focus:ring-indigo-500"
                  />
                </div>

                {/* Classification Select */}
                <div>
                  <label htmlFor="filterClassification" className="block text-xs font-semibold text-slate-700 mb-1">
                    Category
                  </label>
                  <select
                    id="filterClassification"
                    value={filterClassification}
                    onChange={(e) => setFilterClassification(e.target.value)}
                    className="w-full px-3 py-1.5 border border-slate-300 rounded-lg text-xs text-slate-900 focus:ring-2 focus:ring-indigo-500"
                  >
                    <option value="ALL">All Categories</option>
                    <option value="PERSONAL_OR_NAMED">Named Contact</option>
                    <option value="ROLE_BASED">Role Address</option>
                    <option value="NO_REPLY">No-Reply Address</option>
                    <option value="UNKNOWN">Other</option>
                  </select>
                </div>

                {/* Validation Status Select */}
                <div>
                  <label htmlFor="filterValidationStatus" className="block text-xs font-semibold text-slate-700 mb-1">
                    Email Check
                  </label>
                  <select
                    id="filterValidationStatus"
                    value={filterValidationStatus}
                    onChange={(e) => setFilterValidationStatus(e.target.value)}
                    className="w-full px-3 py-1.5 border border-slate-300 rounded-lg text-xs text-slate-900 focus:ring-2 focus:ring-indigo-500"
                  >
                    <option value="ALL">All Statuses</option>
                    <option value="VALID">Format Accepted</option>
                    <option value="UNVERIFIED">Not Independently Verified</option>
                    <option value="INVALID">Rejected Format</option>
                  </select>
                </div>
              </div>

              <div className="flex items-center justify-end space-x-3 pt-1">
                <button
                  type="button"
                  onClick={handleResetFilters}
                  className="px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold text-xs rounded-lg transition-colors"
                >
                  Reset
                </button>
                <button
                  type="submit"
                  disabled={isLoadingResults}
                  className="px-4 py-1.5 bg-slate-800 hover:bg-slate-900 text-white font-semibold text-xs rounded-lg shadow-sm disabled:opacity-50 transition-colors"
                >
                  Apply Filters
                </button>
              </div>
            </form>

            {/* Findings Table Card */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden space-y-4 p-6">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-bold text-slate-900">
                  Discovered Emails ({results.length})
                </h3>
                <button
                  onClick={() => fetchResults(appliedFilters, null, false)}
                  disabled={isLoadingResults}
                  className="inline-flex items-center text-xs font-medium text-slate-600 hover:text-slate-900 px-3 py-1.5 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
                >
                  <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${isLoadingResults ? 'animate-spin' : ''}`} />
                  Refresh Findings
                </button>
              </div>

              {isLoadingResults ? (
                <div className="p-12 text-center space-y-3">
                  <div className="w-8 h-8 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin mx-auto" />
                  <p className="text-sm font-medium text-slate-600">Loading email findings...</p>
                </div>
              ) : results.length === 0 ? (
                <div className="p-12 text-center text-slate-500 space-y-2">
                  <Mail className="w-8 h-8 text-slate-400 mx-auto" />
                  <p className="text-sm font-semibold text-slate-900">No email findings discovered</p>
                  <p className="text-xs max-w-sm mx-auto">
                    {appliedFilters.searchPrefix ||
                    appliedFilters.emailDomain ||
                    appliedFilters.classification !== 'ALL' ||
                    appliedFilters.validationStatus !== 'ALL'
                      ? 'No findings match the applied filter criteria.'
                      : 'No public business email addresses have been extracted for this scan job.'}
                  </p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-600 uppercase tracking-wider">
                        <th className="px-4 py-3">Email Address</th>
                        <th className="px-4 py-3">Domain</th>
                        <th className="px-4 py-3">Category</th>
                        <th className="px-4 py-3">Email Check</th>
                        <th className="px-4 py-3">Sources</th>
                        <th className="px-4 py-3">Last Found</th>
                        <th className="px-4 py-3 text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-200 text-xs font-mono">
                      {results.map((r) => (
                        <tr key={r.finding_id} className="hover:bg-slate-50 transition-colors">
                          <td className="px-4 py-3 font-semibold text-slate-900 whitespace-pre-wrap break-all">
                            {r.canonical_email}
                          </td>

                          <td className="px-4 py-3 text-slate-600">{r.email_domain}</td>

                          <td className="px-4 py-3 font-sans">
                            <ClassificationBadge classification={r.classification} />
                          </td>

                          <td className="px-4 py-3 font-sans">
                            <ValidationBadge status={r.validation_status} />
                          </td>

                          <td className="px-4 py-3 font-sans font-semibold text-slate-700">
                            {r.evidence_count} sources
                          </td>

                          <td className="px-4 py-3 font-sans text-slate-500">
                            {new Date(r.last_found_at).toLocaleString()}
                          </td>

                          <td className="px-4 py-3 text-right font-sans">
                            <button
                              onClick={(e) => {
                                findingTriggerRef.current = e.currentTarget;
                                setSelectedFindingId(r.finding_id);
                              }}
                              disabled={r.evidence_count === 0}
                              title={r.evidence_count === 0 ? "No web page sources available for this finding" : "View web page sources"}
                              className="inline-flex items-center px-2.5 py-1 bg-white hover:bg-slate-100 text-slate-700 font-semibold text-xs rounded border border-slate-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                            >
                              <Eye className="w-3.5 h-3.5 mr-1 text-slate-500" />
                              View Sources
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Results Pagination */}
              {resultsNextCursor && (
                <div className="text-center pt-2">
                  <button
                    onClick={() => fetchResults(appliedFilters, resultsNextCursor, true)}
                    disabled={isLoadingMoreResults}
                    className="px-4 py-2 bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 font-semibold text-xs rounded-lg shadow-sm disabled:opacity-50 transition-colors"
                  >
                    {isLoadingMoreResults ? 'Loading More Findings...' : 'Load More Findings'}
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Tab Content: URLS */}
        {activeTab === 'URLS' && (
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
        )}

        {/* Evidence Panel Slide-over Drawer */}
        <EvidencePanel
          jobId={jobId}
          findingId={selectedFindingId}
          onClose={() => setSelectedFindingId(null)}
          triggerElementRef={findingTriggerRef}
        />
      </div>
    </ProtectedRoute>
  );
}
