'use client';

import React from 'react';
import Link from 'next/link';
import { useAuth } from '@/context/auth-context';

export const Navbar: React.FC = () => {
  const { user, organization, status, logout, logoutAll } = useAuth();

  return (
    <header className="bg-white border-b border-slate-200 sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16 items-center">
          <div className="flex items-center space-x-3">
            <Link href="/" className="text-xl font-bold text-slate-900 tracking-tight flex items-center space-x-2">
              <span className="bg-brand-600 text-white p-1.5 rounded-lg text-sm font-semibold">ED</span>
              <span>Email Discovery</span>
            </Link>
          </div>

          <nav className="flex items-center space-x-4">
            {status === 'authenticated' && user && organization ? (
              <>
                <Link
                  href="/dashboard"
                  className="text-sm font-medium text-slate-700 hover:text-brand-600 transition-colors"
                >
                  Dashboard
                </Link>
                <div className="h-4 w-px bg-slate-200" />
                <div className="flex items-center space-x-3">
                  <div className="text-right">
                    <p className="text-sm font-semibold text-slate-900 leading-tight">
                      {user.display_name || user.email}
                    </p>
                    <div className="flex items-center space-x-1.5 justify-end">
                      <span className="text-xs text-slate-500">{organization.name}</span>
                      <span className="px-1.5 py-0.5 text-[10px] font-semibold bg-brand-50 text-brand-700 rounded border border-brand-200">
                        {organization.role}
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={() => logout()}
                    className="px-3 py-1.5 text-xs font-medium text-slate-700 hover:text-slate-900 bg-slate-100 hover:bg-slate-200 rounded-md transition-colors"
                  >
                    Logout
                  </button>
                  <button
                    onClick={() => logoutAll()}
                    className="px-3 py-1.5 text-xs font-medium text-rose-700 hover:text-rose-900 bg-rose-50 hover:bg-rose-100 rounded-md transition-colors"
                    title="Revoke sessions on all devices"
                  >
                    Logout All
                  </button>
                </div>
              </>
            ) : (
              <>
                <Link
                  href="/login"
                  className="text-sm font-medium text-slate-700 hover:text-brand-600 transition-colors"
                >
                  Log in
                </Link>
                <Link
                  href="/register"
                  className="px-4 py-2 text-sm font-medium text-white bg-brand-600 hover:bg-brand-700 rounded-md shadow-sm transition-colors"
                >
                  Register
                </Link>
              </>
            )}
          </nav>
        </div>
      </div>
    </header>
  );
};
