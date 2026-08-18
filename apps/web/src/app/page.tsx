import Link from 'next/link';

export default function LandingPage() {
  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-16">
      <div className="text-center max-w-3xl mx-auto space-y-6">
        <div className="inline-flex items-center space-x-2 px-3 py-1 bg-brand-50 border border-brand-200 text-brand-700 text-xs font-semibold rounded-full">
          <span>Ethical & Compliant Business Discovery</span>
        </div>
        
        <h1 className="text-4xl sm:text-5xl font-extrabold text-slate-900 tracking-tight">
          Discover publicly listed business emails from web pages
        </h1>

        <p className="text-lg text-slate-600 leading-relaxed">
          High-precision email extraction from non-authenticated public web sources.
          Built strictly for compliant corporate research and lead verification.
        </p>

        <div className="flex justify-center items-center space-x-4 pt-4">
          <Link
            href="/register"
            className="px-6 py-3 text-base font-semibold text-white bg-brand-600 hover:bg-brand-700 rounded-lg shadow-sm transition-colors"
          >
            Get Started
          </Link>
          <Link
            href="/login"
            className="px-6 py-3 text-base font-semibold text-slate-700 hover:text-slate-900 bg-white border border-slate-300 hover:bg-slate-50 rounded-lg transition-colors"
          >
            Sign In
          </Link>
        </div>
      </div>

      <div className="mt-20 border-t border-slate-200 pt-12 grid grid-cols-1 md:grid-cols-3 gap-8 text-slate-600 text-sm">
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
          <h3 className="text-base font-semibold text-slate-900 mb-2">Public Web Sources Only</h3>
          <p>Extracts business contact information strictly from unauthenticated, publicly accessible website pages.</p>
        </div>
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
          <h3 className="text-base font-semibold text-slate-900 mb-2">No CAPTCHA Bypass</h3>
          <p>Strictly respects robots.txt directives, rate limits, host health safety, and website boundaries.</p>
        </div>
        <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
          <h3 className="text-base font-semibold text-slate-900 mb-2">Evidence-Backed Results</h3>
          <p>Every discovered email is linked directly to non-sensitive page snippets and origin URLs for auditability.</p>
        </div>
      </div>
    </div>
  );
}
