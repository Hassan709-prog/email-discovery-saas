import React from 'react';
import { render, screen } from '@testing-library/react';
import { vi, describe, it, expect } from 'vitest';
import LandingPage from '@/app/page';
import { Navbar } from '@/components/layout/Navbar';
import { useAuth } from '@/context/auth-context';

vi.mock('@/context/auth-context', () => ({
  useAuth: vi.fn(),
}));

describe('LandingPage & Navbar Integration', () => {
  it('renders landing page hero, navigation, features, responsible use, and founder contact', () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({ user: null, status: 'unauthenticated' });

    render(
      <>
        <Navbar />
        <LandingPage />
      </>
    );

    // Verify logo and brand
    expect(screen.getByText('Email Discovery')).toBeInTheDocument();

    // Verify hero text
    expect(
      screen.getByText('Find publicly listed business emails—with evidence you can verify.')
    ).toBeInTheDocument();

    // Verify guest action buttons
    expect(screen.getByText('Start Finding Emails')).toBeInTheDocument();
    expect(screen.getByText('Log in')).toBeInTheDocument();

    // Verify analytics section title "What you can track"
    expect(screen.getByText('What you can track')).toBeInTheDocument();

    // Verify founder Hassan Malik and mailto link
    expect(screen.getByText('Built by Hassan Malik')).toBeInTheDocument();
    const mailtoLinks = screen.getAllByRole('link', { name: /hassancs709@gmail.com/i });
    expect(mailtoLinks.length).toBeGreaterThan(0);
    expect(mailtoLinks[0]).toHaveAttribute('href', 'mailto:hassancs709@gmail.com');
  });

  it('renders authenticated navigation state when user is logged in', () => {
    (useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      user: { id: 'user-1', email: 'user@example.com', display_name: 'Hassan' },
      status: 'authenticated',
    });

    render(
      <>
        <Navbar />
        <LandingPage />
      </>
    );

    expect(screen.getByText('Your Scans')).toBeInTheDocument();
    expect(screen.getByText('Hassan')).toBeInTheDocument();
    expect(screen.queryByText('Log in')).not.toBeInTheDocument();
  });
});
