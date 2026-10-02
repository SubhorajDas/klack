import { test, expect } from '@playwright/test';

const wid = '11111111-1111-4111-8111-111111111111';
const cid = '22222222-2222-4222-8222-222222222222';

test.beforeEach(async ({ request }) => {
  await request.post('http://127.0.0.1:8100/__reset');
});

test('visited screens render cached content while expired metadata and history are pending', async ({
  page,
  request,
}) => {
  await page.goto('/login');
  await page.getByLabel('Work email').fill('maya@design.team');
  await page.getByLabel('Password', { exact: true }).fill('correct-horse-battery');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: /Welcome back, maya/ })).toBeVisible();
  await page.goto(`/w/${wid}/channel/${cid}`);
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  await expect(page.getByRole('log')).toContainText('Here’s the latest iteration');
  // Read marking used to clear the entire cache. Let that real mutation finish.
  await page.waitForResponse(
    (response) => response.url().endsWith('/read-cursor') && response.request().method() === 'PUT',
  );
  await page.getByRole('button', { name: 'People', exact: true }).click();
  await expect(page.getByLabel('Search members')).toBeVisible();
  await page.getByRole('button', { name: 'Direct messages', exact: true }).click();
  await expect(page.getByText('Your conversations will appear here.')).toBeVisible();
  await page.getByRole('button', { name: 'Browse channels', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Browse channels', exact: true })).toBeVisible();
  await request.post('http://127.0.0.1:8100/__disconnect');

  // Expire the fetch TTL without waiting or advancing polling timers.
  await page.clock.setFixedTime(new Date(Date.now() + 60_000));
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let waiting = 0;
  await page.route('**/api/v1/workspaces/**', async (route) => {
    if (
      route.request().method() === 'GET' &&
      /\/(memberships|direct-messages|messages)(\?|$)/.test(route.request().url())
    ) {
      waiting++;
      await gate;
    }
    await route.continue();
  });
  try {
    await page.getByRole('button', { name: 'People', exact: true }).click();
    await expect(page.getByLabel('Search members')).toBeVisible();
    await expect.poll(() => waiting).toBeGreaterThan(0);
    await expect(page.getByText('Loading…', { exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Direct messages', exact: true }).click();
    await expect(page.getByText('Your conversations will appear here.')).toBeVisible();
    await expect(page.getByText('Loading…', { exact: true })).toHaveCount(0);
    await page.locator('.sidebar .channel-link').filter({ hasText: 'product-design' }).click();
    await expect(page.getByRole('log')).toContainText('Here’s the latest iteration');
    await expect(page.getByText('Connecting', { exact: true })).toBeVisible();
    await expect(page.getByText('Loading…', { exact: true })).toHaveCount(0);
  } finally {
    release();
  }
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  await expect(page.getByRole('log')).toContainText('Updated while disconnected');
  await expect(page.getByRole('log')).not.toContainText('Here’s the latest iteration');
});
