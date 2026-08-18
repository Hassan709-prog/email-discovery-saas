'use client';

import React, { useEffect, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { sanitizeReturnPath } from './ProtectedRoute';

function GuestRouteContent({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    if (status === 'authenticated') {
      const returnTo = searchParams.get('returnTo');
      const target = sanitizeReturnPath(returnTo);
      router.replace(target);
    }
  }, [status, searchParams, router]);

  if (status === 'loading' || status === 'idle') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="flex flex-col items-center space-y-3">
          <div className="w-8 h-8 border-4 border-brand-600 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm font-medium text-slate-600">Verifying authentication...</p>
        </div>
      </div>
    );
  }

  if (status === 'authenticated') {
    return null;
  }

  return <>{children}</>;
}

export const GuestRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-slate-50">
          <div className="flex flex-col items-center space-y-3">
            <div className="w-8 h-8 border-4 border-brand-600 border-t-transparent rounded-full animate-spin" />
            <p className="text-sm font-medium text-slate-600">Loading...</p>
          </div>
        </div>
      }
    >
      <GuestRouteContent>{children}</GuestRouteContent>
    </Suspense>
  );
};
