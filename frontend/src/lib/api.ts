export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code = '',
    public retryAfter = 0,
  ) {
    super(message);
  }
}

async function send(path: string, method: string, body?: unknown) {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') {
    const csrf = document.cookie
      .split('; ')
      .find((item) => item.startsWith('klack_csrf='))
      ?.slice(11);
    if (csrf) headers['X-CSRF-Token'] = decodeURIComponent(csrf);
  }
  try {
    return await fetch(`/api/v1${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      cache: 'no-store',
      signal: AbortSignal.timeout(20000),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, 'Unable to connect. Check your connection and try again.');
  }
}

async function decode<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    const fields = problem.field_errors ? Object.values(problem.field_errors).flat().join(' ') : '';
    throw new ApiError(
      response.status,
      fields || problem.detail || 'Something went wrong. Please try again.',
      problem.code,
      Number(response.headers.get('Retry-After')) || 0,
    );
  }
  return response.status === 204 || response.status === 202 ? (undefined as T) : response.json();
}

let refreshing: Promise<void> | null = null;
export function refreshSession() {
  if (!refreshing) {
    // Serialize rotation across tabs as well as concurrent requests in this tab.
    const rotate = async () => {
      await decode(await send('/auth/refresh', 'POST'));
    };
    refreshing = (
      navigator.locks
        ? navigator.locks.request('klack-refresh', async () => {
            const current = await send('/auth/me', 'GET');
            if (!current.ok) await rotate();
          })
        : rotate()
    ).finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

export async function api<T>(
  path: string,
  method = 'GET',
  body?: unknown,
  recover = true,
): Promise<T> {
  let response = await send(path, method, body);
  if (
    response.status === 401 &&
    recover &&
    !['/auth/login', '/auth/register', '/auth/refresh'].includes(path)
  ) {
    try {
      await refreshSession();
      response = await send(path, method, body);
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.status))
        window.dispatchEvent(new Event('klack:signed-out'));
      throw error;
    }
  }
  return decode<T>(response);
}

export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : 'Something went wrong. Please try again.';
