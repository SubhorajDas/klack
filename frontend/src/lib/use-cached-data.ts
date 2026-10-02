'use client';

import { useCallback, useSyncExternalStore } from 'react';
import { getBrowserApiCache } from './api-cache';

const serverSnapshot = () => undefined;

export function useCachedData<T>(key: string) {
  const cache = getBrowserApiCache();
  const subscribe = useCallback(
    (changed: () => void) => cache?.store.subscribe(changed) ?? (() => {}),
    [cache],
  );
  const snapshot = useCallback(() => cache?.peek<T>(key), [cache, key]);
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot);
}

export function useCachedApiData<T>(path: string) {
  return useCachedData<T>(`true:${path}`);
}
