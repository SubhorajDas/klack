import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import { readFileSync, existsSync } from 'node:fs';
import type { VoiceCall } from '../src/components/calls';

const uid = '33333333-3333-4333-8333-333333333333';
const peer = '44444444-4444-4444-8444-444444444444';
const wid = '11111111-1111-4111-8111-111111111111';
type State = {
  call: VoiceCall | null;
  history: VoiceCall[];
  callerDevice?: string;
  calleeDevice?: string;
  actions: string[];
  tokens?: { url: string; caller: string; callee: string };
};

async function routes(context: BrowserContext, state: State, actor = uid) {
  if (actor !== uid)
    await context.route('**/api/v1/auth/me', (route) =>
      !route.request().headers().cookie?.includes('klack_access=fixture')
        ? route.fallback()
        : route.fulfill({
            json: {
              id: actor,
              email: 'alex@example.com',
              created_at: new Date().toISOString(),
              email_verified: true,
            },
          }),
    );
  await context.route(/\/api\/v1\/(calls(?:[/?].*)?|workspaces\/.*\/calls\?.*)$/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const body = request.method() === 'POST' ? request.postDataJSON() : {};
    const device = body.device_id || url.searchParams.get('device_id');
    const snapshot = (call: VoiceCall) => ({
      ...call,
      peer_name: actor === uid ? 'alex' : 'maya',
      owned: device === (actor === uid ? state.callerDevice : state.calleeDevice),
    });
    if (url.pathname.includes('/workspaces/'))
      return route.fulfill({ json: { calls: state.history.map(snapshot), next_before: null } });
    if (url.pathname === '/api/v1/calls' && request.method() === 'GET')
      return route.fulfill({
        json: { enabled: true, calls: state.call ? [snapshot(state.call)] : [] },
      });
    if (url.pathname === '/api/v1/calls') {
      state.callerDevice = device;
      state.call = {
        id: body.request_id,
        workspace_id: wid,
        channel_id: body.channel_id,
        caller_id: uid,
        callee_id: peer,
        peer_name: 'alex',
        status: 'ringing',
        created_at: new Date().toISOString(),
        answered_at: null,
        ended_at: null,
        owned: true,
      };
      state.actions.push('start');
      return route.fulfill({ status: 201, json: snapshot(state.call) });
    }
    const action = url.pathname.split('/').at(-1)!;
    if (action === 'token') {
      if (!state.tokens)
        return route.fulfill({ status: 503, json: { detail: 'Test voice service unavailable' } });
      return route.fulfill({
        json: {
          url: state.tokens.url,
          token: actor === uid ? state.tokens.caller : state.tokens.callee,
        },
      });
    }
    if (!state.call) return route.fulfill({ status: 409, json: { detail: 'Call ended' } });
    state.actions.push(action);
    if (action === 'accept') {
      state.calleeDevice = device;
      state.call.status = 'active';
      state.call.answered_at = new Date().toISOString();
    }
    const result = snapshot(state.call);
    if (action === 'decline' || action === 'end') {
      result.status =
        action === 'decline' ? 'declined' : result.status === 'ringing' ? 'cancelled' : 'ended';
      result.ended_at = new Date().toISOString();
      state.history.unshift(result);
      state.call = null;
    }
    return route.fulfill({ json: result });
  });
}

async function login(page: Page) {
  await page.goto('/login');
  await page.getByLabel('Work email').fill('maya@design.team');
  await page.getByLabel('Password', { exact: true }).fill('correct-horse-battery');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: /Welcome back/ })).toBeVisible();
}
async function dm(page: Page) {
  await page.getByRole('button', { name: 'Direct messages', exact: true }).click();
  await page.getByRole('button', { name: 'New direct message' }).click();
  await page.getByLabel('Start a conversation').selectOption('alex-123');
  await page.getByRole('button', { name: 'Open conversation' }).click();
  await expect(page.getByRole('button', { name: 'Start call', exact: true })).toBeEnabled();
}
async function allowMic(page: Page) {
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => new MediaStream();
  });
}

test.beforeEach(async ({ request }) => {
  await request.post('http://127.0.0.1:8100/__reset');
});

