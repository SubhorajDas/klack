import { describe, expect, it } from 'vitest';
import { readersFor, typingLabel } from './conversation-activity';
import type { Message } from './types';

describe('read receipts', () => {
  it('includes readers at or past the message, excludes the sender and earlier positions', () => {
    const message = { id: 'b', created_at: '2026-10-09T10:00:00.123500Z' } as Message;
    const positions = [
      { user_id: 'self', message_id: 'z', created_at: '2026-10-09T11:00:00Z' },
      { user_id: 'earlier', message_id: 'z', created_at: '2026-10-09T10:00:00.123499Z' },
      { user_id: 'same-time-earlier-id', message_id: 'a', created_at: message.created_at },
      { user_id: 'at-message', message_id: 'b', created_at: message.created_at },
      { user_id: 'later', message_id: 'a', created_at: '2026-10-09T10:00:00.123501Z' },
    ];
    expect(readersFor(message, positions, 'self').map((row) => row.user_id)).toEqual([
      'at-message',
      'later',
    ]);
  });
});

describe('typing labels', () => {
  it('names one typist and counts multiple people', () => {
    expect(typingLabel([])).toBe('');
    expect(typingLabel(['Maya'])).toBe('Maya is typing…');
    expect(typingLabel(['Maya', 'Alex'])).toBe('Two people are typing…');
    expect(typingLabel(['Maya', 'Alex', 'Sam'])).toBe('Three people are typing…');
  });
});
