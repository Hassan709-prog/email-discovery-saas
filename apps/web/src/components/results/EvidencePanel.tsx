'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  ApiError,
  FindingEvidenceItemApiResponse,
  ScanJobResultDetailApiResponse,
} from '@/types/api';
import { getScanJobResultDetail, listFindingEvidence } from '@/lib/api-client';
import { ClassificationBadge, ValidationBadge } from '@/components/results/FindingBadges';
import { X, RefreshCw, AlertCircle, FileCode, CheckCircle, HelpCircle } from 'lucide-react';

interface EvidencePanelProps {
  jobId: string;
  findingId: string | null;
  onClose: () => void;
  triggerElementRef?: React.RefObject<HTMLElement | null>;
}

export const EvidencePanel: React.FC<EvidencePanelProps> = ({
  jobId,
  findingId,
  onClose,
  triggerElementRef,
}) => {
  const [detail, setDetail] = useState<ScanJobResultDetailApiResponse | null>(null);
  const [evidenceItems, setEvidenceItems] = useState<FindingEvidenceItemApiResponse[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);

  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const fetchDetailAndEvidence = useCallback(
    async (targetFindingId: string) => {
      // Abort previous in-flight requests
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      const controller = new AbortController();
      abortControllerRef.current = controller;

      setIsLoading(true);
      setError(null);
      setDetail(null);
      setEvidenceItems([]);
      setNextCursor(null);

      try {
        const [detailRes, evidenceRes] = await Promise.all([
          getScanJobResultDetail(jobId, targetFindingId, controller.signal),
          listFindingEvidence(jobId, targetFindingId, { limit: 20 }, controller.signal),
        ]);

        if (controller.signal.aborted) return;
        setDetail(detailRes);
        setEvidenceItems(evidenceRes.items);
        setNextCursor(evidenceRes.next_cursor);
      } catch (err) {
        if ((err as Error)?.name === 'AbortError') return;
        if (err instanceof ApiError) {
          setError(err);
        } else {
          setError(new ApiError(500, { code: 'EVIDENCE_FETCH_ERROR', message: 'Failed to load evidence details.' }));
        }
      } finally {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      }
    },
    [jobId]
  );

  const loadMoreEvidence = async () => {
    if (!findingId || !nextCursor || isLoadingMore) return;
    try {
      setIsLoadingMore(true);
      const res = await listFindingEvidence(
        jobId,
        findingId,
        { limit: 20, cursor: nextCursor },
        abortControllerRef.current?.signal
      );
      if (abortControllerRef.current?.signal.aborted) return;
      setEvidenceItems((prev) => {
        const existingIds = new Set(prev.map((ev) => ev.evidence_id));
        const newItems = res.items.filter((ev) => !existingIds.has(ev.evidence_id));
        return [...prev, ...newItems];
      });
      setNextCursor(res.next_cursor);
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return;
      if (err instanceof ApiError) setError(err);
    } finally {
      setIsLoadingMore(false);
    }
  };

  useEffect(() => {
    if (findingId) {
      fetchDetailAndEvidence(findingId);
    } else {
      setDetail(null);
      setEvidenceItems([]);
    }

    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [findingId, fetchDetailAndEvidence]);

  // Focus management & Escape key accessibility
  useEffect(() => {
    if (!findingId) return;

    const triggerEl = triggerElementRef?.current;

    // Focus close button on open
    setTimeout(() => {
      closeButtonRef.current?.focus();
    }, 50);

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);

    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      // Restore focus to opening button on close
      if (triggerEl) {
        triggerEl.focus();
      }
    };
  }, [findingId, onClose, triggerElementRef]);

  if (!findingId) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="evidence-dialog-title"
      className="fixed inset-0 z-50 overflow-hidden bg-slate-900/40 backdrop-blur-xs flex justify-end"
    >
      {/* Slide-over Container */}
      <div className="w-full max-w-2xl bg-white h-full shadow-2xl flex flex-col border-l border-slate-200 animate-in slide-in-from-right duration-200">
        {/* Header */}
        <div className="p-6 bg-slate-50 border-b border-slate-200 flex items-start justify-between">
          <div className="space-y-1">
            <h2 id="evidence-dialog-title" className="text-xl font-bold text-slate-900 tracking-tight">
              Finding Evidence Details
            </h2>
            {detail && (
              <p className="text-sm font-mono text-brand-700 font-semibold break-all">
                {detail.canonical_email}
              </p>
            )}
          </div>
          <button
            ref={closeButtonRef}
            onClick={onClose}
            aria-label="Close evidence panel"
            className="p-2 text-slate-400 hover:text-slate-700 hover:bg-slate-200/60 rounded-lg transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Error Banner */}
          {error && (
            <div className="bg-rose-50 border border-rose-200 text-rose-800 p-4 rounded-xl text-sm flex items-start space-x-3">
              <AlertCircle className="w-5 h-5 text-rose-600 shrink-0 mt-0.5" />
              <div className="flex-1 space-y-1">
                <p className="font-semibold">{error.message}</p>
                {error.requestId && (
                  <p className="text-xs font-mono text-rose-600">Request ID: {error.requestId}</p>
                )}
                <button
                  onClick={() => fetchDetailAndEvidence(findingId)}
                  className="text-xs font-semibold text-rose-900 underline hover:no-underline pt-1 block"
                >
                  Retry Loading Evidence
                </button>
              </div>
            </div>
          )}

          {/* Loading Skeleton */}
          {isLoading ? (
            <div className="py-16 text-center space-y-3">
              <div className="w-8 h-8 border-4 border-brand-600 border-t-transparent rounded-full animate-spin mx-auto" />
              <p className="text-sm font-medium text-slate-600">Loading evidence records...</p>
            </div>
          ) : (
            <>
              {/* Finding Detail Summary Card */}
              {detail && (
                <div className="bg-white p-5 rounded-xl border border-slate-200 shadow-sm space-y-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <ClassificationBadge classification={detail.classification} />
                    <ValidationBadge status={detail.validation_status} />
                    <span className="text-xs font-semibold px-2 py-0.5 rounded bg-slate-100 text-slate-700 border border-slate-200">
                      {detail.evidence_count} evidence records
                    </span>
                  </div>

                  <div className="grid grid-cols-2 gap-4 text-xs">
                    <div>
                      <p className="text-slate-500 font-medium">Email Domain</p>
                      <p className="font-mono text-slate-900 mt-0.5">{detail.email_domain}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 font-medium">Role Address</p>
                      <p className="font-semibold text-slate-900 mt-0.5">
                        {detail.is_role_based ? 'Yes (Role/Group)' : 'No (Individual)'}
                      </p>
                    </div>
                    <div>
                      <p className="text-slate-500 font-medium">First Discovered</p>
                      <p className="text-slate-900 mt-0.5">{new Date(detail.first_found_at).toLocaleString()}</p>
                    </div>
                    <div>
                      <p className="text-slate-500 font-medium">Last Discovered</p>
                      <p className="text-slate-900 mt-0.5">{new Date(detail.last_found_at).toLocaleString()}</p>
                    </div>
                  </div>
                </div>
              )}

              {/* Evidence Items List */}
              <div className="space-y-4">
                <h3 className="text-xs font-bold uppercase tracking-wider text-slate-500">
                  Discovered Evidence Records ({evidenceItems.length})
                </h3>

                {evidenceItems.length === 0 ? (
                  <div className="p-8 text-center bg-slate-50 rounded-xl border border-slate-200 text-slate-500 text-sm">
                    No evidence records found for this email finding.
                  </div>
                ) : (
                  <div className="space-y-3">
                    {evidenceItems.map((ev, index) => (
                      <div
                        key={ev.evidence_id}
                        className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm space-y-2.5 text-xs"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-mono font-bold text-slate-500">#{index + 1}</span>
                          <div className="flex items-center space-x-2">
                            <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-slate-100 text-slate-800 border border-slate-200">
                              Source: {ev.source_type}
                            </span>
                            <span className="px-2 py-0.5 rounded text-[11px] font-semibold bg-emerald-50 text-emerald-800 border border-emerald-200">
                              Score: {(ev.confidence * 100).toFixed(0)}%
                            </span>
                          </div>
                        </div>

                        {/* Source Page URL - Plain Text Safe Rendering */}
                        <div>
                          <p className="text-slate-500 font-medium">Source Web Page URL:</p>
                          <p className="font-mono text-slate-900 whitespace-pre-wrap break-all mt-0.5 bg-slate-50 p-2 rounded border border-slate-200">
                            {ev.sanitized_page_url}
                          </p>
                        </div>

                        {/* Evidence Snippet - Plain Text Safe Rendering (NO dangerouslySetInnerHTML) */}
                        {ev.snippet && (
                          <div>
                            <p className="text-slate-500 font-medium">Surrounding Text Context Snippet:</p>
                            <p className="font-mono text-slate-800 whitespace-pre-wrap break-all mt-0.5 bg-slate-900 text-slate-100 p-3 rounded-lg text-[11px] leading-relaxed">
                              {ev.snippet}
                            </p>
                          </div>
                        )}

                        <div className="flex items-center justify-between text-[11px] text-slate-500 pt-1 border-t border-slate-100">
                          <p>
                            HTTP Status: {ev.crawled_page_status_code ?? '—'} • Crawl Depth: {ev.crawled_page_depth ?? '—'}
                          </p>
                          <p>{new Date(ev.created_at).toLocaleString()}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Evidence Pagination Button */}
                {nextCursor && (
                  <div className="text-center pt-2">
                    <button
                      onClick={loadMoreEvidence}
                      disabled={isLoadingMore}
                      className="px-4 py-2 bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 font-semibold text-xs rounded-lg shadow-sm disabled:opacity-50 transition-colors"
                    >
                      {isLoadingMore ? 'Loading More Evidence...' : 'Load More Evidence'}
                    </button>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
