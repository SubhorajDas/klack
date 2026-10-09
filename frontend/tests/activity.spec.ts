import { test, expect, type Page } from '@playwright/test';
const wid = '11111111-1111-4111-8111-111111111111';
const cid = '22222222-2222-4222-8222-222222222222';
const peer = 'alex-123';
const fixture = 'http://127.0.0.1:8100';

async function login(page: Page) {
  await page.goto('/login');
  await page.getByLabel('Work email').fill('maya@design.team');
  await page.getByLabel('Password', { exact: true }).fill('correct-horse-battery');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: /Welcome back, maya/ })).toBeVisible();
}

test.beforeEach(async ({ request }) => {
  await request.post(`${fixture}/__reset`);
});

test('online status updates in message avatars and DMs, with an honest unavailable state', async ({
  page,
  request,
}) => {
  await login(page);
  await page.goto(`/w/${wid}/channel/${cid}`);
  await expect(page.getByRole('img', { name: 'alex is offline', exact: true })).toBeVisible();
  await request.post(`${fixture}/__presence`, {
    data: { users: [{ user_id: peer, online: true }] },
  });
  await expect(page.getByRole('img', { name: 'alex is online', exact: true })).toBeVisible();
  const peerId = '44444444-4444-4444-8444-444444444444';
  const opened = await page.request.post('/api/v1/direct-messages', {
    headers: { 'X-CSRF-Token': 'fixture-csrf', Origin: 'http://127.0.0.1:3100' },
    data: { user_id: peerId },
  });
  expect(opened.ok()).toBe(true);
  const dm = await opened.json();
  await request.post(`${fixture}/__presence`, {
    data: { users: [{ user_id: peerId, online: true }] },
  });
  await page.goto(`/dms/${dm.id}`);
  await expect(page.locator('.channel-header .online-label')).toHaveText('Online');
  await request.post(`${fixture}/__presence`, {
    data: { users: [{ user_id: peerId, online: false }] },
  });
  await expect(page.locator('.channel-header .online-label')).toHaveText('Offline');
  await request.post(`${fixture}/__presence`, { data: { available: false } });
  await expect(page.locator('.channel-header .online-label')).toHaveText('Status unavailable');
  await expect(page.locator('.presence-dot')).toHaveCount(0);
});

test('typing names, counts, multiple tabs, expiry, and outgoing stop', async ({
  page,
  request,
}) => {
  await login(page);
  await page.goto(`/w/${wid}/channel/${cid}`);
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  const signal = (user: string, connection: string, typing = true) =>
    request.post(`${fixture}/__activity`, {
      data: { channel_id: cid, user_id: user, connection_id: connection, typing },
    });
  await signal(peer, 'tab1');
  await expect(page.locator('.typing-indicator')).toContainText('is typing');
  await signal(peer, 'tab2');
  await expect(page.locator('.typing-indicator')).not.toContainText('Two');
  await signal('sam-5678', 'tab3');
  await expect(page.locator('.typing-indicator')).toHaveText('Two people are typing…');
  await signal(peer, 'tab1', false);
  await expect(page.locator('.typing-indicator')).toHaveText('Two people are typing…');
  await expect(page.locator('.typing-indicator')).toBeEmpty({ timeout: 9000 });
  await page
    .getByRole('textbox', { name: 'Message product-design', exact: true })
    .fill('Typing test');
  await expect
    .poll(
      async () =>
        (await (await request.get(`${fixture}/__stats`)).json()).typingCommands.at(-1)?.typing,
    )
    .toBe(true);
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`${fixture}/__stats`)).json()).typingCommands.at(-1)?.typing,
    )
    .toBe(false);
});

test('own channel message info lists readers and refreshes live', async ({ page, request }) => {
  await login(page);
  await page.goto(`/w/${wid}/channel/${cid}`);
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  await page.locator('#message-m1').hover();
  await page.locator('#message-m1').getByRole('button', { name: 'Message details' }).click();
  await expect(page.locator('.message-readers')).toContainText('No one else');
  await request.post(`${fixture}/__activity`, {
    data: {
      channel_id: cid,
      readers: [{ user_id: peer, message_id: 'm2', created_at: '2026-09-24T09:28:00Z' }],
    },
  });
  await expect(page.locator('.message-reader')).toHaveCount(1);
  await page.screenshot({ path: 'test-results/channel-read-receipts.png' });
  await page.locator('#message-m2').hover();
  await page.locator('#message-m2').getByRole('button', { name: 'Message details' }).click();
  await expect(page.locator('.message-readers')).toHaveCount(0);
});

test('DM double ticks change from grey sent to blue read and survive reload', async ({
  page,
  request,
}) => {
  await login(page);
  const opened = await page.request.post('/api/v1/direct-messages', {
    headers: { 'X-CSRF-Token': 'fixture-csrf', Origin: 'http://127.0.0.1:3100' },
    data: { user_id: '44444444-4444-4444-8444-444444444444' },
  });
  expect(opened.ok()).toBe(true);
  const dm = await opened.json();
  await page.goto(`/dms/${dm.id}`);
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: /Message/, exact: false }).fill('Read receipt test');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect(page.getByRole('img', { name: 'Sent', exact: true })).toBeVisible();
  const sent = page.locator('.message').filter({ hasText: 'Read receipt test' });
  const messageId = (await sent.getAttribute('id'))!.replace('message-', '');
  await request.post(`${fixture}/__activity`, {
    data: {
      channel_id: dm.id,
      readers: [{ user_id: peer, message_id: messageId, created_at: '2099-01-01T00:00:00Z' }],
    },
  });
  await expect(page.getByRole('img', { name: 'Read', exact: true })).toHaveClass(/read/);
  await page.screenshot({ path: 'test-results/dm-read-receipts.png' });
  await page.reload();
  await expect(page.getByRole('img', { name: 'Read', exact: true })).toBeVisible();
});
