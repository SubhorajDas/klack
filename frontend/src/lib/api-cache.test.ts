import { afterEach, describe, expect, it, vi } from 'vitest';
import { CACHE_TTL_MS, createApiCache, getBrowserApiCache, isCacheable } from './api-cache';
import { api, ApiError } from './api';

afterEach(() => {
  getBrowserApiCache()?.clear();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('Redux API cache', () => {
  it('shares concurrent loads, reuses results, and refreshes expired data', async () => {
    vi.useFakeTimers();
    const cache = createApiCache();
    const load = vi.fn().mockResolvedValue({ channels: [] });
    await Promise.all([cache.read('channels', load), cache.read('channels', load)]);
    await cache.read('channels', load);
    expect(load).toHaveBeenCalledTimes(1);
    expect(cache.store.getState().apiCache.entries.channels.data).toEqual({ channels: [] });
    vi.advanceTimersByTime(CACHE_TTL_MS);
    await cache.read('channels', load);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('does not cache failed requests', async () => {
    const cache = createApiCache();
    const error = new ApiError(429, 'Try later', '', 30);
    const load = vi.fn().mockRejectedValueOnce(error).mockResolvedValueOnce({ ok: true });
    await expect(cache.read('one', load)).rejects.toBe(error);
    await expect(cache.read('one', load)).resolves.toEqual({ ok: true });
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('retains expired snapshots for immediate rendering while revalidating', async () => {
    vi.useFakeTimers();
    const cache = createApiCache();
    await cache.read('visited', async () => ({ name: 'Old name' }));
    vi.advanceTimersByTime(CACHE_TTL_MS + 1);
    await cache.read('other', async () => 'another screen');
    expect(cache.peek('visited')).toEqual({ name: 'Old name' });
    await cache.read('visited', async () => ({ name: 'New name' }));
    expect(cache.peek('visited')).toEqual({ name: 'New name' });
  });

  it('retains conversation previews on metadata changes but clears them on sign-out', () => {
    const cache = createApiCache();
    const session = cache.session();
    cache.write('conversation:one', { messages: [] }, session);
    cache.write('true:/workspaces', { workspaces: [] });
    cache.invalidateMetadata();
    expect(cache.peek('true:/workspaces')).toBeUndefined();
    expect(cache.peek('conversation:one')).toEqual({ messages: [] });
    cache.clear();
    cache.write('conversation:one', { messages: ['old account'] }, session);
    expect(cache.peek('conversation:one')).toBeUndefined();
  });

  it('isolates keys and bounds retained responses', async () => {
    const cache = createApiCache();
    for (let i = 0; i < 101; i++) await cache.read(`workspace-${i}`, async () => i);
    const entries = cache.store.getState().apiCache.entries;
    expect(Object.keys(entries)).toHaveLength(100);
    expect(entries['workspace-0']).toBeUndefined();
    expect(entries['workspace-100'].data).toBe(100);
  });

  it('prevents an old in-flight response from restoring cleared data', async () => {
    const cache = createApiCache();
    let finish!: (value: string) => void;
    const old = cache.read(
      'one',
      () =>
        new Promise<string>((resolve) => {
          finish = resolve;
        }),
    );
    await Promise.resolve();
    cache.clear();
    await cache.read('one', async () => 'new account');
    finish('old account');
    await old;
    expect(cache.store.getState().apiCache.entries.one.data).toBe('new account');
  });

  it('only caches reusable metadata', () => {
    expect(isCacheable('/workspaces')).toBe(true);
    expect(isCacheable('/workspaces/a/channels?include_archived=true')).toBe(true);
    expect(isCacheable('/workspaces/a/memberships')).toBe(true);
    expect(isCacheable('/workspaces/a/channels/b/memberships')).toBe(true);
    for (const path of [
      '/auth/me',
      '/calls/inbox',
      '/workspaces/a/memberships/me',
      '/workspaces/a/alerts',
      '/workspaces/a/channels/b/read-cursor',
      '/workspaces/a/channels/b/messages?limit=50',
      '/workspaces/a/files',
    ]) {
      expect(isCacheable(path)).toBe(false);
    }
  });
});

describe('API cache integration', () => {
  const browser = new EventTarget();
  function setup() {
    vi.stubGlobal('document', { cookie: '' });
    vi.stubGlobal('window', browser);
    const fetcher = vi.fn().mockImplementation(async () => Response.json({ workspaces: [] }));
    vi.stubGlobal('fetch', fetcher);
    return { fetcher };
  }

  it('uses the cache for existing callers and invalidates after mutations', async () => {
    const { fetcher } = setup();
    await api('/workspaces');
    await api('/workspaces');
    expect(fetcher).toHaveBeenCalledTimes(1);
    await api('/workspaces', 'POST', { name: 'new' });
    await api('/workspaces');
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('clears the cache when the session expires', async () => {
    const { fetcher } = setup();
    await api('/workspaces');
    browser.dispatchEvent(new Event('klack:signed-out'));
    await api('/workspaces');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('preserves metadata after message, call, and read-cursor activity', async () => {
    const { fetcher } = setup();
    await api('/workspaces');
    await api('/workspaces/a/channels/b/read-cursor', 'PUT', { message_id: 'one' });
    await api('/workspaces/a/channels/b/messages', 'POST', { body: 'hello' });
    await api('/calls/one/heartbeat', 'POST');
    await api('/workspaces');
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it('keeps query variants separate and leaves realtime reads fresh', async () => {
    const { fetcher } = setup();
    await api('/workspaces/a/channels');
    await api('/workspaces/a/channels?include_archived=true');
    await api('/workspaces/a/channels');
    await api('/workspaces/a/channels/b/messages');
    await api('/workspaces/a/channels/b/messages');
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it('clears metadata after an authorization failure', async () => {
    const { fetcher } = setup();
    await api('/workspaces');
    fetcher.mockResolvedValueOnce(Response.json({ detail: 'Denied' }, { status: 403 }));
    await expect(api('/workspaces/a/memberships/me')).rejects.toBeInstanceOf(ApiError);
    await api('/workspaces');
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
});
