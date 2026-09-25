import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import exec from 'k6/execution';
import { randomBytes } from 'k6/crypto';

const BASE = (__ENV.BASE_URL || 'http://host.docker.internal:3000').replace(/\/$/, '');
const ORIGIN = __ENV.ORIGIN || 'http://127.0.0.1:3000';
const RUN = __ENV.RUN_ID || `k6-${Date.now()}`;
const USERS = Number(__ENV.USERS || 20);
const actions = new Counter('journeys');
const success = new Rate('journey_success');
const latency = new Trend('journey_duration', true);
const accepted = new Counter('invitations_accepted');
const errors = new Counter('api_errors');
const kinds = ['read', 'chat', 'dm', 'thread', 'react', 'channel', 'workspace', 'invite'];
const weights = [30, 25, 15, 12, 10, 3, 2, 3];
const thresholds = {
  'http_req_failed{phase:load}': ['rate<0.01'],
  'http_req_duration{phase:load}': ['p(95)<1000', 'p(99)<2000'],
  journey_success: ['rate>0.99'],
  invitations_accepted: ['count>0'],
};
for (const kind of kinds) {
  thresholds[`journeys{journey:${kind}}`] = ['count>0'];
  thresholds[`journey_success{journey:${kind}}`] = ['rate>0.99'];
  thresholds[`journey_duration{journey:${kind}}`] = ['p(95)<5000'];
}
export const options = {
  scenarios: { users: { executor: 'constant-vus', vus: USERS, duration: __ENV.DURATION || '20m', gracefulStop: '30s' } },
  noCookiesReset: true,
  setupTimeout: '5m',
  thresholds,
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response'],
};

