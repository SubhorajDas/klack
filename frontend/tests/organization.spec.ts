import { test, expect } from '@playwright/test';
const wid = '11111111-1111-4111-8111-111111111111';
const cid = '22222222-2222-4222-8222-222222222222';
const url = `/w/${wid}/channel/${cid}`;

test.beforeEach(async ({ page, request }) => {
  await request.post('http://127.0.0.1:8100/__reset');
  await page.goto('/login');
  await page.getByLabel('Work email').fill('maya@design.team');
  await page.getByLabel('Password', { exact: true }).fill('correct-horse-battery');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: /Welcome back, maya/ })).toBeVisible();
});

test('pins and private saves persist and open the original message', async ({ page }) => {
  await page.goto(url);
  const message = page.locator('#message-m1');
  await message.hover();
  await message.getByRole('button', { name: 'Pin message', exact: true }).click();
  await expect(message.getByRole('button', { name: 'Unpin message' })).toBeEnabled();
  await message.getByRole('button', { name: 'Save message', exact: true }).click();
  await expect(message.getByRole('button', { name: 'Unsave message' })).toBeEnabled();
  await page.getByRole('button', { name: 'Pinned', exact: true }).click();
  await expect(page.locator('.collection-card')).toHaveCount(1);
  await page.getByRole('button', { name: 'View in conversation' }).click();
  await expect(message).toHaveClass(/message-highlight/);
  await page.reload();
  await message.hover();
  await expect(message.getByRole('button', { name: 'Unpin message' })).toBeEnabled();
  await page.getByRole('button', { name: 'Saved messages', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Saved messages' })).toBeVisible();
  await expect(page.locator('.collection-card')).toHaveCount(1);
  await page
    .getByRole('textbox', { name: 'Search loaded saved messages' })
    .fill('not in the message');
  await expect(page.getByText('No matching messages', { exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: 'Search loaded saved messages' }).fill('');
  await page.getByRole('button', { name: 'View in conversation' }).click();
  await expect(page).toHaveURL(/message=m1/);
  await expect(message).toHaveClass(/message-highlight/);
  await page.getByRole('button', { name: 'Saved messages', exact: true }).click();
  await page.getByRole('button', { name: 'Remove from saved' }).click();
  await expect(page.getByText('No saved messages yet', { exact: true })).toBeVisible();
});

test('keyboard mentions become structured content and survive editing', async ({ page }) => {
  await page.goto(url);
  const input = page.getByRole('textbox', { name: 'Message product-design', exact: true });
  await input.fill('Hello @al');
  await expect(page.getByRole('option', { name: /@alex/ })).toBeVisible();
  await input.press('Enter');
  await expect(input.locator('.mention')).toHaveText('@alex');
  await expect(page.locator('.message-list .mention')).toHaveCount(0);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  const sent = page.locator('.message').filter({ has: page.locator('.mention') });
  await expect(sent.locator('.mention')).toHaveText('@alex');
  await sent.hover();
  await sent.getByRole('button', { name: 'Edit message', exact: true }).click();
  await expect(page.getByRole('dialog').locator('.mention')).toHaveText('@alex');
  await page.getByRole('dialog').getByRole('button', { name: 'Save changes' }).click();
  await expect(sent.locator('.mention')).toHaveText('@alex');
});

test('conversation preferences and timed pause can be changed and restored', async ({ page }) => {
  await page.goto(url);
  await page.getByRole('button', { name: 'Conversation notifications', exact: true }).click();
  await page.getByLabel('Mentions only', { exact: false }).click();
  await expect(page.getByLabel('Mentions only', { exact: false })).toBeChecked();
  await page.getByLabel('Mute conversation', { exact: false }).click();
  await expect(page.getByLabel('Mute conversation', { exact: false })).toBeChecked();
  await page.getByRole('button', { name: 'Close dialog' }).click();
  await page.reload();
  await page.getByRole('button', { name: 'Conversation notifications', exact: true }).click();
  await expect(page.getByLabel('Mute conversation', { exact: false })).toBeChecked();
  await page.getByRole('button', { name: 'Close dialog' }).click();
  await page.getByRole('button', { name: 'Alerts', exact: true }).click();
  await page.getByRole('combobox', { name: 'Pause alerts', exact: true }).selectOption('60');
  await expect(page.getByRole('button', { name: 'Resume alerts' })).toBeEnabled();
  await page.getByRole('button', { name: 'Resume alerts' }).click();
  await expect(page.getByRole('button', { name: 'Resume alerts' })).toHaveCount(0);
  await page.screenshot({ path: 'test-results/notification-controls.png' });
});

test('saved messages fit a mobile screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(url);
  const message = page.locator('#message-m1');
  await message.getByRole('button', { name: 'Save message', exact: true }).click();
  await page.goto('/saved');
  await expect(page.locator('.collection-card')).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/saved-mobile.png' });
});

test('background collection refresh keeps older loaded saved messages', async ({ page }) => {
  const items = Array.from({ length: 55 }, (_, index) => ({
    channel: {
      id: cid,
      workspace_id: wid,
      name: 'product-design',
      is_member: true,
      visibility: 'public',
      archived_at: null,
    },
    message: {
      id: `saved-${index}`,
      workspace_id: wid,
      channel_id: cid,
      author_user_id: '33333333-3333-4333-8333-333333333333',
      body: `Saved reference ${index}`,
      created_at: '2026-10-16T00:00:00Z',
      revision: 1,
      deleted_at: null,
    },
  }));
  await page.route('**/api/v1/saved-messages*', (route) => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset') || 0);
    return route.fulfill({
      json: { items: items.slice(offset, offset + 50), next_offset: offset === 0 ? 50 : null },
    });
  });
  await page.goto('/saved');
  await expect(page.locator('.collection-card')).toHaveCount(50);
  await page.getByRole('button', { name: 'Load more' }).click();
  await expect(page.locator('.collection-card')).toHaveCount(55);
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.locator('.collection-card')).toHaveCount(55);
  await expect(page.getByText('Saved reference 54', { exact: true })).toBeVisible();
});
