import { test, expect } from '@playwright/test';

test.describe('Authentication Smoke Tests', () => {
  test('landing page loads and links to register and login', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/Email Discovery/);
    await expect(page.getByRole('heading', { name: /Find publicly listed business emails/i })).toBeVisible();

    const getStartedBtn = page.getByRole('link', { name: /Start Finding Emails/i });
    await expect(getStartedBtn).toBeVisible();
    await getStartedBtn.click();

    await expect(page).toHaveURL(/\/register/);
    await expect(page.getByRole('heading', { name: /Create your account/i })).toBeVisible();
  });

  test('navigation to login page loads credentials form', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByRole('heading', { name: /Sign in to your account/i })).toBeVisible();
    await expect(page.getByLabel(/Email Address/i)).toBeVisible();
    await expect(page.getByLabel(/Password/i)).toBeVisible();
  });
});
