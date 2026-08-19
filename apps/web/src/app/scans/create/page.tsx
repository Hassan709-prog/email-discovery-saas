'use client';

import React, { useState, useRef } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import {
  ApiError,
  PreviewScanInputsApiResponse,
  ScanInputPreviewItemApiResponse,
  ScanJobApiResponse,
} from '@/types/api';
import { createScanJob, previewScanInputs, queueScanJob } from '@/lib/api-client';
import {
  ArrowLeft,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Send,
  RefreshCw,
  AlertCircle,
  RotateCcw,
  ShieldAlert,
  HelpCircle,
  ExternalLink,
  PlusCircle,
  MinusCircle,
} from 'lucide-react';

const SAMPLE_INPUTS = [
  'https://grandeurhillsgroup.com/',
  'https://www.google.com/search?q=home+builders+in+new+york',
  'https://www.archi-builders.com/',
  'https://archi-builders.com/',
  'https://taconicbuilders.com/',
  'https://nybuilt.com/',
  'https://desimonebuilders.com/',
  'https://www.myhomeus.com/?utm_source=gmb&utm_medium=search',
  'https://www.google.com/maps/dir//Example',
  'https://www.google.com/aclk?gclid=example',
  'https://accounts.google.com/SignOutOptions',
  'https://support.google.com/websearch/answer/181196',
  'https://policies.google.com/privacy',
  'https://policies.google.com/terms',
  'http://93.184.216.34/',
  'not-a-valid-url-format',
].join('\n');

