'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import Link from 'next/link';
import { useAuth } from '@/context/auth-context';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import { StatusBadge } from '@/components/jobs/StatusBadge';
import {
  AnalyticsOverviewResponse,
  AnalyticsPeriodEnum,
  ApiError,
  ScanJobApiResponse,
} from '@/types/api';
import { cancelScanJob, getAnalyticsOverview, listScanJobs, queueScanJob } from '@/lib/api-client';
import {
  Plus,
  RefreshCw,
  AlertCircle,
  Inbox,
  ExternalLink,
  BarChart3,
  CheckCircle2,
  Search,
  Activity,
  HelpCircle,
  X,
  ShieldAlert,
} from 'lucide-react';

export default function DashboardPage() {
  const { user } = useAuth();

  // Analytics state
  const [analytics, setAnalytics] = useState<AnalyticsOverviewResponse | null>(null);
  const [analyticsPeriod, setAnalyticsPeriod] = useState<AnalyticsPeriodEnum>('30d');
  const [isLoadingAnalytics, setIsLoadingAnalytics] = useState(true);
  const [analyticsError, setAnalyticsError] = useState<string | null>(null);

  // Chart info modal state
  const [activeChartInfoModal, setActiveChartInfoModal] = useState<'CATEGORY' | 'STATUS' | null>(null);
  const modalCloseBtnRef = useRef<HTMLButtonElement | null>(null);
  const lastTriggerRef = useRef<HTMLButtonElement | null>(null);

  // Scans list state
  const [jobs, setJobs] = useState<ScanJobApiResponse[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('ALL');

  const [isLoadingJobs, setIsLoadingJobs] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionJobId, setActionJobId] = useState<string | null>(null);

  // Focus management & Escape key accessibility for Chart Info Modal
  useEffect(() => {
    if (!activeChartInfoModal) return;

    setTimeout(() => {
      modalCloseBtnRef.current?.focus();
    }, 50);

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setActiveChartInfoModal(null);
        lastTriggerRef.current?.focus();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [activeChartInfoModal]);

  // Fetch Analytics
  const fetchAnalytics = useCallback(async (period: AnalyticsPeriodEnum) => {
    try {
      setIsLoadingAnalytics(true);
      setAnalyticsError(null);
      const data = await getAnalyticsOverview(period);
      setAnalytics(data);
    } catch (err) {
      if (err instanceof ApiError) {
        setAnalyticsError(err.message);
      } else {
        setAnalyticsError('Failed to load dashboard analytics.');
      }
    } finally {
      setIsLoadingAnalytics(false);
    }
  }, []);

  useEffect(() => {
    fetchAnalytics(analyticsPeriod);
  }, [analyticsPeriod, fetchAnalytics]);

  // Fetch Scans List
  const fetchJobs = useCallback(async (filter: string, cursor?: string | null, append = false) => {
    try {
      if (append) {
        setIsLoadingMore(true);
      } else {
        setIsLoadingJobs(true);
      }
      setError(null);

      const statusParam = filter === 'ALL' ? undefined : filter;
      const res = await listScanJobs({
        limit: 20,
        cursor: cursor || undefined,
        status: statusParam,
      });

      setJobs((prev) => {
        if (!append) return res.items;
        const existingIds = new Set(prev.map((j) => j.id));
        const newItems = res.items.filter((j) => !existingIds.has(j.id));
        return [...prev, ...newItems];
      });
      setNextCursor(res.next_cursor);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(new ApiError(500, { code: 'FETCH_ERROR', message: 'Failed to load scan list.' }));
      }
    } finally {
      setIsLoadingJobs(false);
      setIsLoadingMore(false);
    }
  }, []);

  useEffect(() => {
    setNextCursor(null);
    fetchJobs(statusFilter, null, false);
  }, [statusFilter, fetchJobs]);

  const handleQueueJob = async (jobId: string) => {
    try {
      setActionJobId(jobId);
      const updated = await queueScanJob(jobId);
      setJobs((prev) => prev.map((j) => (j.id === jobId ? updated : j)));
      fetchAnalytics(analyticsPeriod);
    } catch (err) {
      if (err instanceof ApiError) setError(err);
    } finally {
      setActionJobId(null);
    }
  };

  const handleCancelJob = async (jobId: string) => {
    try {
      setActionJobId(jobId);
      const updated = await cancelScanJob(jobId);
      setJobs((prev) => prev.map((j) => (j.id === jobId ? updated : j)));
      fetchAnalytics(analyticsPeriod);
    } catch (err) {
      if (err instanceof ApiError) setError(err);
    } finally {
      setActionJobId(null);
    }
  };

  const firstName = user?.display_name ? user.display_name.split(' ')[0] : 'there';

  return (
    <ProtectedRoute>
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
        {/* Page Welcome & Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold text-slate-900 tracking-tight">
              Welcome back, {firstName}!
            </h1>
            <p className="text-sm text-slate-600 mt-1">
              Create scans and track your results
            </p>
          </div>
          <div className="flex items-center space-x-3">
            <Link
              href="/help"
              className="inline-flex items-center justify-center px-3.5 py-2.5 bg-white hover:bg-slate-100 text-slate-700 font-semibold text-sm rounded-lg border border-slate-300 transition-colors"
            >
              <HelpCircle className="w-4 h-4 mr-1.5 text-indigo-600" />
              Help & Tutorial
            </Link>
            <Link
              href="/scans/create"
              className="inline-flex items-center justify-center px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-sm rounded-lg shadow-sm transition-colors"
            >
              <Plus className="w-4 h-4 mr-2" />
              Start New Scan
            </Link>
          </div>
        </div>

        {/* Mailbox Verification Disclaimer Callout Banner */}
        <div className="bg-amber-50 border-l-4 border-amber-500 p-4 rounded-r-xl space-y-1 shadow-xs">
          <div className="flex items-center space-x-2 font-bold text-amber-900 text-xs uppercase tracking-wider">
            <ShieldAlert className="w-4 h-4 text-amber-600 shrink-0" />
            <span>Mailbox Verification Policy</span>
          </div>
          <p className="text-xs text-amber-900 leading-relaxed">
            Email Discovery checks email formatting and records where an address was publicly found. It does not send messages, probe mailboxes, perform SMTP checks, or guarantee deliverability.
          </p>
        </div>

        {/* Real Tenant Analytics Section */}
        <div className="space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 bg-white p-4 rounded-2xl border border-slate-200 shadow-sm">
            <div className="flex items-center space-x-2 text-slate-900 font-semibold text-base">
              <BarChart3 className="w-5 h-5 text-indigo-600" />
              <span>Workspace Analytics</span>
            </div>
            <div className="flex items-center space-x-2">
              <label htmlFor="analyticsPeriod" className="text-xs font-semibold text-slate-600 uppercase tracking-wider">
                Period:
              </label>
              <select
                id="analyticsPeriod"
                value={analyticsPeriod}
                onChange={(e) => setAnalyticsPeriod(e.target.value as AnalyticsPeriodEnum)}
                className="px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="7d">Last 7 days</option>
                <option value="30d">Last 30 days</option>
                <option value="90d">Last 90 days</option>
                <option value="all">All time</option>
              </select>
            </div>
          </div>

          {analyticsError && (
            <div className="bg-rose-50 border border-rose-200 text-rose-800 p-4 rounded-xl text-sm">
              <p className="font-semibold">{analyticsError}</p>
            </div>
          )}

          {isLoadingAnalytics ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="bg-white p-6 rounded-2xl border border-slate-200 animate-pulse h-28" />
              ))}
            </div>
          ) : analytics ? (
            <div className="space-y-6">
              {/* Summary Metric Cards */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-2">
                  <div className="flex items-center justify-between text-slate-500">
                    <span className="text-xs font-semibold uppercase tracking-wider">Websites Processed</span>
                    <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                  </div>
                  <p className="text-3xl font-extrabold text-slate-900">{analytics.websites_processed}</p>
                  <p className="text-xs text-slate-500">
                    {analytics.websites_completed} completed • {analytics.websites_failed} failed
                  </p>
                </div>

                <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-2">
                  <div className="flex items-center justify-between text-slate-500">
                    <span className="text-xs font-semibold uppercase tracking-wider">Emails Found</span>
                    <Search className="w-4 h-4 text-indigo-600" />
                  </div>
                  <p className="text-3xl font-extrabold text-indigo-600">{analytics.emails_discovered}</p>
                  <p className="text-xs text-slate-500">
                    Across {analytics.total_scans} total scan runs
                  </p>
                </div>

                <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-2">
                  <div className="flex items-center justify-between text-slate-500">
                    <span className="text-xs font-semibold uppercase tracking-wider">Success Rate</span>
                    <Activity className="w-4 h-4 text-blue-600" />
                  </div>
                  <p className="text-3xl font-extrabold text-slate-900">
                    {analytics.successful_processing_rate}%
                  </p>
                  <p className="text-xs text-slate-500">
                    Successful website processing
                  </p>
                </div>

                <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-2">
                  <div className="flex items-center justify-between text-slate-500">
                    <span className="text-xs font-semibold uppercase tracking-wider">Active Scans</span>
                    <RefreshCw className="w-4 h-4 text-amber-600" />
                  </div>
                  <p className="text-3xl font-extrabold text-slate-900">{analytics.active_scans}</p>
                  <p className="text-xs text-slate-500">
                    Currently scanning or waiting
                  </p>
                </div>
              </div>

              {/* Categorization & Review Distributions */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Findings by Category Chart Card */}
                <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-slate-900">Findings by Category</h3>
                    <button
                      type="button"
                      aria-label="Information about Findings by Category"
                      aria-expanded={activeChartInfoModal === 'CATEGORY'}
                      onClick={(e) => {
                        lastTriggerRef.current = e.currentTarget;
                        setActiveChartInfoModal('CATEGORY');
                      }}
                      className="p-1 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-full transition-colors"
                    >
                      <HelpCircle className="w-4 h-4" />
                    </button>
                  </div>

                  <p className="text-[11px] text-slate-500">
                    The number is the count of emails in that category and the percentage shows its share of all findings.
                  </p>

                  <div className="space-y-3 text-xs">
                    {[
                      { key: 'PERSONAL_OR_NAMED', label: 'Named Contact', color: 'bg-indigo-500' },
                      { key: 'ROLE_BASED', label: 'Role Address', color: 'bg-blue-500' },
                      { key: 'NO_REPLY', label: 'No-Reply Address', color: 'bg-slate-400' },
                      { key: 'UNKNOWN', label: 'Other', color: 'bg-slate-300' },
                    ].map((item) => {
                      const count = analytics.findings_by_classification[item.key] || 0;
                      const total = analytics.emails_discovered || 1;
                      const pct = analytics.emails_discovered > 0 ? Math.round((count / total) * 100) : 0;
                      return (
                        <div key={item.key} className="space-y-1">
                          <div className="flex justify-between font-medium text-slate-700">
                            <span>{item.label}</span>
                            <span>{count} ({pct}%)</span>
                          </div>
                          <div className="w-full bg-slate-100 rounded-full h-2 overflow-hidden">
                            <div className={`h-full ${item.color}`} style={{ width: `${pct}%` }} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Findings by Review Status Chart Card */}
                <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-slate-900">Findings by Review Status</h3>
                    <button
                      type="button"
                      aria-label="Information about Findings by Review Status"
                      aria-expanded={activeChartInfoModal === 'STATUS'}
                      onClick={(e) => {
                        lastTriggerRef.current = e.currentTarget;
                        setActiveChartInfoModal('STATUS');
                      }}
                      className="p-1 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-full transition-colors"
                    >
                      <HelpCircle className="w-4 h-4" />
                    </button>
                  </div>

                  <p className="text-[11px] text-slate-500">
                    The number is the count of emails in that status and the percentage shows its share of all findings.
                  </p>

                  <div className="space-y-3 text-xs">
                    {[
                      { key: 'VALID', label: 'Format Accepted', color: 'bg-emerald-500' },
                      { key: 'UNVERIFIED', label: 'Not Independently Verified', color: 'bg-amber-500', note: 'Found on public page; mailbox not probed' },
                      { key: 'INVALID', label: 'Rejected Format', color: 'bg-rose-500' },
                    ].map((item) => {
                      const count = analytics.findings_by_validation_status[item.key] || 0;
                      const total = analytics.emails_discovered || 1;
                      const pct = analytics.emails_discovered > 0 ? Math.round((count / total) * 100) : 0;
                      return (
                        <div key={item.key} className="space-y-1">
                          <div className="flex justify-between font-medium text-slate-700">
                            <span>{item.label}</span>
                            <span>{count} ({pct}%)</span>
                          </div>
                          <div className="w-full bg-slate-100 rounded-full h-2 overflow-hidden">
                            <div className={`h-full ${item.color}`} style={{ width: `${pct}%` }} />
                          </div>
                          {item.note && (
                            <p className="text-[10px] text-slate-400 italic">{item.note}</p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            </div>
          ) : null}
        </div>

        {/* Your Scans List Section */}
        <div className="space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 bg-white p-4 rounded-2xl border border-slate-200 shadow-sm">
            <div>
              <h2 className="text-xl font-bold text-slate-900">Your Scans</h2>
            </div>

            <div className="flex items-center space-x-3">
              <label htmlFor="statusFilter" className="text-xs font-semibold uppercase tracking-wider text-slate-600">
                Filter Status:
              </label>
              <select
                id="statusFilter"
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="px-3 py-1.5 border border-slate-300 rounded-lg text-xs font-medium text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="ALL">All Scans</option>
                <option value="DRAFT">Not Started</option>
                <option value="QUEUED">Waiting</option>
                <option value="RUNNING">Scanning</option>
                <option value="CANCELLING">Cancelling</option>
                <option value="COMPLETED">Completed</option>
                <option value="COMPLETED_WITH_ERRORS">Completed with Some Issues</option>
                <option value="FAILED">Failed</option>
                <option value="CANCELLED">Cancelled</option>
              </select>

              <button
                onClick={() => fetchJobs(statusFilter, null, false)}
                disabled={isLoadingJobs}
                className="inline-flex items-center text-xs font-medium text-slate-600 hover:text-slate-900 px-3 py-1.5 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
              >
                <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${isLoadingJobs ? 'animate-spin' : ''}`} />
                Refresh
              </button>
            </div>
          </div>

          {/* Error Banner */}
          {error && (
            <div className="bg-rose-50 border border-rose-200 text-rose-800 p-4 rounded-xl text-sm flex items-start space-x-3">
              <AlertCircle className="w-5 h-5 text-rose-600 mt-0.5 shrink-0" />
              <div className="flex-1 space-y-1">
                <p className="font-semibold">{error.message}</p>
                {error.requestId && (
                  <p className="text-xs font-mono text-rose-600">Request ID: {error.requestId}</p>
                )}
              </div>
              <button
                onClick={() => fetchJobs(statusFilter, null, false)}
                className="text-xs font-semibold underline hover:text-rose-900"
              >
                Retry
              </button>
            </div>
          )}

          {/* Scans Table */}
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
            {isLoadingJobs ? (
              <div className="p-12 text-center space-y-3">
                <div className="w-8 h-8 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin mx-auto" />
                <p className="text-sm font-medium text-slate-600">Loading your scans...</p>
              </div>
            ) : jobs.length === 0 ? (
              <div className="p-8 sm:p-12 text-center space-y-6">
                <div className="w-12 h-12 rounded-full bg-indigo-50 text-indigo-600 flex items-center justify-center mx-auto">
                  <Inbox className="w-6 h-6" />
                </div>
                <div className="space-y-1">
                  <h3 className="text-lg font-bold text-slate-900">No scans found</h3>
                  <p className="text-sm text-slate-500 max-w-md mx-auto">
                    {statusFilter !== 'ALL'
                      ? `No scans match status "${statusFilter}".`
                      : 'Start your first scan to begin discovering published business contacts.'}
                  </p>
                </div>

                {statusFilter === 'ALL' && (
                  <div className="max-w-xl mx-auto bg-slate-50 border border-slate-200 rounded-2xl p-6 text-left space-y-4 shadow-xs">
                    <h4 className="text-sm font-bold text-slate-900 uppercase tracking-wider text-indigo-600">
                      Quick Start Checklist
                    </h4>
                    <div className="space-y-3 text-xs text-slate-700">
                      <div className="flex items-start space-x-3">
                        <span className="w-6 h-6 rounded-full bg-indigo-600 text-white flex items-center justify-center font-bold text-xs shrink-0 mt-0.5">
                          1
                        </span>
                        <div>
                          <p className="font-semibold text-slate-900">Start Your First Scan</p>
                          <p className="text-slate-500 mt-0.5">
                            Click the button below to name your scan job.
                          </p>
                        </div>
                      </div>

                      <div className="flex items-start space-x-3">
                        <span className="w-6 h-6 rounded-full bg-indigo-600 text-white flex items-center justify-center font-bold text-xs shrink-0 mt-0.5">
                          2
                        </span>
                        <div>
                          <p className="font-semibold text-slate-900">Paste Website Addresses</p>
                          <p className="text-slate-500 mt-0.5">
                            Add target supplier, directory, or partner URLs (one per line).
                          </p>
                        </div>
                      </div>

                      <div className="flex items-start space-x-3">
                        <span className="w-6 h-6 rounded-full bg-indigo-600 text-white flex items-center justify-center font-bold text-xs shrink-0 mt-0.5">
                          3
                        </span>
                        <div>
                          <p className="font-semibold text-slate-900">Review & Export</p>
                          <p className="text-slate-500 mt-0.5">
                            Review normalized URLs, monitor progress, and export clean CSV results.
                          </p>
                        </div>
                      </div>
                    </div>

                    <div className="pt-2">
                      <Link
                        href="/scans/create"
                        className="inline-flex items-center justify-center w-full sm:w-auto px-5 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-xs rounded-lg shadow-sm transition-colors"
                      >
                        <Plus className="w-4 h-4 mr-2" />
                        Start Your First Scan
                      </Link>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-600 uppercase tracking-wider">
                      <th className="px-6 py-3.5">Scan Name</th>
                      <th className="px-6 py-3.5">Status</th>
                      <th className="px-6 py-3.5">Websites Added</th>
                      <th className="px-6 py-3.5">Scan Progress</th>
                      <th className="px-6 py-3.5">Emails Found</th>
                      <th className="px-6 py-3.5">Created</th>
                      <th className="px-6 py-3.5 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200 text-sm">
                    {jobs.map((job) => (
                      <tr key={job.id} className="hover:bg-slate-50/80 transition-colors">
                        {/* Scan Name */}
                        <td className="px-6 py-4 font-medium text-slate-900">
                          <Link href={`/scans/${job.id}`} className="hover:text-indigo-600 hover:underline">
                            {job.name || <span className="text-slate-400 italic">Scan {job.id.substring(0, 8)}</span>}
                          </Link>
                        </td>

                        {/* Status Badge */}
                        <td className="px-6 py-4">
                          <StatusBadge status={job.status} />
                        </td>

                        {/* Websites Added */}
                        <td className="px-6 py-4 text-xs space-y-0.5">
                          <p className="font-semibold text-slate-900">{job.valid_input_count} valid</p>
                          <p className="text-slate-500">
                            Total submitted: {job.total_input_count}
                          </p>
                        </td>

                        {/* Scan Progress */}
                        <td className="px-6 py-4 text-xs space-y-0.5">
                          <p className="text-slate-700 font-semibold">
                            {job.completed_count} finished
                            {job.failed_count > 0 && (
                              <span className="text-rose-600 font-normal ml-1">({job.failed_count} failed)</span>
                            )}
                          </p>
                        </td>

                        {/* Emails Found */}
                        <td className="px-6 py-4">
                          <span className="inline-flex items-center px-2.5 py-1 rounded-md bg-indigo-50 text-indigo-700 font-bold text-xs border border-indigo-200">
                            {job.email_finding_count} emails
                          </span>
                        </td>

                        {/* Date */}
                        <td className="px-6 py-4 text-xs text-slate-500">
                          {new Date(job.created_at).toLocaleDateString()}
                        </td>

                        {/* Actions */}
                        <td className="px-6 py-4 text-right space-x-2">
                          {job.status === 'DRAFT' && (
                            <button
                              onClick={() => handleQueueJob(job.id)}
                              disabled={actionJobId === job.id}
                              className="px-2.5 py-1 bg-indigo-600 hover:bg-indigo-700 text-white font-medium text-xs rounded shadow-sm disabled:opacity-50 transition-colors"
                            >
                              {actionJobId === job.id ? 'Starting...' : 'Start Scan'}
                            </button>
                          )}

                          {(job.status === 'QUEUED' || job.status === 'RUNNING') && (
                            <button
                              onClick={() => handleCancelJob(job.id)}
                              disabled={actionJobId === job.id}
                              className="px-2.5 py-1 bg-slate-100 hover:bg-rose-100 hover:text-rose-800 text-slate-700 font-medium text-xs rounded border border-slate-300 disabled:opacity-50 transition-colors"
                            >
                              {actionJobId === job.id ? 'Cancelling...' : 'Cancel'}
                            </button>
                          )}

                          <Link
                            href={`/scans/${job.id}`}
                            className="inline-flex items-center px-2.5 py-1 bg-white hover:bg-slate-100 text-slate-700 font-medium text-xs rounded border border-slate-300 transition-colors"
                          >
                            Results
                            <ExternalLink className="w-3 h-3 ml-1" />
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Pagination */}
            {nextCursor && (
              <div className="p-4 bg-slate-50 border-t border-slate-200 text-center">
                <button
                  onClick={() => fetchJobs(statusFilter, nextCursor, true)}
                  disabled={isLoadingMore}
                  className="px-4 py-2 bg-white border border-slate-300 hover:bg-slate-100 text-slate-700 font-medium text-xs rounded-lg shadow-sm disabled:opacity-50 transition-colors"
                >
                  {isLoadingMore ? 'Loading more scans...' : 'Load More Scans'}
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Accessible Chart Info Modal Dialog */}
        {activeChartInfoModal && (
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="chart-info-modal-title"
            className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in duration-150"
          >
            <div className="bg-white rounded-2xl max-w-lg w-full p-6 shadow-2xl space-y-4 border border-slate-200">
              <div className="flex items-center justify-between border-b border-slate-100 pb-3">
                <h3 id="chart-info-modal-title" className="text-base font-bold text-slate-900 flex items-center space-x-2">
                  <HelpCircle className="w-5 h-5 text-indigo-600" />
                  <span>
                    {activeChartInfoModal === 'CATEGORY' ? 'Findings by Category Explained' : 'Findings by Review Status Explained'}
                  </span>
                </h3>
                <button
                  ref={modalCloseBtnRef}
                  onClick={() => {
                    setActiveChartInfoModal(null);
                    lastTriggerRef.current?.focus();
                  }}
                  aria-label="Close information dialog"
                  className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-lg transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {activeChartInfoModal === 'CATEGORY' ? (
                <div className="space-y-3 text-xs text-slate-700 leading-relaxed">
                  <p>
                    The number is the count of emails in that category and the percentage shows its share of all findings.
                  </p>
                  <div className="space-y-2">
                    <p>
                      <strong className="text-indigo-700">Named Contact</strong> — An email that appears connected to a person, such as <code className="bg-slate-100 px-1 rounded">jane.smith@example.com</code>.
                    </p>
                    <p>
                      <strong className="text-blue-700">Role Address</strong> — A general business inbox for a department or purpose, such as <code className="bg-slate-100 px-1 rounded">sales@</code>, <code className="bg-slate-100 px-1 rounded">info@</code>, or <code className="bg-slate-100 px-1 rounded">support@</code>.
                    </p>
                    <p>
                      <strong className="text-slate-800">No-Reply Address</strong> — An automated address, such as <code className="bg-slate-100 px-1 rounded">noreply@example.com</code>, that usually should not receive replies.
                    </p>
                    <p>
                      <strong className="text-slate-600">Other</strong> — The system found an email but could not confidently place it in the categories above.
                    </p>
                  </div>
                </div>
              ) : (
                <div className="space-y-3 text-xs text-slate-700 leading-relaxed">
                  <p>
                    The number is the count of emails in that status and the percentage shows its share of all findings.
                  </p>
                  <div className="space-y-2">
                    <p>
                      <strong className="text-emerald-800">Format Accepted</strong> — The email has a valid-looking written format. This does not prove that the mailbox exists or receives messages.
                    </p>
                    <p>
                      <strong className="text-amber-800">Not Independently Verified</strong> — The email was found on a public web page, but the system did not contact, ping, or test the mailbox.
                    </p>
                    <p>
                      <strong className="text-rose-800">Rejected Format</strong> — The discovered text looked like an email but failed the system’s formatting or safety checks, so it was not accepted as a usable finding.
                    </p>
                  </div>
                </div>
              )}

              {/* Mailbox Verification Disclaimer Note */}
              <div className="bg-amber-50 p-3 rounded-xl border border-amber-200 text-[11px] text-amber-900 leading-relaxed">
                <strong>Policy Note:</strong> Email Discovery checks email formatting and records where an address was publicly found. It does not send messages, probe mailboxes, perform SMTP checks, or guarantee deliverability.
              </div>

              <div className="pt-2 flex justify-between items-center text-xs">
                <Link
                  href="/help"
                  className="font-semibold text-indigo-600 hover:underline"
                  onClick={() => setActiveChartInfoModal(null)}
                >
                  Read complete Help & Tutorial guide →
                </Link>
                <button
                  type="button"
                  onClick={() => {
                    setActiveChartInfoModal(null);
                    lastTriggerRef.current?.focus();
                  }}
                  className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-900 text-white font-semibold rounded-lg shadow-sm"
                >
                  Got it
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </ProtectedRoute>
  );
}
