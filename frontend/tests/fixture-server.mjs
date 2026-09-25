// Isolated transport fixture. Never imported by the application or used with real data.
import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';
import { WebSocketServer } from 'ws';
const wid = '11111111-1111-4111-8111-111111111111';
const cid = '22222222-2222-4222-8222-222222222222';
const uid = '33333333-3333-4333-8333-333333333333';
const user = {
  id: uid,
  email: 'maya@design.team',
  email_verified: false,
  created_at: '2026-08-01T08:00:00Z',
};
const workspace = { id: wid, name: 'Design team', created_at: user.created_at };
let channels, messages, writes, subscribers, invitations, mode;
let cursors;
let hasWorkspace;
function reset() {
  hasWorkspace = true;
  channels = [
    {
      id: cid,
      workspace_id: wid,
      name: 'product-design',
      visibility: 'public',
      is_member: true,
      archived_at: null,
    },
    {
      id: 'general',
      workspace_id: wid,
      name: 'general',
      visibility: 'public',
      is_member: true,
      archived_at: null,
    },
    {
      id: 'engineering',
      workspace_id: wid,
      name: 'engineering',
      visibility: 'public',
      is_member: false,
      archived_at: null,
    },
    {
      id: 'announcements',
      workspace_id: wid,
      name: 'announcements',
      visibility: 'public',
      is_member: true,
      archived_at: null,
    },
  ];
  messages = [
    {
      id: 'm1',
      author_user_id: uid,
      body: 'Here’s the latest iteration of the onboarding flow. Focused on a simpler, clearer first-run experience. Would love your thoughts!',
      created_at: '2026-09-24T09:14:00Z',
    },
    {
      id: 'm2',
      author_user_id: 'alex-123',
      body: 'This is looking great! The copy feels much more approachable.\nI especially like the second screen — it communicates the value really well.',
      created_at: '2026-09-24T09:28:00Z',
    },
    {
      id: 'm3',
      author_user_id: 'sam-5678',
      body: 'One small suggestion: could we try a version with a lighter illustration on the first screen? It might let the headline shine more.',
      created_at: '2026-09-24T09:37:00Z',
    },
    {
      id: 'm4',
      author_user_id: uid,
      body: 'Good call — I’ll try a lighter version and share an update later today. ✨',
      created_at: '2026-09-24T09:46:00Z',
    },
  ].map((message) => ({
    ...message,
    workspace_id: wid,
    channel_id: cid,
    revision: 1,
    edited_at: null,
    deleted_at: null,
    client_message_id: null,
  }));
  writes = [];
  subscribers = 0;
  invitations = [];
  mode = '';
  cursors = new Map();
}
reset();
const membership = { user_id: uid, workspace_id: wid, role: 'owner', joined_at: user.created_at };
const wss = new WebSocketServer({ noServer: true });
function broadcast(message) {
  for (const client of wss.clients)
    if (client.readyState === 1 && client.channel === message.channel_id)
      client.send(JSON.stringify({ type: 'message.changed', message }));
}
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1:8100');
  const path = url.pathname.replace('/api/v1', '');
  let data = '';
  for await (const chunk of req) data += chunk;
  let body;
  try {
    body = data ? JSON.parse(data) : {};
  } catch {
    body = {};
  }
  const json = (value, status = 200) => {
    res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(status === 204 ? undefined : JSON.stringify(value));
  };
  if (path === '/health') return json({ ok: true });
  if (path === '/__reset') {
    for (const client of wss.clients) client.close();
    reset();
    return json({ ok: true });
  }
  if (path === '/__mode') {
    mode = body.mode;
    if (mode === 'no-workspace') hasWorkspace = false;
    if (mode === 'paged-thread') {
      for (let index = 0; index < 55; index++)
        messages.push({
          ...messages[0],
          id: `reply-${index}`,
          parent_message_id: 'm1',
          body: `Thread history ${index}`,
          created_at: new Date(Date.parse('2026-09-24T10:00:00Z') + index * 1000).toISOString(),
        });
      messages[0].reply_count = 55;
    }
    return json({ ok: true });
  }
  if (path === '/__stats') return json({ writes, subscribers });
  if (path === '/__revoke') {
    for (const client of wss.clients)
      client.send(JSON.stringify({ type: 'subscription.revoked', channel_id: cid }));
    return json({ ok: true });
  }
  if (path === '/__disconnect') {
    messages[0] = { ...messages[0], revision: 4, body: 'Updated while disconnected' };
    for (const client of wss.clients) client.close(1012);
    return json({ ok: true });
  }
  if (req.method !== 'GET' && req.headers.origin !== 'http://127.0.0.1:3100')
    return json({ detail: 'Incorrect browser origin' }, 403);
  if (['/auth/login', '/auth/register'].includes(path)) {
    if (body.password === 'bad') return json({ detail: 'Invalid email or password.' }, 401);
    res.setHeader('Set-Cookie', [
      'klack_access=fixture; HttpOnly; Path=/api/v1; SameSite=Lax',
      'klack_csrf=fixture-csrf; Path=/; SameSite=Lax',
    ]);
    return json({ user, session: { id: 'session' } });
  }
  if (path.includes('password-recovery') || path.includes('email-verification/complete'))
    return json({}, 204);
  if (!req.headers.cookie?.includes('klack_access=fixture'))
    return json({ detail: 'Authentication required.' }, 401);
  if (req.method !== 'GET' && req.headers['x-csrf-token'] !== 'fixture-csrf')
    return json({ detail: 'CSRF token missing.' }, 403);
  if (path === '/auth/me') return json(user);
  if (path === '/auth/refresh') return json({ user });
  if (path === '/auth/email-verification/request') return json({}, 202);
  if (path === '/auth/logout') {
    res.setHeader('Set-Cookie', 'klack_access=; Path=/api/v1; Max-Age=0');
    return json({}, 204);
  }
  if (path === '/auth/sessions')
    return json({
      sessions: [
        {
          id: 'session',
          current: true,
          user_agent: 'Test browser',
          last_seen_at: user.created_at,
          expires_at: '2027-01-01',
        },
      ],
    });
  if (path === '/workspaces') {
    if (req.method === 'GET')
      return json({
        workspaces: [
          ...(hasWorkspace ? [workspace] : []),
          ...(mode === 'leave-another'
            ? [{ ...workspace, id: 'other-workspace', name: 'Other team' }]
            : []),
        ],
      });
    hasWorkspace = true;
    return json({ ...workspace, name: body.name }, 201);
  }
  if (path.endsWith('/invitations')) {
    if (req.method === 'GET') return json({ invitations });
    const invitation = {
      id: randomUUID(),
      expires_at: '2027-01-01',
      accepted_at: null,
      revoked_at: null,
    };
    invitations.push(invitation);
    return json({ invitation, invite_url: 'http://127.0.0.1:3100/join#token=fixture-invite' });
  }
  if (path === '/workspace-invitations/accept') {
    hasWorkspace = true;
    return json(membership, 201);
  }
  if (path === `/workspaces/${wid}/leave` && req.method === 'POST') {
    writes.push({ path, method: req.method });
    if (mode === 'last-owner')
      return json(
        {
          code: 'owner_invariant_violation',
          detail: 'The workspace must retain at least one owner.',
        },
        409,
      );
    hasWorkspace = false;
    return json(null, 204);
  }
  if (path.endsWith('/memberships/me')) {
    if (req.method === 'PUT') {
      const channel = channels.find((item) => path.includes(item.id));
      if (channel) channel.is_member = true;
    }
    return json({ ...membership, role: mode.startsWith('leave-') ? 'member' : membership.role });
  }
  if (path.endsWith('/memberships'))
    return json({
      memberships: [
        membership,
        { ...membership, user_id: 'alex-123', role: 'member', display_name: 'alex' },
        { ...membership, user_id: 'sam-5678', role: 'admin', display_name: 'sam' },
      ],
    });
  if (path.endsWith('/channels')) {
    if (req.method === 'GET') return json({ channels: channels.filter((c) => !c.direct_key) });
    const channel = {
      id: randomUUID(),
      workspace_id: wid,
      name: body.name,
      visibility: body.visibility,
      is_member: true,
      archived_at: null,
    };
    channels.push(channel);
    return json(channel, 201);
  }
  if (path.endsWith('/direct-messages')) {
    if (req.method === 'GET') return json({ channels: channels.filter((c) => c.direct_key) });
    let dm = channels.find((c) => c.direct_key);
    if (!dm) {
      dm = {
        id: randomUUID(),
        workspace_id: wid,
        name: 'direct',
        direct_key: `${uid.replaceAll('-', '')}:${body.user_id.replaceAll('-', '')}`,
        visibility: 'private',
        is_member: true,
        archived_at: null,
      };
      channels.push(dm);
    }
    return json(dm);
  }
  if (path.endsWith('/read-cursor')) {
    const channel = path.split('/').at(-2);
    if (body.message_id) {
      const message = messages.find((m) => m.id === body.message_id);
      if (
        message &&
        (!cursors.has(channel) || message.created_at > cursors.get(channel).created_at)
      )
        cursors.set(channel, message);
    }
    const cursor = cursors.get(channel);
    return json({
      message_id: cursor?.id || null,
      unread_count: messages.filter(
        (m) =>
          m.channel_id === channel &&
          m.author_user_id !== uid &&
          !m.deleted_at &&
          (!cursor || m.created_at > cursor.created_at),
      ).length,
    });
  }
  if (path.includes('/reactions/')) {
    const message = messages.find((m) => m.id === path.split('/').at(-3));
    const emoji = decodeURIComponent(path.split('/').at(-1));
    if (!message) return json({}, 404);
    message.reactions = (message.reactions || []).filter((r) => r[0] !== emoji || r[1] !== uid);
    if (req.method === 'PUT') message.reactions.push([emoji, uid]);
    message.revision++;
    broadcast(message);
    return json(message);
  }
  if (path.endsWith('/messages')) {
    const channel = path.split('/').at(-2);
    if (req.method === 'GET') {
      let snapshot = messages
        .filter(
          (item) =>
            item.channel_id === channel &&
            (item.parent_message_id || null) === url.searchParams.get('parent_message_id'),
        )
        .map((item) => ({ ...item }))
        .reverse();
      if (mode === 'race') await new Promise((resolve) => setTimeout(resolve, 200));
      const before = url.searchParams.get('before');
      if (before) snapshot = snapshot.slice(snapshot.findIndex((m) => m.id === before) + 1);
      const page = snapshot.slice(0, 50);
      return json({ messages: page, next_before: snapshot.length > 50 ? page.at(-1).id : null });
    }
    writes.push(body);
    let message = messages.find((item) => item.client_message_id === body.client_message_id);
    if (!message) {
      message = {
        id: randomUUID(),
        author_user_id: uid,
        workspace_id: wid,
        channel_id: channel,
        body: body.body,
        client_message_id: body.client_message_id,
        parent_message_id: body.parent_message_id || null,
        revision: 1,
        edited_at: null,
        deleted_at: null,
        created_at: new Date().toISOString(),
      };
      messages.push(message);
      if (message.parent_message_id) {
        const root = messages.find((m) => m.id === message.parent_message_id);
        root.reply_count = (root.reply_count || 0) + 1;
        root.revision++;
        broadcast(root);
      }
    }
    broadcast(message);
    if (mode === 'fail-once') {
      mode = '';
      return json({ detail: 'Response lost. Please retry.' }, 503);
    }
    return json(message, 201);
  }
  if (path.includes('/messages/')) {
    const message = messages.find((item) => item.id === path.split('/').at(-1));
    if (!message) return json({ detail: 'Not found' }, 404);
    message.revision++;
    if (req.method === 'DELETE') {
      message.body = null;
      message.deleted_at = new Date().toISOString();
      broadcast(message);
      return json({}, 204);
    }
    message.body = body.body;
    message.edited_at = new Date().toISOString();
    broadcast(message);
    return json(message);
  }
  return json({ detail: 'Fixture route not found' }, 404);
});
server.on('upgrade', (req, socket, head) => {
  if (mode === 'no-socket') {
    socket.destroy();
    return;
  }
  if (
    req.headers.origin !== 'http://127.0.0.1:3100' ||
    !req.headers.cookie?.includes('klack_access=fixture') ||
    req.headers['sec-websocket-protocol'] !== 'klack.realtime.v1'
  ) {
    socket.destroy();
    return;
  }
  wss.handleUpgrade(req, socket, head, (client) => wss.emit('connection', client));
});
wss.on('connection', (client) => {
  client.send(JSON.stringify({ type: 'hello', access_expires_at: '2027-01-01' }));
  client.on('message', (raw) => {
    const command = JSON.parse(String(raw));
    if (command.type === 'subscribe') {
      subscribers++;
      client.channel = command.channel_id;
      client.send(JSON.stringify({ type: 'subscribed', channel_id: command.channel_id }));
      if (mode === 'race')
        setTimeout(
          () =>
            client.readyState === 1 &&
            client.send(
              JSON.stringify({
                type: 'message.changed',
                message: { ...messages[0], body: 'Newer socket revision wins', revision: 3 },
              }),
            ),
          50,
        );
      client.send(JSON.stringify({ type: 'ping' }));
    }
  });
});
server.listen(8100, '127.0.0.1');
