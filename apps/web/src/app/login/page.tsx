'use client';

import React, { useState, Suspense } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '@/context/auth-context';
import { GuestRoute } from '@/components/auth/GuestRoute';
import { sanitizeReturnPath } from '@/components/auth/ProtectedRoute';
import { ApiError, OrganizationChoiceSchema } from '@/types/api';

function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(null);

  const [orgChoices, setOrgChoices] = useState<OrganizationChoiceSchema[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      const res = await login({
        email,
        password,
        organization_id: selectedOrgId || undefined,
      });

      if ('organization_selection_required' in res && res.organization_selection_required) {
        setOrgChoices(res.organizations);
        setIsSubmitting(false);
        return;
      }

      const returnTo = searchParams.get('returnTo');
      const target = sanitizeReturnPath(returnTo);
      router.replace(target);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(new ApiError(500, { code: 'CLIENT_ERROR', message: 'An unexpected error occurred.' }));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSelectOrganization = async (orgId: string) => {
    setSelectedOrgId(orgId);
    setError(null);
    setIsSubmitting(true);

    try {
      await login({
        email,
        password,
        organization_id: orgId,
      });
      const returnTo = searchParams.get('returnTo');
      const target = sanitizeReturnPath(returnTo);
      router.replace(target);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err);
      } else {
        setError(new ApiError(500, { code: 'CLIENT_ERROR', message: 'An unexpected error occurred.' }));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="max-w-md w-full space-y-8 bg-white p-8 rounded-xl border border-slate-200 shadow-sm">
      <div>
        <h2 className="text-center text-2xl font-bold text-slate-900">Sign in to your account</h2>
        <p className="mt-2 text-center text-sm text-slate-600">
          Access your Email Discovery tenant dashboard
        </p>
      </div>

      {error && (
        <div className="bg-rose-50 border border-rose-200 text-rose-800 px-4 py-3 rounded-lg text-sm space-y-1">
          <p className="font-semibold">{error.message}</p>
          {error.requestId && (
            <p className="text-xs text-rose-600 font-mono">Request ID: {error.requestId}</p>
          )}
        </div>
      )}

      {orgChoices ? (
        <div className="space-y-4">
          <div className="bg-amber-50 border border-amber-200 text-amber-900 p-4 rounded-lg text-sm">
            <p className="font-semibold">Multiple Organizations Found</p>
            <p className="text-xs text-amber-700 mt-0.5">Please select the organization tenant you wish to sign into:</p>
          </div>

          <div className="space-y-2">
            {orgChoices.map((org) => (
              <button
                key={org.id}
                onClick={() => handleSelectOrganization(org.id)}
                disabled={isSubmitting}
                className="w-full text-left p-3.5 border border-slate-200 hover:border-brand-500 rounded-lg hover:bg-slate-50 transition-colors flex items-center justify-between"
              >
                <div>
                  <p className="font-semibold text-sm text-slate-900">{org.name}</p>
                  <p className="text-xs text-slate-500">slug: {org.slug}</p>
                </div>
                <span className="px-2 py-0.5 text-xs font-semibold bg-slate-100 text-slate-700 rounded border border-slate-200">
                  {org.role}
                </span>
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={() => setOrgChoices(null)}
            className="w-full text-center text-xs font-medium text-slate-500 hover:text-slate-700 py-1"
          >
            ← Back to credentials
          </button>
        </div>
      ) : (
        <form className="space-y-5" onSubmit={handleSubmit}>
          <div>
            <label htmlFor="email" className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
              Email Address *
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="jane@example.com"
              className="w-full px-3.5 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500"
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
              Password *
            </label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3.5 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500"
            />
          </div>

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full py-2.5 px-4 text-sm font-semibold text-white bg-brand-600 hover:bg-brand-700 rounded-lg shadow-sm disabled:opacity-50 transition-colors"
          >
            {isSubmitting ? 'Signing in...' : 'Sign In'}
          </button>
        </form>
      )}

      <div className="text-center text-xs text-slate-500 pt-2">
        Don&apos;t have an account?{' '}
        <Link href="/register" className="font-semibold text-brand-600 hover:text-brand-700">
          Register account
        </Link>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <GuestRoute>
      <div className="min-h-[calc(100vh-4rem)] flex items-center justify-center py-12 px-4 sm:px-6 lg:px-8">
        <Suspense fallback={
          <div className="w-full max-w-md bg-white p-8 rounded-xl border border-slate-200 shadow-sm text-center text-sm text-slate-600">
            Loading...
          </div>
        }>
          <LoginForm />
        </Suspense>
      </div>
    </GuestRoute>
  );
}
