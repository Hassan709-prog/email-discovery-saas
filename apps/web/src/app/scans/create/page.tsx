'use client';

import React, { useState, useRef } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import {
  ApiError,
  PreviewScanInputsApiResponse,
  ScanJobApiResponse,
} from '@/types/api';
import { createScanJob, previewScanInputs, queueScanJob } from '@/lib/api-client';
import { ArrowLeft, CheckCircle2, AlertTriangle, XCircle, Send, RefreshCw, AlertCircle } from 'lucide-react';

export default function CreateScanPage() {
  const router = useRouter();

  const [jobName, setJobName] = useState('');
  const [inputText, setInputText] = useState('');

  const [previewResult, setPreviewResult] = useState<PreviewScanInputsApiResponse | null>(null);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  // Idempotency management: Store key for specific payload string
  const idempotencyKeyRef = useRef<string | null>(null);
  const lastPayloadKeyRef = useRef<string>('');

  // Draft state tracking if create succeeds but queue fails
  const [savedDraftJob, setSavedDraftJob] = useState<ScanJobApiResponse | null>(null);

  const getOrGenerateIdempotencyKey = (payloadString: string): string => {
    if (lastPayloadKeyRef.current === payloadString && idempotencyKeyRef.current) {
      return idempotencyKeyRef.current;
    }
    const newKey = crypto.randomUUID();
    idempotencyKeyRef.current = newKey;
    lastPayloadKeyRef.current = payloadString;
    return newKey;
  };

  const handleSampleClick = () => {
    setJobName('Supplier Websites');
    setInputText('https://example.com/about\nhttps://acme-corp.com/contact\nexample.org/team');
    setPreviewResult(null);
    setSavedDraftJob(null);
  };

  const handlePreview = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setError(null);
    setSavedDraftJob(null);

    const rawInputs = inputText
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0);

    if (rawInputs.length === 0) {
      setError(
        new ApiError(400, {
          code: 'NO_INPUTS',
          message: 'Please paste at least one website address.',
        })
      );
      return;
    }

    setIsPreviewing(true);
    try {
      const res = await previewScanInputs({ inputs: rawInputs });
      setPreviewResult(res);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(new ApiError(500, { code: 'PREVIEW_ERROR', message: 'Failed to review website inputs.' }));
      }
    } finally {
      setIsPreviewing(false);
    }
  };

  const handleCreateAndQueue = async () => {
    if (!previewResult || isSubmitting) return;
    setError(null);
    setIsSubmitting(true);

    const rawInputs = inputText
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0);

    const payloadString = JSON.stringify({ name: jobName || null, inputs: rawInputs });
    let currentJob = savedDraftJob;

    try {
      if (!currentJob) {
        const idempotencyKey = getOrGenerateIdempotencyKey(payloadString);
        currentJob = await createScanJob(
          {
            name: jobName.trim() || undefined,
            inputs: rawInputs,
            source_type: 'MANUAL',
          },
          idempotencyKey
        );
        setSavedDraftJob(currentJob);
      }

      await queueScanJob(currentJob.id);
      router.push(`/scans/${currentJob.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(
          new ApiError(500, {
            code: 'CREATE_QUEUE_ERROR',
            message: 'Your scan was saved but has not started. Try starting it again.',
          })
        );
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRetryQueueOnly = async () => {
    if (!savedDraftJob || isSubmitting) return;
    setError(null);
    setIsSubmitting(true);

    try {
      await queueScanJob(savedDraftJob.id);
      router.push(`/scans/${savedDraftJob.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(
          new ApiError(500, {
            code: 'QUEUE_RETRY_ERROR',
            message: 'Your scan was saved but has not started. Try starting it again.',
          })
        );
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ProtectedRoute>
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        {/* Navigation Header */}
        <div className="flex items-center space-x-3">
          <Link
            href="/dashboard"
            className="p-2 text-slate-500 hover:text-slate-900 rounded-lg hover:bg-slate-100 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Start a New Scan</h1>
            <p className="text-sm text-slate-600 mt-0.5">
              Paste website addresses to review inputs and discover published business contacts.
            </p>
          </div>
        </div>

        {/* Global Error Banner */}
        {error && (
          <div className="bg-rose-50 border border-rose-200 text-rose-800 p-4 rounded-xl text-sm space-y-1">
            <div className="flex items-center space-x-2 font-semibold">
              <AlertCircle className="w-5 h-5 text-rose-600 shrink-0" />
              <span>{error.message}</span>
            </div>
            {error.requestId && (
              <p className="text-xs font-mono text-rose-600 pl-7">Request ID: {error.requestId}</p>
            )}
          </div>
        )}

        {/* Partial Queue Failure Banner */}
        {savedDraftJob && error && (
          <div className="bg-amber-50 border border-amber-200 text-amber-900 p-4 rounded-xl text-sm space-y-3">
            <div>
              <p className="font-semibold">Your scan was saved but has not started. Try starting it again.</p>
            </div>
            <div className="flex items-center space-x-3">
              <button
                type="button"
                onClick={handleRetryQueueOnly}
                disabled={isSubmitting}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-xs rounded-lg shadow-sm disabled:opacity-50 transition-colors"
              >
                {isSubmitting ? 'Starting...' : 'Start Scan Again'}
              </button>
              <Link
                href={`/scans/${savedDraftJob.id}`}
                className="text-xs font-semibold text-amber-900 hover:underline"
              >
                View Scan Details →
              </Link>
            </div>
          </div>
        )}

        {/* Form Container */}
        <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-5">
          <div>
            <div className="flex justify-between items-center mb-1">
              <label htmlFor="jobName" className="block text-xs font-semibold uppercase tracking-wider text-slate-700">
                Scan Name
              </label>
              <div className="flex items-center space-x-2 text-[11px] text-slate-500">
                <span>Try examples:</span>
                {['Supplier Websites', 'Local Contractors', 'Public Directory'].map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    onClick={() => setJobName(ex)}
                    className="hover:text-indigo-600 underline"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
            <input
              id="jobName"
              type="text"
              value={jobName}
              onChange={(e) => setJobName(e.target.value)}
              placeholder="e.g., Supplier Websites"
              className="w-full px-3.5 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          <div>
            <div className="flex justify-between items-center mb-1">
              <label htmlFor="inputText" className="block text-xs font-semibold uppercase tracking-wider text-slate-700">
                Paste website addresses, one per line *
              </label>
              <button
                type="button"
                onClick={handleSampleClick}
                className="text-xs font-medium text-indigo-600 hover:underline"
              >
                Load sample inputs
              </button>
            </div>
            <textarea
              id="inputText"
              rows={8}
              value={inputText}
              onChange={(e) => {
                setInputText(e.target.value);
                setPreviewResult(null);
                setSavedDraftJob(null);
              }}
              placeholder={`https://example.com/about\nhttps://acme-corp.com/contact\nexample.org/team`}
              className="w-full px-3.5 py-2.5 border border-slate-300 rounded-lg text-sm font-mono text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <p className="text-xs text-slate-500 mt-1">
              Valid addresses are normalized before starting. Scans strictly check public, permitted web pages.
            </p>
          </div>

          <div className="flex items-center justify-between pt-2">
            <button
              type="button"
              onClick={() => handlePreview()}
              disabled={isPreviewing || isSubmitting || !inputText.trim()}
              className="inline-flex items-center px-4 py-2 bg-slate-800 hover:bg-slate-900 text-white font-semibold text-sm rounded-lg shadow-sm disabled:opacity-50 transition-colors"
            >
              <RefreshCw className={`w-4 h-4 mr-2 ${isPreviewing ? 'animate-spin' : ''}`} />
              {isPreviewing ? 'Reviewing Websites...' : 'Review Websites'}
            </button>

            {previewResult && !savedDraftJob && (
              <button
                type="button"
                onClick={handleCreateAndQueue}
                disabled={isSubmitting || previewResult.valid_input_count === 0}
                className="inline-flex items-center px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-sm rounded-lg shadow-sm disabled:opacity-50 transition-colors"
              >
                <Send className="w-4 h-4 mr-2" />
                {isSubmitting ? 'Starting...' : 'Save and Start Scan'}
              </button>
            )}
          </div>
        </div>

        {/* Website Review Breakdown */}
        {previewResult && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
              <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Total Websites</p>
                <p className="text-2xl font-bold text-slate-900 mt-1">{previewResult.total_input_count}</p>
              </div>

              <div className="bg-white p-4 rounded-xl border border-emerald-200 bg-emerald-50/30 shadow-sm">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold text-emerald-800 uppercase tracking-wider">Valid Websites</p>
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                </div>
                <p className="text-2xl font-bold text-emerald-900 mt-1">{previewResult.valid_input_count}</p>
              </div>

              <div className="bg-white p-4 rounded-xl border border-amber-200 bg-amber-50/30 shadow-sm">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold text-amber-800 uppercase tracking-wider">Duplicates Removed</p>
                  <AlertTriangle className="w-4 h-4 text-amber-600" />
                </div>
                <p className="text-2xl font-bold text-amber-900 mt-1">{previewResult.duplicate_input_count}</p>
              </div>

              <div className="bg-white p-4 rounded-xl border border-rose-200 bg-rose-50/30 shadow-sm">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold text-rose-800 uppercase tracking-wider">Invalid Addresses</p>
                  <XCircle className="w-4 h-4 text-rose-600" />
                </div>
                <p className="text-2xl font-bold text-rose-900 mt-1">{previewResult.invalid_input_count}</p>
              </div>
            </div>

            {/* Invalid Table */}
            {previewResult.invalid_input_count > 0 && (
              <div className="bg-white rounded-xl border border-rose-200 shadow-sm overflow-hidden space-y-3">
                <div className="bg-rose-50 px-6 py-3 border-b border-rose-200">
                  <h3 className="text-xs font-bold text-rose-900 uppercase tracking-wider">
                    Invalid Website Addresses ({previewResult.invalid_input_count})
                  </h3>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-600 uppercase tracking-wider">
                        <th className="px-6 py-2.5">#</th>
                        <th className="px-6 py-2.5">Original Input</th>
                        <th className="px-6 py-2.5">Error Reason</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-200 text-xs">
                      {previewResult.previews
                        .filter((item) => item.classification === 'INVALID')
                        .map((item) => (
                          <tr key={item.original_index} className="hover:bg-slate-50">
                            <td className="px-6 py-3 font-mono text-slate-500">{item.original_index + 1}</td>
                            <td className="px-6 py-3 font-mono text-slate-900 whitespace-pre-wrap break-all">
                              {item.original_input}
                            </td>
                            <td className="px-6 py-3 text-rose-700 font-medium">
                              {item.error_message || item.error_code || 'Invalid URL syntax'}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </ProtectedRoute>
  );
}
