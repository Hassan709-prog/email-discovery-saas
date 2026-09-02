'use client';

import React, { useEffect } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { useAuth } from '@/context/auth-context';

export function sanitizeReturnPath(path: string | null): string {
  if (!path) return '/dashboard';
  // Reject open redirects (external URLs or //)
  if (path.startsWith('/') && !path.startsWith('//') && !path.includes('\\')) {
    return path;
  }
  return '/dashboard';
}

export const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { status, retrySession } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status === 'unauthenticated') {
      const returnTo = encodeURIComponent(pathname);
      router.replace(`/login?returnTo=${returnTo}`);
    }
  }, [status, pathname, router]);

  if (status === 'loading' || status === 'idle') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="flex flex-col items-center space-y-3">
          <div className="w-8 h-8 border-4 border-brand-600 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm font-medium text-slate-600">Loading session...</p>
        </div>
      </div>
    );
  }

  if (status === 'restoration_failed') {
    const returnTo = encodeURIComponent(sanitizeReturnPath(pathname));
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 p-4">
        <div className="flex flex-col items-center space-y-4 max-w-md text-center bg-white p-6 rounded-lg shadow-sm border border-slate-200">
          <div className="w-12 h-12 bg-amber-50 rounded-full flex items-center justify-center text-amber-600">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <h3 className="text-lg font-semibold text-slate-900">Session Restoration Failed</h3>
          <p className="text-sm text-slate-600">
            Unable to verify your session due to a temporary network issue. Please try again.
          </p>
          <div className="flex items-center space-x-3">
            <button
              onClick={() => retrySession()}
              className="px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white font-medium text-sm rounded-md transition-colors"
            >
              Retry Session
            </button>
            <button
              onClick={() => router.replace(`/login?returnTo=${returnTo}`)}
              className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 font-medium text-sm rounded-md transition-colors"
            >
              Sign in again
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (status === 'unauthenticated') {
    return null;
  }

  return <>{children}</>;
};