export default function CreateScanPage() {
  const router = useRouter();

  const [jobName, setJobName] = useState('');
  const [inputText, setInputText] = useState('');
  const [overrides, setOverrides] = useState<Record<number, boolean>>({});

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
    setJobName('New York Builders & Contractors');
    setInputText(SAMPLE_INPUTS);
    setOverrides({});
    setPreviewResult(null);
    setSavedDraftJob(null);
  };

  const handlePreview = async (
    e?: React.FormEvent,
    overrideMap: Record<number, boolean> = overrides
  ) => {
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
      const res = await previewScanInputs({
        inputs: rawInputs,
        overrides: Object.keys(overrideMap).length > 0 ? overrideMap : undefined,
      });
      setPreviewResult(res);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(
          new ApiError(500, {
            code: 'PREVIEW_ERROR',
            message: 'Failed to review website inputs. Check URL formatting and try again.',
          })
        );
      }
    } finally {
      setIsPreviewing(false);
    }
  };

  const handleToggleOverride = (index: number, currentSelected: boolean) => {
    const nextOverrides = {
      ...overrides,
      [index]: !currentSelected,
    };
    setOverrides(nextOverrides);
    handlePreview(undefined, nextOverrides);
  };

  const handleRestoreDefaults = () => {
    setOverrides({});
    handlePreview(undefined, {});
  };

  const handleCreateAndQueue = async () => {
    if (!previewResult || isSubmitting) return;
    setError(null);
    setIsSubmitting(true);

    const rawInputs = inputText
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0);

    const payloadString = JSON.stringify({
      name: jobName || null,
      inputs: rawInputs,
      overrides,
    });
    let currentJob = savedDraftJob;

    try {
      if (!currentJob) {
        const idempotencyKey = getOrGenerateIdempotencyKey(payloadString);
        currentJob = await createScanJob(
          {
            name: jobName.trim() || undefined,
            inputs: rawInputs,
            overrides: Object.keys(overrides).length > 0 ? overrides : undefined,
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
      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
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
              Paste website addresses to clean, review, and discover published business contacts.
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
              <p className="font-semibold">
                Your scan was saved but has not started. Try starting it again.
              </p>
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
              <label
                htmlFor="jobName"
                className="block text-xs font-semibold uppercase tracking-wider text-slate-700"
              >
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
              <label
                htmlFor="inputText"
                className="block text-xs font-semibold uppercase tracking-wider text-slate-700"
              >
                Paste website addresses, one per line *
              </label>
              <button
                type="button"
                onClick={handleSampleClick}
                className="text-xs font-medium text-indigo-600 hover:underline"
              >
                Load noisy sample inputs
              </button>
            </div>
            <textarea
              id="inputText"
              rows={8}
              value={inputText}
              onChange={(e) => {
                setInputText(e.target.value);
                setOverrides({});
                setPreviewResult(null);
                setSavedDraftJob(null);
              }}
              placeholder={`https://example.com/about\nhttps://acme-corp.com/contact\nexample.org/team`}
              className="w-full px-3.5 py-2.5 border border-slate-300 rounded-lg text-sm font-mono text-slate-900 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <p className="text-xs text-slate-500 mt-1">
              Inputs are automatically cleaned and checked before scanning. Unrelated platform links, duplicates, and tracking parameters are processed offline.
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
              <div className="flex items-center space-x-3">
                {Object.keys(overrides).length > 0 && (
                  <button
                    type="button"
                    onClick={handleRestoreDefaults}
                    className="inline-flex items-center text-xs font-semibold text-slate-600 hover:text-slate-900 px-3 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
                  >
                    <RotateCcw className="w-3.5 h-3.5 mr-1.5" />
                    Restore Default Decisions
                  </button>
                )}

                <button
                  type="button"
                  onClick={handleCreateAndQueue}
                  disabled={isSubmitting || previewResult.final_target_count === 0}
                  className="inline-flex items-center px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-sm rounded-lg shadow-sm disabled:opacity-50 transition-colors"
                >
                  <Send className="w-4 h-4 mr-2" />
                  {isSubmitting
                    ? 'Starting...'
                    : `Save and Start Scan (${previewResult.final_target_count} ${
                        previewResult.final_target_count === 1 ? 'Target' : 'Targets'
                      })`}
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Website Review Dashboard & Breakdown */}
        {previewResult && (
          <div className="space-y-6">
            {/* Metric Summary Grid */}
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
              <div className="bg-white p-3.5 rounded-xl border border-slate-200 shadow-sm">
                <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                  Total Raw
                </p>
                <p className="text-xl font-bold text-slate-900 mt-0.5">
                  {previewResult.total_input_count}
                </p>
              </div>

              <div className="bg-white p-3.5 rounded-xl border border-emerald-200 bg-emerald-50/20 shadow-sm">
                <p className="text-[11px] font-semibold text-emerald-800 uppercase tracking-wider">
                  Ready to Check
                </p>
                <p className="text-xl font-bold text-emerald-900 mt-0.5">
                  {previewResult.ready_to_check_count}
                </p>
              </div>

              <div className="bg-white p-3.5 rounded-xl border border-blue-200 bg-blue-50/20 shadow-sm">
                <p className="text-[11px] font-semibold text-blue-800 uppercase tracking-wider">
                  Needs Review
                </p>
                <p className="text-xl font-bold text-blue-900 mt-0.5">
                  {previewResult.needs_review_count}
                </p>
              </div>

              <div className="bg-white p-3.5 rounded-xl border border-purple-200 bg-purple-50/20 shadow-sm">
                <p className="text-[11px] font-semibold text-purple-800 uppercase tracking-wider">
                  Unrelated Excluded
                </p>
                <p className="text-xl font-bold text-purple-900 mt-0.5">
                  {previewResult.unrelated_platform_count}
                </p>
              </div>

              <div className="bg-white p-3.5 rounded-xl border border-amber-200 bg-amber-50/20 shadow-sm">
                <p className="text-[11px] font-semibold text-amber-800 uppercase tracking-wider">
                  Duplicates
                </p>
                <p className="text-xl font-bold text-amber-900 mt-0.5">
                  {previewResult.duplicate_input_count}
                </p>
              </div>

              <div className="bg-white p-3.5 rounded-xl border border-rose-200 bg-rose-50/20 shadow-sm">
                <p className="text-[11px] font-semibold text-rose-800 uppercase tracking-wider">
                  Invalid
                </p>
                <p className="text-xl font-bold text-rose-900 mt-0.5">
                  {previewResult.invalid_input_count}
                </p>
              </div>

              <div className="bg-indigo-900 p-3.5 rounded-xl text-white shadow-sm">
                <p className="text-[11px] font-semibold uppercase tracking-wider opacity-80">
                  Scan Targets
                </p>
                <p className="text-xl font-bold mt-0.5">{previewResult.final_target_count}</p>
              </div>
            </div>

            {/* Zero Scan Targets Warning */}
            {previewResult.final_target_count === 0 && (
              <div className="bg-rose-50 border border-rose-200 p-4 rounded-xl text-sm text-rose-900 flex items-center space-x-3">
                <ShieldAlert className="w-5 h-5 text-rose-600 shrink-0" />
                <div>
                  <p className="font-semibold">No eligible websites are ready to scan.</p>
                  <p className="text-xs text-rose-700 mt-0.5">
                    Review the excluded platform links and invalid inputs below. You may include platform URLs if intended using &quot;Include anyway&quot;.
                  </p>
                </div>
              </div>
            )}

            {/* Detailed Preview Items Table */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden space-y-3">
              <div className="bg-slate-50 px-6 py-3 border-b border-slate-200 flex justify-between items-center">
                <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider">
                  Input Review &amp; Target Consolidation Breakdown ({previewResult.previews.length} Items)
                </h3>
                <span className="text-xs text-slate-500 font-medium">
                  {previewResult.accepted_canonical_targets.length} Clean Scan Targets Selected
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-slate-50 border-b border-slate-200 text-[11px] font-semibold text-slate-600 uppercase tracking-wider">
                      <th className="px-4 py-2.5">#</th>
                      <th className="px-4 py-2.5">Original Input</th>
                      <th className="px-4 py-2.5">Decision Status</th>
                      <th className="px-4 py-2.5">Canonical Scan Target</th>
                      <th className="px-4 py-2.5">Reason &amp; Explanation</th>
                      <th className="px-4 py-2.5 text-right">Scan Inclusion</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200 text-xs">
                    {previewResult.previews.map((item) => {
                      return (
                        <tr
                          key={item.original_index}
                          className={`hover:bg-slate-50 ${
                            item.is_selected ? 'bg-emerald-50/10' : ''
                          }`}
                        >
                          <td className="px-4 py-3 font-mono text-slate-400">
                            {item.original_index + 1}
                          </td>
                          <td className="px-4 py-3 font-mono text-slate-900 break-all max-w-xs">
                            {item.original_input}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            <span
                              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-medium ${
                                item.ui_label === 'Ready to check'
                                  ? 'bg-emerald-100 text-emerald-800'
                                  : item.ui_label === 'Review recommended'
                                  ? 'bg-blue-100 text-blue-800'
                                  : item.ui_label === 'Duplicate website'
                                  ? 'bg-amber-100 text-amber-800'
                                  : item.ui_label === 'Unrelated platform link'
                                  ? 'bg-purple-100 text-purple-800'
                                  : 'bg-rose-100 text-rose-800'
                              }`}
                            >
                              {item.ui_label}
                            </span>
                          </td>
                          <td className="px-4 py-3 font-mono text-slate-700 break-all max-w-xs">
                            {item.canonical_target ? (
                              <span className="text-emerald-700 font-semibold">
                                {item.canonical_target}
                              </span>
                            ) : (
                              <span className="text-slate-400 font-normal italic">
                                None (Excluded)
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-slate-600 max-w-sm">
                            {item.explanation}
                          </td>
                          <td className="px-4 py-3 text-right whitespace-nowrap">
                            {item.user_override_permitted ? (
                              <button
                                type="button"
                                onClick={() =>
                                  handleToggleOverride(item.original_index, item.is_selected)
                                }
                                className={`inline-flex items-center px-3 py-1 rounded-lg text-xs font-semibold shadow-xs transition-colors ${
                                  item.is_selected
                                    ? 'bg-rose-100 text-rose-700 hover:bg-rose-200'
                                    : 'bg-indigo-50 text-indigo-700 hover:bg-indigo-100 border border-indigo-200'
                                }`}
                              >
                                {item.is_selected ? (
                                  <>
                                    <MinusCircle className="w-3.5 h-3.5 mr-1" />
                                    Unselect
                                  </>
                                ) : (
                                  <>
                                    <PlusCircle className="w-3.5 h-3.5 mr-1" />
                                    Include anyway
                                  </>
                                )}
                              </button>
                            ) : (
                              <span className="text-[11px] text-rose-500 font-medium italic">
                                Cannot Override
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </ProtectedRoute>
  );
}
