'use client';

import React from 'react';
import Link from 'next/link';
import { useAuth } from '@/context/auth-context';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';

export default function DashboardPage() {
  const { user, organization } = useAuth();

  return (
    <ProtectedRoute>
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-10 space-y-8">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Tenant Dashboard</h1>
          <p className="text-sm text-slate-600">
            Welcome back, {user?.display_name || user?.email}
          </p>
        </div>

        {/* Tenant Profile Overview Card */}
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm space-y-6">
          <div className="border-b border-slate-100 pb-4">
            <h2 className="text-lg font-semibold text-slate-900">Organization & User Profile</h2>
            <p className="text-xs text-slate-500">Authenticated workspace tenant details</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-3">
              <div>
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">User Email</span>
                <p className="text-sm font-medium text-slate-900">{user?.email}</p>
              </div>
              <div>
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Display Name</span>
                <p className="text-sm font-medium text-slate-900">{user?.display_name || 'N/A'}</p>
              </div>
              <div>
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">User Status</span>
                <p className="text-sm font-medium text-slate-900">{user?.status}</p>
              </div>
            </div>

            <div className="space-y-3">
              <div>
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Organization Name</span>
                <p className="text-sm font-medium text-slate-900">{organization?.name}</p>
              </div>
              <div>
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Organization Slug</span>
                <p className="text-sm font-medium text-slate-900">{organization?.slug}</p>
              </div>
              <div>
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Tenant Role</span>
                <div>
                  <span className="px-2 py-0.5 text-xs font-semibold bg-brand-50 text-brand-700 rounded border border-brand-200">
                    {organization?.role}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Phase 3B Workflow Placeholder */}
        <div className="bg-slate-100 p-6 rounded-xl border border-slate-200 border-dashed flex flex-col md:flex-row items-center justify-between gap-4">
          <div>
            <h3 className="text-base font-semibold text-slate-900">Scan Job Management</h3>
            <p className="text-xs text-slate-600 mt-0.5">
              Create scan jobs, monitor real-time progress, explore findings, and export CSV results.
            </p>
          </div>
          <Link
            href="/scans/create"
            className="px-4 py-2 text-sm font-semibold text-white bg-slate-400 cursor-not-allowed rounded-lg shadow-sm pointer-events-none"
            title="Scan Job creation workflow will be unlocked in Phase 3B"
          >
            Create Scan (Phase 3B)
          </Link>
        </div>
      </div>
    </ProtectedRoute>
  );
}
