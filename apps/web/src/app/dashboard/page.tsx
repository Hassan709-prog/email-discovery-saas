'use client';

import React, { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import { StatusBadge } from '@/components/jobs/StatusBadge';
import { ApiError, ScanJobApiResponse, ScanJobStatus } from '@/types/api';
import { cancelScanJob, listScanJobs, queueScanJob } from '@/lib/api-client';
import { Plus, RefreshCw, AlertCircle, Inbox, ExternalLink } from 'lucide-react';

export default function DashboardPage() {
  const [jobs, setJobs] = useState<ScanJobApiResponse[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('ALL');

  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [actionJobId, setActionJobId] = useState<string | null>(null);

  const fetchJobs = useCallback(async (filter: string, cursor?: string | null, append = false) => {
    try {
      if (append) {
        setIsLoadingMore(true);
      } else {
        setIsLoading(true);
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
        // Deduplicate appended items by job ID
        const existingIds = new Set(prev.map((j) => j.id));
        const newItems = res.items.filter((j) => !existingIds.has(j.id));
        return [...prev, ...newItems];
      });
      setNextCursor(res.next_cursor);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(new ApiError(500, { code: 'FETCH_ERROR', message: 'Failed to load scan jobs.' }));
      }
    } finally {
      setIsLoading(false);
      setIsLoadingMore(false);
    }
  }, []);

  // Reset accumulated items and cursor when status filter changes
  useEffect(() => {
    setNextCursor(null);
    fetchJobs(statusFilter, null, false);
  }, [statusFilter, fetchJobs]);

  const handleQueueJob = async (jobId: string) => {
    try {
      setActionJobId(jobId);
      const updated = await queueScanJob(jobId);
      setJobs((prev) => prev.map((j) => (j.id === jobId ? updated : j)));
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      }
    } finally {
      setActionJobId(null);
    }
  };

  const handleCancelJob = async (jobId: string) => {
    try {
      setActionJobId(jobId);
      const updated = await cancelScanJob(jobId);
      setJobs((prev) => prev.map((j) => (j.id === jobId ? updated : j)));
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      }
    } finally {
      setActionJobId(null);
    }
  };

  return (
    <ProtectedRoute>
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        {/* Page Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Scan Jobs Dashboard</h1>
            <p className="text-sm text-slate-600 mt-1">
              Manage tenant batch email discovery jobs and monitor execution progress.
            </p>
          </div>
          <Link
            href="/scans/create"
            className="inline-flex items-center justify-center px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white font-semibold text-sm rounded-lg shadow-sm transition-colors"
          >
            <Plus className="w-4 h-4 mr-2" />
            Create New Scan
          </Link>
        </div>

        {/* Error Notification */}
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

        {/* Controls & Filter Toolbar */}
        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center space-x-3 w-full sm:w-auto">
            <label htmlFor="statusFilter" className="text-xs font-semibold uppercase tracking-wider text-slate-600">
              Filter Status:
            </label>
            <select
              id="statusFilter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="px-3 py-1.5 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              <option value="ALL">All Statuses</option>
              <option value="DRAFT">Draft</option>
              <option value="QUEUED">Queued</option>
              <option value="RUNNING">Running</option>
              <option value="CANCELLING">Cancelling</option>
              <option value="COMPLETED">Completed</option>
              <option value="COMPLETED_WITH_ERRORS">Partial (Completed with Errors)</option>
              <option value="FAILED">Failed</option>
              <option value="CANCELLED">Cancelled</option>
            </select>
          </div>

          <button
            onClick={() => fetchJobs(statusFilter, null, false)}
            disabled={isLoading}
            className="inline-flex items-center text-xs font-medium text-slate-600 hover:text-slate-900 px-3 py-1.5 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>

        {/* Table Content */}
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          {isLoading ? (
            <div className="p-12 text-center space-y-3">
              <div className="w-8 h-8 border-4 border-brand-600 border-t-transparent rounded-full animate-spin mx-auto" />
              <p className="text-sm font-medium text-slate-600">Loading scan jobs...</p>
            </div>
          ) : jobs.length === 0 ? (
            <div className="p-12 text-center space-y-4">
              <div className="w-12 h-12 rounded-full bg-slate-100 flex items-center justify-center mx-auto text-slate-400">
                <Inbox className="w-6 h-6" />
              </div>
              <div className="space-y-1">
                <h3 className="text-base font-semibold text-slate-900">No scan jobs found</h3>
                <p className="text-sm text-slate-500 max-w-sm mx-auto">
                  {statusFilter !== 'ALL'
                    ? `No jobs match status filter "${statusFilter}".`
                    : 'Start your first batch email discovery job by pasting target URLs.'}
                </p>
              </div>
              {statusFilter === 'ALL' && (
                <Link
                  href="/scans/create"
                  className="inline-flex items-center px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white font-semibold text-sm rounded-lg shadow-sm transition-colors"
                >
                  <Plus className="w-4 h-4 mr-2" />
                  Create First Scan
                </Link>
              )}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-600 uppercase tracking-wider">
                    <th className="px-6 py-3.5">Job Name</th>
                    <th className="px-6 py-3.5">Status</th>
                    <th className="px-6 py-3.5">Input Quality</th>
                    <th className="px-6 py-3.5">Execution Progress</th>
                    <th className="px-6 py-3.5">Findings</th>
                    <th className="px-6 py-3.5">Created</th>
                    <th className="px-6 py-3.5 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200 text-sm">
                  {jobs.map((job) => (
                    <tr key={job.id} className="hover:bg-slate-50/80 transition-colors">
                      {/* Job Name */}
                      <td className="px-6 py-4 font-medium text-slate-900">
                        <Link href={`/scans/${job.id}`} className="hover:text-brand-600 hover:underline">
                          {job.name || <span className="text-slate-400 italic">Untitled Job</span>}
                        </Link>
                        <p className="text-xs text-slate-500 font-mono mt-0.5">{job.id.substring(0, 8)}...</p>
                      </td>

                      {/* Status Badge */}
                      <td className="px-6 py-4">
                        <StatusBadge status={job.status} />
                      </td>

                      {/* Input Quality Breakdown */}
                      <td className="px-6 py-4 text-xs space-y-0.5">
                        <p className="font-semibold text-slate-900">Total: {job.total_input_count}</p>
                        <p className="text-slate-600">
                          <span className="text-emerald-700 font-medium">{job.valid_input_count} valid</span>
                          {' • '}
                          <span className="text-amber-700 font-medium">{job.duplicate_input_count} dupes</span>
                          {' • '}
                          <span className="text-rose-700 font-medium">{job.invalid_input_count} invalid</span>
                        </p>
                      </td>

                      {/* Execution Progress */}
                      <td className="px-6 py-4 text-xs space-y-0.5">
                        <p className="text-slate-700">
                          <span className="font-semibold text-emerald-700">{job.completed_count} completed</span>
                          {job.failed_count > 0 && (
                            <span className="font-semibold text-rose-700 ml-1.5">{job.failed_count} failed</span>
                          )}
                        </p>
                        <p className="text-slate-500">
                          {job.running_count > 0 && `${job.running_count} running • `}
                          {job.queued_count > 0 && `${job.queued_count} queued`}
                        </p>
                      </td>

                      {/* Email Finding Count */}
                      <td className="px-6 py-4">
                        <span className="inline-flex items-center px-2.5 py-1 rounded-md bg-emerald-50 text-emerald-800 font-bold text-xs border border-emerald-200">
                          {job.email_finding_count} emails
                        </span>
                      </td>

                      {/* Creation Date */}
                      <td className="px-6 py-4 text-xs text-slate-500">
                        {new Date(job.created_at).toLocaleString()}
                      </td>

                      {/* Action Buttons */}
                      <td className="px-6 py-4 text-right space-x-2">
                        {job.status === 'DRAFT' && (
                          <button
                            onClick={() => handleQueueJob(job.id)}
                            disabled={actionJobId === job.id}
                            className="px-2.5 py-1 bg-amber-600 hover:bg-amber-700 text-white font-medium text-xs rounded shadow-sm disabled:opacity-50 transition-colors"
                          >
                            {actionJobId === job.id ? 'Queueing...' : 'Queue Job'}
                          </button>
                        )}

                        {(job.status === 'QUEUED' || job.status === 'RUNNING') && (
                          <button
                            onClick={() => handleCancelJob(job.id)}
                            disabled={actionJobId === job.id}
                            className="px-2.5 py-1 bg-slate-200 hover:bg-rose-100 hover:text-rose-800 text-slate-700 font-medium text-xs rounded border border-slate-300 disabled:opacity-50 transition-colors"
                          >
                            {actionJobId === job.id ? 'Cancelling...' : 'Cancel'}
                          </button>
                        )}

                        <Link
                          href={`/scans/${job.id}`}
                          className="inline-flex items-center px-2.5 py-1 bg-white hover:bg-slate-100 text-slate-700 font-medium text-xs rounded border border-slate-300 transition-colors"
                        >
                          Details
                          <ExternalLink className="w-3 h-3 ml-1" />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Keyset Pagination Controls */}
          {nextCursor && (
            <div className="p-4 bg-slate-50 border-t border-slate-200 text-center">
              <button
                onClick={() => fetchJobs(statusFilter, nextCursor, true)}
                disabled={isLoadingMore}
                className="px-4 py-2 bg-white border border-slate-300 hover:bg-slate-100 text-slate-700 font-medium text-xs rounded-lg shadow-sm disabled:opacity-50 transition-colors"
              >
                {isLoadingMore ? 'Loading more jobs...' : 'Load More Jobs'}
              </button>
            </div>
          )}
        </div>
      </div>
    </ProtectedRoute>
  );
}
