'use client';

import React, { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { useAuth } from '@/context/auth-context';
import { X, ArrowRight, ArrowLeft, CheckCircle2, HelpCircle, Layers, Mail, Search, FileText, Download, ShieldAlert } from 'lucide-react';

const STORAGE_KEY = 'email-discovery:onboarding-complete:v1';

export const OnboardingTutorialModal: React.FC = () => {
  const { user, status } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);

  const modalRef = useRef<HTMLDivElement | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  const previouslyFocusedElementRef = useRef<HTMLElement | null>(null);

  const steps = [
    {
      title: 'Welcome to Email Discovery SaaS',
      icon: <Layers className="w-6 h-6 text-indigo-600" />,
      content: (
        <div className="space-y-2 text-xs text-slate-700 leading-relaxed">
          <p>
            Email Discovery helps business users find publicly listed business contact emails across supplier, directory, or contractor web pages—with source evidence you can verify.
          </p>
          <div className="bg-indigo-50 p-3 rounded-xl border border-indigo-100 text-indigo-900">
            <strong>Personal Workspace:</strong> Your workspace is automatically created and isolated so your scans and findings remain private to your account.
          </div>
        </div>
      ),
    },
    {
      title: 'Step 1: Start a New Scan',
      icon: <FileText className="w-6 h-6 text-indigo-600" />,
      content: (
        <div className="space-y-2 text-xs text-slate-700 leading-relaxed">
          <p>
            Click <strong>Start New Scan</strong> from your dashboard. Give your scan a name (such as <em>Supplier Websites</em>) and paste public web page addresses, one per line.
          </p>
          <p className="text-slate-500">
            You can use example buttons or load sample inputs to quickly test the scan creation flow.
          </p>
        </div>
      ),
    },
    {
      title: 'Step 2: Review Website Inputs',
      icon: <Search className="w-6 h-6 text-indigo-600" />,
      content: (
        <div className="space-y-2 text-xs text-slate-700 leading-relaxed">
          <p>
            Click <strong>Review Websites</strong> before running a scan. The system automatically normalizes URLs, removes duplicates, and flags syntax errors.
          </p>
          <p className="text-slate-500">
            Once reviewed, click <strong>Save and Start Scan</strong> to queue your job safely.
          </p>
        </div>
      ),
    },
    {
      title: 'Step 3: Track Real-Time Progress',
      icon: <CheckCircle2 className="w-6 h-6 text-emerald-600" />,
      content: (
        <div className="space-y-2 text-xs text-slate-700 leading-relaxed">
          <p>
            Your scan progress is displayed in simple non-technical terms:
          </p>
          <ul className="list-disc pl-4 space-y-1 text-slate-600">
            <li><strong>Not Started</strong> — Saved draft scan.</li>
            <li><strong>Waiting</strong> — Queued in line to scan.</li>
            <li><strong>Scanning</strong> — Actively processing target web pages.</li>
            <li><strong>Completed</strong> — Scan finished discovering emails.</li>
          </ul>
        </div>
      ),
    },
    {
      title: 'Step 4: Understand Categories & Email Checks',
      icon: <Mail className="w-6 h-6 text-indigo-600" />,
      content: (
        <div className="space-y-2 text-xs text-slate-700 leading-relaxed">
          <p>
            Discovered emails are classified into plain language categories:
          </p>
          <div className="space-y-1">
            <p><strong>Named Contact:</strong> Personal business address (e.g. <code className="bg-slate-100 px-1 rounded">jane@example.com</code>).</p>
            <p><strong>Role Address:</strong> Department inbox (e.g. <code className="bg-slate-100 px-1 rounded">sales@</code>, <code className="bg-slate-100 px-1 rounded">info@</code>).</p>
            <p><strong>Format Accepted:</strong> Valid-looking email structure.</p>
          </div>
          <div className="bg-amber-50 p-2.5 rounded-lg border border-amber-200 text-amber-900 text-[11px]">
            <strong>Note:</strong> <em>Not Independently Verified</em> means found on a public web page without probing the mailbox.
          </div>
        </div>
      ),
    },
    {
      title: 'Step 5: View Sources & Export CSV',
      icon: <Download className="w-6 h-6 text-indigo-600" />,
      content: (
        <div className="space-y-2 text-xs text-slate-700 leading-relaxed">
          <p>
            Click <strong>View Sources</strong> on any email row to see the exact web page URL and surrounding text snippet where the email was found.
          </p>
          <p>
            When your scan completes, click <strong>Export Findings (CSV)</strong> to download your clean, structured spreadsheet.
          </p>
          <div className="pt-2 flex items-center justify-between text-indigo-600 font-semibold">
            <Link href="/help" onClick={() => markComplete()} className="hover:underline">
              Read complete Help Guide →
            </Link>
          </div>
        </div>
      ),
    },
  ];

  const markComplete = () => {
    try {
      localStorage.setItem(STORAGE_KEY, 'true');
    } catch {
      // Storage unavailable fallback
    }
    setIsOpen(false);
    // Focus restoration
    if (previouslyFocusedElementRef.current && typeof previouslyFocusedElementRef.current.focus === 'function') {
      previouslyFocusedElementRef.current.focus();
    }
  };

  useEffect(() => {
    try {
      const completed = localStorage.getItem(STORAGE_KEY);
      if (!completed) {
        if (typeof document !== 'undefined' && document.activeElement) {
          previouslyFocusedElementRef.current = document.activeElement as HTMLElement;
        }
        setIsOpen(true);
      }
    } catch {
      setIsOpen(true);
    }

    const handleReopen = () => {
      if (typeof document !== 'undefined' && document.activeElement) {
        previouslyFocusedElementRef.current = document.activeElement as HTMLElement;
      }
      setCurrentStep(0);
      setIsOpen(true);
    };

    window.addEventListener('reopen-tutorial', handleReopen);
    return () => {
      window.removeEventListener('reopen-tutorial', handleReopen);
    };
  }, []);

  // Real Focus Trap & Escape Key Listener
  useEffect(() => {
    if (!isOpen) return;

    // Focus close button initially or on step change
    const timer = setTimeout(() => {
      closeBtnRef.current?.focus();
    }, 50);

    const getFocusableElements = (): HTMLElement[] => {
      if (!modalRef.current) return [];
      const selectors = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
      return Array.from(modalRef.current.querySelectorAll<HTMLElement>(selectors)).filter(
        (el) => el.getAttribute('aria-hidden') !== 'true' && el.style.display !== 'none' && el.style.visibility !== 'hidden'
      );
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        markComplete();
        return;
      }

      if (e.key === 'Tab') {
        const focusables = getFocusableElements();
        if (focusables.length === 0) {
          e.preventDefault();
          return;
        }

        const firstEl = focusables[0];
        const lastEl = focusables[focusables.length - 1];

        if (e.shiftKey) {
          // Shift + Tab: Wrap from first to last
          if (document.activeElement === firstEl || !modalRef.current?.contains(document.activeElement)) {
            e.preventDefault();
            lastEl.focus();
          }
        } else {
          // Tab: Wrap from last to first
          if (document.activeElement === lastEl || !modalRef.current?.contains(document.activeElement)) {
            e.preventDefault();
            firstEl.focus();
          }
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, currentStep]);

  if (!isOpen || status !== 'authenticated' || !user) return null;

  const isFirstStep = currentStep === 0;
  const isLastStep = currentStep === steps.length - 1;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="tutorial-modal-title"
      className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in duration-200"
    >
      <div
        ref={modalRef}
        className="bg-white rounded-2xl max-w-lg w-full p-6 shadow-2xl space-y-5 border border-slate-200"
      >
        {/* Header */}
        <div className="flex items-start justify-between border-b border-slate-100 pb-4">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-indigo-50 rounded-xl">
              {steps[currentStep].icon}
            </div>
            <div>
              <span className="text-[11px] font-bold uppercase tracking-wider text-indigo-600">
                Step {currentStep + 1} of {steps.length}
              </span>
              <h3 id="tutorial-modal-title" className="text-base font-bold text-slate-900">
                {steps[currentStep].title}
              </h3>
            </div>
          </div>
          <button
            ref={closeBtnRef}
            onClick={markComplete}
            aria-label="Close onboarding tutorial"
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-lg transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Step Body */}
        <div className="py-2 min-h-[140px]">
          {steps[currentStep].content}
        </div>

        {/* Mailbox Verification Disclaimer Note */}
        <div className="bg-amber-50 p-3 rounded-xl border border-amber-200 text-[11px] text-amber-900 flex items-start space-x-2">
          <ShieldAlert className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
          <p>
            Email Discovery checks formatting and records public web page locations. It does not send emails or probe mailboxes.
          </p>
        </div>

        {/* Footer Navigation */}
        <div className="flex items-center justify-between pt-2 border-t border-slate-100">
          <button
            type="button"
            onClick={markComplete}
            className="text-xs font-semibold text-slate-500 hover:text-slate-800 transition-colors"
          >
            Skip for Now
          </button>

          <div className="flex items-center space-x-2">
            {!isFirstStep && (
              <button
                type="button"
                onClick={() => setCurrentStep((prev) => prev - 1)}
                className="px-3.5 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold text-xs rounded-lg transition-colors flex items-center"
              >
                <ArrowLeft className="w-3.5 h-3.5 mr-1" />
                Previous
              </button>
            )}

            {!isLastStep ? (
              <button
                type="button"
                onClick={() => setCurrentStep((prev) => prev + 1)}
                className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-xs rounded-lg shadow-sm transition-colors flex items-center"
              >
                Next Step
                <ArrowRight className="w-3.5 h-3.5 ml-1" />
              </button>
            ) : (
              <button
                type="button"
                onClick={markComplete}
                className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-xs rounded-lg shadow-sm transition-colors flex items-center"
              >
                Complete Tutorial
                <CheckCircle2 className="w-3.5 h-3.5 ml-1" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
