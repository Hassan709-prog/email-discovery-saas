'use client';

import React from 'react';
import Link from 'next/link';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import { ArrowLeft, HelpCircle, ShieldAlert, Tag, CheckCircle2, PlayCircle, FileText, Search, Download, Layers } from 'lucide-react';

export default function HelpPage() {
  const triggerReopenTutorial = () => {
    try {
      localStorage.removeItem('email-discovery:onboarding-complete:v1');
      window.dispatchEvent(new Event('reopen-tutorial'));
    } catch {
      // localStorage disabled fallback
    }
  };

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-slate-50 flex flex-col font-sans text-slate-900">
        <main className="flex-1 max-w-4xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-10 space-y-8">
          {/* Header navigation */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div className="flex items-center space-x-3">
              <Link
                href="/dashboard"
                className="p-2 text-slate-500 hover:text-slate-900 rounded-lg hover:bg-slate-200/60 transition-colors"
              >
                <ArrowLeft className="w-5 h-5" />
              </Link>
              <div>
                <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-slate-900">
                  Help & Complete User Guide
                </h1>
                <p className="text-sm text-slate-600 mt-1">
                  Non-technical guide covering sign-in, website scanning, results, and CSV export.
                </p>
              </div>
            </div>

            <button
              onClick={triggerReopenTutorial}
              className="inline-flex items-center px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white font-semibold text-xs rounded-lg shadow-sm transition-colors"
            >
              <PlayCircle className="w-4 h-4 mr-1.5" />
              Reopen Interactive Tutorial
            </button>
          </div>

          {/* Mailbox Verification Disclaimer Callout */}
          <div className="bg-amber-50 border-l-4 border-amber-500 p-5 rounded-r-xl space-y-2 shadow-xs">
            <div className="flex items-center space-x-2 font-bold text-amber-900 text-sm">
              <ShieldAlert className="w-5 h-5 text-amber-600 shrink-0" />
              <span>Important Mailbox Disclosure</span>
            </div>
            <p className="text-xs text-amber-900 leading-relaxed">
              Email Discovery checks email formatting and records where an address was publicly found. It does not send messages, probe mailboxes, perform SMTP checks, or guarantee deliverability.
            </p>
          </div>

          {/* Section 1: Complete End-to-End Workflow Guide */}
          <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-6">
            <div className="flex items-center space-x-3 border-b border-slate-100 pb-4">
              <div className="p-2 bg-indigo-50 text-indigo-600 rounded-lg">
                <Layers className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-slate-900">Complete Workflow Guide</h2>
                <p className="text-xs text-slate-500">
                  From creating your account to exporting verified business emails.
                </p>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1.5">
                <div className="flex items-center space-x-2 font-bold text-slate-900 text-sm">
                  <span className="w-5 h-5 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs">1</span>
                  <span>Sign-In & Workspace</span>
                </div>
                <p className="text-slate-600">
                  Log in securely with your credentials. Your personal workspace is automatically set up for tenant-isolated email discovery.
                </p>
              </div>

              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1.5">
                <div className="flex items-center space-x-2 font-bold text-slate-900 text-sm">
                  <span className="w-5 h-5 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs">2</span>
                  <span>Paste Target Websites</span>
                </div>
                <p className="text-slate-600">
                  Go to <strong>Start New Scan</strong>, give your scan a name, and paste public website addresses (one per line). Click <strong>Review Websites</strong>.
                </p>
              </div>

              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1.5">
                <div className="flex items-center space-x-2 font-bold text-slate-900 text-sm">
                  <span className="w-5 h-5 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs">3</span>
                  <span>Syntax Review & Normalization</span>
                </div>
                <p className="text-slate-600">
                  The system reviews valid addresses, removes duplicates, and flags invalid syntax errors. Click <strong>Save and Start Scan</strong> to begin.
                </p>
              </div>

              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1.5">
                <div className="flex items-center space-x-2 font-bold text-slate-900 text-sm">
                  <span className="w-5 h-5 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs">4</span>
                  <span>Track Real-Time Progress</span>
                </div>
                <p className="text-slate-600">
                  Monitor progress bars on your scan details page. Statuses show <em>Not Started</em>, <em>Waiting</em>, <em>Scanning</em>, or <em>Completed</em>.
                </p>
              </div>

              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1.5">
                <div className="flex items-center space-x-2 font-bold text-slate-900 text-sm">
                  <span className="w-5 h-5 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs">5</span>
                  <span>Review Findings & Evidence</span>
                </div>
                <p className="text-slate-600">
                  Filter findings by category or email check status. Click <strong>View Sources</strong> to inspect the exact web page URL and text context where an email was found.
                </p>
              </div>

              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1.5">
                <div className="flex items-center space-x-2 font-bold text-slate-900 text-sm">
                  <span className="w-5 h-5 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs">6</span>
                  <span>Export Clean CSV</span>
                </div>
                <p className="text-slate-600">
                  Download a complete CSV export containing canonical emails, domains, categories, review statuses, and source evidence counts.
                </p>
              </div>
            </div>
          </div>

          {/* Section 2: Findings by Category */}
          <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-6">
            <div className="flex items-center space-x-3 border-b border-slate-100 pb-4">
              <div className="p-2 bg-indigo-50 text-indigo-600 rounded-lg">
                <Tag className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-slate-900">Findings by Category</h2>
                <p className="text-xs text-slate-500">
                  Understanding how discovered email addresses are classified.
                </p>
              </div>
            </div>

            <p className="text-xs text-slate-600 leading-relaxed">
              The category chart shows the breakdown of discovered emails based on their name structure and standard prefix patterns. In your dashboard, the number represents the total count of emails in that category, and the percentage shows its share of all findings.
            </p>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1">
                <span className="font-bold text-indigo-700 text-sm">Named Contact</span>
                <p className="text-slate-700">
                  An email that appears connected to a person, such as <code className="bg-slate-200 px-1 py-0.5 rounded text-[11px]">jane.smith@example.com</code>.
                </p>
              </div>

              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1">
                <span className="font-bold text-blue-700 text-sm">Role Address</span>
                <p className="text-slate-700">
                  A general business inbox for a department or purpose, such as <code className="bg-slate-200 px-1 py-0.5 rounded text-[11px]">sales@</code>, <code className="bg-slate-200 px-1 py-0.5 rounded text-[11px]">info@</code>, or <code className="bg-slate-200 px-1 py-0.5 rounded text-[11px]">support@</code>.
                </p>
              </div>

              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1">
                <span className="font-bold text-slate-700 text-sm">No-Reply Address</span>
                <p className="text-slate-700">
                  An automated address, such as <code className="bg-slate-200 px-1 py-0.5 rounded text-[11px]">noreply@example.com</code>, that usually should not receive replies.
                </p>
              </div>

              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1">
                <span className="font-bold text-slate-600 text-sm">Other</span>
                <p className="text-slate-700">
                  The system found an email but could not confidently place it in the categories above.
                </p>
              </div>
            </div>
          </div>

          {/* Section 3: Findings by Review Status */}
          <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-6">
            <div className="flex items-center space-x-3 border-b border-slate-100 pb-4">
              <div className="p-2 bg-emerald-50 text-emerald-600 rounded-lg">
                <CheckCircle2 className="w-5 h-5" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-slate-900">Findings by Review Status</h2>
                <p className="text-xs text-slate-500">
                  How format checks and web page evidence are evaluated.
                </p>
              </div>
            </div>

            <div className="space-y-4 text-xs">
              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1">
                <span className="font-bold text-emerald-800 text-sm">Format Accepted</span>
                <p className="text-slate-700">
                  The email has a valid-looking written format. This does not prove that the mailbox exists or receives messages.
                </p>
              </div>

              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1">
                <span className="font-bold text-amber-800 text-sm">Not Independently Verified</span>
                <p className="text-slate-700">
                  The email was found on a public web page, but the system did not contact, ping, or test the mailbox.
                </p>
              </div>

              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-1">
                <span className="font-bold text-rose-800 text-sm">Rejected Format</span>
                <p className="text-slate-700">
                  The discovered text looked like an email but failed the system’s formatting or safety checks, so it was not accepted as a usable finding.
                </p>
              </div>
            </div>
          </div>
        </main>
      </div>
    </ProtectedRoute>
  );
}
