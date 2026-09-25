import { test, expect, type Page } from '@playwright/test';
const wid = '11111111-1111-4111-8111-111111111111';
const cid = '22222222-2222-4222-8222-222222222222';
const channelUrl = `/w/${wid}/channel/${cid}`;
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
test.beforeEach(async ({ request }) => {
  await request.post('http://127.0.0.1:8100/__reset');
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
  await expect(composer).toHaveValue('');
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
  ).toHaveValue('');
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
  ).toHaveValue('Keep this draft');
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

test('threads, reactions, and direct messages work through the browser', async ({ page }) => {
  await login(page);
  await openChannel(page);
  await expect(page.locator('article.message').filter({ hasText: 'alex' }).first()).toBeVisible();
  await expect(page.locator('.message-list')).not.toContainText('Member alex-123');
  const first = page.locator('article.message').first();
  await first.getByRole('button', { name: 'Add 👍 reaction', exact: true }).click();
  await expect(first.getByRole('button', { name: 'Remove 👍 reaction' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await first.getByRole('button', { name: 'Reply in thread' }).click();
  const panel = page.locator('.thread-panel');
  await panel.getByRole('textbox').fill('A focused thread reply');
  await panel.getByRole('textbox').press('Enter');
  await expect(panel.getByRole('log')).toContainText('A focused thread reply');
  await expect(page.locator('.message-list')).not.toContainText('A focused thread reply');
  await expect(first.getByRole('button', { name: '1 reply' })).toBeVisible();
  await page.screenshot({ path: 'test-results/thread-desktop.png', fullPage: true });
  await panel.getByRole('button', { name: 'Edit reply', exact: true }).click();
  await page.getByRole('dialog').getByRole('textbox').fill('Updated reply');
  await page.getByRole('button', { name: 'Save reply' }).click();
  await expect(panel.getByRole('log')).toContainText('Updated reply');
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.getByRole('textbox')).toBeVisible();
  await page.screenshot({
    path: 'test-results/thread-mobile.png',
    fullPage: true,
    animations: 'disabled',
  });
  await panel.getByRole('button', { name: 'Close thread' }).click();
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole('button', { name: 'Direct messages', exact: true }).click();
  await page.getByRole('button', { name: 'New direct message' }).click();
  await page.getByLabel('Start a conversation').selectOption('alex-123');
  await page.getByRole('button', { name: 'Open conversation' }).click();
  await page.locator('.composer textarea').fill('A private hello');
  await page.locator('.composer textarea').press('Enter');
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

test('unread cursors persist and thread history paginates without entering the channel feed', async ({
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
  await request.post('http://127.0.0.1:8100/__mode', { data: { mode: 'paged-thread' } });
  await page.reload();
  await page.locator('article.message').first().getByRole('button', { name: '55 replies' }).click();
  const panel = page.locator('.thread-panel');
  await expect(panel.locator('.thread-reply')).toHaveCount(50);
  await panel.getByRole('button', { name: 'Load older replies' }).click();
  await expect(panel.locator('.thread-reply')).toHaveCount(55);
  await expect(page.locator('.message-list')).not.toContainText('Thread history');
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
    for (const name of [
      'Direct messages',
      'Browse channels',
      'People',
      'Settings',
      'Add channels',
    ]) {
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
    await page.locator('.workspace-switch').click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Leave workspace', exact: true })
      .click();
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
      await expect(page.locator('.workspace-switch')).toContainText('Other team');
    }
    await page.getByRole('button', { name: 'Account settings', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
    stats = await (await request.get('http://127.0.0.1:8100/__stats')).json();
    expect(
      stats.writes.filter((entry: { path: string }) => entry.path.endsWith('/leave')),
    ).toHaveLength(1);
  });
}