let phase = 'setup';
function request(jar, method, path, body, name) {
  const csrf = jar.cookiesForURL(`${BASE}/api/v1/`)['klack_csrf'];
  const response = http.request(method, `${BASE}/api/v1${path}`, body == null ? null : JSON.stringify(body), {
    jar, headers: { Origin: ORIGIN, 'Content-Type': 'application/json', 'X-CSRF-Token': csrf ? csrf[0] : '' },
    tags: { name, phase }, timeout: '15s',
  });
  const ok = check(response, { [`${name}: 2xx`]: r => r.status >= 200 && r.status < 300 });
  if (!ok) {
    errors.add(1, { endpoint: name, status: String(response.status), phase });
    // Never log response bodies: invitation URLs and credentials are sensitive.
    throw new Error(`${name}: HTTP ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}
const choose = array => array[Math.floor(Math.random() * array.length)];
const wp = id => `/workspaces/${id}`;
const cp = (w, c) => `${wp(w)}/channels/${c}`;
function uuid() {
  const bytes = new Uint8Array(randomBytes(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const s = Array.from(bytes, x => x.toString(16).padStart(2, '0')).join('');
  return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`;
}
function postMessage(jar, w, c, body, parent) {
  return request(jar, 'POST', `${cp(w,c)}/messages`, { body, client_message_id: uuid(), ...(parent ? { parent_message_id: parent } : {}) }, 'message.create');
}
function inviteToken(url) {
  const marker = '#token=';
  if (!url.includes(marker)) throw new Error('Invalid invitation URL');
  return decodeURIComponent(url.split(marker)[1]);
}

export function setup() {
  if (!__ENV.MAIN_EMAIL || !__ENV.MAIN_PASSWORD) throw new Error('MAIN_EMAIL and MAIN_PASSWORD required');
  if (USERS < 2 || USERS > 20) throw new Error('USERS must be 2–20');
  const users = [], jars = [];
  for (let i = 0; i < USERS; i++) {
    const jar = new http.CookieJar();
    const account = i === 0
      ? { email: __ENV.MAIN_EMAIL, password: __ENV.MAIN_PASSWORD }
      : { email: `${RUN}.user${i}@loadtest.example`, password: `K6-${uuid()}-aA1!` };
    const auth = request(jar, 'POST', `/auth/${i === 0 ? 'login' : 'register'}`, account, i === 0 ? 'auth.login' : 'auth.register');
    users.push({ id: auth.user.id, cookies: jar.cookiesForURL(`${BASE}/api/v1/auth/refresh`), authenticatedAt: Date.now() });
    jars.push(jar);
  }
  const hub = request(jars[0], 'POST', '/workspaces', { name: `K6 ${RUN} - shared hub` }, 'workspace.create');
  const channel = request(jars[0], 'POST', `${wp(hub.id)}/channels`, { name: 'load-test-lounge' }, 'channel.create');
  for (let i = 1; i < USERS; i++) {
    const invitation = request(jars[0], 'POST', `${wp(hub.id)}/invitations`, null, 'invitation.create');
    // Bootstrap manual handoff; runtime invitations are delivered through actual DMs.
    request(jars[i], 'POST', '/workspace-invitations/accept', { token: inviteToken(invitation.invite_url) }, 'invitation.accept');
    request(jars[i], 'PUT', `${cp(hub.id,channel.id)}/memberships/me`, null, 'channel.join');
    postMessage(jars[i], hub.id, channel.id, `[${RUN}] User ${i + 1} is ready for the performance test.`);
  }
  console.log(`Run ${RUN}: ${USERS} identities ready; shared hub ${hub.id}`);
  return { run: RUN, users, hub: hub.id, channel: channel.id };
}

let state;
function init(data) {
  const index = exec.vu.idInTest - 1;
  const jar = http.cookieJar();
  for (const [name, values] of Object.entries(data.users[index].cookies)) {
    const path = name === 'klack_refresh' ? '/api/v1/auth' : name === 'klack_access' ? '/api/v1' : '/';
    jar.set(BASE, name, values[0], { path });
  }
  state = { index, jar, refreshAt: data.users[index].authenticatedAt, owned: [], seen: new Set(), sequence: 0 };
}
function dm(data, target) {
  return request(state.jar, 'POST', `${wp(data.hub)}/direct-messages`, { user_id: data.users[target].id }, 'dm.open');
}
function shareInvite(data, workspace, target) {
  if (workspace.invited.has(target)) return;
  const invitation = request(state.jar, 'POST', `${wp(workspace.id)}/invitations`, null, 'invitation.create');
  const conversation = dm(data, target);
  postMessage(state.jar, data.hub, conversation.id, `[${RUN}] Join my test workspace: ${invitation.invite_url}`);
  workspace.invited.add(target);
}
function createWorkspace(data) {
  const created = request(state.jar, 'POST', '/workspaces', { name: `K6 ${RUN} - user ${state.index + 1} project ${state.owned.length + 1}` }, 'workspace.create');
  const workspace = { id: created.id, invited: new Set([state.index]) };
  state.owned.push(workspace);
  const channel = request(state.jar, 'POST', `${wp(workspace.id)}/channels`, { name: 'general' }, 'channel.create');
  postMessage(state.jar, workspace.id, channel.id, `[${RUN}] New project created by user ${state.index + 1}.`);
  shareInvite(data, workspace, state.index === 0 ? 1 : 0);
}
function receiveInvites(data) {
  const conversations = request(state.jar, 'GET', `${wp(data.hub)}/direct-messages`, null, 'dm.list').channels;
  for (const conversation of conversations) {
    const messages = request(state.jar, 'GET', `${cp(data.hub,conversation.id)}/messages?limit=100`, null, 'dm.history').messages;
    for (const message of messages) {
      if (message.author_user_id === data.users[state.index].id || !message.body || !message.body.includes('#token=') || state.seen.has(message.id)) continue;
      const url = message.body.split(' ').pop();
      const member = request(state.jar, 'POST', '/workspace-invitations/accept', { token: inviteToken(url) }, 'invitation.accept');
      state.seen.add(message.id);
      accepted.add(1);
      const channels = request(state.jar, 'GET', `${wp(member.workspace_id)}/channels`, null, 'channel.list').channels;
      for (const channel of channels) {
        request(state.jar, 'PUT', `${cp(member.workspace_id,channel.id)}/memberships/me`, null, 'channel.join');
        postMessage(state.jar, member.workspace_id, channel.id, `[${RUN}] User ${state.index + 1} accepted the shared invitation.`);
      }
    }
  }
}
function journey(data, kind) {
  const jar = state.jar;
  const channelPath = cp(data.hub, data.channel);
  if (kind === 'workspace') {
    createWorkspace(data);
  } else if (kind === 'channel') {
    if (!state.owned.length) createWorkspace(data);
    request(jar, 'POST', `${wp(choose(state.owned).id)}/channels`, { name: `topic-${state.index}-${state.sequence++}` }, 'channel.create');
  } else if (kind === 'invite') {
    if (!state.owned.length) createWorkspace(data);
    const workspace = choose(state.owned);
    const peers = data.users.map((_, i) => i).filter(i => !workspace.invited.has(i));
    if (peers.length) shareInvite(data, workspace, choose(peers));
    receiveInvites(data);
  } else if (kind === 'dm') {
    const peers = data.users.map((_, i) => i).filter(i => i !== state.index);
    const target = state.index !== 0 && Math.random() < 0.3 ? 0 : choose(peers);
    const conversation = dm(data, target);
    postMessage(jar, data.hub, conversation.id, `[${RUN}] User ${state.index + 1}: checking in with user ${target + 1}, iteration ${exec.vu.iterationInScenario}.`);
    request(jar, 'GET', `${cp(data.hub,conversation.id)}/messages?limit=30`, null, 'dm.history');
  } else if (kind === 'chat') {
    postMessage(jar, data.hub, data.channel, `[${RUN}] User ${state.index + 1}: ${choose(['Sharing a project update.', 'Ready to review the next task.', 'The new workspace is ready.', 'Discussing the next release.'])} (${exec.vu.iterationInScenario})`);
  } else {
    const messages = request(jar, 'GET', `${channelPath}/messages?limit=50`, null, 'message.history').messages.filter(m => m.body);
    if (!messages.length) throw new Error('No root messages available');
    const root = choose(messages);
    if (kind === 'thread') {
      postMessage(jar, data.hub, data.channel, `[${RUN}] User ${state.index + 1}: replying to this discussion.`, root.id);
      request(jar, 'GET', `${channelPath}/messages?parent_message_id=${root.id}`, null, 'thread.history');
    } else if (kind === 'react') {
      request(jar, 'PUT', `${channelPath}/messages/${root.id}/reactions/${encodeURIComponent(choose(['👍','❤️','😂','🎉','👀','✅']))}`, null, 'reaction.add');
    } else {
      request(jar, 'GET', '/workspaces', null, 'workspace.list');
      request(jar, 'GET', `${wp(data.hub)}/channels`, null, 'channel.list');
      request(jar, 'PUT', `${channelPath}/read-cursor`, { message_id: messages[0].id }, 'read.advance');
    }
  }
}
export default function (data) {
  phase = 'load';
  if (!state) init(data);
  let n = Math.random() * 100, kind = kinds[0];
  for (let i = 0; i < kinds.length; i++) { n -= weights[i]; if (n < 0) { kind = kinds[i]; break; } }
  // Cover every operation once per identity before the randomized workload.
  if (exec.vu.iterationInScenario < kinds.length) kind = kinds[exec.vu.iterationInScenario];
  const start = Date.now();
  let ok = false;
  try {
    if (Date.now() - state.refreshAt > 10 * 60 * 1000) {
      request(state.jar, 'POST', '/auth/refresh', null, 'auth.refresh');
      state.refreshAt = Date.now();
    }
    journey(data, kind);
    if (exec.vu.iterationInScenario % 15 === 0) receiveInvites(data);
    ok = true;
  } catch (error) { console.warn(`VU ${state.index + 1} ${kind}: ${error.message}`); }
  actions.add(1, { journey: kind }); success.add(ok, { journey: kind }); latency.add(Date.now() - start, { journey: kind });
  sleep(2 + Math.random() * 4);
}
export function handleSummary(data) {
  const output = { run: RUN, baseUrl: BASE, users: USERS, duration: __ENV.DURATION || '20m', weights: Object.fromEntries(kinds.map((k,i) => [k, weights[i]])), ...data };
  return { [__ENV.SUMMARY_PATH || '/results/summary.json']: JSON.stringify(output, null, 2), stdout: `Run ${RUN} complete; summary saved.\n` };
}