test('outgoing call survives navigation, cancels, and appears in history', async ({
  page,
  context,
}) => {
  const state: State = { call: null, history: [], actions: [] };
  await routes(context, state);
  await allowMic(page);
  await login(page);
  await dm(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Voice call' })).toContainText('Calling…');
  await page.getByRole('button', { name: 'Back to direct messages', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Voice call' })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel call' }).click();
  await expect(page.getByRole('region', { name: 'Voice call' })).toHaveCount(0);
  await page.getByRole('button', { name: /alex/ }).click();
  await page.getByRole('button', { name: 'Calls', exact: true }).click();
  await expect(page.getByText('Cancelled call', { exact: true })).toBeVisible();
  expect(state.actions).toEqual(['start', 'end']);
});

test('incoming popup can be declined from anywhere and missed calls remain in history', async ({
  page,
  context,
}) => {
  const state: State = { call: null, history: [], actions: [] };
  await routes(context, state);
  await login(page);
  await dm(page);
  state.call = {
    id: crypto.randomUUID(),
    workspace_id: wid,
    channel_id: page.url().split('/').at(-1)!,
    caller_id: peer,
    callee_id: uid,
    peer_name: 'alex',
    status: 'ringing',
    created_at: new Date().toISOString(),
    answered_at: null,
    ended_at: null,
    owned: false,
  };
  await expect(page.getByRole('dialog', { name: 'Incoming voice call' })).toBeVisible();
  await page.screenshot({ path: 'test-results/incoming-call-desktop.png' });
  await page.getByRole('button', { name: 'Decline', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  state.history.push({ ...state.history[0], id: crypto.randomUUID(), status: 'missed' });
  await page.getByRole('button', { name: 'Calls', exact: true }).click();
  await expect(page.getByText('Missed call', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/call-history-mobile.png' });
});

test('disabled voice calls stop background inbox polling', async ({ page, context }) => {
  let requests = 0;
  await context.route(/\/api\/v1\/calls\?device_id=/, (route) => {
    requests++;
    return route.fulfill({ json: { enabled: false, calls: [] } });
  });
  await login(page);
  await expect.poll(() => requests).toBeGreaterThan(0);
  // Development Strict Mode may mount twice; count after initial requests settle.
  await page.waitForTimeout(500);
  const initialRequests = requests;
  // Observe more than two normal polling intervals; the disabled feature stays quiet.
  await page.waitForTimeout(5500);
  expect(requests).toBe(initialRequests);
});

test('microphone denial prevents an outgoing call', async ({ page, context }) => {
  const state: State = { call: null, history: [], actions: [] };
  await routes(context, state);
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      throw new DOMException('Denied', 'NotAllowedError');
    };
  });
  await login(page);
  await dm(page);
  await page.getByRole('button', { name: 'Start call', exact: true }).click();
  await expect(page.locator('.call-notice')).toContainText('Allow microphone access');
  expect(state.actions).toEqual([]);
});

test('two browsers exchange LiveKit audio, mute, and hang up', async ({ browser }) => {
  test.skip(
    process.env.LIVEKIT_SMOKE !== '1' || !existsSync('.env.livekit-smoke.json'),
    'Opt-in LiveKit Cloud media smoke test',
  );
  const tokens = JSON.parse(readFileSync('.env.livekit-smoke.json', 'utf8'));
  const state: State = { call: null, history: [], actions: [], tokens };
  const first = await browser.newContext({ permissions: ['microphone'] });
  const second = await browser.newContext({ permissions: ['microphone'] });
  try {
    await routes(first, state);
    await routes(second, state, peer);
    const caller = await first.newPage();
    const callee = await second.newPage();
    await login(caller);
    await login(callee);
    await callee.reload();
    await expect(callee.getByRole('heading', { name: /Welcome back, alex/ })).toBeVisible();
    await dm(caller);
    await caller.getByRole('button', { name: 'Start call', exact: true }).click();
    await expect(callee.getByRole('dialog', { name: 'Incoming voice call' })).toBeVisible();
    await callee.getByRole('button', { name: 'Accept', exact: true }).click();
    await expect(caller.getByRole('region', { name: 'Voice call' })).toContainText('Connected');
    await expect(callee.getByRole('region', { name: 'Voice call' })).toContainText('Connected');
    await expect(caller.locator('.call-audio audio')).toHaveCount(1);
    await expect(callee.locator('.call-audio audio')).toHaveCount(1);
    await caller.getByRole('button', { name: 'Mute microphone', exact: true }).click();
    await expect(
      caller.getByRole('button', { name: 'Unmute microphone', exact: true }),
    ).toHaveAttribute('aria-pressed', 'true');
    await caller.getByRole('button', { name: 'Unmute microphone', exact: true }).click();
    await expect(
      caller.getByRole('button', { name: 'Mute microphone', exact: true }),
    ).toBeEnabled();
    await caller.screenshot({ path: 'test-results/live-voice-call.png' });
    await caller.getByRole('button', { name: 'Hang up', exact: true }).click();
    await expect(caller.getByRole('region', { name: 'Voice call' })).toHaveCount(0);
    await expect(callee.getByRole('region', { name: 'Voice call' })).toHaveCount(0);
  } finally {
    await first.close();
    await second.close();
  }
});
