'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { useAuth } from '@/context/auth-context';
import { Menu, X, Plus, HelpCircle, LogOut } from 'lucide-react';

export const Navbar: React.FC = () => {
  const { user, status, logout } = useAuth();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  return (
    <header className="bg-white/90 backdrop-blur-md border-b border-slate-200 sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16 items-center">
          {/* Logo & Application Brand */}
          <div className="flex items-center space-x-3">
            <Link href="/" className="text-xl font-bold text-slate-900 tracking-tight flex items-center space-x-2">
              <span className="bg-indigo-600 text-white p-1.5 rounded-lg text-sm font-semibold flex items-center justify-center w-8 h-8">
                ED
              </span>
              <span className="font-extrabold text-slate-900">Email Discovery</span>
            </Link>
          </div>

          {/* Desktop Navigation */}
          <nav className="hidden md:flex items-center space-x-6 text-sm font-medium">
            <Link
              href="/help"
              className="text-slate-600 hover:text-indigo-600 transition-colors flex items-center space-x-1"
            >
              <HelpCircle className="w-4 h-4 text-indigo-500" />
              <span>Help & Tutorial</span>
            </Link>

            {status === 'authenticated' && user ? (
              <>
                <Link
                  href="/dashboard"
                  className="text-slate-600 hover:text-indigo-600 transition-colors font-medium"
                >
                  Your Scans
                </Link>
                <Link
                  href="/scans/create"
                  className="inline-flex items-center px-3.5 py-1.5 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg shadow-sm transition-colors"
                >
                  <Plus className="w-3.5 h-3.5 mr-1" />
                  Start New Scan
                </Link>

                <div className="h-4 w-px bg-slate-200" />

                <div className="flex items-center space-x-3">
                  <span className="text-sm font-semibold text-slate-900 truncate max-w-[160px]">
                    {user.display_name || user.email}
                  </span>
                  <button
                    onClick={() => logout()}
                    className="inline-flex items-center px-2.5 py-1 text-xs font-medium text-slate-700 hover:text-slate-900 bg-slate-100 hover:bg-slate-200 rounded-md transition-colors"
                  >
                    <LogOut className="w-3 h-3 mr-1 text-slate-500" />
                    Logout
                  </button>
                </div>
              </>
            ) : (
              <>
                <Link
                  href="/login"
                  className="text-slate-600 hover:text-indigo-600 transition-colors font-medium"
                >
                  Log in
                </Link>
                <Link
                  href="/register"
                  className="px-4 py-2 text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg shadow-sm transition-colors"
                >
                  Register
                </Link>
              </>
            )}
          </nav>

          {/* Mobile Menu Toggle Button */}
          <div className="flex md:hidden">
            <button
              type="button"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="p-2 rounded-lg text-slate-600 hover:text-indigo-600 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              aria-expanded={mobileMenuOpen}
              aria-label="Toggle Navigation Menu"
            >
              {mobileMenuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
            </button>
          </div>
        </div>
      </div>

      {/* Mobile Drawer Menu */}
      {mobileMenuOpen && (
        <div className="md:hidden bg-white border-b border-slate-200 px-4 pt-3 pb-6 space-y-4 animate-in slide-in-from-top duration-150">
          <nav className="flex flex-col space-y-3 text-sm font-medium">
            <Link
              href="/help"
              onClick={() => setMobileMenuOpen(false)}
              className="text-slate-700 hover:text-indigo-600 py-1 flex items-center space-x-2"
            >
              <HelpCircle className="w-4 h-4 text-indigo-500" />
              <span>Help & Tutorial</span>
            </Link>

            {status === 'authenticated' && user ? (
              <>
                <Link
                  href="/dashboard"
                  onClick={() => setMobileMenuOpen(false)}
                  className="text-slate-700 hover:text-indigo-600 py-1"
                >
                  Your Scans
                </Link>
                <Link
                  href="/scans/create"
                  onClick={() => setMobileMenuOpen(false)}
                  className="inline-flex items-center justify-center px-4 py-2 text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg shadow-sm transition-colors"
                >
                  <Plus className="w-4 h-4 mr-1.5" />
                  Start New Scan
                </Link>
                <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-900 truncate">
                    {user.display_name || user.email}
                  </span>
                  <button
                    onClick={() => {
                      setMobileMenuOpen(false);
                      logout();
                    }}
                    className="px-3 py-1.5 text-xs font-medium text-slate-700 hover:text-slate-900 bg-slate-100 hover:bg-slate-200 rounded-md transition-colors"
                  >
                    Logout
                  </button>
                </div>
              </>
            ) : (
              <div className="pt-2 border-t border-slate-100 flex flex-col space-y-2">
                <Link
                  href="/login"
                  onClick={() => setMobileMenuOpen(false)}
                  className="text-center py-2 text-sm font-medium text-slate-700 hover:text-indigo-600 bg-slate-50 rounded-lg"
                >
                  Log in
                </Link>
                <Link
                  href="/register"
                  onClick={() => setMobileMenuOpen(false)}
                  className="text-center py-2 text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg shadow-sm"
                >
                  Register
                </Link>
              </div>
            )}
          </nav>
        </div>
      )}
    </header>
  );
};
