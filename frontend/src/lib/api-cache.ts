import { configureStore, createSlice, type PayloadAction } from '@reduxjs/toolkit';

export const CACHE_TTL_MS = 10_000;
const MAX_ENTRIES = 100;
type Entry = { data: unknown; expiresAt: number };

const cache = createSlice({
  name: 'apiCache',
  initialState: { entries: {} as Record<string, Entry> },
  reducers: {
    received(state, action: PayloadAction<{ key: string; data: unknown; now: number }>) {
      const { key, data, now } = action.payload;
      // Expiry controls fetching, not rendering. Retain bounded stale snapshots
      // so a visited screen can render immediately while it revalidates.
      delete state.entries[key];
      state.entries[key] = { data, expiresAt: now + CACHE_TTL_MS };
      const keys = Object.keys(state.entries);
      for (const name of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) {
        delete state.entries[name];
      }
    },
    cleared(state) {
      state.entries = {};
    },
    metadataInvalidated(state) {
      for (const key of Object.keys(state.entries)) {
        if (!key.startsWith('conversation:')) delete state.entries[key];
      }
    },
    removed(state, action: PayloadAction<string>) {
      delete state.entries[action.payload];
    },
  },
});

// Only metadata is reusable. Realtime snapshots, permission checks, unread
// cursors, signed URLs, and call credentials must always reach the server.
export function isCacheable(path: string) {
  const resource = path.split('?')[0];
  return (
    resource === '/workspaces' ||
    resource === '/contacts' ||
    resource === '/direct-messages' ||
    /^\/workspaces\/[^/]+\/(channels|direct-messages|memberships|invitations)$/.test(resource) ||
    /^\/workspaces\/[^/]+\/channels\/[^/]+\/memberships$/.test(resource)
  );
}

export function createApiCache() {
  const store = configureStore({ reducer: { apiCache: cache.reducer }, devTools: false });
  const pending = new Map<string, Promise<unknown>>();
  let generation = 0;
  let session = 0;

  return {
    store,
    session() {
      return session;
    },
    peek<T>(key: string) {
      return store.getState().apiCache.entries[key]?.data as T | undefined;
    },
    write(key: string, data: unknown, expectedSession = session) {
      if (expectedSession !== session) return;
      store.dispatch(cache.actions.received({ key, data, now: Date.now() }));
    },
    remove(key: string) {
      store.dispatch(cache.actions.removed(key));
    },
    invalidateMetadata() {
      generation++;
      pending.clear();
      store.dispatch(cache.actions.metadataInvalidated());
    },
    clear() {
      generation++;
      session++;
      pending.clear();
      store.dispatch(cache.actions.cleared());
    },
    async read<T>(key: string, load: () => Promise<T>): Promise<T> {
      const entry = store.getState().apiCache.entries[key];
      if (entry && entry.expiresAt > Date.now()) return entry.data as T;
      const existing = pending.get(key);
      if (existing) return existing as Promise<T>;

      const version = generation;
      // Defer loading so even a synchronous failure clears the pending entry.
      const request = Promise.resolve()
        .then(load)
        .then((data) => {
          if (version === generation) {
            store.dispatch(cache.actions.received({ key, data, now: Date.now() }));
          }
          return data;
        })
        .finally(() => {
          if (pending.get(key) === request) pending.delete(key);
        });
      pending.set(key, request);
      return request;
    },
  };
}

let browserCache: ReturnType<typeof createApiCache> | undefined;
export function getBrowserApiCache() {
  // Never share user responses across server renders.
  if (typeof window === 'undefined') return undefined;
  if (!browserCache) {
    browserCache = createApiCache();
    window.addEventListener('klack:signed-out', () => browserCache?.clear());
  }
  return browserCache;
}
