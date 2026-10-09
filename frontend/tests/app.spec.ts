import { test, expect, type Page } from '@playwright/test';
const wid = '11111111-1111-4111-8111-111111111111';
const cid = '22222222-2222-4222-8222-222222222222';
const channelUrl = `/w/${wid}/channel/${cid}`;
test('global badges include other workspaces and clear together when alerts are read', async ({
  page,
}) => {
  const otherWorkspace = '55555555-5555-4555-8555-555555555555';
  const otherChannel = '66666666-6666-4666-8666-666666666666';
  const direct = '77777777-7777-4777-8777-777777777777';
  const counts: {
    total: number;
    direct_messages: number;
    workspaces: Record<string, number>;
    channels: Record<string, number>;
  } = {
    total: 6,
    direct_messages: 3,
    workspaces: { [wid]: 1, [otherWorkspace]: 2 },
    channels: { [cid]: 1, [otherChannel]: 2, [direct]: 3 },
  };
  await page.route('**/api/v1/workspaces', async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({
      response,
      json: {
        workspaces: [
          ...data.workspaces,
          { id: otherWorkspace, name: 'Other workspace', created_at: '2026-10-04T00:00:00Z' },
        ],
      },
    });
  });
  await page.route('**/api/v1/unread-counts', (route) => route.fulfill({ json: counts }));
  const alerts = [
    {
      channel: {
        id: otherChannel,
        workspace_id: otherWorkspace,
        name: 'remote-updates',
        visibility: 'public',
        is_member: true,
        archived_at: null,
      },
      message: {
        id: 'remote-message',
        author_user_id: 'peer',
        body: 'An update from another workspace',
        created_at: '2026-10-04T00:00:00Z',
      },
      unread_count: 2,
    },
    {
      channel: {
        id: direct,
        workspace_id: otherWorkspace,
        name: 'private',
        visibility: 'private',
        direct_key: '33333333333343338333333333333333:44444444444444448444444444444444',
        is_member: true,
        archived_at: null,
      },
      message: {
        id: 'direct-message',
        author_user_id: 'peer',
        body: 'A personal update',
        created_at: '2026-10-04T00:00:00Z',
      },
      unread_count: 3,
    },
  ];
  await page.route('**/api/v1/alerts', (route) => route.fulfill({ json: { alerts } }));
  const marked: string[] = [];
  await page.route('**/read-cursor', async (route) => {
    if (route.request().method() !== 'PUT') return route.continue();
    const channel = route.request().url().split('/channels/')[1].split('/')[0];
    marked.push(channel);
    counts.total -= counts.channels[channel];
    counts.channels[channel] = 0;
    if (channel === direct) counts.direct_messages = 0;
    else counts.workspaces[otherWorkspace] = 0;
    await route.fulfill({ json: { unread_count: 0 } });
  });
  await login(page);
  const badge = (name: string) =>
    page.getByRole('button', { name, exact: true }).locator('.unread-badge');
  await expect(badge('Direct messages')).toHaveText('3');
  await expect(badge('Alerts')).toHaveText('6');
  await expect(badge('Design team')).toHaveText('1');
  await expect(badge('Other workspace')).toHaveText('2');
  await expect(
    page.locator('.channel-link').filter({ hasText: 'product-design' }).locator('.unread-badge'),
  ).toHaveText('1');
  await page.getByRole('button', { name: 'Alerts', exact: true }).click();
  await expect(page).toHaveURL(/\/activity$/);
  await expect(page.getByRole('list', { name: 'Unread conversations' })).toContainText(
    'Other workspace',
  );
  await page.getByRole('button', { name: 'Mark shown as read', exact: true }).click();
  await expect(badge('Direct messages')).toHaveCount(0);
  await expect(badge('Other workspace')).toHaveCount(0);
  await expect(badge('Alerts')).toHaveText('1');
  expect(marked.sort()).toEqual([otherChannel, direct].sort());
  await page.screenshot({ path: 'test-results/global-unread-desktop.png' });
});
test('workspace rail switches directly and plus only offers create and join', async ({
  page,
  request,
}) => {
  await request.post('http://127.0.0.1:8100/__mode', { data: { mode: 'leave-another' } });
  await login(page);
  const workspaces = page.getByRole('group', { name: 'Workspaces', exact: true });
  await expect(workspaces.getByRole('button')).toHaveCount(2);
  await expect(
    workspaces.getByRole('button', { name: 'Design team', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await workspaces.getByRole('button', { name: 'Other team', exact: true }).click();
  await expect(page).toHaveURL(/other-workspace\/home$/);
  await expect(page.locator('.workspace-heading')).toHaveText('Other team');
  await expect(workspaces.getByRole('button', { name: 'Other team', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 600 });
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await workspaces.getByRole('button', { name: 'Design team', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/w/${wid}/home$`));
  await expect(page.locator('.sidebar')).not.toBeInViewport();
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('button', { name: 'Add workspace', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Add a workspace', exact: true });
  await expect(
    dialog.getByRole('button', { name: 'Create a workspace', exact: true }),
  ).toBeVisible();
  await expect(
    dialog.getByRole('button', { name: 'Join with an invite', exact: true }),
  ).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Leave workspace', exact: true })).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: 'Sign out', exact: true })).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Join with an invite', exact: true }).click();
  await expect(page.getByLabel('Invitation link or token')).toBeVisible();
});
test('channel sidebar fits seven channels on desktop and mobile', async ({ page }) => {
  await page.route('**/api/v1/workspaces/*/channels?include_archived=true', async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({
      response,
      json: {
        channels: Array.from({ length: 7 }, (_, index) => ({
          ...data.channels[0],
          id: index === 0 ? cid : `sidebar-channel-${index}`,
          name: [
            'product-design',
            'general',
            'engineering',
            'announcements',
            'team-updates',
            'ideas',
            'random',
          ][index],
          is_member: true,
        })),
      },
    });
  });
  await page.setViewportSize({ width: 1440, height: 760 });
  await login(page);
  const sidebar = page.locator('.sidebar');
  const list = sidebar.locator('.channel-list');
  await expect(list.locator('.channel-link:not(.subtle)')).toHaveCount(7);
  for (const name of ['Saved', 'People', 'Settings']) {
    await expect(sidebar.getByRole('button', { name, exact: true })).toHaveCount(0);
  }
  expect(await list.evaluate((element) => element.scrollHeight <= element.clientHeight)).toBe(true);
  await page.screenshot({ path: 'test-results/sidebar-desktop.png' });
  await page.getByRole('button', { name: 'Direct messages', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Direct messages', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'People', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'People', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Alerts', exact: true }).click();
  await expect(page.getByRole('heading', { name: /^Alerts/ })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 600 });
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await expect(sidebar).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
  await expect(list.getByRole('button', { name: 'random', exact: true })).toBeInViewport();
  expect(await list.evaluate((element) => element.scrollHeight <= element.clientHeight)).toBe(true);
  await page.screenshot({ path: 'test-results/sidebar-mobile.png' });
  await sidebar.getByRole('button', { name: 'Home', exact: true }).click();
  await expect(sidebar).not.toBeInViewport();
  await expect(page.getByRole('button', { name: 'Alerts', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'People', exact: true })).toBeVisible();
});
async function login(page: Page) {
  await page.goto('/login');
  await page.getByLabel('Work email').fill('maya@design.team');
  await page.getByLabel('Password', { exact: true }).fill('correct-horse-battery');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: /Welcome back, maya/ })).toBeVisible();
}
async function openChannel(page: Page) {
  await page.goto(channelUrl);
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  await expect(page.getByRole('log')).toContainText('Here’s the latest iteration');
}

test('file-only sends survive retry and reload, download, and appear in Files', async ({
  page,
  request,
}) => {
  await login(page);
  await openChannel(page);
  await expect(page.getByRole('button', { name: 'Attach files' })).toBeEnabled();
  await page.getByLabel('Choose attachments').setInputFiles({
    name: 'meeting-notes.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('Notes from our meeting'),
  });
  await expect(page.locator('.editor-attachment')).toContainText('Ready');
  await page.reload();
  await expect(page.locator('.editor-attachment')).toContainText('meeting-notes.txt');
  await request.post('http://127.0.0.1:8100/__mode', { data: { mode: 'fail-once' } });
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect(page.getByText('Response lost. Please retry.')).toBeVisible();
  await page.getByRole('button', { name: 'Retry send', exact: true }).last().click();
  const download = page
    .getByRole('log')
    .getByRole('button', { name: 'Download meeting-notes.txt' });
  await expect(download).toHaveCount(1);
  await expect(page.locator('.editor-attachment')).toHaveCount(0);
  const received = page.waitForEvent('download');
  await download.click();
  expect((await received).suggestedFilename()).toBe('meeting-notes.txt');
  await page.reload();
  await expect(download).toHaveCount(1);
  await page.getByRole('button', { name: 'Files', exact: true }).click();
  await expect(page.getByLabel('Shared files')).toContainText('meeting-notes.txt');
  await page.screenshot({ path: 'test-results/files-desktop.png', fullPage: true });
});

test('uploads retry individually, reject too many files, and can be removed', async ({
  page,
  request,
}) => {
  await login(page);
  await openChannel(page);
  await expect(page.getByRole('button', { name: 'Attach files' })).toBeEnabled();
  const input = page.getByLabel('Choose attachments');
  await input.setInputFiles(
    Array.from({ length: 6 }, (_, index) => ({
      name: `${index}.txt`,
      mimeType: 'text/plain',
      buffer: Buffer.from('a'),
    })),
  );
  await expect(page.getByText('Choose up to 5 files per message.')).toBeVisible();
  await request.post('http://127.0.0.1:8100/__mode', { data: { mode: 'upload-fail-once' } });
  await input.setInputFiles({
    name: 'retry.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('retry me'),
  });
  await expect(page.locator('.editor-attachment')).toContainText('Connection interrupted.');
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Retry upload' }).click();
  await expect(page.locator('.editor-attachment')).toContainText('Ready');
  await page.getByRole('button', { name: 'Remove retry.txt' }).click();
  await expect(page.locator('.editor-attachment')).toHaveCount(0);
});

test('files can be sent as quoted replies and in private DMs on mobile', async ({ page }) => {
  await login(page);
  await openChannel(page);
  await page.locator('article.message').first().hover();
  await page
    .locator('article.message')
    .first()
    .getByRole('button', { name: 'Reply', exact: true })
    .click();
  await expect(page.getByLabel('Replying to message')).toBeVisible();
  await page
    .getByLabel('Choose attachments')
    .setInputFiles({ name: 'reply.txt', mimeType: 'text/plain', buffer: Buffer.from('reply') });
  await expect(page.locator('.editor-attachment')).toContainText('Ready');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect(
    page.locator('article.message').filter({ hasText: 'reply.txt' }).locator('.message-quote'),
  ).toContainText('onboarding flow');
  await expect(page.getByLabel('Replying to message')).toHaveCount(0);
  await page.getByRole('button', { name: 'Direct messages', exact: true }).click();
  await page.getByRole('button', { name: 'New direct message' }).click();
  await page.getByLabel('Start a conversation').selectOption('alex-123');
  await page.getByRole('button', { name: 'Open conversation' }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('button', { name: 'Attach files' })).toBeEnabled();
  await page
    .getByLabel('Choose attachments')
    .setInputFiles({ name: 'private.txt', mimeType: 'text/plain', buffer: Buffer.from('private') });
  await expect(page.locator('.editor-attachment')).toContainText('Ready');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect(page.getByRole('log')).toContainText('private.txt');
  await page.screenshot({ path: 'test-results/files-mobile.png', fullPage: true });
});
test.beforeEach(async ({ request }) => {
  await request.post('http://127.0.0.1:8100/__reset');
});

test('rich messages preserve text, attachments, and code through draft restore, edit, and reload', async ({
  page,
  request,
}) => {
  await login(page);
  await openChannel(page);
  const composer = page.getByRole('textbox', { name: 'Message product-design', exact: true });
  await composer.fill('Before the attachment');
  await page.getByRole('button', { name: 'Attach files', exact: true }).click();
  await page
    .getByLabel('Choose attachments')
    .setInputFiles({ name: 'example.txt', mimeType: 'text/plain', buffer: Buffer.from('example') });
  await expect(page.locator('.editor-attachment')).toContainText('Ready');
  await composer.press('Control+End');
  await composer.pressSequentially('After the attachment');
  await composer.press('Shift+Enter');
  await page.getByRole('button', { name: 'Insert Markdown', exact: true }).click();
  await page
    .getByRole('textbox', { name: 'Markdown', exact: true })
    .fill('```python\nprint("hello")\n```\n\n**Final note**');
  await page.getByRole('button', { name: 'Insert', exact: true }).click();
  await expect(composer.locator('pre')).toContainText('print("hello")');
  await expect(composer.locator('strong').filter({ hasText: 'Final note' })).toContainText(
    'Final note',
  );
  await page.reload();
  await expect(composer.locator('pre')).toContainText('print("hello")');
  await expect(page.locator('.editor-attachment')).toContainText('Ready');
  await page.screenshot({ path: 'test-results/rich-composer-desktop.png', fullPage: true });
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect(composer).toHaveText('');
  const message = page.locator('article.message').filter({ hasText: 'Before the attachment' });
  await expect(message.getByRole('button', { name: 'Download example.txt' })).toBeVisible();
  await expect(message.locator('pre')).toContainText('print("hello")');
  await expect(message.locator('strong').filter({ hasText: 'Final note' })).toContainText(
    'Final note',
  );
  const stats = await (await request.get('http://127.0.0.1:8100/__stats')).json();
  const write = stats.writes.find((entry: { body?: string }) =>
    entry.body?.includes('Before the attachment'),
  );
  expect(write.document.content.map((node: { type: string }) => node.type)).toEqual([
    'paragraph',
    'attachment',
    'paragraph',
    'codeBlock',
    'paragraph',
  ]);
  expect(write.document.content[1].attrs.id).toBe(write.attachment_ids[0]);
  await message.hover();
  await message.getByRole('button', { name: 'Edit message', exact: true }).click();
  const edit = page.getByRole('dialog', { name: 'Edit message', exact: true });
  await expect(edit.locator('pre')).toContainText('print("hello")');
  await expect(edit.locator('.editor-attachment')).toContainText('example.txt');
  const input = edit.getByRole('textbox', { name: 'Message', exact: true });
  await input.press('Control+Home');
  await input.pressSequentially('Edited: ');
  await edit.getByRole('button', { name: 'Save changes' }).click();
  await page.reload();
  await expect(message).toContainText('Edited: Before the attachment');
  await expect(message.locator('pre')).toContainText('print("hello")');
  await expect(message.getByRole('button', { name: 'Download example.txt' })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.sidebar')).not.toBeInViewport();
  await page.screenshot({ path: 'test-results/rich-message-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('code and list Enter continue content, while Ctrl+Enter sends', async ({ page }) => {
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await login(page);
  await openChannel(page);
  const composer = page.getByRole('textbox', { name: 'Message product-design', exact: true });
  await page.getByRole('button', { name: 'Code block', exact: true }).click();
  await page.getByLabel('Code language').selectOption('javascript');
  await composer.pressSequentially('const a = 1;');
  await composer.press('Enter');
  await composer.pressSequentially('console.log(a);');
  await expect(composer.locator('pre')).toContainText('console.log(a);');
  await composer.press('Control+Enter');
  await expect(page.getByRole('log').locator('pre')).toContainText('const a = 1;\nconsole.log(a);');
  await page.getByRole('button', { name: 'Copy code', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Copied', exact: true })).toBeVisible();
  await expect(composer).toHaveText('');
  await page.getByRole('button', { name: 'Numbered list', exact: true }).click();
  await composer.pressSequentially('First');
  await composer.press('Enter');
  await composer.pressSequentially('Second');
  await expect(composer.locator('li')).toHaveCount(2);
  await composer.press('Control+Enter');
  await expect(page.getByRole('log').locator('ol li')).toHaveCount(2);
  await composer.pressSequentially('```python');
  await composer.press('Enter');
  await expect(composer.locator('pre')).toHaveCount(1);
  await composer.pressSequentially('print("typed fence")');
  await composer.press('Control+Enter');
  await expect(page.getByRole('log').locator('pre').last()).toContainText('print("typed fence")');
});

test('sign in, responsive workspace, channel browser and channel creation', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
  await page.screenshot({ path: 'test-results/login-desktop.png', fullPage: true });
  await login(page);
  await page.screenshot({ path: 'test-results/home-desktop.png', fullPage: true });
  await page.getByRole('button', { name: 'Browse channels', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Browse channels', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Create channel', exact: true }).last().click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Channel name').fill('design-systems');
  await dialog.getByLabel('Private', { exact: false }).check();
  await dialog.getByRole('button', { name: 'Create channel', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'design-systems', exact: false })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Start the conversation' })).toBeVisible();
});

test('real proxy transports cookies, CSRF, socket updates, edits, and deletions', async ({
  page,
}) => {
  await login(page);
  await openChannel(page);
  await page.screenshot({ path: 'test-results/channel-desktop.png', fullPage: true });
  const composer = page.getByRole('textbox', { name: 'Message product-design', exact: true });
  await composer.fill('A new idea for the team');
  await composer.press('Enter');
  await expect(
    page.getByRole('log').getByText('A new idea for the team', { exact: true }),
  ).toHaveCount(1);
  await expect(composer).toHaveText('');
  const row = page.locator('article').filter({ hasText: 'A new idea for the team' });
  await row.hover();
  await row.getByRole('button', { name: 'Edit message', exact: true }).click();
  await page
    .getByRole('dialog')
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('A better idea for the team');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  const edited = page.locator('article').filter({ hasText: 'A better idea for the team' });
  await expect(edited).toBeVisible();
  await edited.hover();
  await edited.getByRole('button', { name: 'Delete message', exact: true }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Delete message', exact: true })
    .click();
  await expect(page.getByRole('log')).not.toContainText('A better idea');
  await expect(page.getByRole('log')).toContainText('This message was deleted.');
});

test('an uncertain send retries with its original client ID and never duplicates', async ({
  page,
  request,
}) => {
  await login(page);
  await openChannel(page);
  await request.post('http://127.0.0.1:8100/__mode', { data: { mode: 'fail-once' } });
  await page
    .getByRole('textbox', { name: 'Message product-design', exact: true })
    .fill('Retry-safe message');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await expect(page.locator('.send-error')).toContainText('Response lost');
  await page.getByRole('button', { name: 'Retry send', exact: true }).first().click();
  await expect(
    page.getByRole('textbox', { name: 'Message product-design', exact: true }),
  ).toHaveText('');
  await expect(page.getByRole('log').getByText('Retry-safe message', { exact: true })).toHaveCount(
    1,
  );
  const stats = await (await request.get('http://127.0.0.1:8100/__stats')).json();
  expect(stats.writes).toHaveLength(2);
  expect(stats.writes[0].client_message_id).toBe(stats.writes[1].client_message_id);
});

test('socket revisions win the initial history race; reconnect refreshes and revocation clears content', async ({
  page,
  request,
}) => {
  await login(page);
  await request.post('http://127.0.0.1:8100/__mode', { data: { mode: 'race' } });
  await page.goto(channelUrl);
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  await expect(page.getByRole('log')).toContainText('Newer socket revision wins');
  await expect(page.getByRole('log')).not.toContainText('Here’s the latest iteration');
  await request.post('http://127.0.0.1:8100/__mode', { data: { mode: '' } });
  await request.post('http://127.0.0.1:8100/__disconnect');
  await expect(page.getByRole('log')).toContainText('Updated while disconnected');
  await request.post('http://127.0.0.1:8100/__revoke');
  await expect(page.getByText('Access unavailable', { exact: true })).toBeVisible();
  await expect(page.getByRole('log')).not.toContainText('Updated while disconnected');
  await expect(
    page.getByRole('textbox', { name: 'Message product-design', exact: true }),
  ).toHaveCount(0);
});

test('invites, profile and recovery follow the existing API contract', async ({ page }) => {
  await login(page);
  await page.getByRole('button', { name: 'People', exact: true }).click();
  await page.getByRole('button', { name: 'Invite people', exact: true }).click();
  await page.getByRole('button', { name: 'Create invite link', exact: true }).click();
  await expect(page.getByLabel('Invitation link', { exact: true })).toHaveValue(/join#token=/);
  await page.getByRole('button', { name: 'Close dialog' }).click();
  await page.getByRole('button', { name: 'Account settings', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'maya', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Send verification link', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Verification requested');
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Welcome back', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Forgot password?' }).click();
  await expect(page.getByRole('heading', { name: 'Forgot your password?' })).toBeVisible();
  await page.getByLabel('Work email').fill('maya@design.team');
  await page.getByRole('button', { name: 'Send reset link', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('If an account exists');
});

test('mobile navigation, joining, drafts and empty/search states', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.screenshot({ path: 'test-results/navigation-mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'Browse channels', exact: true }).click();
  await page
    .getByRole('row')
    .filter({ hasText: 'engineering' })
    .getByRole('button', { name: 'View', exact: true })
    .click();
  await page.getByRole('button', { name: 'Join channel', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Start the conversation' })).toBeVisible();
  await page.goto(channelUrl);
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  await page
    .getByRole('textbox', { name: 'Message product-design', exact: true })
    .fill('Keep this draft');
  await page.reload();
  await expect(
    page.getByRole('textbox', { name: 'Message product-design', exact: true }),
  ).toHaveText('Keep this draft');
  await page.screenshot({ path: 'test-results/channel-mobile.png', fullPage: true });
  await page.getByRole('textbox', { name: 'Search loaded messages' }).fill('no such message');
  await expect(page.getByRole('heading', { name: 'No messages found' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('history is readable when live transport is unavailable', async ({ page, request }) => {
  await login(page);
  await request.post('http://127.0.0.1:8100/__mode', { data: { mode: 'no-socket' } });
  await page.goto(channelUrl);
  await expect(page.getByRole('log')).toContainText('Here’s the latest iteration');
  await expect(page.getByText('Connection lost. Reconnecting…', { exact: false })).toBeVisible();
});

test('an invitation survives the create-account flow without entering a query string', async ({
  page,
}) => {
  await page.goto('/join#token=fixture-invite');
  await page.getByRole('link', { name: 'Create an account' }).click();
  await expect(page.getByRole('heading', { name: 'Create your account' })).toBeVisible();
  await page.getByLabel('Work email').fill('maya@design.team');
  await page.getByLabel('Password', { exact: true }).fill('correct-horse-battery');
  await page.getByRole('button', { name: 'Create account', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Join your team' });
  await expect(dialog.getByLabel('Invitation link or token')).toHaveValue('fixture-invite');
  await dialog.getByRole('button', { name: 'Join workspace' }).click();
  await expect(page.getByRole('heading', { name: /Welcome back, maya/ })).toBeVisible();
  expect(page.url()).not.toContain('fixture-invite');
});

test('quoted replies, reactions, and direct messages work through the browser', async ({
  page,
}) => {
  await login(page);
  await openChannel(page);
  await expect(page.locator('article.message').filter({ hasText: 'alex' }).first()).toBeVisible();
  await expect(page.locator('.message-list')).not.toContainText('Member alex-123');
  const first = page.locator('article.message').first();
  await expect(first.locator('.reaction-chip')).toHaveCount(0);
  await first.getByRole('button', { name: 'Add reaction', exact: true }).click();
  const picker = first.getByRole('dialog', { name: 'Choose a reaction' });
  await expect(picker.getByRole('button')).toHaveCount(6);
  await page.keyboard.press('Escape');
  await expect(picker).toHaveCount(0);
  await expect(first.getByRole('button', { name: 'Add reaction', exact: true })).toBeFocused();
  await first.getByRole('button', { name: 'Add reaction', exact: true }).click();
  await first.getByRole('button', { name: 'Add 👍 reaction', exact: true }).click();
  await expect(picker).toHaveCount(0);
  await expect(first.locator('.reaction-chip')).toHaveText('👍 1');
  await expect(first.getByRole('button', { name: 'Remove 👍 reaction' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await page.request.post('http://127.0.0.1:8100/__reaction', {
    data: { message_id: 'm1', emoji: '👍' },
  });
  await expect(first.locator('.reaction-chip')).toHaveText('👍 2');
  await first.getByRole('button', { name: 'Remove 👍 reaction', exact: true }).click();
  await expect(first.getByRole('button', { name: 'Add 👍 reaction', exact: true })).toHaveText(
    '👍 1',
  );
  await first.getByRole('button', { name: 'Add reaction', exact: true }).click();
  await page.screenshot({ path: 'test-results/reaction-picker-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(picker).toBeVisible();
  await expect
    .poll(() =>
      page.locator('.sidebar').evaluate((element) => element.getBoundingClientRect().right),
    )
    .toBeLessThanOrEqual(0);
  await expect(picker).toBeInViewport();
  await page.screenshot({
    path: 'test-results/reaction-picker-mobile.png',
    animations: 'disabled',
  });
  await page.locator('.composer .rich-editor-input').click();
  await expect(picker).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.locator('.composer .rich-editor-input').fill('Keep this draft');
  await first.hover();
  await first.getByRole('button', { name: 'Reply', exact: true }).click();
  await expect(page.locator('.composer .rich-editor-input')).toHaveText('Keep this draft');
  await page.getByRole('button', { name: 'Cancel reply' }).click();
  await expect(page.locator('.composer .rich-editor-input')).toHaveText('Keep this draft');
  await first.hover();
  await first.getByRole('button', { name: 'Reply', exact: true }).click();
  await page.locator('.composer .rich-editor-input').fill('A focused quoted reply');
  await page.locator('.composer .rich-editor-input').press('Enter');
  const reply = page
    .locator('article.message')
    .filter({ has: page.locator('p', { hasText: 'A focused quoted reply' }) });
  await expect(reply.locator('.message-quote')).toContainText('onboarding flow');
  await reply.getByRole('button', { name: 'View original message' }).click();
  await expect(first).toHaveClass(/message-highlight/);
  await reply.getByRole('button', { name: 'Edit message', exact: true }).click();
  await page.getByRole('dialog').getByRole('textbox').fill('Updated reply');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(reply).toHaveCount(0);
  const editedReply = page
    .locator('article.message')
    .filter({ has: page.locator('p', { hasText: 'Updated reply' }) });
  await editedReply.hover();
  await editedReply.getByRole('button', { name: 'Reply', exact: true }).click();
  await page.locator('.composer .rich-editor-input').fill('Replying to a reply');
  await page.locator('.composer .rich-editor-input').press('Enter');
  await expect(
    page
      .locator('article.message')
      .filter({ has: page.locator('p', { hasText: 'Replying to a reply' }) })
      .locator('.message-quote'),
  ).toContainText('Updated reply');
  await page.screenshot({ path: 'test-results/quoted-reply-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.composer .rich-editor-input')).toBeVisible();
  await page.screenshot({
    path: 'test-results/quoted-reply-mobile.png',
    fullPage: true,
    animations: 'disabled',
  });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole('button', { name: 'Direct messages', exact: true }).click();
  await page.getByRole('button', { name: 'New direct message' }).click();
  await page.getByLabel('Start a conversation').selectOption('alex-123');
  await page.getByRole('button', { name: 'Open conversation' }).click();
  await page.locator('.composer .rich-editor-input').fill('A private hello');
  await page.locator('.composer .rich-editor-input').press('Enter');
  await expect(page.getByRole('log')).toContainText('A private hello');
  await page.getByRole('button', { name: 'Back to direct messages', exact: true }).click();
  await page.getByRole('button', { name: /alex/ }).click();
  await expect(page.getByRole('log')).toContainText('A private hello');
  await expect(page).toHaveURL(/\/dms\/.+/);
  await page.reload();
  await expect(page.getByRole('log')).toContainText('A private hello');
  await page.getByRole('textbox', { name: 'Search loaded messages' }).fill('does not exist');
  await expect(page.getByRole('heading', { name: 'No messages found' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear search' }).click();
  await page.screenshot({ path: 'test-results/dm-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('log')).toContainText('A private hello');
  await page.getByRole('button', { name: 'Back to direct messages' }).click();
  await expect(page.getByRole('heading', { name: 'Direct messages' })).toBeVisible();
});

test('quoted drafts survive reload, retries keep their quote, and deletion redacts previews', async ({
  page,
  request,
}) => {
  await login(page);
  await openChannel(page);
  const original = page.locator('#message-m1');
  await original.hover();
  await original.getByRole('button', { name: 'Reply', exact: true }).click();
  await page.locator('.composer .rich-editor-input').fill('Saved quoted draft');
  await page.reload();
  await expect(page.locator('.composer .rich-editor-input')).toHaveText('Saved quoted draft');
  await expect(page.getByLabel('Replying to message')).toContainText('onboarding flow');
  await request.post('http://127.0.0.1:8100/__mode', { data: { mode: 'fail-once' } });
  await page.locator('.composer .rich-editor-input').press('Enter');
  await expect(page.locator('.send-error')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Cancel reply' })).toBeDisabled();
  await page.reload();
  await expect(page.getByLabel('Replying to message')).toBeVisible();
  await page.getByRole('button', { name: 'Retry send', exact: true }).first().click();
  await expect(page.getByLabel('Replying to message')).toHaveCount(0);
  const reply = page
    .locator('article.message')
    .filter({ has: page.locator('p', { hasText: 'Saved quoted draft' }) });
  await expect(reply).toHaveCount(1);
  const stats = await (await request.get('http://127.0.0.1:8100/__stats')).json();
  expect(stats.writes[0].reply_to_message_id).toBe('m1');
  expect(stats.writes[1]).toEqual(stats.writes[0]);
  await original.hover();
  await original.getByRole('button', { name: 'Edit message', exact: true }).click();
  await page.getByRole('dialog').getByRole('textbox').fill('Updated original context');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(reply.locator('.message-quote')).toContainText('Updated original context');
  await original.hover();
  await original.getByRole('button', { name: 'Delete message', exact: true }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Delete message', exact: true })
    .click();
  await expect(reply.locator('.message-quote')).toContainText('Message deleted');
  await expect(reply.locator('.message-quote')).not.toContainText('Updated original context');
  await page.reload();
  await expect(reply.locator('.message-quote')).toContainText('Message deleted');
});

test('existing replies appear inline and quotes load older originals', async ({
  page,
  request,
}) => {
  await login(page);
  const channelButton = page.locator('.sidebar').getByRole('button', { name: /product-design/ });
  await expect(channelButton.getByLabel('2 unread messages')).toBeVisible();
  await channelButton.click();
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  await expect(channelButton.getByLabel('2 unread messages')).toHaveCount(0);
  await page.reload();
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  await expect(channelButton.getByLabel('2 unread messages')).toHaveCount(0);
  await request.post('http://127.0.0.1:8100/__mode', { data: { mode: 'paged-replies' } });
  await page.reload();
  await expect(page.locator('article.message')).toHaveCount(50);
  await expect(page.locator('article.message').first().locator('.message-quote')).toContainText(
    'onboarding flow',
  );
  await page
    .locator('article.message')
    .first()
    .getByRole('button', { name: 'View original message' })
    .click();
  await expect(page.locator('#message-m1')).toHaveClass(/message-highlight/);
  await expect(page.getByRole('button', { name: 'Back to latest' })).toBeVisible();
  await page.getByRole('button', { name: 'Back to latest' }).click();
  await expect(page.locator('article.message')).toHaveCount(50);
  await page.getByRole('button', { name: 'Load older messages' }).click();
  await expect(page.locator('article.message')).toHaveCount(59);
  await expect(page.locator('.message-list')).toContainText('Reply history');
});

for (const action of ['create', 'join'] as const) {
  test(`new user can access account settings and ${action} a first workspace`, async ({
    page,
    request,
  }) => {
    await request.post('http://127.0.0.1:8100/__mode', { data: { mode: 'no-workspace' } });
    const workspaceRequests: string[] = [];
    page.on('request', (request) => {
      if (request.url().includes('/api/v1/workspaces/')) workspaceRequests.push(request.url());
    });
    await page.goto('/login');
    await page.getByLabel('Work email').fill('maya@design.team');
    await page.getByLabel('Password', { exact: true }).fill('correct-horse-battery');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Your team starts here, maya' })).toBeVisible();
    for (const name of ['Browse channels', 'People', 'Settings', 'Add channels']) {
      await expect(page.locator('.sidebar').getByRole('button', { name, exact: true })).toHaveCount(
        0,
      );
    }
    await expect(page.getByRole('textbox', { name: 'Search channels' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Explore channels' })).toHaveCount(0);
    await page.getByRole('button', { name: 'Account settings', exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Security', exact: true }).click();
    await expect(page.getByText('Test browser', { exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: 'Open navigation' }).click();
    await page.getByRole('button', { name: 'Home', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Your team starts here, maya' })).toBeVisible();
    await page.screenshot({ path: `test-results/onboarding-${action}-mobile.png`, fullPage: true });
    expect(workspaceRequests).toEqual([]);
    if (action === 'create') {
      await page.getByRole('button', { name: 'Create a workspace', exact: true }).click();
      await page.getByLabel('Workspace name').fill('My first team');
      await page.getByRole('button', { name: 'Create workspace', exact: true }).click();
    } else {
      await page.getByRole('button', { name: 'Join with an invite', exact: true }).click();
      await page.getByLabel('Invitation link or token').fill('fixture-invite');
      await page.getByRole('button', { name: 'Join workspace', exact: true }).click();
    }
    await expect(page).toHaveURL(new RegExp(`/w/${wid}/home$`));
    await expect(page.getByRole('heading', { name: /Welcome back, maya/ })).toBeVisible();
    await page.getByRole('button', { name: 'Open navigation' }).click();
    await expect(page.getByRole('button', { name: 'Direct messages', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Browse channels', exact: true })).toBeVisible();
  });
}

for (const mode of ['leave-only', 'leave-another', 'last-owner']) {
  test(`leave workspace handles ${mode}`, async ({ page, request }) => {
    await request.post('http://127.0.0.1:8100/__mode', { data: { mode } });
    await login(page);
    await page.getByRole('button', { name: 'People', exact: true }).click();
    await page.getByRole('heading', { name: 'People', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Leave workspace', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Leave workspace', exact: true });
    await expect(dialog).toContainText('Design team');
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    let stats = await (await request.get('http://127.0.0.1:8100/__stats')).json();
    expect(
      stats.writes.filter((entry: { path: string }) => entry.path.endsWith('/leave')),
    ).toHaveLength(0);
    await page.getByRole('button', { name: 'Leave workspace', exact: true }).click();
    await dialog.getByRole('button', { name: 'Leave workspace', exact: true }).click();
    if (mode === 'last-owner') {
      await expect(dialog.getByRole('alert')).toContainText('Make another member an owner');
      await expect(page).toHaveURL(new RegExp(`/w/${wid}/people$`));
      return;
    }
    await expect(dialog).toHaveCount(0);
    if (mode === 'leave-only') {
      await expect(
        page.getByRole('heading', { name: 'Your team starts here, maya' }),
      ).toBeVisible();
      await expect(page.getByRole('button', { name: 'People', exact: true })).toHaveCount(0);
      await page.reload();
      await expect(
        page.getByRole('heading', { name: 'Your team starts here, maya' }),
      ).toBeVisible();
    } else {
      await expect(page).toHaveURL(/other-workspace\/home$/);
      await expect(page.locator('.workspace-heading')).toContainText('Other team');
    }
    await page.getByRole('button', { name: 'Account settings', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
    stats = await (await request.get('http://127.0.0.1:8100/__stats')).json();
    expect(
      stats.writes.filter((entry: { path: string }) => entry.path.endsWith('/leave')),
    ).toHaveLength(1);
  });
}

test('alerts filter, search, and persist read state on mobile', async ({ page }) => {
  await login(page);
  await page.getByRole('button', { name: 'Alerts', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Alerts' })).toBeVisible();
  const inbox = page.getByRole('list', { name: 'Unread conversations' });
  await expect(inbox).toContainText('product-design');
  await page.getByRole('button', { name: 'Direct messages', exact: true }).last().click();
  await expect(page.getByText('No matching alerts')).toBeVisible();
  await page.getByRole('button', { name: 'All alerts', exact: true }).click();
  await page.getByLabel('Search alerts').fill('does-not-exist');
  await expect(page.getByText('No matching alerts')).toBeVisible();
  await page.getByLabel('Search alerts').fill('product-design');
  await expect(inbox).toBeVisible();
  await page.screenshot({ path: 'test-results/alerts-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.sidebar')).not.toBeInViewport();
  await page.screenshot({ path: 'test-results/alerts-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await inbox.getByRole('button', { name: 'Mark as read', exact: true }).click();
  await page.getByLabel('Search alerts').fill('');
  await expect(page.getByText('You’re all caught up')).toBeVisible();
  await page.reload();
  await expect(page.getByText('You’re all caught up')).toBeVisible();
});

test('alerts keep unread items on failed mark and offer retry after load failure', async ({
  page,
}) => {
  await login(page);
  await page.route('**/api/v1/alerts', (route) =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Alerts unavailable' }),
    }),
  );
  await page.getByRole('button', { name: 'Alerts', exact: true }).click();
  await expect(page.getByText('Alerts unavailable')).toBeVisible();
  await expect(page.getByText('You’re all caught up')).toHaveCount(0);
  await page.unroute('**/api/v1/alerts');
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  const inbox = page.getByRole('list', { name: 'Unread conversations' });
  await expect(inbox).toBeVisible();
  await page.route('**/read-cursor', (route) =>
    route.request().method() === 'PUT'
      ? route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Please try again' }),
        })
      : route.continue(),
  );
  await page.getByRole('button', { name: 'Mark shown as read', exact: true }).click();
  await expect(page.getByText('Please try again')).toBeVisible();
  await expect(inbox).toContainText('product-design');
  await inbox.getByRole('button', { name: 'Open conversation' }).click();
  await expect(page).toHaveURL(new RegExp(`/channel/${cid}`));
});
