import { describe, expect, it } from 'vitest';
import { mergeMessages, quoteMessage } from './messages';
import type { Message } from './types';
const base: Message = {
  id: 'one',
  workspace_id: 'workspace',
  channel_id: 'channel',
  author_user_id: 'author',
  body: 'hello',
  created_at: '2026-09-24T09:00:00Z',
  edited_at: null,
  deleted_at: null,
  client_message_id: 'client',
  revision: 1,
};
describe('revision reconciliation', () => {
  it('retains a newer socket edit when stale history arrives', () => {
    const edit = { ...base, body: 'updated', revision: 2 };
    expect(mergeMessages([edit], [base])).toEqual([edit]);
    expect(mergeMessages([base], [edit])).toEqual([edit]);
  });
  it('never restores deleted content with an older edit or duplicate', () => {
    const deleted = { ...base, body: null, deleted_at: '2026-09-24T10:00:00Z', revision: 3 };
    expect(mergeMessages([deleted], [base, { ...base, revision: 2 }, deleted])).toEqual([deleted]);
  });
  it('deduplicates a REST response and its socket echo and orders history', () => {
    const older = { ...base, id: 'older', created_at: '2026-09-23T09:00:00Z' };
    expect(mergeMessages([base], [base, older])).toEqual([older, base]);
  });
  it('refreshes quotes on edits and erases deleted originals despite stale reply snapshots', () => {
    const reply = { ...base, id: 'reply', reply_to_message_id: base.id, quote: quoteMessage(base) };
    const edited = { ...base, body: 'changed original', revision: 2 };
    const updated = mergeMessages([reply], [edited]).find((m) => m.id === 'reply')!;
    expect(updated.quote?.body).toBe('changed original');
    const deleted = { ...edited, body: null, deleted_at: '2026-09-24T10:00:00Z', revision: 3 };
    const result = mergeMessages([updated], [deleted]);
    expect(mergeMessages(result, [reply]).find((m) => m.id === 'reply')?.quote).toEqual(
      quoteMessage(deleted),
    );
  });
  it('accepts a refreshed server quote even when the reply revision has not changed', () => {
    const reply = {
      ...base,
      id: 'reply',
      reply_to_message_id: 'original',
      quote: quoteMessage({ ...base, id: 'original' }),
    };
    const fresh = { ...reply, quote: { ...reply.quote, body: 'edited elsewhere', revision: 4 } };
    expect(mergeMessages([reply], [fresh])[0].quote?.body).toBe('edited elsewhere');
    expect(mergeMessages([fresh], [reply])[0].quote?.revision).toBe(4);
  });
});
