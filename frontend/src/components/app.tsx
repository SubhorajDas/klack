'use client';
import { useEffect, useRef, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { api, ApiError, errorMessage } from '@/lib/api';
import type { User } from '@/lib/types';
import { Auth } from './auth';
import { WorkspaceApp } from './workspace-app';
import { CallsProvider } from './calls';
import { Alert, Loading, Logo } from './ui';

export function App() {
  const path = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const pendingInvite = useRef<string | null>(null);
  useEffect(() => {
    if (path === '/join')
      pendingInvite.current = new URLSearchParams(window.location.hash.slice(1)).get('token');
  }, [path]);
  useEffect(() => {
    try {
      document.documentElement.dataset.compact = String(
        localStorage.getItem('klack:compact') === 'true',
      );
    } catch {
      /* Browser preferences are optional. */
    }
    let cancelled = false;
    setLoading(true);
    setError('');
    api<User>('/auth/me')
      .then((value) => {
        if (!cancelled) setUser(value);
      })
      .catch((failure) => {
        if (!cancelled && !(failure instanceof ApiError && [401, 403].includes(failure.status)))
          setError(errorMessage(failure));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    const expired = () => setUser(null);
    window.addEventListener('klack:signed-out', expired);
    return () => {
      cancelled = true;
      window.removeEventListener('klack:signed-out', expired);
    };
  }, [attempt]);
  if (loading)
    return (
      <main className="boot">
        <Logo />
        <Loading label="Opening your workspace…" />
      </main>
    );
  if (['/recover-password', '/verify-email'].includes(path))
    return <Auth key={path} path={path} signedIn={setUser} />;
  if (!user)
    return (
      <>
        <Auth
          key={path}
          path={path}
          signedIn={(value) => {
            setUser(value);
            if (pendingInvite.current) {
              router.replace(`/join#token=${encodeURIComponent(pendingInvite.current)}`);
              pendingInvite.current = null;
            } else if (path !== '/join') router.replace('/');
          }}
        />
        {error && (
          <div className="connection-notice">
            <Alert>{error}</Alert>
            <button onClick={() => setAttempt(attempt + 1)}>Try connecting again</button>
          </div>
        )}
      </>
    );
  return (
    <CallsProvider key={user.id} user={user}>
      <WorkspaceApp user={user} setUser={setUser} />
    </CallsProvider>
  );
}
