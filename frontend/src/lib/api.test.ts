import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';

function setup() {
  vi.stubGlobal('document', { cookie: 'klack_csrf=csrf-value' });
  vi.stubGlobal('navigator', {});
  vi.stubGlobal('window', { dispatchEvent: vi.fn(), addEventListener: vi.fn() });
}
afterEach(() => vi.unstubAllGlobals());
describe('browser API transport', () => {
  it('sends credentials and the current CSRF cookie on mutations', async () => {
    setup();
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetcher);
    await api('/test', 'DELETE');
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/test',
      expect.objectContaining({
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'X-CSRF-Token': 'csrf-value' },
      }),
    );
  });
  it('shares one refresh across concurrent expired-session requests', async () => {
    setup();
    let refreshed = false;
    let rotations = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.endsWith('/auth/refresh')) {
          rotations++;
          await new Promise((resolve) => setTimeout(resolve, 10));
          refreshed = true;
          return Response.json({});
        }
        return refreshed
          ? Response.json({ ok: true })
          : Response.json({ detail: 'Expired' }, { status: 401 });
      }),
    );
    const results = await Promise.all([api('/one'), api('/two')]);
    expect(results).toEqual([{ ok: true }, { ok: true }]);
    expect(rotations).toBe(1);
  });
  it('does not replay an uncertain mutation or a failed login', async () => {
    setup();
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ detail: 'Uncertain' }, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ detail: 'Bad credentials' }, { status: 401 }));
    vi.stubGlobal('fetch', fetcher);
    await expect(
      api('/messages', 'POST', { body: 'hello', client_message_id: 'stable' }),
    ).rejects.toBeInstanceOf(ApiError);
    await expect(api('/auth/login', 'POST', {})).rejects.toThrow('Bad credentials');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('surfaces refresh throttling without signing the user out', async () => {
    setup();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url.endsWith('/auth/refresh')
          ? Response.json(
              { detail: 'Try later' },
              { status: 429, headers: { 'Retry-After': '30' } },
            )
          : Response.json({}, { status: 401 }),
      ),
    );
    await expect(api('/auth/me')).rejects.toMatchObject({ status: 429, retryAfter: 30 });
    expect(window.dispatchEvent).not.toHaveBeenCalled();
  });
});
