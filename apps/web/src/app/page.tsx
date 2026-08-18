'use client';

import React from 'react';
import Link from 'next/link';
import { useAuth } from '@/context/auth-context';

export default function LandingPage() {
  const { user } = useAuth();

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 flex flex-col font-sans selection:bg-indigo-500 selection:text-white">
      <main className="flex-1">
        {/* 2. Hero Section */}
        <section className="relative overflow-hidden bg-gradient-to-b from-indigo-50/50 via-white to-slate-50 pt-16 pb-24 lg:pt-24 lg:pb-32">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
            <div className="max-w-3xl mx-auto text-center space-y-6">
              <span className="inline-flex items-center px-3.5 py-1 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-800 border border-indigo-200">
                Ethical Business Contact Discovery
              </span>
              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-slate-900 tracking-tight leading-tight">
                Find publicly listed business emails—with evidence you can verify.
              </h1>
              <p className="text-lg sm:text-xl text-slate-600 max-w-2xl mx-auto leading-relaxed">
                Add business websites, let Email Discovery check permitted public pages, and review organized findings with their source evidence.
              </p>
              <div className="pt-4 flex flex-col sm:flex-row items-center justify-center gap-4">
                {user ? (
                  <Link
                    href="/dashboard"
                    className="w-full sm:w-auto px-8 py-3.5 text-base font-semibold text-white bg-indigo-600 hover:bg-indigo-700 rounded-xl shadow-md transition-all text-center"
                  >
                    Go to Dashboard
                  </Link>
                ) : (
                  <Link
                    href="/register"
                    className="w-full sm:w-auto px-8 py-3.5 text-base font-semibold text-white bg-indigo-600 hover:bg-indigo-700 rounded-xl shadow-md transition-all text-center"
                  >
                    Start Finding Emails
                  </Link>
                )}
                <a
                  href="#how-it-works"
                  className="w-full sm:w-auto px-8 py-3.5 text-base font-semibold text-slate-700 bg-white hover:bg-slate-100 border border-slate-300 rounded-xl shadow-sm transition-all text-center"
                >
                  See How It Works
                </a>
              </div>
            </div>
          </div>
        </section>

        {/* 3. How It Works */}
        <section id="how-it-works" className="py-20 bg-white border-t border-slate-200">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center max-w-2xl mx-auto mb-16 space-y-3">
              <h2 className="text-3xl font-bold text-slate-900 tracking-tight">How It Works</h2>
              <p className="text-base text-slate-600">Discover public contact addresses in five straightforward steps.</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-5 gap-6">
              {[
                { step: '1', title: 'Add website addresses', desc: 'Paste business target domains or website URLs directly into your scan.' },
                { step: '2', title: 'Review & correct inputs', desc: 'Automatic pre-scan check removes duplicate websites and validates domain formats.' },
                { step: '3', title: 'Start the scan', desc: 'Initiate crawler processing adhering strictly to robots.txt rules and public pages.' },
                { step: '4', title: 'Review discovered emails', desc: 'Inspect findings organized by role-based or named contact classifications.' },
                { step: '5', title: 'Export results to CSV', desc: 'Download clean CSV reports with verified web page sources for your workflow.' },
              ].map((item) => (
                <div key={item.step} className="bg-slate-50 border border-slate-200 p-6 rounded-2xl flex flex-col justify-between hover:border-indigo-300 transition-colors">
                  <div>
                    <span className="w-9 h-9 rounded-xl bg-indigo-600 text-white flex items-center justify-center font-bold text-sm mb-4">
                      {item.step}
                    </span>
                    <h3 className="font-semibold text-slate-900 mb-2 text-base">{item.title}</h3>
                    <p className="text-xs text-slate-600 leading-relaxed">{item.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* 4. Features */}
        <section id="features" className="py-20 bg-slate-50 border-t border-slate-200">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center max-w-2xl mx-auto mb-16 space-y-3">
              <h2 className="text-3xl font-bold text-slate-900 tracking-tight">Real Capabilities</h2>
              <p className="text-base text-slate-600">Built specifically for business workflows requiring verifiable contact data.</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-8">
              {[
                {
                  title: 'Checks public business pages',
                  desc: 'Scans permitted web pages to extract published business contact addresses.',
                  icon: (
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
                  ),
                },
                {
                  title: 'Removes duplicate websites',
                  desc: 'Normalizes domains and strips redundant duplicate URL entries automatically.',
                  icon: (
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                  ),
                },
                {
                  title: 'Tracks scan progress',
                  desc: 'Real-time state monitoring of queued, scanning, and completed target websites.',
                  icon: (
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                  ),
                },
                {
                  title: 'Organizes discovered emails',
                  desc: 'Classifies findings into named contacts, role-based addresses, or no-reply emails.',
                  icon: (
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
                  ),
                },
                {
                  title: 'Shows where each email was found',
                  desc: 'Every finding links to exact source web pages and surrounding text snippets.',
                  icon: (
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 21h7a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v11m0 5l4.879-4.879m0 0a3 3 0 10-4.243-4.242 3 3 0 004.243 4.242z" />
                  ),
                },
                {
                  title: 'Filters & exports findings',
                  desc: 'Search by prefix or classification and export clean CSV datasets with one click.',
                  icon: (
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  ),
                },
              ].map((feat, i) => (
                <div key={i} className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm hover:border-indigo-200 transition-all space-y-3">
                  <div className="w-10 h-10 rounded-xl bg-indigo-50 text-indigo-600 flex items-center justify-center">
                    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      {feat.icon}
                    </svg>
                  </div>
                  <h3 className="text-lg font-semibold text-slate-900">{feat.title}</h3>
                  <p className="text-xs text-slate-600 leading-relaxed">{feat.desc}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* 5. Analytics Feature Preview ("What you can track") */}
        <section id="analytics" className="py-20 bg-white border-t border-slate-200">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center max-w-2xl mx-auto mb-16 space-y-3">
              <span className="text-xs font-semibold text-indigo-600 uppercase tracking-wider">Dashboard Feature Overview</span>
              <h2 className="text-3xl font-bold text-slate-900 tracking-tight">What you can track</h2>
              <p className="text-base text-slate-600">Registered users receive real-time, tenant-isolated analytics backed by PostgreSQL data.</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              <div className="bg-slate-50 border border-slate-200 p-6 rounded-2xl space-y-3">
                <h3 className="text-base font-semibold text-slate-900 flex items-center space-x-2">
                  <span className="w-2 h-2 rounded-full bg-indigo-600"></span>
                  <span>Website Processing Metrics</span>
                </h3>
                <p className="text-xs text-slate-600 leading-relaxed">
                  Track total websites submitted, successfully processed sites, failed attempts, and overall processing completion rate across your scans.
                </p>
              </div>

              <div className="bg-slate-50 border border-slate-200 p-6 rounded-2xl space-y-3">
                <h3 className="text-base font-semibold text-slate-900 flex items-center space-x-2">
                  <span className="w-2 h-2 rounded-full bg-indigo-600"></span>
                  <span>Findings Breakdown</span>
                </h3>
                <p className="text-xs text-slate-600 leading-relaxed">
                  Monitor discoveries by contact category—separating named executive emails, general role addresses, and no-reply addresses.
                </p>
              </div>

              <div className="bg-slate-50 border border-slate-200 p-6 rounded-2xl space-y-3">
                <h3 className="text-base font-semibold text-slate-900 flex items-center space-x-2">
                  <span className="w-2 h-2 rounded-full bg-indigo-600"></span>
                  <span>Scan Activity Timelines</span>
                </h3>
                <p className="text-xs text-slate-600 leading-relaxed">
                  Review daily scan creation activity and discovery counts filtered over 7-day, 30-day, 90-day, or all-time periods.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* 6. Responsible Use */}
        <section id="responsible-use" className="py-20 bg-slate-50 border-t border-slate-200">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="max-w-3xl mx-auto bg-white p-8 sm:p-10 rounded-2xl border border-slate-200 shadow-sm space-y-6">
              <div className="flex items-center space-x-3 text-indigo-600">
                <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
                <h2 className="text-2xl font-bold text-slate-900">Responsible Use Policy</h2>
              </div>
              <p className="text-sm text-slate-600 leading-relaxed">
                Email Discovery is designed strictly for lawful public web contact indexing. We operate under explicit safety rules:
              </p>
              <ul className="space-y-3 text-sm text-slate-700">
                {[
                  'Public, non-authenticated pages only',
                  'No CAPTCHA bypass or automated solver attempting',
                  'No login-wall crawling or password-protected content access',
                  'No mailbox probing, SMTP pinging, or inbox verification',
                  'No marketing-email sending or outbound campaign execution',
                  'Users are responsible for lawful use and compliance with applicable privacy requirements',
                ].map((item, idx) => (
                  <li key={idx} className="flex items-start space-x-3">
                    <span className="text-indigo-600 font-bold mt-0.5">•</span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        {/* 7. Founder & Contact */}
        <section id="contact" className="py-20 bg-white border-t border-slate-200">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center space-y-6">
            <h2 className="text-3xl font-bold text-slate-900 tracking-tight">Founder & Contact</h2>
            <p className="text-base text-slate-600 max-w-xl mx-auto">
              Have questions or feedback about Email Discovery? Reach out directly.
            </p>
            <div className="inline-flex flex-col sm:flex-row items-center justify-center gap-4 bg-slate-50 border border-slate-200 px-6 py-4 rounded-xl">
              <span className="text-sm font-semibold text-slate-900">Built by Hassan Malik</span>
              <span className="hidden sm:inline text-slate-300">|</span>
              <a
                href="mailto:hassancs709@gmail.com"
                className="text-sm font-semibold text-indigo-600 hover:text-indigo-700 underline underline-offset-4"
              >
                hassancs709@gmail.com
              </a>
            </div>
          </div>
        </section>
      </main>

      {/* 8. Footer */}
      <footer className="bg-slate-900 text-slate-400 py-12 border-t border-slate-800 text-xs">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="space-y-1 text-center md:text-left">
            <p className="font-semibold text-white text-sm">Email Discovery SaaS</p>
            <p>Built by Hassan Malik • Contact: hassancs709@gmail.com</p>
          </div>
          <div className="flex flex-wrap justify-center gap-6">
            <a href="#how-it-works" className="hover:text-white transition-colors">How It Works</a>
            <a href="#features" className="hover:text-white transition-colors">Features</a>
            <a href="#analytics" className="hover:text-white transition-colors">Analytics</a>
            <a href="#responsible-use" className="hover:text-white transition-colors">Responsible Use</a>
          </div>
          <p>© 2026 Email Discovery. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
