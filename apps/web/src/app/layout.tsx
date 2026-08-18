import type { Metadata } from 'next';
import './globals.css';
import { AuthProvider } from '@/context/auth-context';
import { Navbar } from '@/components/layout/Navbar';
import { OnboardingTutorialModal } from '@/components/tutorial/OnboardingTutorialModal';

export const metadata: Metadata = {
  title: 'Email Discovery SaaS',
  description: 'Ethical public business email discovery engine.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen flex flex-col bg-slate-50 text-slate-900">
        <AuthProvider>
          <Navbar />
          <main className="flex-1">{children}</main>
          <OnboardingTutorialModal />
        </AuthProvider>
      </body>
    </html>
  );
}
